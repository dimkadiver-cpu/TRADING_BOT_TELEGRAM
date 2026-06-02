# CLEAN_LOG Gap Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chiudere i gap della spec `docs/superpowers/specs/2026-05-31-clean-log-gap-remediation-design.md` portando CLEAN_LOG a propagare dati reali di fill/fee/PnL, renderizzare i formati richiesti, aggiungere gli eventi mancanti e introdurre debounce/aggregazione.

**Architecture:** La correzione procede bottom-up: prima si preservano i dati nei payload exchange/lifecycle, poi si accumula PnL sulla chain, poi `outbox_writer.py` costruisce payload clean-log ricchi e `clean_log.py` li formatta. Gli eventi nuovi restano proiezioni del lifecycle esistente dove possibile; solo l'aggregazione introduce un worker dedicato che compatta righe outbox mature marcando le originali `SUPPRESSED`.

**Tech Stack:** Python 3.12, sqlite3, raw SQL migrations in `db/ops_migrations/`, Pydantic v2, pytest, asyncio control-plane dispatcher.

---

## Scope Check

La spec attraversa sei sottosistemi dipendenti:

1. exchange event ingestion e lifecycle event payload;
2. schema e accumulo PnL su `ops_trade_chains`;
3. payload builder e formatter CLEAN_LOG;
4. eventi derivati `ENTRY_CANCELLED` e `BE_EXIT`;
5. outbox debounce/aggregation worker;
6. `CANCEL_FAILED` e formatter pause/resume.

Il piano resta in un solo file per mantenere il contratto end-to-end, ma l'esecuzione va fatta come sei parti sequenziali. Ogni parte lascia software testabile da sola.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | Propagare i campi normalizzati `exec_price`, `exec_qty`, `exec_fee`, `closed_size` fino agli eventi classificati dal websocket. |
| `src/runtime_v2/execution_gateway/event_sync.py` | Propagare `fill_price`, `filled_qty`, `exec_fee`, `closed_size` nei path REST/reconciliation. |
| `src/runtime_v2/lifecycle/event_processor.py` | Normalizzare i payload lifecycle per fill/close/cancel, senza copiare JSON raw quando serve contratto stabile. |
| `src/runtime_v2/lifecycle/workers.py` | Persistenza risultato lifecycle, accumulo PnL/fee dopo eventi di chiusura e proiezione clean-log. |
| `db/ops_migrations/010_ops_pnl_columns.sql` | Aggiungere colonne cumulative PnL/fee/funding/margin. |
| `db/ops_migrations/011_ops_outbox_aggregation.sql` | Aggiungere colonne outbox per debounce e aggregation. |
| `src/runtime_v2/control_plane/outbox_writer.py` | Mappa lifecycle -> notification, filtro eventi, calcolo `send_after`, payload builder ricco e final result. |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | Formatter CLEAN_LOG spec-compliant per tutti i tipi evento. |
| `src/runtime_v2/control_plane/formatters/pause.py` | Formatter pause/resume in inglese spec-compliant. |
| `src/runtime_v2/control_plane/aggregation_worker.py` | Worker nuovo per TP batch, update compositi e multi-chain summary. |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | Saltare `SUPPRESSED` e rispettare `send_after`. |
| `src/runtime_v2/control_plane/bootstrap.py` | Avviare e fermare `AggregationWorker` insieme al control plane. |
| `src/runtime_v2/control_plane/config.py` | Caricare i nuovi campi `clean_log`. |
| `src/runtime_v2/control_plane/models.py` | Estendere literal/status/config models per `SUPPRESSED` e debounce. |
| `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py` | Copertura websocket payload enrichment. |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | Copertura REST/reconciliation payload enrichment e cancel failed. |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | Copertura lifecycle payload normalizzati. |
| `tests/runtime_v2/lifecycle/test_workers.py` | Copertura accumulo PnL/fee e `allocated_margin`. |
| `tests/runtime_v2/control_plane/test_migration_010.py` | Verifica schema PnL. |
| `tests/runtime_v2/control_plane/test_migration_011.py` | Verifica schema aggregation. |
| `tests/runtime_v2/control_plane/test_outbox_writer.py` | Proiezione payload ricchi, BE_EXIT, ENTRY_CANCELLED, send_after. |
| `tests/runtime_v2/control_plane/test_clean_log_formatter.py` | Formatter core aggiornati. |
| `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py` | Formatter eventi avanzati e nuovi handler. |
| `tests/runtime_v2/control_plane/test_aggregation_worker.py` | Aggregazione TP/update/multi-chain. |
| `tests/runtime_v2/control_plane/test_dispatcher.py` | Dispatcher rispetta `send_after` e ignora `SUPPRESSED`. |
| `tests/runtime_v2/control_plane/test_config.py` | Config clean-log debounce/aggregation. |

---

## Acceptance Contract

**Done means:**
- i lifecycle events di fill/close contengono `fill_price`, `filled_qty`, `exec_fee` e dati chiusura quando disponibili;
- `ops_trade_chains` accumula `cumulative_gross_pnl`, `cumulative_fees`, `cumulative_funding`, `allocated_margin`;
- i formatter CLEAN_LOG producono i campi richiesti dalla spec per gli eventi esistenti e nuovi;
- `ENTRY_CANCELLED`, `BE_EXIT`, `TP_BATCH_FILLED`, `MULTI_CHAIN_UPDATE`, `MULTI_CHAIN_CLOSED`, `CANCEL_FAILED` sono mappati/formattati;
- il dispatcher non invia righe immature (`send_after`) o soppresse (`SUPPRESSED`);
- i 16 AC della spec madre CLEAN_LOG sono rieseguibili con esito PASS o con una limitazione documentata solo per funding reale Bybit.

**Primary signal:**
- `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane tests\runtime_v2\lifecycle tests\runtime_v2\execution_gateway -q`

**Secondary signals:**
- `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q`
- `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py tests\runtime_v2\control_plane\test_aggregation_worker.py tests\runtime_v2\control_plane\test_dispatcher.py -q`
- `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py tests\runtime_v2\lifecycle\test_workers.py -q`

---

## Part 1: Enrichment payload lifecycle events

### Task 1: Preserve exchange fill fields in WS and REST event payloads

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Test: `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py`
- Test: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 1: Write failing WS payload enrichment test**

Add to `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py`:

```python
def test_ws_fill_payload_preserves_price_qty_fee_and_closed_size(tmp_path):
    # Existing helpers in this file already apply ops migrations and build repo/normalizer/classifier.
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    repo = GatewayCommandRepository(db_path)
    _seed_open_chain(db_path, chain_id=10, symbol="BTC/USDT", side="LONG")

    raw = ExchangeRawEvent(
        source="bybit_ws",
        event_type="trade",
        symbol="BTC/USDT",
        side="Sell",
        order_id="oid-tp1",
        order_link_id="tsb:main:10:tp:1:77",
        exec_price=68000.0,
        exec_qty=0.002,
        exec_fee=1.10,
        closed_size=0.002,
        raw_json={},
    )
    classified = EventClassifier(known_order_link_ids=repo.get_known_order_link_ids()).classify(raw)
    inserted = repo.insert_raw_and_classified(classified)

    assert inserted is True
    conn = sqlite3.connect(db_path)
    payload_json = conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=10 AND event_type='TP_FILLED'"
    ).fetchone()[0]
    conn.close()
    payload = json.loads(payload_json)
    assert payload["fill_price"] == 68000.0
    assert payload["filled_qty"] == 0.002
    assert payload["exec_fee"] == 1.10
    assert payload["closed_size"] == 0.002
```

- [ ] **Step 2: Write failing REST reconciliation enrichment tests**

Add to `tests/runtime_v2/execution_gateway/test_event_sync.py`:

```python
def test_save_fill_event_includes_exec_fee_for_tp(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    repo = GatewayCommandRepository(db_path)
    _seed_command(db_path, command_id=77, chain_id=10, command_type="PLACE_TP",
                  client_order_id="tsb:main:10:tp:1:77")
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=FakeAdapter(),
        repo=repo,
        execution_account_id="main",
    )
    raw = RawAdapterOrder(
        client_order_id="tsb:main:10:tp:1:77",
        exchange_order_id="ex-tp-1",
        status="FILLED",
        filled_qty=0.002,
        average_price=68000.0,
        fee=1.10,
    )

    assert worker._save_fill_event("tsb:main:10:tp:1:77", raw) is True
    conn = sqlite3.connect(db_path)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()[0])
    conn.close()
    assert payload["fill_price"] == 68000.0
    assert payload["filled_qty"] == 0.002
    assert payload["exec_fee"] == 1.10
    assert payload["closed_size"] == 0.002
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_bybit_ws_fill_watcher.py::test_ws_fill_payload_preserves_price_qty_fee_and_closed_size tests\runtime_v2\execution_gateway\test_event_sync.py::test_save_fill_event_includes_exec_fee_for_tp -q
```

Expected: FAIL because `exec_fee` and/or `closed_size` are missing from persisted payloads.

- [ ] **Step 4: Implement payload enrichment in the producer layer**

In `src/runtime_v2/execution_gateway/repositories.py`, where `insert_raw_and_classified()` builds `payload` for `ops_exchange_events`, make the payload include:

```python
payload = {
    **payload,
    "fill_price": classified.raw_event.exec_price,
    "filled_qty": classified.raw_event.exec_qty,
    "exec_fee": classified.raw_event.exec_fee,
    "closed_size": classified.raw_event.closed_size,
}
```

In `src/runtime_v2/execution_gateway/event_sync.py`, update `_save_fill_event()` payload:

```python
payload: dict = {
    "fill_price": raw.average_price,
    "filled_qty": raw.filled_qty,
    "exec_fee": getattr(raw, "fee", None),
    "closed_size": raw.filled_qty if event_type in {
        "TP_FILLED", "SL_FILLED", "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED",
    } else None,
    "command_id": coid.command_id,
}
```

Also update `run_position_reconciliation()` and `run_trade_based_reconciliation()` payloads to include `"exec_fee": getattr(trade, "fee", None)` where a trade object exists and `"closed_size": open_qty` or `trade.amount`.

- [ ] **Step 5: Run focused gateway tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_bybit_ws_fill_watcher.py tests\runtime_v2\execution_gateway\test_event_sync.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit if commits are allowed**

```bash
git add src/runtime_v2/execution_gateway/repositories.py src/runtime_v2/execution_gateway/event_sync.py tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(clean-log): preserve fill fee payload fields"
```

### Task 2: Normalize lifecycle fill payloads

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 1: Write failing lifecycle payload tests**

Add to `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
def test_tp_filled_lifecycle_event_preserves_fill_price_qty_and_fee():
    chain = _make_chain(entry_avg_price=65000.0, open_position_qty=0.01)
    event = _make_exchange_event(
        event_type="TP_FILLED",
        payload={
            "tp_level": 1,
            "is_final": False,
            "fill_price": 68000.0,
            "filled_qty": 0.002,
            "exec_fee": 1.10,
            "closed_size": 0.002,
        },
    )
    result = LifecycleEventProcessor().process(event, chain, [])
    payload = json.loads(result.lifecycle_events[0].payload_json)
    assert payload == {
        "tp_level": 1,
        "is_final": False,
        "fill_price": 68000.0,
        "filled_qty": 0.002,
        "exec_fee": 1.10,
        "closed_size": 0.002,
    }


def test_entry_filled_lifecycle_event_preserves_exec_fee():
    chain = _make_chain(lifecycle_state="WAITING_ENTRY", open_position_qty=0.0)
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 65000.0, "filled_qty": 0.004, "exec_fee": 0.90},
    )
    result = LifecycleEventProcessor().process(event, chain, [])
    payload = json.loads(result.lifecycle_events[0].payload_json)
    assert payload["fill_price"] == 65000.0
    assert payload["filled_qty"] == 0.004
    assert payload["exec_fee"] == 0.90


@pytest.mark.parametrize("event_type", ["SL_FILLED", "CLOSE_FULL_FILLED", "CLOSE_PARTIAL_FILLED"])
def test_close_lifecycle_payload_is_normalized(event_type):
    chain = _make_chain(entry_avg_price=65000.0, open_position_qty=0.01)
    event = _make_exchange_event(
        event_type=event_type,
        payload={"fill_price": 64000.0, "filled_qty": 0.01, "exec_fee": 1.70},
    )
    result = LifecycleEventProcessor().process(event, chain, [])
    payload = json.loads(result.lifecycle_events[0].payload_json)
    assert payload["fill_price"] == 64000.0
    assert payload["filled_qty"] == 0.01
    assert payload["exec_fee"] == 1.70
    assert payload["closed_size"] == 0.01
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py::test_tp_filled_lifecycle_event_preserves_fill_price_qty_and_fee tests\runtime_v2\lifecycle\test_event_processor.py::test_entry_filled_lifecycle_event_preserves_exec_fee tests\runtime_v2\lifecycle\test_event_processor.py::test_close_lifecycle_payload_is_normalized -q
```

Expected: FAIL because `_process_tp_filled()` only emits `tp_level` and `is_final`, and entry fill omits fee.

- [ ] **Step 3: Add a local payload helper in `event_processor.py`**

Add near the top of `event_processor.py`:

```python
def _normalized_fill_payload(payload: dict, *, default_qty: float = 0.0) -> dict:
    filled_qty = payload.get("filled_qty")
    if filled_qty is None:
        filled_qty = payload.get("fill_qty")
    if filled_qty is None:
        filled_qty = payload.get("closed_size")
    if filled_qty is None:
        filled_qty = default_qty
    filled_qty_float = float(filled_qty or 0.0)
    return {
        "fill_price": payload.get("fill_price"),
        "filled_qty": filled_qty_float,
        "exec_fee": payload.get("exec_fee"),
        "closed_size": payload.get("closed_size", filled_qty_float),
    }
```

Update `_process_entry_filled()` first lifecycle event payload:

```python
payload_json=json.dumps({
    "fill_price": fill_price,
    "filled_qty": fill_qty,
    "exec_fee": payload.get("exec_fee"),
})
```

Update `_process_tp_filled()`:

```python
fill_payload = _normalized_fill_payload(payload, default_qty=fill_qty)
tp_payload = {
    "tp_level": tp_level,
    "is_final": is_final,
    **fill_payload,
}
...
payload_json=json.dumps(tp_payload)
```

Update `_process_sl_filled()`, `_process_close_full_filled()`, `_process_close_partial_filled()` to use:

```python
fill_payload = _normalized_fill_payload(payload, default_qty=fill_qty)
...
payload_json=json.dumps(fill_payload)
```

- [ ] **Step 4: Run lifecycle event processor tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit if commits are allowed**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): normalize fill payloads for clean log"
```

---

## Part 2: PnL computation and Final Result

### Task 3: Add PnL columns and allocated margin

**Files:**
- Create: `db/ops_migrations/010_ops_pnl_columns.sql`
- Modify: `src/runtime_v2/lifecycle/repositories.py`
- Test: `tests/runtime_v2/control_plane/test_migration_010.py`
- Test: `tests/runtime_v2/lifecycle/test_repositories.py`

- [ ] **Step 1: Write failing migration test**

Create `tests/runtime_v2/control_plane/test_migration_010.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def test_migration_010_adds_pnl_columns(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(ops_trade_chains)")}
    conn.close()
    assert columns["cumulative_gross_pnl"] == "REAL"
    assert columns["cumulative_fees"] == "REAL"
    assert columns["cumulative_funding"] == "REAL"
    assert columns["allocated_margin"] == "REAL"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_migration_010.py -q
```

Expected: FAIL because migration file is missing.

- [ ] **Step 3: Create migration 010**

Create `db/ops_migrations/010_ops_pnl_columns.sql`:

```sql
-- db/ops_migrations/010_ops_pnl_columns.sql

ALTER TABLE ops_trade_chains ADD COLUMN cumulative_gross_pnl REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN cumulative_fees REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN cumulative_funding REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN allocated_margin REAL;
```

- [ ] **Step 4: Populate allocated_margin when creating chains**

In `src/runtime_v2/lifecycle/repositories.py`, locate `TradeChainRepository.save()`. Parse `risk_snapshot_json` before the insert:

```python
allocated_margin = None
try:
    risk_snapshot = json.loads(chain.risk_snapshot_json or "{}")
    raw_margin = risk_snapshot.get("risk_amount")
    if raw_margin is not None:
        allocated_margin = float(raw_margin)
except (TypeError, ValueError, json.JSONDecodeError):
    allocated_margin = None
```

Add `allocated_margin` to the insert column list and value list.

- [ ] **Step 5: Add repository test for allocated margin**

Add to `tests/runtime_v2/lifecycle/test_repositories.py`:

```python
def test_trade_chain_save_populates_allocated_margin_from_risk_amount(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    repo = TradeChainRepository(db_path)
    chain = TradeChain(
        source_enrichment_id=1,
        canonical_message_id=1,
        raw_message_id=1,
        trader_id="trader_a",
        account_id="main",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT",
        management_plan_json="{}",
        risk_snapshot_json=json.dumps({"risk_amount": 100.0}),
    )
    saved = repo.save(chain)
    conn = sqlite3.connect(db_path)
    margin = conn.execute(
        "SELECT allocated_margin FROM ops_trade_chains WHERE trade_chain_id=?",
        (saved.trade_chain_id,),
    ).fetchone()[0]
    conn.close()
    assert margin == 100.0
```

- [ ] **Step 6: Run migration and repository tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_migration_010.py tests\runtime_v2\lifecycle\test_repositories.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit if commits are allowed**

```bash
git add db/ops_migrations/010_ops_pnl_columns.sql src/runtime_v2/lifecycle/repositories.py tests/runtime_v2/control_plane/test_migration_010.py tests/runtime_v2/lifecycle/test_repositories.py
git commit -m "feat(lifecycle): add chain pnl accumulators"
```

### Task 4: Accumulate gross PnL and fees in worker persistence

**Files:**
- Modify: `src/runtime_v2/lifecycle/workers.py`
- Test: `tests/runtime_v2/lifecycle/test_workers.py`

- [ ] **Step 1: Write failing PnL accumulation tests**

Add to `tests/runtime_v2/lifecycle/test_workers.py`:

```python
def test_worker_accumulates_long_tp_pnl_and_fee(ops_db):
    chain_id = _seed_chain(
        ops_db,
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="OPEN",
        entry_avg_price=65000.0,
        open_position_qty=0.01,
    )
    worker = _make_worker(ops_db)
    result = EventProcessorResult(
        new_lifecycle_state="PARTIALLY_CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="TP_FILLED",
                source_type="exchange_event",
                payload_json=json.dumps({
                    "tp_level": 1,
                    "is_final": False,
                    "fill_price": 68000.0,
                    "filled_qty": 0.002,
                    "exec_fee": 1.10,
                    "closed_size": 0.002,
                }),
                idempotency_key=f"tp:{chain_id}:1",
            )
        ],
        execution_commands=[],
        new_open_position_qty=0.008,
        new_closed_position_qty=0.002,
    )
    worker._persist_result(chain_id, result)
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT cumulative_gross_pnl, cumulative_fees FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    conn.close()
    assert row == (6.0, 1.10)


def test_worker_accumulates_short_sl_pnl_negative_when_price_above_entry(ops_db):
    chain_id = _seed_chain(
        ops_db,
        symbol="ETH/USDT",
        side="SHORT",
        lifecycle_state="OPEN",
        entry_avg_price=3000.0,
        open_position_qty=0.5,
    )
    worker = _make_worker(ops_db)
    result = EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="SL_FILLED",
                source_type="exchange_event",
                payload_json=json.dumps({
                    "fill_price": 3050.0,
                    "filled_qty": 0.5,
                    "exec_fee": 2.0,
                    "closed_size": 0.5,
                }),
                idempotency_key=f"sl:{chain_id}:1",
            )
        ],
        execution_commands=[],
        new_open_position_qty=0.0,
        new_closed_position_qty=0.5,
    )
    worker._persist_result(chain_id, result)
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT cumulative_gross_pnl, cumulative_fees FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    conn.close()
    assert row == (-25.0, 2.0)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py::test_worker_accumulates_long_tp_pnl_and_fee tests\runtime_v2\lifecycle\test_workers.py::test_worker_accumulates_short_sl_pnl_negative_when_price_above_entry -q
```

Expected: FAIL because worker does not update PnL columns.

- [ ] **Step 3: Add PnL helper in `workers.py`**

Add near `_now()`:

```python
_PNL_EVENT_TYPES = {
    "TP_FILLED",
    "SL_FILLED",
    "CLOSE_FULL_FILLED",
    "CLOSE_PARTIAL_FILLED",
}


def _accumulate_pnl_for_events(
    conn: sqlite3.Connection,
    *,
    chain_id: int,
    events: list[LifecycleEvent],
) -> None:
    row = conn.execute(
        "SELECT side, entry_avg_price FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    if row is None:
        return
    side = str(row[0] or "").upper()
    entry_avg_price = row[1]
    if entry_avg_price is None:
        return
    side_sign = 1.0 if side == "LONG" else -1.0
    gross_total = 0.0
    fee_total = 0.0
    for event in events:
        if event.event_type not in _PNL_EVENT_TYPES:
            continue
        try:
            payload = json.loads(event.payload_json or "{}")
        except json.JSONDecodeError:
            continue
        fill_price = payload.get("fill_price")
        closed_qty = payload.get("closed_size", payload.get("filled_qty"))
        if fill_price is None or closed_qty is None:
            continue
        gross_total += float(closed_qty) * (float(fill_price) - float(entry_avg_price)) * side_sign
        fee_total += float(payload.get("exec_fee") or 0.0)
    if gross_total != 0.0 or fee_total != 0.0:
        conn.execute(
            """
            UPDATE ops_trade_chains
            SET cumulative_gross_pnl = COALESCE(cumulative_gross_pnl, 0.0) + ?,
                cumulative_fees = COALESCE(cumulative_fees, 0.0) + ?
            WHERE trade_chain_id=?
            """,
            (gross_total, fee_total, chain_id),
        )
```

In `_persist_result()`, after inserting lifecycle events and before `project_clean_log_for_chain(conn, chain_id)`, call:

```python
_accumulate_pnl_for_events(conn, chain_id=chain_id, events=result.lifecycle_events)
```

- [ ] **Step 4: Run worker tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit if commits are allowed**

```bash
git add src/runtime_v2/lifecycle/workers.py tests/runtime_v2/lifecycle/test_workers.py
git commit -m "feat(lifecycle): accumulate realised pnl and fees"
```

---

## Part 3: Payload builder and formatter fixes

### Task 5: Extend `project_clean_log_for_chain()` SELECT and final result payload

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Write failing final result payload test**

Add to `tests/runtime_v2/control_plane/test_outbox_writer.py`:

```python
def test_tp_final_payload_includes_final_result_and_pnl_fields(ops_db):
    conn = sqlite3.connect(ops_db)
    now = _now()
    with conn:
        _seed_chain(conn, 700)
        conn.execute(
            """
            UPDATE ops_trade_chains
            SET entry_avg_price=?, open_position_qty=?, filled_entry_qty=?,
                cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, allocated_margin=?
            WHERE trade_chain_id=?
            """,
            (65000.0, 0.002, 0.01, 350.0, 5.75, 0.0, 1000.0, 700),
        )
        _seed_event(conn, 700, "TP_FILLED", "tp_final:700:1", {
            "tp_level": 3,
            "is_final": True,
            "fill_price": 71000.0,
            "filled_qty": 0.002,
            "exec_fee": 1.65,
            "closed_size": 0.002,
        })
        project_clean_log_for_chain(conn, 700)
    notification_type, payload_json = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    payload = json.loads(payload_json)
    assert notification_type == "TP_FILLED_FINAL"
    assert payload["fill_price"] == 71000.0
    assert payload["fee"] == 1.65
    assert payload["pnl"] == 12.0
    assert payload["final_result"] == {
        "roi_net_pct": 34.425,
        "total_pnl_net": 344.25,
        "gross_pnl": 350.0,
        "fees": -5.75,
        "funding": 0.0,
        "close_reason": "TAKE_PROFIT",
    }
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py::test_tp_final_payload_includes_final_result_and_pnl_fields -q
```

Expected: FAIL because outbox payload does not read PnL columns.

- [ ] **Step 3: Add helper functions in `outbox_writer.py`**

Add:

```python
def _side_pnl(side: str | None, entry_avg_price: float | None, fill_price, qty) -> float | None:
    if entry_avg_price is None or fill_price is None or qty is None:
        return None
    sign = 1.0 if str(side).upper() == "LONG" else -1.0
    return round(float(qty) * (float(fill_price) - float(entry_avg_price)) * sign, 8)


def _closed_pct(qty, filled_entry_qty: float | None) -> float | None:
    if qty is None or not filled_entry_qty:
        return None
    return round(float(qty) / float(filled_entry_qty) * 100.0, 2)


def _remaining_pct(open_position_qty: float | None, filled_entry_qty: float | None) -> float | None:
    if open_position_qty is None or not filled_entry_qty:
        return None
    return round(float(open_position_qty) / float(filled_entry_qty) * 100.0, 2)


def _final_result(
    *,
    gross_pnl: float | None,
    fees: float | None,
    funding: float | None,
    allocated_margin: float | None,
    close_reason: str,
) -> dict:
    gross = float(gross_pnl or 0.0)
    fee_total = float(fees or 0.0)
    funding_total = float(funding or 0.0)
    net = gross - fee_total + funding_total
    roi = None
    if allocated_margin and float(allocated_margin) > 0.0:
        roi = round(net / float(allocated_margin) * 100.0, 4)
    return {
        "roi_net_pct": roi,
        "total_pnl_net": round(net, 8),
        "gross_pnl": round(gross, 8),
        "fees": round(-fee_total, 8),
        "funding": round(funding_total, 8),
        "close_reason": close_reason,
    }
```

Extend the chain SELECT in `project_clean_log_for_chain()`:

```python
"entry_avg_price, current_stop_price, "
"source_chat_id, telegram_message_id, "
"cumulative_gross_pnl, cumulative_fees, cumulative_funding, allocated_margin, "
"filled_entry_qty, open_position_qty, be_protection_status "
```

Pass those values into `_build_payload()`.

- [ ] **Step 4: Update fill payload branches**

For `TP_FILLED` and `TP_FILLED_FINAL`, return fields:

```python
closed_qty = ev.get("closed_size", ev.get("filled_qty"))
fill_price = ev.get("fill_price")
final_result = None
if notification_type == "TP_FILLED_FINAL":
    final_result = _final_result(
        gross_pnl=cumulative_gross_pnl,
        fees=cumulative_fees,
        funding=cumulative_funding,
        allocated_margin=allocated_margin,
        close_reason="TAKE_PROFIT",
    )
return {
    **base,
    "tp_level": tp_level,
    "tp_price": tp_price,
    "fill_price": fill_price,
    "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
    "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
    "fee": ev.get("exec_fee"),
    "remaining_pct": _remaining_pct(open_position_qty, filled_entry_qty),
    "sl_current": current_stop_price,
    "final_result": final_result,
    "source": ev.get("source", "exchange"),
}
```

Use the same pattern for `SL_FILLED`, `POSITION_CLOSED`, `CLOSE_PARTIAL_FILLED`, and later `BE_EXIT`.

- [ ] **Step 5: Run outbox writer tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "feat(clean-log): build pnl rich payloads"
```

### Task 6: Make formatter helpers spec-compliant

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Write failing formatter helper tests**

Add to `tests/runtime_v2/control_plane/test_clean_log_formatter.py`:

```python
def test_footer_adds_separator_before_link():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "source": "original_message",
        "link": "https://t.me/c/1/2",
    })
    assert "Source: original_message\n" + _SEP + "\nhttps://t.me/c/1/2" in text


def test_update_done_uses_operation_label_and_square_bullet():
    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "operations": ["Move SL to BE"],
        "changed": [{"field": "SL", "old": 64000, "new": 65020, "note": "Changed by rule after TP_1"}],
        "source": "trader_update",
    })
    assert "Operation:" in text
    assert "Applied:" not in text
    assert "▪️ Move SL to BE" in text
    assert "SL: 64,000 -> 65,020 *" in text
    assert "* Changed by rule after TP_1" in text
```

If `_SEP` is not exported in tests, compare the literal separator from the module output or import it deliberately as private test fixture.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py -q
```

Expected: FAIL because current formatter uses old bullets, footer, labels.

- [ ] **Step 3: Update shared helper contract**

In `clean_log.py`, replace `_footer()` with:

```python
def _footer(source: str, link: str | None = None, trader_id: str | None = None) -> list[str]:
    lines = [_SEP]
    if trader_id:
        lines.append(f"Trader: {trader_id}")
    lines.append(f"Source: {source}")
    if link:
        lines.extend([_SEP, link])
    return lines
```

Add:

```python
_BULLET = "▪️"


def _fmt_money(value, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if signed and number >= 0 else ""
    return f"{prefix}{number:.2f} USDT"


def _fmt_pct(value, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if signed and number >= 0 else ""
    return f"{prefix}{number:.2f}%".replace(".00%", "%")


def _final_result_lines(final_result: dict | None) -> list[str]:
    if not final_result:
        return []
    lines = [_SEP, "Final Result:"]
    if final_result.get("roi_net_pct") is not None:
        lines.append(f"ROI net: {_fmt_pct(final_result['roi_net_pct'], signed=True)}")
    lines.append(f"Total PnL net: {_fmt_money(final_result.get('total_pnl_net'), signed=True)}")
    lines.append(f"Gross PnL: {_fmt_money(final_result.get('gross_pnl'), signed=True)}")
    lines.append(f"Fees: {_fmt_money(final_result.get('fees'), signed=True)}")
    lines.append(f"Funding: {_fmt_money(final_result.get('funding'), signed=True)}")
    return lines
```

- [ ] **Step 4: Run formatter tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: existing tests may need assertion updates to the new spec output; final result PASS.

- [ ] **Step 5: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
git commit -m "feat(clean-log): align formatter shared layout"
```

### Task 7: Update per-event CLEAN_LOG formatters

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`

- [ ] **Step 1: Add focused tests for existing event fixes**

Add tests for these exact assertions:

```python
def test_tp_filled_renders_closed_pnl_fee_remaining_and_be_label():
    text = format_clean_log("TP_FILLED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "tp_level": 1,
        "tp_price": 68000.0,
        "fill_price": 68000.0,
        "closed_pct": 30.0,
        "pnl": 70.20,
        "fee": 1.10,
        "remaining_pct": 70.0,
        "sl_current": 65020.0,
        "be_protection_status": "PROTECTED",
        "source": "exchange",
    })
    assert "TP_1: 68,000" in text
    assert "Closed: 30%" in text
    assert "PnL: +70.20 USDT" in text
    assert "Fee: 1.10 USDT" in text
    assert "Position: 70%" in text
    assert "SL: 65,020 BE" in text


def test_sl_filled_renders_sl_label_and_final_result():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "sl_price": 64000.0,
        "closed_pct": 100.0,
        "pnl": -50.0,
        "fee": 1.70,
        "final_result": {
            "roi_net_pct": -5.17,
            "total_pnl_net": -51.70,
            "gross_pnl": -50.0,
            "fees": -1.70,
            "funding": 0.0,
            "close_reason": "STOP_LOSS",
        },
        "source": "exchange",
    })
    assert "SL: 64,000" in text
    assert "Fill:" not in text
    assert "Closed: 100%" in text
    assert "Final Result:" in text
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: FAIL on old labels and missing fields.

- [ ] **Step 3: Implement all per-event branch updates**

Update:
- `_signal_accepted`: risk marker/footnote, range/ladder/deferred market labels.
- `_signal_rejected`: include TPs; split `Rejected:` and `Reason:`.
- `_review_required`: body entries/SL/TPs; footer reason/action/trader.
- `_entry_opened`: `Entry_N: price Type`, qty symbol unit, fee, filled pct, changed vs signal.
- `_entry_updated`: chart icon, `Filled`, `Position`, `Fee`, `Pending`.
- `_tp_filled`: closed pct, PnL, fee, remaining position, BE label, changed section.
- `_sl_filled`: `SL:`, closed pct, PnL, fee, final result.
- `_position_closed`: `Closed`, `Qty`, `Price`, `Reason`, final result.
- `_update_done`, `_update_partial`, `_update_rejected`: operation/cancelled/changed/reason structure.
- `_pending_timeout`: chart icon and `Expired/Open since/Action/Position`.
- `_reconciliation_warning`: multiline `Issue:`.
- `_reconciliation_fixed`: `Fixed:` and `Protection:`.
- `_reentry_accepted`: accepted icon, body entries/SL/TPs, footer previous chain.

Use existing helper functions; do not add a second formatter abstraction.

- [ ] **Step 4: Run formatter suites**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
git commit -m "feat(clean-log): format existing events to spec"
```

---

## Part 4: New event types ENTRY_CANCELLED and BE_EXIT

### Task 8: Project and format ENTRY_CANCELLED

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`

- [ ] **Step 1: Write failing projection tests**

Add:

```python
def test_pending_entry_cancelled_projects_entry_cancelled_unless_position_closed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 800)
        _seed_event(conn, 800, "PENDING_ENTRY_CANCELLED", "pending_cancelled:800:1", {
            "sequence": 2,
            "price": 64000.0,
            "entry_type": "LIMIT",
            "cancel_reason": "trader_update",
        })
        project_clean_log_for_chain(conn, 800)
    row = conn.execute("SELECT notification_type FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row == ("ENTRY_CANCELLED",)


def test_pending_entry_cancelled_position_closed_is_filtered(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 801)
        _seed_event(conn, 801, "PENDING_ENTRY_CANCELLED", "pending_cancelled:801:1", {
            "sequence": 2,
            "cancel_reason": "position_closed",
        })
        project_clean_log_for_chain(conn, 801)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 0
```

- [ ] **Step 2: Write failing formatter test**

Add:

```python
def test_entry_cancelled_formatter_renders_partial_fill_and_position():
    text = format_clean_log("ENTRY_CANCELLED", {
        "chain_id": 150,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "cancelled_entry": {"sequence": 2, "price": 64000.0, "entry_type": "LIMIT"},
        "partial_fill_pct": 30.0,
        "partial_fill_qty": 0.002,
        "avg_entry": 64820.0,
        "total_filled_qty": 0.006,
        "source": "trader_update",
        "link": "https://t.me/c/1/2",
    })
    assert "ENTRY CANCELLED" in text
    assert "Entry_2: 64,000 Limit" in text
    assert "Partial fill: 30% (0.002 BTC kept)" in text
    assert "Avg entry: 64,820" in text
    assert "Total filled: 0.006 BTC" in text
```

- [ ] **Step 3: Implement mapping, filter and payload**

In `_CLEAN_LOG_EVENT_MAP`:

```python
"PENDING_ENTRY_CANCELLED": "ENTRY_CANCELLED",
```

Inside `project_clean_log_for_chain()` after notification type lookup:

```python
if notification_type == "ENTRY_CANCELLED" and ev.get("cancel_reason") == "position_closed":
    continue
```

In `_build_payload()`:

```python
if notification_type == "ENTRY_CANCELLED":
    sequence = ev.get("sequence")
    cancelled_entry = {
        "sequence": sequence,
        "price": ev.get("price"),
        "entry_type": ev.get("entry_type", "LIMIT"),
    }
    planned_qty = ev.get("planned_entry_qty")
    partial_qty = ev.get("partial_fill_qty", ev.get("filled_qty"))
    partial_pct = None
    if planned_qty and partial_qty is not None:
        partial_pct = round(float(partial_qty) / float(planned_qty) * 100.0, 2)
    return {
        **base,
        "cancelled_entry": cancelled_entry,
        "partial_fill_pct": partial_pct,
        "partial_fill_qty": partial_qty,
        "avg_entry": entry_avg_price,
        "total_filled_qty": filled_entry_qty,
        "source": ev.get("source", ev.get("cancel_reason", "runtime")),
        "link": ev.get("source_message_link"),
    }
```

- [ ] **Step 4: Add formatter branch**

Add `_entry_cancelled(p)` and dispatcher branch:

```python
if notification_type == "ENTRY_CANCELLED":
    return _entry_cancelled(payload)
```

- [ ] **Step 5: Run tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_outbox_writer.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
git commit -m "feat(clean-log): add entry cancelled notification"
```

### Task 9: Detect and format BE_EXIT

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`

- [ ] **Step 1: Write failing BE_EXIT projection test**

Add:

```python
def test_close_full_filled_on_protected_chain_projects_be_exit(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 900)
        conn.execute(
            "UPDATE ops_trade_chains SET be_protection_status='PROTECTED', entry_avg_price=?, cumulative_gross_pnl=?, cumulative_fees=?, allocated_margin=? WHERE trade_chain_id=?",
            (65000.0, 118.0, 5.70, 10000.0, 900),
        )
        _seed_event(conn, 900, "CLOSE_FULL_FILLED", "close_full:900:1", {
            "fill_price": 65020.0,
            "filled_qty": 0.01,
            "exec_fee": 1.70,
            "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 900)
    row = conn.execute("SELECT notification_type, payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row[0] == "BE_EXIT"
    payload = json.loads(row[1])
    assert payload["close_reason"] == "BREAKEVEN_AFTER_TP"
    assert payload["exit_price"] == 65020.0
```

- [ ] **Step 2: Write failing BE_EXIT formatter test**

Add:

```python
def test_be_exit_formatter_renders_final_result():
    text = format_clean_log("BE_EXIT", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "exit_price": 65020.0,
        "closed_pct": 100.0,
        "pnl": 0.0,
        "fee": 1.70,
        "final_result": {
            "roi_net_pct": 1.15,
            "total_pnl_net": 112.30,
            "gross_pnl": 118.0,
            "fees": -5.70,
            "funding": 0.0,
            "close_reason": "BREAKEVEN_AFTER_TP",
        },
        "source": "exchange",
    })
    assert "BE EXIT" in text
    assert "Exit: 65,020 BE" in text
    assert "Close reason: BREAKEVEN_AFTER_TP" in text
    assert "Final Result:" in text
```

- [ ] **Step 3: Implement BE_EXIT notification selection**

In `project_clean_log_for_chain()`, after terminal TP promotion:

```python
if event_type == "CLOSE_FULL_FILLED" and be_protection_status == "PROTECTED":
    notification_type = "BE_EXIT"
```

In `_build_payload()`:

```python
if notification_type == "BE_EXIT":
    closed_qty = ev.get("closed_size", ev.get("filled_qty"))
    fill_price = ev.get("fill_price")
    return {
        **base,
        "exit_price": fill_price,
        "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
        "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
        "fee": ev.get("exec_fee"),
        "close_reason": "BREAKEVEN_AFTER_TP",
        "final_result": _final_result(
            gross_pnl=cumulative_gross_pnl,
            fees=cumulative_fees,
            funding=cumulative_funding,
            allocated_margin=allocated_margin,
            close_reason="BREAKEVEN_AFTER_TP",
        ),
        "source": ev.get("source", "exchange"),
    }
```

- [ ] **Step 4: Add `_be_exit()` formatter and dispatcher branch**

Add:

```python
if notification_type == "BE_EXIT":
    return _be_exit(payload)
```

- [ ] **Step 5: Run tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_outbox_writer.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
git commit -m "feat(clean-log): detect breakeven exits"
```

---

## Part 5: Aggregation Worker

### Task 10: Add outbox aggregation schema and config

**Files:**
- Create: `db/ops_migrations/011_ops_outbox_aggregation.sql`
- Modify: `src/runtime_v2/control_plane/models.py`
- Modify: `src/runtime_v2/control_plane/config.py`
- Test: `tests/runtime_v2/control_plane/test_migration_011.py`
- Test: `tests/runtime_v2/control_plane/test_config.py`

- [ ] **Step 1: Write failing migration test**

Create `tests/runtime_v2/control_plane/test_migration_011.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def test_migration_011_adds_outbox_aggregation_columns(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    outbox_columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_notification_outbox)")}
    conn.close()
    assert {"send_after", "aggregation_group", "source_message_id"} <= outbox_columns
```

- [ ] **Step 2: Write failing config test**

Add to `tests/runtime_v2/control_plane/test_config.py`:

```python
def test_clean_log_aggregation_config_defaults():
    cfg = ControlPlaneConfig(
        token="t",
        chat_id=-1001,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=1),
            tech_log=TechLogConfig(thread_id=2),
            clean_log=CleanLogConfig(thread_id=3),
        ),
    )
    assert cfg.topics.clean_log.debounce_check_interval_seconds == 5
    assert cfg.topics.clean_log.aggregate_fills_seconds == 30
    assert cfg.topics.clean_log.aggregate_updates_seconds == 20
    assert cfg.topics.clean_log.multi_chain_summary_threshold == 3
    assert cfg.topics.clean_log.max_messages_per_chain_per_minute == 4
```

- [ ] **Step 3: Create migration 011**

Create `db/ops_migrations/011_ops_outbox_aggregation.sql`:

```sql
-- db/ops_migrations/011_ops_outbox_aggregation.sql

ALTER TABLE ops_notification_outbox ADD COLUMN send_after TEXT;
ALTER TABLE ops_notification_outbox ADD COLUMN aggregation_group TEXT;
ALTER TABLE ops_notification_outbox ADD COLUMN source_message_id TEXT;
```

- [ ] **Step 4: Extend models**

In `src/runtime_v2/control_plane/models.py`:

```python
OutboxStatus = Literal["PENDING", "SENDING", "SENT", "FAILED", "SUPPRESSED"]
```

Add to `CleanLogConfig`:

```python
debounce_check_interval_seconds: int = 5
aggregate_fills_seconds: int = 30
aggregate_updates_seconds: int = 20
multi_chain_summary_threshold: int = 3
max_messages_per_chain_per_minute: int = 4
```

Add to `NotificationOutboxEntry`:

```python
send_after: datetime | None = None
aggregation_group: str | None = None
source_message_id: str | None = None
```

- [ ] **Step 5: Run migration/config tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_migration_011.py tests\runtime_v2\control_plane\test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit if commits are allowed**

```bash
git add db/ops_migrations/011_ops_outbox_aggregation.sql src/runtime_v2/control_plane/models.py tests/runtime_v2/control_plane/test_migration_011.py tests/runtime_v2/control_plane/test_config.py
git commit -m "feat(clean-log): add outbox aggregation schema"
```

### Task 11: Set `send_after` and aggregation keys when writing outbox rows

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Write failing send_after tests**

Add:

```python
def test_tp_filled_outbox_has_aggregation_delay(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="TP_FILLED",
            chain_id=145,
            payload={"chain_id": 145},
            dedupe_key="clean:tp:145:1",
        )
    row = conn.execute(
        "SELECT send_after, aggregation_group FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row[0] is not None
    assert row[1] == "145:tp_batch"


def test_high_priority_clean_log_has_immediate_send_after(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SL_FILLED",
            chain_id=145,
            payload={"chain_id": 145},
            dedupe_key="clean:sl:145:1",
        )
    row = conn.execute("SELECT send_after FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row[0] is not None
```

- [ ] **Step 2: Implement send_after policy**

Change `_record()` signature to accept `send_after`, `aggregation_group`, `source_message_id`, and insert them.

Add helpers:

```python
def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _send_after_for(notification_type: str) -> str:
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return _iso_after(20)
    if notification_type in {"TP_FILLED", "TP_FILLED_FINAL"}:
        return _iso_after(30)
    return _now()


def _aggregation_group(notification_type: str, chain_id: int | None, payload: dict) -> str | None:
    if chain_id is None:
        return None
    if notification_type in {"TP_FILLED", "TP_FILLED_FINAL"}:
        return f"{chain_id}:tp_batch"
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return f"{chain_id}:{payload.get('source_message_id') or 'update_batch'}"
    return None
```

In `write_clean_log_event()`, pass:

```python
send_after=_send_after_for(notification_type),
aggregation_group=_aggregation_group(notification_type, chain_id, payload),
source_message_id=payload.get("source_message_id"),
```

- [ ] **Step 3: Run outbox tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "feat(clean-log): set outbox debounce metadata"
```

### Task 12: Dispatcher respects `send_after` and `SUPPRESSED`

**Files:**
- Modify: `src/runtime_v2/control_plane/notification_dispatcher.py`
- Test: `tests/runtime_v2/control_plane/test_dispatcher.py`

- [ ] **Step 1: Write failing dispatcher tests**

Add:

```python
async def test_dispatcher_skips_future_send_after(ops_db):
    _seed(ops_db)
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_notification_outbox SET send_after=?",
        ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),),
    )
    conn.commit()
    conn.close()
    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    assert await disp.drain_once() == 0
    assert sender.sent == []


async def test_dispatcher_ignores_suppressed_rows(ops_db):
    _seed(ops_db)
    conn = sqlite3.connect(ops_db)
    conn.execute("UPDATE ops_notification_outbox SET status='SUPPRESSED'")
    conn.commit()
    conn.close()
    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    assert await disp.drain_once() == 0
```

- [ ] **Step 2: Update `_claim_pending()` SQL**

Change:

```sql
WHERE status='PENDING'
```

to:

```sql
WHERE status='PENDING'
  AND (send_after IS NULL OR send_after <= ?)
```

Pass `_now()` as parameter before `LIMIT`.

- [ ] **Step 3: Run dispatcher tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_dispatcher.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/notification_dispatcher.py tests/runtime_v2/control_plane/test_dispatcher.py
git commit -m "feat(clean-log): delay and suppress outbox dispatch"
```

### Task 13: Implement AggregationWorker

**Files:**
- Create: `src/runtime_v2/control_plane/aggregation_worker.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_aggregation_worker.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`

- [ ] **Step 1: Write failing aggregation tests**

Create `tests/runtime_v2/control_plane/test_aggregation_worker.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.runtime_v2.control_plane.aggregation_worker import AggregationWorker


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _mature() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def _seed_outbox(conn, *, chain_id: int, notification_type: str, payload: dict, key: str, group: str, source_message_id: str | None = None):
    conn.execute(
        """
        INSERT INTO ops_notification_outbox
            (notification_type, destination, payload_json, priority, status, dedupe_key,
             attempts, created_at, send_after, aggregation_group, source_message_id)
        VALUES (?, 'CLEAN_LOG', ?, 'MEDIUM', 'PENDING', ?, 0, ?, ?, ?, ?)
        """,
        (notification_type, json.dumps({"chain_id": chain_id, **payload}), key,
         _mature(), _mature(), group, source_message_id),
    )


def test_tp_batch_aggregation_suppresses_originals_and_creates_batch(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)
    with conn:
        _seed_outbox(conn, chain_id=145, notification_type="TP_FILLED",
                     payload={"tp_level": 1, "pnl": 70.2, "fee": 1.1, "closed_pct": 30.0},
                     key="tp1", group="145:tp_batch")
        _seed_outbox(conn, chain_id=145, notification_type="TP_FILLED",
                     payload={"tp_level": 2, "pnl": 165.4, "fee": 1.65, "closed_pct": 40.0},
                     key="tp2", group="145:tp_batch")
    conn.close()

    assert AggregationWorker(db_path).run_once() == 1
    conn = sqlite3.connect(db_path)
    statuses = conn.execute(
        "SELECT notification_type, status FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='TP_BATCH_FILLED'"
    ).fetchone()[0])
    conn.close()
    assert statuses[:2] == [("TP_FILLED", "SUPPRESSED"), ("TP_FILLED", "SUPPRESSED")]
    assert statuses[2] == ("TP_BATCH_FILLED", "PENDING")
    assert payload["total_pnl"] == 235.6
    assert payload["total_fees"] == 2.75
```

- [ ] **Step 2: Create `aggregation_worker.py`**

Create with this public contract:

```python
class AggregationWorker:
    def __init__(self, ops_db_path: str, *, now_fn: Callable[[], datetime] | None = None) -> None:
        self._ops_db = ops_db_path
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> int:
        with sqlite3.connect(self._ops_db) as conn:
            created = 0
            created += self._aggregate_tp_batches(conn)
            created += self._aggregate_update_batches(conn)
            created += self._aggregate_multi_chain_updates(conn)
            return created
```

Implement:
- `_aggregate_tp_batches()`: group mature `TP_FILLED`/`TP_FILLED_FINAL` by `aggregation_group`; if group size > 1, insert `TP_BATCH_FILLED`, mark originals `SUPPRESSED`.
- `_aggregate_update_batches()`: group mature `UPDATE_*` same chain/source; if group size > 1, insert merged `UPDATE_DONE` or `UPDATE_PARTIAL` depending rejected content; suppress originals.
- `_aggregate_multi_chain_updates()`: group by `source_message_id`; if distinct chains > 3, insert `MULTI_CHAIN_UPDATE`, suppress DONE rows only.

Use dedupe keys:

```python
f"clean:aggregate:tp_batch:{aggregation_group}"
f"clean:aggregate:update_batch:{aggregation_group}"
f"clean:aggregate:multi_chain_update:{source_message_id}"
```

- [ ] **Step 3: Add formatter tests and handlers**

Add tests for:

```python
def test_tp_batch_filled_formatter():
    text = format_clean_log("TP_BATCH_FILLED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "targets": [
            {"tp_level": 1, "tp_price": 68000, "closed_pct": 30, "pnl": 70.2, "fee": 1.1},
            {"tp_level": 2, "tp_price": 71000, "closed_pct": 40, "pnl": 165.4, "fee": 1.65},
        ],
        "total_closed_pct": 70,
        "total_pnl": 235.6,
        "total_fees": 2.75,
        "remaining_pct": 30,
        "sl_current": 65020,
        "be_protection_status": "PROTECTED",
        "source": "exchange",
    })
    assert "TP1 + TP2 FILLED" in text
    assert "Filled targets:" in text
    assert "Total:" in text


def test_multi_chain_update_formatter():
    text = format_clean_log("MULTI_CHAIN_UPDATE", {
        "operations": ["Move SL to BE"],
        "chains": [
            {"chain_id": 160, "symbol": "BTC/USDT", "side": "SHORT", "status": "DONE"},
            {"chain_id": 161, "symbol": "ETH/USDT", "side": "SHORT", "status": "DONE"},
            {"chain_id": 163, "symbol": "XRP/USDT", "side": "SHORT", "status": "REJECTED"},
        ],
        "summary": {"done": 2, "rejected": 1},
        "source": "trader_update",
    })
    assert "UPDATE APPLIED - MULTI CHAIN" in text
    assert "#160 BTC/USDT SHORT - DONE" in text
```

Add formatter branches for `TP_BATCH_FILLED`, `MULTI_CHAIN_UPDATE`, `MULTI_CHAIN_CLOSED`.

- [ ] **Step 4: Run aggregation and formatter tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_aggregation_worker.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: PASS.

- [ ] **Step 5: Wire worker in bootstrap**

In `src/runtime_v2/control_plane/bootstrap.py`, instantiate `AggregationWorker(ops_db_path)` and include it in the `ControlPlane` dataclass. If bootstrap already owns async tasks, start a loop that sleeps `config.topics.clean_log.debounce_check_interval_seconds` and calls `run_once()`. If bootstrap currently returns passive objects only, expose `aggregation_worker` and leave task startup next to dispatcher startup in `main.py`.

- [ ] **Step 6: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/aggregation_worker.py src/runtime_v2/control_plane/bootstrap.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_aggregation_worker.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
git commit -m "feat(clean-log): aggregate batched notifications"
```

---

## Part 6: CANCEL_FAILED and Pause/Resume format

### Task 14: Emit and format CANCEL_FAILED

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/execution_gateway/test_event_sync.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`

- [ ] **Step 1: Write failing tests**

Add formatter test:

```python
def test_cancel_failed_formatter():
    text = format_clean_log("CANCEL_FAILED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_ref": "Entry_2",
        "entry_price": 64000.0,
        "attempts": 3,
        "source": "timeout_worker",
    })
    assert "CANCEL FAILED" in text
    assert "Cancellation of Entry_2 failed after 3 attempts." in text
    assert "manual review required" in text
```

Add projection test:

```python
def test_entry_cancel_failed_projects_cancel_failed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 950)
        _seed_event(conn, 950, "ENTRY_CANCEL_FAILED", "entry_cancel_failed:950:1", {
            "entry_ref": "Entry_2",
            "entry_price": 64000.0,
            "attempts": 3,
            "source": "timeout_worker",
        })
        project_clean_log_for_chain(conn, 950)
    row = conn.execute("SELECT notification_type FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row == ("CANCEL_FAILED",)
```

- [ ] **Step 2: Map event and build payload**

In `_CLEAN_LOG_EVENT_MAP`:

```python
"ENTRY_CANCEL_FAILED": "CANCEL_FAILED",
```

In `_build_payload()`:

```python
if notification_type == "CANCEL_FAILED":
    return {
        **base,
        "entry_ref": ev.get("entry_ref"),
        "entry_price": ev.get("entry_price"),
        "attempts": ev.get("attempts", 3),
        "source": ev.get("source", "timeout_worker"),
    }
```

- [ ] **Step 3: Emit `ENTRY_CANCEL_FAILED` from timeout/reconciliation failure owner**

Find the retry exhaustion owner for `CANCEL_PENDING_ENTRY` commands. If the existing owner is `ExchangeEventSyncWorker._save_cancelled_event()` or command retry logic, emit:

```python
self._repo.insert_exchange_event(
    coid.trade_chain_id,
    "ENTRY_CANCEL_FAILED",
    json.dumps({
        "entry_ref": f"Entry_{coid.sequence}",
        "entry_price": raw.price if hasattr(raw, "price") else None,
        "attempts": _MAX_ATTEMPTS,
        "source": "timeout_worker",
    }),
    f"ENTRY_CANCEL_FAILED:{coid.trade_chain_id}:{client_order_id}",
)
```

If the actual retry owner is not in `event_sync.py`, inspect `src/runtime_v2/execution_gateway/command_worker.py` and place the emission where a command transitions from retryable failure to terminal failure. The emitted lifecycle event type and payload must match the projection test above.

- [ ] **Step 4: Add `_cancel_failed()` formatter branch**

Add:

```python
if notification_type == "CANCEL_FAILED":
    return _cancel_failed(payload)
```

- [ ] **Step 5: Run tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_event_sync.py tests\runtime_v2\control_plane\test_outbox_writer.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit if commits are allowed**

```bash
git add src/runtime_v2/execution_gateway/event_sync.py src/runtime_v2/execution_gateway/command_worker.py src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/execution_gateway/test_event_sync.py tests/runtime_v2/control_plane/test_outbox_writer.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
git commit -m "feat(clean-log): notify cancel failures"
```

### Task 15: Update pause/resume formatter text

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/pause.py`
- Test: `tests/runtime_v2/control_plane/test_control_formatters.py`

- [ ] **Step 1: Write failing pause/resume format tests**

Add:

```python
def test_format_pause_spec_english():
    text = format_pause(scope="GLOBAL", mode="BLOCK_NEW_ENTRIES", source="operator", command="/pause")
    assert "EXECUTION PAUSED" in text
    assert "Scope: GLOBAL" in text
    assert "Mode: BLOCK_NEW_ENTRIES" in text
    assert "Effect:" in text
    assert "Source: operator" in text
    assert "Command: /pause" in text


def test_format_resume_spec_english():
    text = format_resume(scope="GLOBAL", mode="LIVE", source="operator", command="/resume")
    assert "EXECUTION RESUMED" in text
    assert "Scope: GLOBAL" in text
    assert "Mode: LIVE" in text
    assert "Effect:" in text
    assert "Source: operator" in text
    assert "Command: /resume" in text
```

- [ ] **Step 2: Update `pause.py` only for pause/resume**

Keep `format_start()` unchanged. Implement:

```python
def format_pause(*, scope: str, mode: str, source: str, command: str) -> str:
    return "\n".join([
        "⏸️ EXECUTION PAUSED",
        _SEP,
        f"Scope: {scope}",
        f"Mode: {mode}",
        "Effect: new entries are blocked while existing positions remain managed",
        f"Source: {source}",
        f"Command: {command}",
    ])


def format_resume(*, scope: str, mode: str, source: str, command: str) -> str:
    return "\n".join([
        "▶️ EXECUTION RESUMED",
        _SEP,
        f"Scope: {scope}",
        f"Mode: {mode}",
        "Effect: new entries may be accepted again according to rules",
        f"Source: {source}",
        f"Command: {command}",
    ])
```

If existing callers use positional or differently named args, add backwards-compatible optional parameters and adapt tests to current call sites.

- [ ] **Step 3: Run formatter tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_control_formatters.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit if commits are allowed**

```bash
git add src/runtime_v2/control_plane/formatters/pause.py tests/runtime_v2/control_plane/test_control_formatters.py
git commit -m "feat(control-plane): align pause resume messages"
```

---

## End-of-plan verification

- [ ] Run control-plane suite:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane -q
```

- [ ] Run lifecycle suite:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle -q
```

- [ ] Run execution gateway suite:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway -q
```

- [ ] Run import smoke:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -c "import main; print('import ok')"
```

- [ ] Manual DB smoke:
  - apply `main.py --migrate` on a scratch DB;
  - seed a chain with ENTRY, TP1, TP2, final close lifecycle rows;
  - call `project_clean_log_for_chain()`;
  - run `AggregationWorker.run_once()`;
  - run `TelegramNotificationDispatcher.drain_once()` with fake sender;
  - verify one root CLEAN_LOG message and replies with expected notification types.

---

## Documentation Update

After implementation, update `docs/AUDIT.md` or a control-plane status doc only if the behavior is actually shipped and tested. Record:

```text
CLEAN_LOG gap remediation:
- fill/fee payload enrichment: implemented and tested
- PnL/final result: implemented and tested; Bybit funding remains 0.0 until per-trade funding source exists
- formatter spec alignment: implemented and tested
- ENTRY_CANCELLED/BE_EXIT/CANCEL_FAILED: implemented and tested
- aggregation/debounce: implemented and tested
- remaining limitation: funding is displayed as +0.00 USDT by design
```

---

## Self-Review

**Spec coverage:** Sezione 1 is covered by Tasks 1-2. Sezione 2 is covered by Tasks 3-5. Sezione 3 is covered by Tasks 5-7. Sezione 4 is covered by Tasks 8-9. Sezione 5 is covered by Tasks 10-13. Sezione 6 is covered by Tasks 14-15.

**Placeholder scan:** No forbidden placeholder markers remain. The only conditional instruction is for locating the actual owner of retry exhaustion in Task 14; it names the candidate files and the exact emitted event contract.

**Type consistency:** Event names are consistent across mapping, formatter, tests and aggregation: `ENTRY_CANCELLED`, `BE_EXIT`, `TP_BATCH_FILLED`, `MULTI_CHAIN_UPDATE`, `MULTI_CHAIN_CLOSED`, `CANCEL_FAILED`. Outbox status includes `SUPPRESSED`; dispatcher only claims `PENDING` rows whose `send_after` is mature.

**Split decision:** The plan is intentionally split into six execution parts inside one file. Splitting into six separate plan files is not necessary unless execution will be delegated to separate branches/worktrees.
