# Gap — Exchange event sync & automatismi engine

Data analisi: 2026-05-25  
Sessione: verifica sincronizzazione eventi exchange / automatismi engine

---

## Contesto

Indagine sulla catena completa di ricezione eventi dall'exchange e loro effetto sugli
automatismi gestiti dall'engine (deferred BE, auto-cancel averaging, trigger BE da TP).

---

## BUG CRITICI (automatismi rotti in produzione)

### BUG-1 — `_handle_cancelled_order` non include `cancelled_order_ids` nel payload

**File:** `src/runtime_v2/execution_gateway/event_sync.py`  
**Metodo:** `ExchangeEventSyncWorker._handle_cancelled_order()`

**Codice attuale:**
```python
payload = json.dumps({
    "command_id": coid.command_id,
    "position_already_open": position_already_open,
    "cancel_reason": raw.cancel_reason,
    # MANCA: "cancelled_order_ids": [client_order_id]
})
```

**Effetto:** L'evento `PENDING_ENTRY_CANCELLED_CONFIRMED` arriva al lifecycle senza
indicare quale ordine è stato cancellato. Il processor non riesce a identificare la leg
corrispondente nel piano.

**Fix richiesto:** Aggiungere `"cancelled_order_ids": [raw.client_order_id]` (o equivalente
identificativo dell'ordine cancellato) nel payload.

---

### BUG-2 — `client_order_id` nel piano è un placeholder, non l'ID exchange

**File:** `src/runtime_v2/lifecycle/execution_plan.py`  
**File correlato:** `src/runtime_v2/lifecycle/event_processor.py`

**Problema:**  
Il piano di esecuzione viene creato con `client_order_id` placeholder:
```python
# execution_plan.py — build()
client_order_id = f"place_entry:{enrichment_id}:leg{seq}"   # placeholder
client_order_id = f"place_entry_attached:{enrichment_id}:leg{seq}"  # leg 1
```

L'ID reale dell'ordine su Bybit viene generato dal gateway con formato:
```
tsb:{chain_id}:{cmd_id}:{role}:{seq}:{nonce}
```

Il placeholder **non viene mai aggiornato** nel `plan_state_json` dopo il piazzamento.

**Effetto:** Anche se BUG-1 fosse risolto e `cancelled_order_ids` contenesse
`["tsb:42:17:entry:2:abc"]`, il match in `_mark_entry_leg_status` cercherebbe
`"place_entry:1:leg2"` → **nessun match → leg mai marcata CANCELLED**.

**Fix richiesto:**  
Opzione A: Aggiornare `plan_state_json` a ogni piazzamento ordine con l'ID exchange reale.  
Opzione B (preferibile): In `_process_pending_entry_cancelled_confirmed` identificare
la leg tramite `command_id` (già presente nel payload) facendo lookup in
`ops_execution_commands` → ricavare `leg_id` dalla correlazione comando → leg.

---

### BUG-3 — `_persist_result` non espande i comandi `CANCEL_PENDING_ENTRY`

**File:** `src/runtime_v2/lifecycle/workers.py`  
**Metodo:** `LifecycleEventWorker._persist_result()`

**Problema:**  
Quando `_process_tp_filled()` emette un `CANCEL_PENDING_ENTRY` automatico
(auto-cancel averaging), il command viene inserito in `ops_execution_commands` senza
espansione. Il metodo `_expand_cancel_pending_commands()` (che traduce i comandi generici
in comandi per ogni ordine pending specifico) è chiamato **solo** da `_persist_update`
(path aggiornamento Telegram), **non** da `_persist_result` (path eventi exchange).

**Effetto:** Il gateway riceve un `CANCEL_PENDING_ENTRY` senza l'informazione su quale
`entry_client_order_id` cancellare effettivamente, oppure usa un placeholder che
non corrisponde a un ordine reale su Bybit.

**Fix richiesto:** Chiamare `_expand_cancel_pending_commands` anche in `_persist_result`
prima di inserire i comandi in DB, oppure rendere il gateway capace di ricavare
autonomamente gli ordini pending da cancellare per quella chain.

---

## CATENA DI FALLIMENTO COMPLETA — Deferred BE

Il risultato combinato dei tre bug sopra è che il meccanismo deferred BE è rotto
end-to-end in produzione:

```
TP1 colpito
  → _process_tp_filled() emette CANCEL_PENDING_ENTRY
      con entry_client_order_id = "place_entry:{id}:leg2"   [BUG-2 + BUG-3]
  → LifecycleEventWorker._persist_result() inserisce senza espansione  [BUG-3]
  → Gateway cancella l'averaging su Bybit (con ID exchange reale, lookup interno)
  → Bybit conferma cancellazione
  → ExchangeEventSyncWorker genera PENDING_ENTRY_CANCELLED_CONFIRMED
      senza cancelled_order_ids                              [BUG-1]
  → _process_pending_entry_cancelled_confirmed():
      cancelled_order_ids = []
      _mark_entry_leg_status([], CANCELLED) → nessuna leg toccata
      remaining_averaging = get_pending_averaging_legs() → [leg2]  ancora PENDING
      if not remaining_averaging: → FALSE
      → MOVE_STOP_TO_BREAKEVEN deferred non scatta MAI
```

**Conseguenza pratica:**  
- Il BE automatico dopo cancel averaging **non funziona**  
- Le leg averaging restano `PENDING` nel piano anche dopo cancellazione fisica su exchange  
- Lo stato interno diverge da quello reale dell'exchange

---

## GAP MINORI (non critici ma da correggere)

### GAP 1 — `_rehydrate_chain_from_history` legge solo eventi `DONE`

**File:** `src/runtime_v2/lifecycle/entry_gate.py`  
**Metodo:** `_rehydrate_chain_from_history()`  
**Impatto:** BE drop in race condition — Bassa ma possibile in live

**Problema:**
```python
SELECT event_type, payload_json FROM ops_exchange_events
WHERE trade_chain_id=? AND processing_status='DONE'
-- GAP: eventi in stato NEW (arrivati ma non ancora processati) vengono ignorati
```

**Scenario:** Un `ENTRY_FILLED` arriva dall'exchange ma è ancora `NEW` in DB.
In contemporanea un update Telegram (`U_MOVE_STOP`) chiama `_rehydrate_chain_from_history`.
La rehydration non vede il fill → costruisce stato chain stale → emette `MOVE_STOP_TO_BREAKEVEN`
su una chain che il lifecycle non ha ancora marcato `OPEN`.

**Fix:** Includere `processing_status IN ('NEW', 'DONE')` nella query, applicando
gli eventi in ordine cronologico (`ORDER BY received_at ASC`).

---

### GAP 2 — Fill parziali invisibili fino al fill completo

**File:** `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py`  
**Impatto:** Chain stuck in `WAITING_ENTRY` — Solo per LIMIT parziali

**Problema:**
```python
"partially_filled": "OPEN"   # → is_filled = False
```

Un ordine limit parzialmente fillato viene trattato come ancora aperto.
Nessun evento `ENTRY_FILLED` viene generato. La chain resta `WAITING_ENTRY`
e il lifecycle non aggiorna `filled_entry_qty`, `open_position_qty`, `entry_avg_price`
finché l'ordine non viene fillato completamente.

**Scenario pratico:** Ordine LIMIT BTC su book sottile — 60% fillato subito, 40%
attende. Il bot crede di non avere ancora posizione. Se arriva un update Telegram
(es. `CLOSE_FULL`) sulla chain, viene processato come se la posizione fosse assente.

**Fix:** Gestire `partially_filled` come evento separato `ENTRY_PARTIALLY_FILLED`,
oppure fare polling sulla qty reale della posizione via `get_position_qty`.

---

### GAP 3 — WebSocket non rileva cancellazioni ordini

**File:** `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`  
**Metodo:** `_process_order_batch()`  
**Impatto:** Ritardo detect cancel — Bassa, polling copre

**Problema:**
```python
if raw.is_filled:    # solo fill vengono processati
    ...
# status='Cancelled' viene silenziosamente ignorato dal WS
```

Le cancellazioni sono rilevate **solo** dal polling REST (`ExchangeEventSyncWorker`).
Se il polling è lento o offline, la detect di cancel è ritardata.

**Fix:** Aggiungere gestione `status == 'Cancelled'` in `_process_order_batch`,
generando direttamente il `PENDING_ENTRY_CANCELLED_CONFIRMED` dal WS handler.

---

### GAP 4 — `run_tp_reconciliation` misclassifica SL come `TP_FILLED`

**File:** `src/runtime_v2/execution_gateway/event_sync.py`  
**Metodo:** `run_tp_reconciliation()` → `_save_tp_fill()`  
**Impatto:** Evento tipo sbagliato — Benigno (chain chiude comunque)

**Problema:**
```python
# _save_tp_fill()
"is_final": current_qty == 0.0,   # True quando posizione = 0
# → salva evento TP_FILLED con is_final=True
```

La reconciliation rileva che `qty=0` e salva `TP_FILLED` (is_final=True).
Ma `qty=0` può significare sia TP finale colpito **sia SL colpito**.

Se lo SL viene colpito mentre c'è un TP command `DONE` in attesa di reconciliation,
la chain riceve un `TP_FILLED` invece di `SL_FILLED`.

**Perché è benigno:** Entrambi gli eventi portano la chain a `CLOSED`. L'event_processor
gestisce `is_final=True` su TP_FILLED e `SL_FILLED` allo stesso modo (terminazione chain).

**Perché va comunque fixato:**
- Reporting/audit: la chain mostra TP invece di SL come causa chiusura
- Logica futura: se si aggiunge business logic differenziata su SL vs TP (es. statistiche,
  notifiche diverse), il dato storico sarà corrotto

**Fix:** Prima di salvare `TP_FILLED`, verificare se esiste un `SL_FILLED` recente
per la stessa chain in `ops_exchange_events`, oppure confrontare il `fill_price` con
`current_stop_price` della chain per distinguere SL da TP.

---

### GAP 5 — `run_tp_reconciliation` usa `tp_size=0` per `SET_POSITION_TPSL_FULL`

**File:** `src/runtime_v2/execution_gateway/event_sync.py`  
**Metodo:** `run_tp_reconciliation()`

I comandi `SET_POSITION_TPSL_FULL` non includono `tp_size` nel payload.
```python
tp_size = float(payload.get("tp_size", 0))   # → sempre 0.0 per TP_FULL
expected_after_tp = filled_qty - 0.0         # = filled_qty
if qty < filled_qty * 0.95:                  # soglia praticamente sempre True
```
La condizione diventa quasi sempre vera → falsi positivi nella detect TP.

---

### GAP 6 — `run_position_reconciliation` rileva solo chiusure complete

**File:** `src/runtime_v2/execution_gateway/event_sync.py`  
**Metodo:** `run_position_reconciliation()`

Rileva solo `qty=0` (posizione completamente chiusa esternamente).
Chiusure parziali manuali (trader chiude metà posizione direttamente su exchange)
non vengono rilevate. Il bot continua a gestire la chain come se la posizione
fosse intera.

---

## Priorità fix suggerita

| Priorità | Riferimento | Sintomo osservato | Probabilità in live |
|----------|-------------|-------------------|---------------------|
| 🔴 P1 | BUG-1 payload `cancelled_order_ids` mancante | Deferred BE mai attivo | Certa |
| 🔴 P1 | BUG-2 match leg per `command_id` invece di placeholder | Deferred BE mai attivo | Certa |
| 🔴 P1 | BUG-3 `_persist_result` senza espansione cancel | Auto-cancel averaging non funziona | Certa |
| 🟡 P2 | GAP 1 — rehydration legge solo `DONE` | BE drop in race condition | Bassa ma possibile in live |
| 🟡 P2 | GAP 2 — partial fills invisibili | Chain stuck in `WAITING_ENTRY` | Solo per LIMIT parziali |
| 🟢 P3 | GAP 3 — WS no cancellazioni | Ritardo detect cancel | Bassa, polling copre |
| 🟢 P3 | GAP 4 — TP_FULL misclassifica SL | Evento tipo sbagliato | Benigno (chain chiude) |
| 🟢 P3 | GAP 5 — `tp_size=0` in reconciliation | Falsi positivi TP detect | Basso |
| 🟢 P3 | GAP 6 — partial close esterna | Non rilevata da reconciliation | Basso |

---

## Nota sui test esistenti

I test in `tests/runtime_v2/lifecycle/test_event_processor.py` iniettano
`PENDING_ENTRY_CANCELLED_CONFIRMED` con `{"cancelled_order_ids": ["cid_leg3"]}`
e usano piani con leg ID corrispondenti.

→ I test **passano** ma **non riproducono il comportamento production**.  
→ La divergenza test/produzione maschera BUG-1 e BUG-2.

I test andrebbero aggiornati per riflettere il payload reale prodotto da
`_handle_cancelled_order` e verificare che il fix sia corretto end-to-end.
