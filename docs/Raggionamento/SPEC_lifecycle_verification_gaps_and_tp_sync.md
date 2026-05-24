# SPEC — Lifecycle Verification Gaps e TP Sync

**Data:** 2026-05-24  
**Stato:** DRAFT — aggiornato dopo revisione architetturale  
**Branch target:** `feat/unified-execution-plan` → `main`  
**Sessione di analisi:** debugging live demo Bybit, verifica gateway + event_sync

---

## Contesto

Durante una sessione di live demo su Bybit sono emersi 4 problemi distinti nell'architettura
del sistema di verifica lifecycle / sincronizzazione TP:

1. `CLOSE_FULL` / `CLOSE_PARTIAL` falliva con `KeyError: 'qty'` — **già fixato**
2. `MOVE_STOP_TO_BREAKEVEN` / `MOVE_STOP` non producono `STOP_MOVED_CONFIRMED`
   → `be_protection_status` bloccato su `BE_MOVE_PENDING` per sempre
3. `SYNC_PROTECTIVE_ORDERS` non produce `PROTECTIVE_ORDERS_SYNCED`
   → il lifecycle non riceve feedback di conferma
4. Rilevamento hit TP per Mode C (`SET_POSITION_TPSL_*`) usa heuristica polling
   → latenza 60s, nessun `fill_price` / `filled_qty` reale

Questo documento specifica i fix per i punti 2, 3 e 4.

---

## Principio di verifica — decisione architetturale

### Bybit API sincrona vs asincrona

I comandi del sistema si dividono in due categorie con **garanzie di conferma diverse**:

**Categoria A — Ordini standalone (asincroni)**
```
PLACE_ENTRY, CLOSE_FULL, CLOSE_PARTIAL
→ Bybit accetta l'ordine (HTTP 200) ma il fill avviene in futuro
→ serve polling / WS per sapere quando è fillato
→ conferma reale = fill event da exchange
```

**Categoria B — Operazioni posizione-level (sincrone)**
```
MOVE_STOP_TO_BREAKEVEN, MOVE_STOP  → trading_stop o edit_order
SET_POSITION_TPSL_PARTIAL/FULL     → trading_stop
SYNC_PROTECTIVE_ORDERS             → trading_stop / edit_order
→ Bybit applica la modifica nel momento stesso in cui risponde
→ retCode=0 + assenza di errore = operazione eseguita
→ non esiste nulla di asincrono da aspettare
```

### Perché retCode=0 è sufficiente per la Categoria B

`trading_stop` con retCode=0 significa che Bybit ha modificato la posizione
**prima** di rispondere. Non è una promessa futura — è una conferma sincrona.

Fare una chiamata aggiuntiva `fetch_positions()` per confrontare il nuovo
stop price con quello inviato è **over-engineering**:
- aggiunge una chiamata API inutile
- introduce una race condition (il prezzo potrebbe già aver triggerato lo stop
  nell'istante tra l'esecuzione e la verifica)
- non aggiunge sicurezza reale rispetto a retCode=0

### La regola

```
retCode=0 + nessuna eccezione CCXT  →  operazione confermata  →  emetti evento lifecycle
```

Il gap attuale non è nella verifica dell'esecuzione (che c'era già tramite retCode).
Il gap è che dopo retCode=0 **nessuno emette l'evento lifecycle**.
Il gateway marcava DONE e si fermava lì.

---

## Fix 1 — STOP_MOVED_CONFIRMED e PROTECTIVE_ORDERS_SYNCED

### Problema

I comandi `MOVE_STOP_TO_BREAKEVEN`, `MOVE_STOP` e `SYNC_PROTECTIVE_ORDERS` sono
in `_FIRE_AND_FORGET`: vengono marcati `DONE` immediatamente dopo retCode=0,
senza che venga inserito alcun evento in `ops_exchange_events`.

Conseguenze:

- `be_protection_status` rimane `BE_MOVE_PENDING` per sempre dopo un BE move
- `current_stop_price` non viene mai aggiornato dopo `MOVE_STOP`
- Il lifecycle non sa mai che la sincronizzazione protettiva è completata

Il `LifecycleEventProcessor` ha già i handler:
- `_process_stop_moved_confirmed()` → aggiorna `be_protection_status = PROTECTED`, `current_stop_price`
- `PROTECTIVE_ORDERS_SYNCED` → evento informativo senza handler esplicito

Ma nessuno inserisce questi eventi in `ops_exchange_events`.

### Soluzione

Dopo retCode=0 (place_order riuscito), il gateway inserisce direttamente l'evento
corrispondente in `ops_exchange_events` e poi chiama `mark_done()`.

Nessuna chiamata API aggiuntiva di verifica — retCode=0 è già la conferma.

#### Mappa comando → evento

```python
_FIRE_AND_FORGET_EVENTS: dict[str, str] = {
    "MOVE_STOP_TO_BREAKEVEN": "STOP_MOVED_CONFIRMED",
    "MOVE_STOP":               "STOP_MOVED_CONFIRMED",
    "SYNC_PROTECTIVE_ORDERS":  "PROTECTIVE_ORDERS_SYNCED",
    # SET_POSITION_TPSL_* esclusi: il loro hit è rilevato separatamente
    # CANCEL_PENDING_ENTRY escluso: conferma arriva indirettamente via PLACE_ENTRY CANCELLED
}
```

#### Modifica gateway.py

```python
# Dopo mark_sent(), per i comandi _FIRE_AND_FORGET:
if cmd.command_type in _FIRE_AND_FORGET:
    event_type = _FIRE_AND_FORGET_EVENTS.get(cmd.command_type)
    if event_type:
        self._emit_confirmed_event(cmd=cmd, event_type=event_type, payload=payload)
    self._repo.mark_done(cmd.command_id)
```

`_emit_confirmed_event()` esegue `INSERT OR IGNORE` in `ops_exchange_events`
con `processing_status = 'NEW'`. Non fa chiamate all'exchange.

#### Payload degli eventi

**`STOP_MOVED_CONFIRMED`:**
```json
{
  "new_stop_price": "<payload.new_stop_price>",
  "is_breakeven": "<payload.is_breakeven>",
  "command_id": "<cmd.command_id>"
}
```

**`PROTECTIVE_ORDERS_SYNCED`:**
```json
{
  "command_id": "<cmd.command_id>"
}
```

#### Idempotency key

```
STOP_MOVED_CONFIRMED:{trade_chain_id}:{command_id}
PROTECTIVE_ORDERS_SYNCED:{trade_chain_id}:{command_id}
```

Usare `command_id` (non `exchange_order_id` che non esiste per queste operazioni)
garantisce unicità senza ambiguità.

#### Dipendenze

Il gateway deve poter scrivere in `ops_exchange_events`. Opzioni:
- aggiungere `insert_exchange_event(trade_chain_id, event_type, payload_json, idempotency_key)`
  a `GatewayCommandRepository` (preferita — mantiene l'accesso DB centralizzato)
- oppure passare `ops_db_path` al gateway (già presente in altri componenti)

#### Test richiesti

- `test_move_stop_to_be_emits_stop_moved_confirmed`:
  dopo `gateway.process()` con `MOVE_STOP_TO_BREAKEVEN` riuscito,
  `ops_exchange_events` contiene `event_type='STOP_MOVED_CONFIRMED'`
  con `is_breakeven=True` e `new_stop_price` corretto.

- `test_move_stop_emits_stop_moved_confirmed`:
  stesso per `MOVE_STOP` con `is_breakeven=False`.

- `test_sync_protective_orders_emits_protective_orders_synced`:
  `ops_exchange_events` contiene `PROTECTIVE_ORDERS_SYNCED`.

- `test_fire_and_forget_failed_does_not_emit_event`:
  se `place_order()` fallisce (result.success=False), nessun evento inserito.

- `test_set_tpsl_does_not_emit_direct_event`:
  `SET_POSITION_TPSL_PARTIAL/FULL` non emettono eventi lifecycle diretti.

---

## Fix 2 — watchMyTrades per rilevamento TP in tempo reale

### Problema

`SET_POSITION_TPSL_PARTIAL/FULL` usa l'API Bybit `trading_stop` (posizione-level).
Non crea un ordine standalone con `orderLinkId`. Il `BybitWsFillWatcher` attuale
usa solo `watchOrders` e filtra per `clientOrderId` → i fill TP Mode C vengono ignorati.

La rilevazione attuale usa `run_tp_reconciliation()` con heuristica position-qty:
- latenza ~60s (periodo del poll fallback)
- nessun `fill_price` / `filled_qty` reale nel payload
- `is_final` approssimato

`watchMyTrades` (CCXT Pro) riceve tutti i fill reali inclusi quelli position-level,
con `price`, `amount`, `symbol`, `side` ma **senza** `clientOrderId`.

### Prerequisito — Fix idempotency key TP_FILLED

Attualmente WS e polling usano chiavi diverse per lo stesso evento:

| Source | Chiave attuale |
|---|---|
| WS `_normalize_and_save()` | `TP_FILLED:{chain_id}:{exchange_order_id}` |
| Polling `_save_tp_fill()` | `TP_FILLED:reconciliation:{chain_id}:{tp_level}` |
| watchMyTrades (nuovo) | da definire |

Con chiavi diverse: se WS e polling rilevano entrambi il fill →
lifecycle processa `TP_FILLED` due volte → doppia riduzione `open_position_qty`,
doppio `MOVE_STOP_TO_BREAKEVEN`.

**Fix — chiave comune per tutte le source:**

```
TP_FILLED:{chain_id}:level:{tp_level}
```

File da aggiornare:
- `event_sync._save_tp_fill()` — aggiorna idempotency_key
- `event_sync._normalize_and_save()` role="tp" — usa `coid.sequence` come tp_level
- `ws_fill_watcher._save_fill()` role="tp" — usa `coid.sequence` come tp_level
- `ws_fill_watcher._save_tp_fill_from_trade()` (nuovo) — stessa chiave

### Soluzione — watchMyTrades

Aggiungere un secondo task asincrono `_watch_trades_forever()` in `BybitWsFillWatcher`
che ascolta `watchMyTrades` e inserisce `TP_FILLED` con dati reali.

#### Matching fill → chain + tp_level

I fill position-level non hanno `clientOrderId`. Il matching:

1. Filtra trade per `reduceOnly=True` (i TP chiudono posizione)
2. Cerca chain aperte (`lifecycle_state IN ('OPEN','PARTIALLY_CLOSED')`) con stesso `symbol` + `side`
3. Per ogni chain: legge i `SET_POSITION_TPSL_*` con status `DONE`
   (sono i TP attivi correntemente impostati via trading_stop)
4. Confronta `trade.price` con `payload.take_profit` (tolerance ±1% per slippage)
5. Match univoco → `tp_level = payload.tp_sequence`
6. `is_final`: letto da `trade["info"]["posQty"]` se disponibile,
   altrimenti `get_position_qty()` in step successivo

**Caso ambiguo** (2+ chain sullo stesso symbol+side con TP a prezzi simili):
skip silenzioso + polling fallback gestisce. Loggare per analisi.

#### Payload `TP_FILLED` da watchMyTrades

```json
{
  "tp_level": 1,
  "is_final": true,
  "fill_price": 67350.5,
  "filled_qty": 0.01,
  "source": "watch_my_trades",
  "exchange_trade_id": "trade-xyz"
}
```

Idempotency key: `TP_FILLED:{chain_id}:level:{tp_level}`

#### File da modificare

**`src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`**

1. `_watch_trades_forever()` — task asincrono parallelo a `_watch_orders_forever()`
2. `_process_trade_batch()` — logica matching
3. `_save_tp_fill_from_trade()` — INSERT OR IGNORE
4. `_save_fill()` role="tp" — aggiorna idempotency_key

**`src/runtime_v2/execution_gateway/event_sync.py`**

1. `_save_tp_fill()` — aggiorna idempotency_key
2. `_normalize_and_save()` role="tp" — aggiorna idempotency_key

**`src/runtime_v2/execution_gateway/repositories.py`**

```python
def get_active_tp_commands(self, trade_chain_id: int) -> list[dict]:
    """Payload dei SET_POSITION_TPSL_* DONE per chain aperta."""
```

#### run_tp_reconciliation() — ruolo residuo

Il polling rimane come fallback:
- WS disconnesso durante il fill (gap di riconnessione)
- Fill avvenuto prima dell'avvio del runtime

Con WS attivo il polling non trova nulla nella maggior parte dei casi
(idempotency key comune blocca duplicati). Frequenza invariata (60s).

#### Test richiesti

- `test_idempotency_key_tp_filled_unified`:
  WS inserisce `TP_FILLED`, poi `run_tp_reconciliation()`:
  `ops_exchange_events` ha esattamente 1 riga.

- `test_watch_trades_tp_fill_matched`:
  trade con `price` che matcha TP attivo → INSERT con `fill_price` e `filled_qty` reali.

- `test_watch_trades_tp_fill_ambiguous_skipped`:
  2 chain stesso symbol/side, TP a prezzi simili → nessun INSERT.

- `test_watch_trades_tp_fill_is_final_true`:
  trade che azzera posizione → `is_final=True`.

- `test_watch_trades_ignores_non_reduce_only`:
  fill non reduceOnly (entry/SL) → ignorati da `_process_trade_batch()`.

---

## Sequenza di implementazione

```
Step 1 — Fix idempotency key TP_FILLED          (~30 min)
         event_sync._save_tp_fill()
         event_sync._normalize_and_save() role=tp
         ws_fill_watcher._save_fill() role=tp
         → test: idempotency WS + polling (1 sola riga)

Step 2 — Fix STOP_MOVED_CONFIRMED               (~2h)
         repo.insert_exchange_event()
         gateway._emit_confirmed_event()
         gateway: MOVE_STOP_TO_BREAKEVEN/MOVE_STOP → STOP_MOVED_CONFIRMED
         gateway: SYNC_PROTECTIVE_ORDERS → PROTECTIVE_ORDERS_SYNCED
         → test: 5 casi gateway

Step 3 — watchMyTrades                          (~1 giorno)
         ws_fill_watcher._watch_trades_forever()
         ws_fill_watcher._process_trade_batch()
         ws_fill_watcher._save_tp_fill_from_trade()
         repo.get_active_tp_commands()
         → test: 5 casi WS trade matching

Step 4 — Test integrazione                      (~2h)
         lifecycle end-to-end: entry → TP hit → MOVE_BE → be_protection_status=PROTECTED
         verifica su Bybit Demo con ciclo reale
```

---

## Acceptance criteria

| # | Criterio | Verifica |
|---|---|---|
| AC1 | Dopo `MOVE_STOP_TO_BREAKEVEN` con retCode=0, `ops_exchange_events` ha `STOP_MOVED_CONFIRMED` senza chiamate aggiuntive all'exchange | test unitario |
| AC2 | Dopo `STOP_MOVED_CONFIRMED` processato, `be_protection_status = PROTECTED` e `current_stop_price` aggiornato | test integrazione lifecycle |
| AC3 | Dopo `SYNC_PROTECTIVE_ORDERS` con retCode=0, `ops_exchange_events` ha `PROTECTIVE_ORDERS_SYNCED` | test unitario |
| AC4 | `TP_FILLED` da WS e da polling usano la stessa idempotency key → 1 sola riga | test idempotency |
| AC5 | `watchMyTrades` inserisce `TP_FILLED` con `fill_price` e `filled_qty` reali entro 1s dal fill | verifica live demo |
| AC6 | Match non univoco → skip silenzioso, polling fallback gestisce correttamente | test chain ambigue |
| AC7 | Nessuna regressione (273 passed + skipped bybit_testnet) | `pytest tests/runtime_v2/` |

---

## Rischi

| Rischio | Probabilità | Mitigazione |
|---|---|---|
| `trade["info"]["posQty"]` non disponibile su Bybit Demo | Media | Fallback: `get_position_qty()` sincrono; `is_final` determinato nel tick successivo |
| Match ambiguo su multi-chain stesso symbol/side | Bassa in demo | Skip + polling fallback; loggare per analisi |
| `watchMyTrades` riceve fill entry/SL già gestiti da `watchOrders` | Certa | Filtrare su `reduceOnly=True` prima del matching |
| retCode=0 emesso ma Bybit in stato inconsistente (bug exchange) | Molto bassa | Già mitigato da reconciliation periodica `run_position_reconciliation()` |

---

## Decisioni architetturali registrate

| Decisione | Motivazione |
|---|---|
| retCode=0 è conferma sufficiente per `trading_stop` | API sincrona: Bybit applica prima di rispondere. Fetch aggiuntivo è over-engineering e introduce race condition. |
| Nessuno stato `CONFIRMED` intermedio | Aggiunge complessità senza beneficio reale per operazioni sincrone. La finestra DONE→lifecycle è di 1 tick (~1s), accettabile. |
| Idempotency key comune `TP_FILLED:{chain_id}:level:{tp_level}` | Previene doppio processing indipendentemente dalla source (WS / polling / watchMyTrades). |
| `run_tp_reconciliation()` mantenuto come fallback | Copre gap WS e fill avvenuti prima dell'avvio. Con idempotency comune non causa duplicati. |

---

## File toccati (riepilogo)

```
src/runtime_v2/execution_gateway/gateway.py
src/runtime_v2/execution_gateway/repositories.py
src/runtime_v2/execution_gateway/event_sync.py
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py
tests/runtime_v2/execution_gateway/test_gateway.py
tests/runtime_v2/execution_gateway/test_event_sync.py
tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py   (nuovo o da estendere)
```
