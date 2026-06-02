# CLEAN_LOG Gap Remediation — Design Spec

**Data:** 2026-05-31
**Riferimento spec:** `docs/Raggionamento/Controllo_Notifica/CLEAN_LOG_SPEC.md` v2.0
**Approccio:** A — Patch progressiva (bottom-up): dati → PnL → formatter → nuovi eventi → aggregazione → fix residui

---

## Contesto

La verifica del sistema notifiche ha rilevato 7/16 acceptance criteria falliti.
I gap si dividono in tre categorie:

- **Dati mancanti** — `fill_price`, `exec_fee`, `pnl` non propagati ai lifecycle events
- **Formatter errati** — emoji sbagliate, label errate, sezioni mancanti in `clean_log.py`
- **Feature assenti** — ENTRY_CANCELLED, BE_EXIT, Final Result, debounce, multi-chain

Le 6 sezioni di questa spec devono essere implementate in ordine: ogni sezione dipende
dai dati prodotti dalla precedente.

---

## Sezione 1 — Enrichment payload lifecycle events

### Problema

I payload degli eventi `TP_FILLED`, `SL_FILLED`, `CLOSE_FULL_FILLED` non includono
`fill_price`, `exec_fee`, `filled_qty`. Il dato esiste nell'`ExchangeRawEvent` (campo
`exec_fee` dal normalizer) ma viene perso prima di arrivare ai lifecycle events.

### Interventi

**`ws_fill_watcher.py` + `event_sync.py`**

Il payload scritto in `ops_exchange_events.payload_json` deve includere esplicitamente:
```json
{
  "fill_price": 68000.0,
  "filled_qty": 0.002,
  "exec_fee": 1.10,
  "closed_size": 0.002
}
```
Questi campi vengono dall'`ExchangeRawEvent`: `exec_price`, `exec_qty`, `exec_fee`,
`closed_size`. Il path REST (`event_sync.py`) deve fare lo stesso dove disponibile.

**`event_processor.py` — `_process_tp_filled`**

Attualmente scrive solo `{"tp_level": X, "is_final": X}`.
Estendere leggendo i campi dall'`exchange_event.payload_json`:
```python
payload_json = json.dumps({
    "tp_level": tp_level,
    "is_final": is_final,
    "fill_price": ev_payload.get("fill_price"),
    "filled_qty": ev_payload.get("filled_qty"),
    "exec_fee": ev_payload.get("exec_fee"),
})
```

**`event_processor.py` — `_process_entry_filled`**

Aggiungere `exec_fee` al payload esistente.

**`event_processor.py` — `_process_sl_filled` / `_process_close_full_filled` / `_process_close_partial_filled`**

Normalizzare in modo esplicito (non copiare `exchange_event.payload_json` raw):
stessa struttura di `TP_FILLED` con `fill_price`, `filled_qty`, `exec_fee`.

### File toccati

- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- `src/runtime_v2/execution_gateway/event_sync.py`
- `src/runtime_v2/lifecycle/event_processor.py`

---

## Sezione 2 — PnL computation e Final Result

### Problema

`gross_pnl` non viene mai calcolato. Il `Final Result` richiede PnL cumulativo
su tutti i TP + fee totali + funding. Non esistono colonne per accumulare questi dati.

### Formula (da spec §13)

```
gross_pnl_per_fill = closed_qty × (fill_price − entry_avg_price)   # LONG
gross_pnl_per_fill = closed_qty × (entry_avg_price − fill_price)   # SHORT
total_pnl_net      = Σ gross_pnl − Σ fees + Σ funding
ROI_net            = total_pnl_net / allocated_margin
```

### Nuove colonne — migration `010_ops_pnl_columns.sql`

```sql
ALTER TABLE ops_trade_chains ADD COLUMN cumulative_gross_pnl REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN cumulative_fees      REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN cumulative_funding   REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN allocated_margin     REAL;
```

`allocated_margin` viene popolato al momento dell'accettazione segnale dal campo
`risk_amount` in `risk_snapshot_json`.

### Accumulo in `lifecycle/workers.py`

Dopo ogni `TP_FILLED` / `SL_FILLED` / `CLOSE_FULL_FILLED` / `CLOSE_PARTIAL_FILLED`:

```python
gross_pnl = closed_qty * abs(fill_price - entry_avg_price) * sign(side)
conn.execute("""
    UPDATE ops_trade_chains
    SET cumulative_gross_pnl = cumulative_gross_pnl + ?,
        cumulative_fees      = cumulative_fees + ?
    WHERE trade_chain_id = ?
""", (gross_pnl, exec_fee or 0.0, chain_id))
```

dove `sign(side)` = +1 per LONG, -1 per SHORT.

### Funding

Bybit non invia funding granulare per singolo trade. Il campo `cumulative_funding`
rimane sempre `0.0` per ora — mostrato nel Final Result come `+0.00 USDT`.
Documentato come limitazione nota.

### ROI net

Calcolabile solo se `allocated_margin` è disponibile e > 0.
Se `None` o 0, la riga `ROI net:` viene omessa (spec §13: "solo se allocated_margin disponibile").

### Struttura `final_result` dict (usata da payload builder)

```python
{
    "roi_net_pct": 4.82,           # None se allocated_margin assente
    "total_pnl_net": 344.25,
    "gross_pnl": 350.00,
    "fees": -5.75,
    "funding": 0.00,
    "close_reason": "TAKE_PROFIT", # STOP_LOSS | BREAKEVEN_AFTER_TP | MANUAL_CLOSE | TIMEOUT
}
```

### File toccati

- `db/ops_migrations/010_ops_pnl_columns.sql`
- `src/runtime_v2/lifecycle/workers.py`
- `src/runtime_v2/control_plane/outbox_writer.py` (lettura colonne in `project_clean_log_for_chain`)

---

## Sezione 3 — Payload builder + Formatter fixes

### 3a — `outbox_writer.py` — `_build_payload` refactor

La SELECT in `project_clean_log_for_chain` viene estesa:

```python
"SELECT symbol, side, entry_mode, trader_id, "
"plan_state_json, risk_snapshot_json, "
"entry_avg_price, current_stop_price, "
"source_chat_id, telegram_message_id, "
"cumulative_gross_pnl, cumulative_fees, cumulative_funding, allocated_margin, "
"filled_entry_qty, open_position_qty "
"FROM ops_trade_chains WHERE trade_chain_id=?"
```

**Campi aggiunti per tipo evento:**

| Evento | Campi nuovi nel payload |
|--------|------------------------|
| `ENTRY_OPENED` | `entry_ref`, `entry_type`, symbol unit per Qty, `fee` |
| `ENTRY_UPDATED` | `entry_ref`, `entry_type`, `fee`, `filled_pct`, `pending_entries` |
| `TP_FILLED` | `fill_price`, `closed_pct`, `pnl`, `fee`, `remaining_pct` |
| `TP_FILLED_FINAL` | tutto come TP_FILLED + `final_result` dict |
| `SL_FILLED` | `sl_price` (label corretto), `closed_pct`, `pnl`, `fee`, `final_result` |
| `POSITION_CLOSED` | `closed_pct`, `close_price`, `close_reason_text`, `pnl`, `fee`, `final_result` |
| `UPDATE_DONE` | `operations[]`, `cancelled_entries[]`, `changed[]` (list of `{field, old, new, note}`) |
| `UPDATE_PARTIAL` | `applied[]`, `rejected[]` (con `reason` per azione), `cancelled_entries[]` |
| `UPDATE_REJECTED` | `operations[]`, `rejected_reason_text` |
| `PENDING_ENTRY_EXPIRED` | `expired_entry` dict, `open_since_hours`, `remaining_position` dict |
| `RECONCILIATION_FIXED` | `protection` dict con SL corrente |
| `REENTRY_ACCEPTED` | `entries[]`, `sl`, `tps[]`, `trader_id`, `previous_chain_closed` bool |
| `SIGNAL_ACCEPTED` | `risk_footnote`, `risk_marker` (`"*"` / `"↓ *"` / `"(trader hint) *"`) |
| `SIGNAL_REJECTED` | `tps[]`, `rejected_code`, `rejected_reason_text` |
| `REVIEW_REQUIRED` | `entries[]`, `sl`, `tps[]`, `trader_id` |

### 3b — `clean_log.py` — fix trasversali

- **Secondo separatore prima del link:** `_footer(source, link)` aggiunge `────────────────`
  prima di `link` quando presente.
- **Bullet:** `▪️` al posto di `•` ovunque.
- **`_footer` esteso** con parametro opzionale `trader_id`.

### 3b — `clean_log.py` — fix per tipo

**`_signal_accepted`**
- `Risk: X% *` con asterisco quando `risk_marker` è presente
- Footnote `* Risk from operation_rules` / `* use_trader_risk_hint: true` / `* Reduced from trader hint X%`
- RANGE → `Entry zone: min — max Limit`
- LADDER → `Entry_N: price Limit (X%)`
- Deferred market → `Entry_1: Market  (qty at fill, risk: X%)`

**`_signal_rejected`**
- Aggiunge TPs nel body
- Separa `Rejected: <codice>` e `Reason: <testo>` come due righe distinte

**`_review_required`**
- Sposta entries/SL/TPs nel body
- Sposta Reason/Action nel footer section (dopo separatore)
- Aggiunge `Trader:` nel footer

**`_entry_opened`**
- Formato `Entry_N: price Type` per il fill (non `Price: value`)
- Unità symbol nella Qty (`0.004 BTC`)
- Aggiunge `Fee: X USDT`
- Aggiunge `Filled: X%` nella sezione Position
- Aggiunge sezione `Changed vs signal:` se slippage > `market_execution.tolerance_pct`

**`_entry_updated`**
- Emoji `📊` (non `✏️`)
- Sezioni `Filled:` e `Position:` strutturate
- Aggiunge `Fee:`, `Filled: X%`, `Pending: none`

**`_tp_filled`**
- Aggiunge `Closed: X%`, `PnL: ±XX USDT`, `Fee: XX USDT`
- Aggiunge `Position: X%` in Remaining
- SL con label BE quando `be_protection_status == "PROTECTED"` → `SL: 65,020 BE`
- Sezione `Changed: SL: old → new BE *` se BE automatico incluso nello stesso ciclo

**`_tp_filled_final`**
- Tutto come `_tp_filled` + sezione `Final Result:` separata da `────────────────`

**`_sl_filled`**
- Label `SL:` (non `Fill:`)
- Aggiunge `Closed: 100%`, `PnL:`, `Fee:`
- Sezione `Final Result:`

**`_position_closed`**
- Sezione `Closed: / Qty: / Price: / Reason:`
- Aggiunge `PnL:`, `Fee:`
- Sezione `Final Result:`

**`_update_done`**
- Label `Operation:` (non `Applied:`)
- Bullet `▪️`
- Sezione `Cancelled:` con entry ref + price + type
- Sezione `Changed:` con formato `SL: old → new *`
- Footnote `* Changed from original signal` / `* Changed by rule after TP_N`

**`_update_partial`**
- Bullet `▪️`
- `Reason:` per azione respinta sotto il bullet corrispondente
- Sezione `Cancelled:` dove applicabile

**`_update_rejected`**
- Sezione `Operation:` con azioni tentate
- Sezione `Rejected:` con testo descrittivo

**`_pending_timeout`**
- Emoji `📊` (non `⏰`)
- Sezioni `Expired: / Open since: / Action: / Position:`

**`_reconciliation_warning`**
- Formato multiriga: `Issue:\ntesto` (non `Issue: testo` inline)

**`_reconciliation_fixed`**
- Label `Fixed:` (non `Issue resolved:`)
- Sezione `Protection:` con SL corrente

**`_reentry_accepted`**
- Emoji `✅` (non `🔄`)
- Entries/SL/TPs nel body
- `Trader:` + `Previous chain: #N (closed)` nel footer

**Nuovi handler aggiunti al dispatcher `format_clean_log`:**
- `"ENTRY_CANCELLED"` → `_entry_cancelled(p)`
- `"BE_EXIT"` → `_be_exit(p)`
- `"TP_BATCH_FILLED"` → `_tp_batch_filled(p)` *(aggiunto in Sezione 5)*
- `"MULTI_CHAIN_UPDATE"` → `_multi_chain_update(p)` *(aggiunto in Sezione 5)*
- `"MULTI_CHAIN_CLOSED"` → `_multi_chain_closed(p)` *(aggiunto in Sezione 5)*
- `"CANCEL_FAILED"` → `_cancel_failed(p)` *(aggiunto in Sezione 6)*

### File toccati

- `src/runtime_v2/control_plane/outbox_writer.py`
- `src/runtime_v2/control_plane/formatters/clean_log.py`

---

## Sezione 4 — Nuovi event types: ENTRY_CANCELLED e BE_EXIT

### 4a — ENTRY_CANCELLED

**Trigger:** Il lifecycle event `PENDING_ENTRY_CANCELLED` esiste già (emesso da
`_process_pending_entry_cancelled_confirmed`). Non è mappato in `_CLEAN_LOG_EVENT_MAP`.

**Mapping da aggiungere:**
```python
"PENDING_ENTRY_CANCELLED": "ENTRY_CANCELLED",
```

**Filtro obbligatorio in `project_clean_log_for_chain`:**
`PENDING_ENTRY_CANCELLED` viene emesso anche quando la posizione viene chiusa
(`cancel_reason: "position_closed"`). In quel caso NON deve produrre ENTRY_CANCELLED.
```python
if notification_type == "ENTRY_CANCELLED":
    if ev.get("cancel_reason") == "position_closed":
        continue  # salta — entry cancel è conseguenza della chiusura
```

**Payload `_build_payload` per `ENTRY_CANCELLED`:**
```python
{
    "chain_id": 150,
    "symbol": "BTC/USDT",
    "side": "LONG",
    "cancelled_entry": {
        "sequence": 2,
        "price": 64000.0,
        "entry_type": "LIMIT",
    },
    "partial_fill_pct": 30.0,     # None se fill = 0%
    "partial_fill_qty": 0.002,    # None se fill = 0%
    "avg_entry": 64820.0,
    "total_filled_qty": 0.006,
    "source": "trader_update",
    "link": "https://...",
}
```

`partial_fill_pct` = `filled_entry_qty / planned_entry_qty * 100` per la leg cancellata.
Quando `partial_fill_pct` è 0 o None → mostrare `Fill: 0% (no fill occurred)`.

**Formatter `_entry_cancelled`:**
```
📊 #150 — ENTRY CANCELLED
────────────────
BTC/USDT — 📈 LONG

Cancelled:
Entry_2: 64,000 Limit
Partial fill: 30% (0.002 BTC kept)

Position:
Avg entry: 64,820
Total filled: 0.006 BTC
Pending: none

────────────────
Source: trader_update
────────────────
https://...
```

### 4b — BE_EXIT

**Problema:** Un'uscita breakeven arriva come `CLOSE_FULL_FILLED`. Il sistema mappa
`CLOSE_FULL_FILLED → POSITION_CLOSED` con `Close reason: MANUAL_CLOSE` — sbagliato.

**Rilevamento:** In `project_clean_log_for_chain`, se `event_type == "CLOSE_FULL_FILLED"`
e `be_protection_status == "PROTECTED"` nella chain row, la `notification_type` diventa
`"BE_EXIT"` invece di `"POSITION_CLOSED"`. Nessuna modifica all'`event_processor`.

**`_build_payload` per `BE_EXIT`:**
- Stessa struttura di `POSITION_CLOSED`
- `close_reason` = `"BREAKEVEN_AFTER_TP"`
- `exit_price` = `fill_price` dal lifecycle event

**Formatter `_be_exit`:**
```
📊 #145 — BE EXIT — POSITION CLOSED
────────────────
BTC/USDT — 📈 LONG

Exit: 65,020 BE
Closed: 100%
PnL: +0.00 USDT
Fee: 1.70 USDT

────────────────
Final Result:
ROI net: +1.15%
Total PnL net: +112.30 USDT
Gross PnL: +118.00 USDT
Fees: -5.70 USDT
Funding: +0.00 USDT

────────────────
Close reason: BREAKEVEN_AFTER_TP
Source: exchange
```

### File toccati

- `src/runtime_v2/control_plane/outbox_writer.py`
- `src/runtime_v2/control_plane/formatters/clean_log.py`

---

## Sezione 5 — Aggregation Worker

### Schema — `011_ops_outbox_aggregation.sql`

```sql
ALTER TABLE ops_notification_outbox
    ADD COLUMN send_after TEXT;        -- ISO: hold fino a questo momento
ALTER TABLE ops_notification_outbox
    ADD COLUMN aggregation_group TEXT; -- key raggruppamento (chain_id:group_tag)
ALTER TABLE ops_notification_outbox
    ADD COLUMN source_message_id TEXT; -- per multi-chain grouping
```

Nuovo status: `SUPPRESSED` — riga sostituita da aggregato, dispatcher la salta.

### Modifica dispatcher (minima)

```sql
-- Prima:
WHERE status='PENDING'
-- Dopo:
WHERE status='PENDING'
  AND (send_after IS NULL OR send_after <= ?)
```

### Regole `send_after` in `outbox_writer.py`

| Tipo evento | `send_after` |
|-------------|-------------|
| HIGH priority (SL_FILLED, SIGNAL_REJECTED, REVIEW_REQUIRED, POSITION_CLOSED) | `now` |
| UPDATE_DONE / UPDATE_PARTIAL / UPDATE_REJECTED | `now + aggregate_updates_seconds` (20s) |
| TP_FILLED / TP_FILLED_FINAL | `now + aggregate_fills_seconds` (30s) |
| Tutti gli altri | `now` |

### Componente `AggregationWorker`

Nuovo file: `src/runtime_v2/control_plane/aggregation_worker.py`

Gira ogni `debounce_check_interval_seconds` (5s default). Tre step ad ogni ciclo:

**Step 1 — TP batch aggregation**

```python
group_key = (chain_id, "tp_batch")
```

Trova gruppi di `TP_FILLED` maturi (send_after ≤ now) per la stessa chain.
- >1 TP nella finestra → produce `TP_BATCH_FILLED` con payload aggregato
  (somma PnL e fee, remaining dalla chain corrente). Marca originali `SUPPRESSED`.
- 1 solo TP → nessuna azione (il dispatcher lo invia normalmente).

**Step 2 — UPDATE compositi (stessa chain)**

```python
group_key = (chain_id, source_message_id or "update_batch")
```

Trova UPDATE_* maturi per la stessa chain con stesso `source_message_id`
o nella finestra `aggregate_updates_seconds`:
- >1 UPDATE per stessa chain e stessa sorgente → fonde in unico messaggio
  (`applied` = unione, `changed` = unione). Marca originali `SUPPRESSED`.

**Step 3 — Multi-chain update summary**

Raggruppa UPDATE maturi per `source_message_id` su chain diverse:
- **1–3 chain:** nessun summary — righe per-chain al dispatcher normalmente.
- **>3 chain:** crea riga `MULTI_CHAIN_UPDATE` con lista chain + esiti.
  - Righe DONE: marcate `SUPPRESSED` (compattate nel summary).
  - Righe REJECTED/PARTIAL: rimangono `PENDING` (reply dettagliate obbligatorie).

### Formatter aggiuntivi

**`_tp_batch_filled`** — per `TP_BATCH_FILLED`:
```
📊 #145 — TP1 + TP2 FILLED
────────────────
BTC/USDT — 📈 LONG

Filled targets:
TP_1: 68,000 | Closed: 30% | PnL: +70.20 USDT | Fee: 1.10 USDT
TP_2: 71,000 | Closed: 40% | PnL: +165.40 USDT | Fee: 1.65 USDT

Total:
Closed: 70%
PnL: +235.60 USDT
Fees: 2.75 USDT

Remaining:
Position: 30%
SL: 65,020 BE

────────────────
Source: exchange
```

**`_multi_chain_update`** — summary >3 chain:
```
🔁 UPDATE APPLIED — MULTI CHAIN
────────────────
Operation:
▪️ Move SL to BE

Affected chains:
#160 BTC/USDT SHORT — DONE
#161 ETH/USDT SHORT — DONE
#163 XRP/USDT SHORT — REJECTED

Summary:
Done: 2
Rejected: 1

────────────────
Source: trader_update
────────────────
https://...
```

**`_multi_chain_closed`** — close totale con PnL consolidato:
```
🔁 MULTI CHAIN CLOSED
────────────────
Operation:
▪️ Close all positions

Chains closed: N
#160 BTC/USDT SHORT | +83.20 USDT | +3.19%
...

────────────────
Aggregate Result:
Gross PnL: +107.50 USDT
Fees: -8.40 USDT
Funding: +0.00 USDT
Net PnL: +99.10 USDT

────────────────
Close reason: MANUAL_CLOSE
Source: trader_update
────────────────
https://...
```

### Config aggiuntiva in `ControlPlaneConfig`

```yaml
clean_log:
  debounce_check_interval_seconds: 5
  aggregate_fills_seconds: 30
  aggregate_updates_seconds: 20
  multi_chain_summary_threshold: 3
  max_messages_per_chain_per_minute: 4
```

### File toccati

- `db/ops_migrations/011_ops_outbox_aggregation.sql`
- `src/runtime_v2/control_plane/aggregation_worker.py` *(nuovo)*
- `src/runtime_v2/control_plane/notification_dispatcher.py`
- `src/runtime_v2/control_plane/outbox_writer.py`
- `src/runtime_v2/control_plane/formatters/clean_log.py`
- `src/runtime_v2/control_plane/bootstrap.py`
- `src/runtime_v2/control_plane/config.py`

---

## Sezione 6 — CANCEL_FAILED e Pause/Resume format

### 6a — CANCEL_FAILED

**Trigger:** Il timeout worker tenta cancellazione exchange; fallisce dopo `_MAX_ATTEMPTS`.

**Mapping:**
```python
"ENTRY_CANCEL_FAILED": "CANCEL_FAILED",
```

**Payload:**
```python
{
    "chain_id": 145,
    "symbol": "BTC/USDT",
    "side": "LONG",
    "entry_ref": "Entry_2",
    "entry_price": 64000.0,
    "attempts": 3,
    "source": "timeout_worker",
}
```

**Formatter `_cancel_failed`:**
```
⚠️ #145 — CANCEL FAILED
────────────────
BTC/USDT — 📈 LONG

Issue:
Cancellation of Entry_2 failed after 3 attempts.
Order may still be active on exchange.

Action:
manual review required

────────────────
Source: timeout_worker
```

### 6b — CLOSE_REQUESTED (out of scope)

Rimane fuori scope. Se implementato in futuro: nuova riga in event map con policy `off`
inizialmente, poi formatter dedicato (spec §17.1).

### 6c — Pause/Resume format — spec-compliant EN

`format_pause` → `⏸️ EXECUTION PAUSED` con Scope, Mode, Effect, Source, Command.
`format_resume` → `▶️ EXECUTION RESUMED` con Scope, Mode, Effect, Source, Command.
`format_start` rimane invariato (messaggio interno, non definito dalla spec).

**File toccati:**
- `src/runtime_v2/control_plane/outbox_writer.py`
- `src/runtime_v2/control_plane/formatters/clean_log.py`
- `src/runtime_v2/control_plane/formatters/pause.py`

---

## Riepilogo file toccati

| Sezione | File |
|---------|------|
| 1 | `ws_fill_watcher.py`, `event_sync.py`, `event_processor.py` |
| 2 | `010_ops_pnl_columns.sql`, `workers.py`, `outbox_writer.py` |
| 3 | `outbox_writer.py`, `clean_log.py` |
| 4 | `outbox_writer.py`, `clean_log.py` |
| 5 | `011_ops_outbox_aggregation.sql`, `aggregation_worker.py`, `notification_dispatcher.py`, `outbox_writer.py`, `clean_log.py`, `bootstrap.py`, `config.py` |
| 6 | `outbox_writer.py`, `clean_log.py`, `pause.py` |

## Acceptance Criteria — obiettivo post-implementazione

Tutti i 16 AC della spec §18 devono risultare PASS.
AC correntemente falliti che questa spec risolve: 6, 7, 8, 9, 10, 12, 13.
