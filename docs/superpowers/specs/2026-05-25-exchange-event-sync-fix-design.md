# Design — Exchange Event Sync & Automatismi Engine Fix

**Data:** 2026-05-25  
**Riferimento:** `docs/debugging/gap_exchange_event_sync_automatismi.md`  
**Scope:** Fix completo di 3 bug critici P1 + 2 gap P2 + 4 gap P3

---

## Contesto

La catena di ricezione eventi dall'exchange e il loro effetto sugli automatismi engine
(deferred BE, auto-cancel averaging, trigger BE da TP) è rotta in produzione a causa di
tre bug che si compongono. Questa spec copre tutti i fix dal documento di analisi.

---

## Sezione 1 — Bug P1: catena deferred BE rotta end-to-end

I tre bug P1 si compongono in sequenza: senza tutti e tre i fix la catena non funziona.

### BUG-1 — Payload `PENDING_ENTRY_CANCELLED_CONFIRMED` incompleto

**File:** `src/runtime_v2/execution_gateway/event_sync.py`  
**Metodo:** `ExchangeEventSyncWorker._handle_cancelled_order()`

Aggiungere `cancelled_order_ids` (il vero ID exchange dell'ordine cancellato) e `sequence`
(estratto da `coid.sequence`, disponibile senza query aggiuntive) al payload:

```python
payload = json.dumps({
    "command_id":            coid.command_id,
    "position_already_open": position_already_open,
    "cancel_reason":         raw.cancel_reason,
    "cancelled_order_ids":   [client_order_id],   # AGGIUNTO
    "sequence":              coid.sequence,        # AGGIUNTO
})
```

`coid` è già parsato da `coid_mod.parse(client_order_id)` con formato
`tsb:{chain_id}:{cmd_id}:{role}:{seq}:{nonce}`, quindi `coid.sequence` è il numero
di sequenza della leg senza overhead.

---

### BUG-2 — Match leg per `sequence` come fallback in `_process_pending_entry_cancelled_confirmed`

**File:** `src/runtime_v2/lifecycle/event_processor.py`

Il piano di esecuzione contiene `client_order_id` placeholder (`place_entry:{id}:leg2`)
che non viene mai aggiornato con l'ID reale exchange. Il match in `_mark_entry_leg_status`
fallisce silenziosamente.

Fix: aggiungere un helper `_mark_entry_leg_status_by_sequence` e usarlo come fallback
quando il match per `client_order_id` non produce risultati:

```python
# in _process_pending_entry_cancelled_confirmed
new_plan_state_json = self._mark_entry_leg_status(
    chain.plan_state_json,
    client_order_ids=cancelled_order_ids,
    new_status="CANCELLED",
)

# fallback: match per sequence (piano ha placeholder, ID exchange non aggiornato)
if new_plan_state_json is None:
    sequence = payload.get("sequence")
    if sequence is not None:
        new_plan_state_json = self._mark_entry_leg_status_by_sequence(
            chain.plan_state_json,
            sequence=int(sequence),
            new_status="CANCELLED",
        )
```

`_mark_entry_leg_status_by_sequence`: cerca le leg con `leg["sequence"] == sequence`
e `leg["status"] == "PENDING"`, aggiorna via `ExecutionPlanBuilder.update_leg_status`.

Il match per `client_order_id` rimane come percorso primario (funzionerà se in futuro
il piano verrà aggiornato con gli ID reali al momento del piazzamento ordine).

---

### BUG-3 — `_persist_result` non espande i comandi `CANCEL_PENDING_ENTRY`

**File:** `src/runtime_v2/lifecycle/cancel_expander.py` (nuovo)  
**File:** `src/runtime_v2/lifecycle/entry_gate.py` (refactor)  
**File:** `src/runtime_v2/lifecycle/workers.py` (uso)

#### Nuovo modulo `lifecycle/cancel_expander.py`

Estrarre da `entry_gate.py` le due funzioni in un modulo condiviso:

```python
# cancel_expander.py
def expand_cancel_pending_commands(
    conn: sqlite3.Connection,
    *,
    trade_chain_id: int,
    command_type: str,
    payload_json: str,
    idempotency_key: str,
) -> list[tuple[str, str]]:
    """Espande CANCEL_PENDING_ENTRY in un comando per ogni ordine pending reale.
    Ritorna lista di (payload_json, idempotency_key) da inserire in DB.
    Per tutti gli altri tipi di comando ritorna il comando originale invariato.
    """
    ...

def load_pending_entry_client_order_ids(
    conn: sqlite3.Connection,
    trade_chain_id: int,
) -> list[str]:
    """Legge i client_order_id reali (tsb:...) dei comandi PLACE_ENTRY ancora attivi."""
    ...
```

`entry_gate.py` importa da `cancel_expander` (rimuovere le definizioni locali).

#### `workers.py` — chiamata in `_persist_result`

```python
from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands

# in _persist_result, loop comandi:
for cmd in result.execution_commands:
    for payload_json_exp, idempotency_key_exp in expand_cancel_pending_commands(
        conn,
        trade_chain_id=chain_id,
        command_type=cmd.command_type,
        payload_json=cmd.payload_json,
        idempotency_key=cmd.idempotency_key,
    ):
        conn.execute(
            "INSERT OR IGNORE INTO ops_execution_commands (...) VALUES (?,?,?,?,?,?,?)",
            (chain_id, cmd.command_type, "PENDING",
             payload_json_exp, idempotency_key_exp, now, now),
        )
```

Questo garantisce che l'auto-cancel averaging emesso da `_process_tp_filled` arrivi
al gateway con l'ID exchange reale (da `ops_execution_commands` PLACE_ENTRY attivi)
invece del placeholder dal piano.

---

### Catena deferred BE funzionante (solo se configurata)

La catena si attiva solo quando `ManagementPlanConfig` della chain ha:

| Campo | Valore |
|---|---|
| `cancel_pending_by_engine` | `True` |
| `cancel_averaging_pending_after` | `"tp1"` (o livello colpito) |
| `be_trigger` | stesso livello TP |
| `chain.be_protection_status` | non `PROTECTED` né `BE_MOVE_PENDING` |

```
TP1 colpito
  → [solo se cancel_pending_by_engine=True AND cancel_averaging_pending_after="tp1"]
  → _process_tp_filled emette CANCEL_PENDING_ENTRY per ogni averaging leg
      [solo se be_trigger="tp1" AND be_protection_status non già attivo]
      → set flag _be_deferred_by_auto_cancel nel plan
  → _persist_result chiama expand_cancel_pending_commands         [fix BUG-3]
      → comando con entry_client_order_id reale (tsb:...) in DB
  → Gateway cancella averaging su Bybit con ID corretto
  → Bybit conferma cancellazione
  → _handle_cancelled_order produce evento con cancelled_order_ids + sequence  [fix BUG-1]
  → _process_pending_entry_cancelled_confirmed:
      - match per client_order_id fallisce (piano ha placeholder) ← atteso
      - fallback per sequence → leg trovata → CANCELLED            [fix BUG-2]
      - get_pending_averaging_legs() → lista vuota
      - flag _be_deferred_by_auto_cancel presente
      → MOVE_STOP_TO_BREAKEVEN emesso ✓
```

---

### Test da aggiornare

I test in `tests/runtime_v2/lifecycle/test_event_processor.py` iniettano
`PENDING_ENTRY_CANCELLED_CONFIRMED` con `{"cancelled_order_ids": ["cid_leg3"]}` e
piani con ID corrispondenti — non riproducono il comportamento production.

Aggiornare i test per:
1. Payload reale da `_handle_cancelled_order`: `cancelled_order_ids` con ID exchange,
   `sequence` con numero leg, piano con placeholder ID
2. Verificare che il fallback per `sequence` funzioni end-to-end
3. Aggiungere test per path non-configurato (cancel senza deferred BE)

---

## Sezione 2 — Gap P2: race condition rehydration e partial fills

### GAP 1 — `_rehydrate_chain_from_history` cieca sugli eventi `NEW`

**File:** `src/runtime_v2/lifecycle/entry_gate.py`

Fix a una riga nella query:

```sql
-- PRIMA
AND processing_status = 'DONE'

-- DOPO
AND processing_status IN ('NEW', 'DONE')
```

L'`ORDER BY received_at, exchange_event_id` già presente garantisce l'ordine cronologico.
La rehydration è read-only sul contenuto degli eventi (aggiorna la chain locale in memoria,
poi scrive su `ops_trade_chains`) quindi includere `NEW` è sicuro — l'evento viene poi
processato regolarmente dal `LifecycleEventWorker` quando arriva il suo turno.

Aggiungere contestualmente `"ENTRY_PARTIALLY_FILLED"` a `_ENTRY_HISTORY_EVENT_TYPES`
(anticipando il fix GAP 2 qui sotto).

---

### GAP 2 — Partial fills invisibili fino al fill completo

**File:** `status_mapper.py`, `ws_fill_watcher.py`, `event_sync.py`, `event_processor.py`, `entry_gate.py`

#### Step 1 — `StatusMapper` distingue `partially_filled`

```python
_STATUS_MAP = {
    "open":             "OPEN",
    "partially_filled": "PARTIALLY_FILLED",   # era "OPEN"
    "closed":           "FILLED",
    ...
}
```

Aggiungere `is_partially_filled: bool` a `RawAdapterOrder` (property:
`self.status == "PARTIALLY_FILLED"`).

#### Step 2 — WS handler gestisce partial fills

```python
# ws_fill_watcher.py — _process_order_batch
if raw.is_filled:
    self._save_fill(client_order_id, raw)
elif raw.is_partially_filled:
    self._save_partial_fill(client_order_id, raw)
```

`_save_partial_fill` emette `ENTRY_PARTIALLY_FILLED`.  
Idempotency key: `ENTRY_PARTIALLY_FILLED:{chain_id}:{exchange_order_id}:{filled_qty_rounded}`  
Previene doppio conteggio se la stessa update WS arriva due volte con qty cumulativa identica.

#### Step 3 — Polling path (`event_sync.py`)

Stesso pattern: se `raw.is_partially_filled`, chiamare `_handle_partial_fill`
invece di ignorare l'ordine.

#### Step 4 — `EventProcessor` gestisce `ENTRY_PARTIALLY_FILLED`

Nuovo handler `_process_entry_partially_filled`:

```python
def _process_entry_partially_filled(self, exchange_event, chain, active_commands):
    payload = json.loads(exchange_event.payload_json)
    partial_qty  = float(payload.get("filled_qty", 0.0))
    fill_price   = float(payload.get("fill_price", 0.0))

    new_filled_qty = (chain.filled_entry_qty or 0.0) + partial_qty
    new_open_qty   = max(0.0, new_filled_qty - (chain.closed_position_qty or 0.0))
    new_avg        = _recalculate_weighted_avg(chain, partial_qty, fill_price)

    return EventProcessorResult(
        new_lifecycle_state=None,          # resta WAITING_ENTRY
        new_be_protection_status=None,     # nessun automatismo
        new_filled_entry_qty=new_filled_qty,
        new_open_position_qty=new_open_qty,
        entry_avg_price=new_avg,
        execution_commands=[],
        lifecycle_events=[],
        ...
    )
```

La leg nel piano resta `PENDING`. Nessun trigger BE o auto-cancel. La chain resta
`WAITING_ENTRY` finché il fill non è completo.

#### Step 5 — Rehydration

`_ENTRY_HISTORY_EVENT_TYPES` (entry_gate.py):

```python
_ENTRY_HISTORY_EVENT_TYPES = frozenset({"ENTRY_FILLED", "ENTRY_PARTIALLY_FILLED"})
```

Scenario coperto:

```
Ordine LIMIT BTC, 60% fillato → ENTRY_PARTIALLY_FILLED
  → filled_entry_qty=0.6, open_position_qty=0.6, chain resta WAITING_ENTRY

Arriva U_CLOSE_FULL da Telegram:
  → open_position_qty=0.6 > 0 → CLOSE_FULL processato correttamente ✓
  (senza fix: open_position_qty=0 → CLOSE_FULL rifiutato o no-op ← errato)
```

---

## Sezione 3 — Gap P3: bassa priorità

### GAP 3 — WS non rileva cancellazioni ordini

**File:** `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`

`StatusMapper` mappa già `"canceled"/"cancelled"` a `"CANCELLED"`. Aggiungere il branch:

```python
# _process_order_batch
if raw.is_filled:
    self._save_fill(client_order_id, raw)
elif raw.is_partially_filled:
    self._save_partial_fill(client_order_id, raw)
elif raw.status == "CANCELLED":
    self._save_cancel_from_ws(client_order_id, raw)   # nuovo
```

`_save_cancel_from_ws` replica la logica di `_handle_cancelled_order` in `event_sync.py`:
parsa il coid, costruisce payload con `cancelled_order_ids`, `sequence`, `cancel_reason`,
salva `PENDING_ENTRY_CANCELLED_CONFIRMED` con `INSERT OR IGNORE`.
L'idempotency key è identica a quella del polling REST → nessun duplicato se entrambi
rilevano la cancellazione.

---

### GAP 4 — `run_tp_reconciliation` misclassifica SL come `TP_FILLED`

**File:** `src/runtime_v2/execution_gateway/event_sync.py`

Quando `current_qty == 0.0`, prima di salvare `TP_FILLED`, verificare se esiste
già un evento `SL_FILLED` per la stessa chain:

```python
# run_tp_reconciliation — prima di _save_tp_fill
if current_qty == 0.0:
    if self._has_sl_filled_event(trade_chain_id):
        # Chain chiusa da SL, non sovrascrivere con TP_FILLED
        self._repo.mark_done(cmd_id)
        continue
```

```python
def _has_sl_filled_event(self, trade_chain_id: int) -> bool:
    conn = sqlite3.connect(self._ops_db)
    try:
        row = conn.execute(
            "SELECT 1 FROM ops_exchange_events "
            "WHERE trade_chain_id=? AND event_type='SL_FILLED' LIMIT 1",
            (trade_chain_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
```

Evita logica di price-matching fragile. Se `SL_FILLED` non esiste ancora al momento
del check, il reconciliation salverà `TP_FILLED` (accettabile: il WS avrà già
eventualmente registrato l'SL separatamente). Migliora la qualità dei dati storici
e prepara la base per logica differenziata SL/TP futura.

---

### GAP 5 — `tp_size=0` per `SET_POSITION_TPSL_FULL` in reconciliation

**File:** `src/runtime_v2/execution_gateway/event_sync.py`, `_get_sent_tp_commands`

Per i comandi `SET_POSITION_TPSL_FULL` che non includono `tp_size` nel payload,
derivare la qty dalla chain (già disponibile nel JOIN con `ops_trade_chains`):

```python
tp_size = float(payload.get("tp_size") or 0)
if tp_size == 0 and cmd_type == "SET_POSITION_TPSL_FULL":
    tp_size = filled_entry_qty   # dalla JOIN già presente
```

Con `tp_size = filled_entry_qty`:
```
expected_after_tp = filled_entry_qty - filled_entry_qty = 0
condizione: qty < 0 * 0.95 → sempre False
```

Elimina i falsi positivi. La detect di chiusura completa per TP_FULL è delegata a
`run_position_reconciliation` (rileva `qty == 0`).

---

### GAP 6 — `run_position_reconciliation` rileva solo chiusure complete

**File:** `src/runtime_v2/execution_gateway/event_sync.py`

Estendere il check per rilevare anche chiusure parziali esterne significative
(riduzione >10% della posizione):

```python
# Chiusura completa esterna
if qty == 0.0 and open_qty > 0.0:
    self._save_externally_closed(chain_id, symbol, side, open_qty)

# Chiusura parziale esterna (riduzione >10%)
elif 0.0 < qty < open_qty * 0.90:
    reduction = open_qty - qty
    self._save_externally_partial_closed(chain_id, symbol, side, reduction)
```

`_save_externally_partial_closed` emette `CLOSE_PARTIAL_FILLED` con
`filled_qty=reduction` e `source="position_reconciliation"`. Il processor già gestisce
`CLOSE_PARTIAL_FILLED` aggiornando `closed_position_qty` e `open_position_qty` senza
chiudere la chain.

La soglia 10% è conservativa: esclude drift di rounding Bybit ma cattura chiusure
manuali reali. Configurabile via `operation_config.yaml` in futuro.

---

## File modificati — riepilogo

| File | Tipo modifica | Bug/Gap |
|------|--------------|---------|
| `execution_gateway/event_sync.py` | modifica | BUG-1, GAP 4, GAP 5, GAP 6 |
| `lifecycle/cancel_expander.py` | **nuovo** | BUG-3 |
| `lifecycle/entry_gate.py` | refactor (import) + fix query | BUG-3, GAP 1 |
| `lifecycle/workers.py` | modifica | BUG-3 |
| `lifecycle/event_processor.py` | modifica | BUG-2, GAP 2 |
| `execution_gateway/adapters/ccxt_bybit/status_mapper.py` | modifica | GAP 2 |
| `execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | modifica | GAP 2, GAP 3 |
| `execution_gateway/models.py` | modifica | GAP 2 |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | aggiornamento | BUG-1, BUG-2 |

---

## Priorità di implementazione

```
🔴 P1  BUG-1 + BUG-2 + BUG-3  (un unico task, si compongono)
🟡 P2  GAP 1                   (una riga, zero rischio)
🟡 P2  GAP 2                   (nuovo event type, più file)
🟢 P3  GAP 3                   (WS cancellazioni)
🟢 P3  GAP 4 + GAP 5           (reconciliation quality)
🟢 P3  GAP 6                   (partial close esterna)
```
