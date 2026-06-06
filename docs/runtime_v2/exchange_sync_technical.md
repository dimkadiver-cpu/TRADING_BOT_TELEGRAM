# Sincronizzazione eventi exchange — riferimento tecnico

Questo documento descrive l'architettura interna del sistema di sincronizzazione eventi
exchange in `src/runtime_v2/`. Include flusso dati, tabelle, formule di accumulo e gap
tecnici con riferimenti precisi al codice.

---

## 1. Architettura del flusso eventi

### Path principale — WebSocket

```
Bybit WS streams
  watch_orders | watch_my_trades | watch_positions
        ↓
BybitWsFillWatcher._process_batch()
  [adapters/ccxt_bybit/ws_fill_watcher.py]
        ↓
EventNormalizer.from_order() / from_trade() / from_position()
  [execution_gateway/event_ingest/normalizer.py]
        → ExchangeRawEvent (dataclass)
        ↓
EventClassifier.classify()
  [execution_gateway/event_ingest/classifier.py]
        → ClassifiedEvent (event_type, trade_chain_id, tp_level, should_forward_to_lifecycle)
        ↓
GatewayCommandRepository.insert_raw_and_classified()
  [execution_gateway/repositories.py]
        ├── INSERT OR IGNORE → exchange_raw_events  (audit, sempre)
        └── INSERT OR IGNORE → ops_exchange_events  (lifecycle, solo se should_forward_to_lifecycle=True)
        ↓
LifecycleEventWorker.run_once()
  [lifecycle/workers.py]
        ↓
LifecycleEventProcessor.process()
  [lifecycle/event_processor.py]
        → EventProcessorResult
        ↓
workers._persist_result()
  [lifecycle/workers.py]
        ├── UPDATE ops_trade_chains
        ├── INSERT ops_lifecycle_events
        ├── INSERT ops_execution_commands
        └── _accumulate_pnl_for_events()
```

### Path fallback — REST polling

```
ExchangeEventSyncWorker.run_reconciliation()
  [execution_gateway/event_sync.py]
        ↓
adapter.get_order_status(client_order_id)
        ↓
_save_fill_event() / _save_cancelled_event()
        ↓
GatewayCommandRepository.insert_exchange_event()
        → ops_exchange_events (stesso consumer del path WS)
```

### Post-classification enrichment — TP/SL senza orderLinkId

Bybit non mette `orderLinkId` sugli ordini TP/SL attached a livello di posizione.
Il watcher lo gestisce dopo `classify()`:

```python
# ws_fill_watcher._process_batch()
if classified.event_type in ("TP_FILLED", "SL_FILLED") and classified.trade_chain_id is None:
    fill_side = raw.side
    position_side = "LONG" if fill_side.lower() == "sell" else "SHORT"
    chain_id = self._repo.resolve_chain_for_fill(raw.symbol, position_side)
    # resolve_chain_for_fill() ritorna l'id solo se c'è esattamente 1 chain aperta
    # per quel symbol+side — disambiguazione conservativa
```

---

## 2. Tre livelli di reconciliation

| Metodo | Classe | Trigger | Meccanismo |
|--------|--------|---------|------------|
| `run_reconciliation()` | `ExchangeEventSyncWorker` | WS fallback o polling | poll REST per comandi `SENT/ACK`, inserisce fill/cancel in `ops_exchange_events` |
| `run_position_reconciliation()` | `ExchangeEventSyncWorker` | polling periodico | confronta `open_position_qty` DB vs `adapter.get_position_qty()` → `CLOSE_FULL_FILLED` se size=0 |
| `run_trade_based_reconciliation()` | `ExchangeEventSyncWorker` | polling periodico | `adapter.fetch_recent_reduce_trades()` per chain OPEN/PARTIALLY_CLOSED con TP attivi → `TP_FILLED` se trade reduce trovato |
| `run_protective_orders_reconciliation()` | `ExchangeEventSyncWorker` | polling periodico | `adapter.fetch_position_details()` → `PROTECTIVE_ORDER_CANCELLED` se `take_profit == 0.0` senza fill precedente |

Tutti e quattro usano `idempotency_key` su `INSERT OR IGNORE` — rieseguibili senza side effect.

---

## 3. Dati persistiti

### `exchange_raw_events` — audit immutabile

Scritto da `GatewayCommandRepository.insert_raw_and_classified()` (repositories.py:581).
Una riga per ogni evento grezzo ricevuto, mai modificata.

| Campo | Fonte |
|-------|-------|
| `exchange_event_id` | execId (trade) / orderId (order) / `pos:{symbol}:{side}:{seq}` (position) |
| `source_stream` | `watch_my_trades` / `watch_orders` / `watch_positions` / `fetch_my_trades` |
| `exec_price`, `exec_qty` | `execPrice`/`execQty` da Bybit info dict |
| `closed_size` | `closedSize` — quantità chiusa in questa esecuzione |
| `exec_fee` | `execFee` (trade) / `cumExecFee` (order) |
| `fee_rate` | `feeRate` |
| `exec_value` | `execValue` — valore USDT dell'esecuzione |
| `cum_exec_qty` | `cumExecQty` — quantità totale eseguita sull'ordine |
| `pos_qty` | `posQty` — size posizione dopo questa esecuzione |
| `position_take_profit`, `position_stop_loss` | dal stream `watch_positions` |
| `raw_info_json` | dict completo `trade["info"]` / `order["info"]` / `position["info"]` |
| `classified_event_type`, `trade_chain_id`, `tp_level` | output del classifier |
| `forwarded_to_lifecycle` | 1 se inserito anche in `ops_exchange_events` |

### `ops_exchange_events` — input del lifecycle worker

Scritto da `insert_raw_and_classified()` e `insert_exchange_event()`.
Consumato da `LifecycleEventWorker.run_once()` con `processing_status='NEW'`.

Payload per tipo di evento (campo `payload_json`):

| Tipo evento | Campi nel payload |
|-------------|-------------------|
| `ENTRY_FILLED` | `fill_price`, `filled_qty`, `exec_fee`, `command_id` |
| `TP_FILLED` | `fill_price`, `filled_qty`, `exec_fee`, `closed_size`, `tp_level`, `is_final` |
| `SL_FILLED` | `fill_price`, `filled_qty`, `exec_fee`, `closed_size` |
| `CLOSE_FULL_FILLED` | `fill_price`, `filled_qty`, `exec_fee`, `closed_size` |
| `CLOSE_PARTIAL_FILLED` | `fill_price`, `filled_qty`, `exec_fee`, `closed_size` |
| `PENDING_ENTRY_CANCELLED_CONFIRMED` | `command_id`, `position_already_open`, `cancel_reason`, `cancelled_order_ids`, `sequence` |
| `STOP_MOVED_CONFIRMED` | `new_stop_price`, `is_breakeven` |

### `ops_trade_chains` — stato operativo della chain

Aggiornato da `workers._persist_result()` dopo ogni `LifecycleEventProcessor.process()`.

| Campo | Aggiornato da | Note |
|-------|--------------|-------|
| `entry_avg_price` | `ENTRY_FILLED` | media pesata: `(old_avg × old_qty + fill_price × fill_qty) / new_qty` |
| `filled_entry_qty` | `ENTRY_FILLED` | +fill_qty |
| `open_position_qty` | `ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`, `CLOSE_*` | +fill su entry, -fill su close |
| `closed_position_qty` | `TP_FILLED`, `SL_FILLED`, `CLOSE_*` | +fill_qty |
| `current_stop_price` | `STOP_MOVED_CONFIRMED` | prezzo stop attuale post-move |
| `be_protection_status` | `STOP_MOVED_CONFIRMED` (is_breakeven=True) | `NOT_PROTECTED` → `BE_MOVE_PENDING` → `PROTECTED` |
| `risk_already_realized` | `ENTRY_FILLED` | `fill_qty × abs(fill_price − sl_price)` |
| `risk_remaining` | `ENTRY_FILLED` | `max(0, risk_total − risk_already_realized)` |
| `cumulative_gross_pnl` | fill events (vedi §4) | PnL lordo cumulato |
| `cumulative_fees` | fill events (vedi §4) | fee cumulate |
| `cumulative_funding` | **mai scritto** (vedi Gap §6) | — |
| `allocated_margin` | creazione chain | `risk_snapshot.risk_amount` — campo legacy, non più denominatore ROI |
| `peak_margin_used` | aggiornato a ogni fill di entry | massimo margine reale impiegato nel tempo; denominatore di `roi_net_pct` |
| `initial_risk_amount` | creazione chain | rischio monetario iniziale; denominatore di `return_on_risk_pct` |
| `plan_state_json` | `ENTRY_FILLED`, `PENDING_ENTRY_CANCELLED_CONFIRMED` | aggiorna status delle leg (PENDING→FILLED/CANCELLED) |

### `ops_lifecycle_events` — audit decisioni

Ogni decisione del lifecycle è registrata con `previous_state`, `next_state` e `payload_json`
contenente i dati di fill. Scritto da `workers._persist_result()` e `entry_gate.py`.

---

## 4. Accumulo PnL — formula e meccanismo

`workers._accumulate_pnl_for_events()` (workers.py:39):

```python
side_sign = 1.0 if side == "LONG" else -1.0

for event in lifecycle_events:
    if event.event_type not in {"TP_FILLED", "SL_FILLED", "CLOSE_FULL_FILLED", "CLOSE_PARTIAL_FILLED"}:
        continue
    fill_price  = payload["fill_price"]
    closed_qty  = payload["closed_size"] or payload["filled_qty"]
    exec_fee    = payload["exec_fee"] or 0.0
    gross_total += closed_qty × (fill_price − entry_avg_price) × side_sign
    fee_total   += exec_fee

UPDATE ops_trade_chains
SET cumulative_gross_pnl = COALESCE(cumulative_gross_pnl, 0.0) + gross_total,
    cumulative_fees      = COALESCE(cumulative_fees, 0.0)      + fee_total
WHERE trade_chain_id = ?
```

Chiamato all'interno della transazione di `_persist_result()`, dopo l'INSERT degli eventi
lifecycle e prima della scrittura dei comandi.

`entry_avg_price` usato nel calcolo è quello appena aggiornato (stesso tick se è un fill
ENTRY_FILLED + TP_FILLED nella stessa transazione — ma in pratica non accade: ENTRY_FILLED
e TP_FILLED sono eventi separati).

### Metriche ROI nel report finale

```
total_pnl_net       = cumulative_gross_pnl - cumulative_fees + cumulative_funding
roi_net_pct         = total_pnl_net / peak_margin_used * 100
return_on_risk_pct  = total_pnl_net / initial_risk_amount * 100
```

- `roi_net_pct` usa il massimo margine reale storicamente impiegato (`peak_margin_used`).
- `return_on_risk_pct` usa il rischio monetario iniziale (`initial_risk_amount`).
- `allocated_margin` resta campo legacy compatibile, non più fonte di verità per il report finale.
- Se il denominatore richiesto è `NULL`, il campo risultante è `null` e il renderer mostra `n/a`.

---

## 5. Automatismi del lifecycle per tipo di evento

### ENTRY_FILLED → `_process_entry_filled()`

1. Calcola nuovo `entry_avg_price` (media pesata) e `filled_entry_qty`, `open_position_qty`
2. Se primo fill (`WAITING_ENTRY`): `new_state = OPEN`
3. Aggiorna `risk_already_realized`, `risk_remaining` se `sl_price` disponibile in `risk_snapshot_json`
4. Imposta `release_waiting_position = True` → sblocca tutti i comandi `WAITING_POSITION` → `PENDING`
5. `PostFillProtectionRebuilder.build_after_fill()` → eventuale `REBUILD_PARTIAL_TPS`
6. Se presente flag `_be_deferred_by_auto_cancel` e non restano averaging leg pendenti: emette `MOVE_STOP_TO_BREAKEVEN`

### TP_FILLED → `_process_tp_filled()`

1. `is_final`: `CLOSED`, altrimenti `PARTIALLY_CLOSED`
2. Aggiorna `open_position_qty`, `closed_position_qty`
3. Se `cancel_averaging_pending_after == f"tp{tp_level}"` e ci sono averaging leg pendenti:
   - Emette `CANCEL_PENDING_ENTRY`
   - Se BE deve partire nello stesso tick: salva flag `_be_deferred_by_auto_cancel` nel `plan_state_json`
4. Se `be_trigger == f"tp{tp_level}"` e nessun flag deferred:
   - Emette `MOVE_STOP_TO_BREAKEVEN`, `be_protection_status = BE_MOVE_PENDING`

### SL_FILLED → `_process_sl_filled()`

1. `CLOSED`, `open_position_qty = 0`

### CLOSE_FULL_FILLED → `_process_close_full_filled()`

1. `CLOSED`, `open_position_qty = 0`
2. Emette `CANCEL_PENDING_ENTRY` (pulizia averaging pendenti)

### CLOSE_PARTIAL_FILLED → `_process_close_partial_filled()`

1. Riduce `open_position_qty`, incrementa `closed_position_qty`
2. `CLOSED` se `open_position_qty <= 0`, altrimenti `PARTIALLY_CLOSED`

### STOP_MOVED_CONFIRMED → `_process_stop_moved_confirmed()`

1. Aggiorna `current_stop_price`
2. Se `is_breakeven`: `be_protection_status = PROTECTED`

### PENDING_ENTRY_CANCELLED_CONFIRMED → `_process_pending_entry_cancelled_confirmed()`

1. Marca leg `CANCELLED` in `plan_state_json` (match per client_order_id, fallback per sequence)
2. Se flag `_be_deferred_by_auto_cancel` presente e nessuna averaging leg rimasta:
   emette `MOVE_STOP_TO_BREAKEVEN`, rimuove flag
3. Se `open_position_qty == 0` e nessuna leg pendente e nessun entry in SENT/ACK: `CANCELLED`
4. Altrimenti: `NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED`

---

## 6. Gap tecnici

### Gap 1 — PnL e fee nel DB ma non accessibili via modello

**Gravità: Alta**

`_accumulate_pnl_for_events()` scrive `cumulative_gross_pnl` e `cumulative_fees` in DB.
`_CHAIN_COLS` in `repositories.py:23` include questi campi nella SELECT.
`_chain_from_row()` (repositories.py:32) li destruttura dalla riga:

```python
# repositories.py riga 38
(..., cumulative_gross_pnl, cumulative_fees, cumulative_funding, allocated_margin, ...) = row
```

Ma il modello `TradeChain` ha `model_config = ConfigDict(extra="forbid")` e non ha questi
campi — vengono estratti dalla riga ma mai passati al costruttore. Sono silenziosamente persi.

`PnlView` in `status_queries.py` non esegue SELECT su questi campi.

`format_pnl()` in `formatters/pnl.py:35` stampa:
```python
"Realized PnL: n/a",
"Unrealized PnL: n/a",
"ROI/Funding/Fees: n/a",
```

**I dati ci sono nel DB, ma nessuno li legge.**

### Gap 2 — Funding fee non tracciate

**Gravità: Media**

`ops_trade_chains.cumulative_funding` esiste nello schema, è in `_CHAIN_COLS`, viene
destrutturato in `_chain_from_row()`. Nessun worker lo scrive mai.

Le funding fee vengono pagate/ricevute ogni 8h su posizioni aperte overnight.
Senza questo dato il PnL netto reale non è calcolabile:

```
PnL netto = cumulative_gross_pnl - cumulative_fees - cumulative_funding
```

### Gap 3 — PROTECTIVE_ORDER_CANCELLED non gestito dal lifecycle

**Gravità: Media**

`run_protective_orders_reconciliation()` inserisce un evento di tipo
`PROTECTIVE_ORDER_CANCELLED` in `ops_exchange_events` quando rileva che il TP attached
è stato rimosso esternamente senza fill.

`LifecycleEventProcessor.process()` non ha handler per questo tipo:

```python
# event_processor.py
logger.warning("unhandled exchange event type: %s", etype)
return EventProcessorResult(...)  # no-op
```

Il tipo non è nemmeno nel literal `ExchangeEventType` in `models.py`. La chain resta
nello stato corrente senza nessuna reazione: nessuna notifica all'utente, nessun flag
di review, nessun cleanup.

---

*Fonte: analisi diretta del codice in `src/runtime_v2/` — giugno 2026.*
*Versione semplice: `exchange_sync_overview.md`.*
