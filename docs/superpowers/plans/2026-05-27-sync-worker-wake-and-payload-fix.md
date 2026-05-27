# Sync Worker Wake + Payload Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the ~10s lifecycle delay caused by REST-detected exchange events, and fix the WS payload key mismatch that causes fills to be processed with qty=0.

**Architecture:** Two independent surgical fixes. Fix 1: inject `wake_callback` into `ExchangeEventSyncWorker` and call it after every successful `insert_exchange_event` — same pattern already used by `BybitWsFillWatcher`. Fix 2: rename `exec_price`/`exec_qty` to `fill_price`/`filled_qty` in `insert_raw_and_classified`'s payload dict so the WS path writes keys that `event_processor` can read.

**Tech Stack:** Python 3.12, SQLite, `asyncio.Event`, existing `GatewayCommandRepository`, `ExchangeEventSyncWorker`.

---

## File map

| File | Change |
|---|---|
| `src/runtime_v2/execution_gateway/event_sync.py` | Add `wake_callback` param to `__init__`; call it after each successful insert in all 4 reconciliation methods |
| `src/runtime_v2/execution_gateway/repositories.py` | Rename `exec_price`→`fill_price`, `exec_qty`→`filled_qty` in `insert_raw_and_classified` payload |
| `main.py` | Pass `wake_callback` to `ExchangeEventSyncWorker(...)` |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | Add tests: wake_callback called on fill, not called when no new insert |
| `tests/runtime_v2/execution_gateway/test_repository_extensions.py` | Update payload key assertions from `exec_price`→`fill_price`, `exec_qty`→`filled_qty` |

---

## Task 1: wake_callback in ExchangeEventSyncWorker

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Test: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/execution_gateway/test_event_sync.py`:

```python
def test_run_reconciliation_calls_wake_callback_on_fill(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 9001, 42, "PLACE_ENTRY", "tsb:42:9001:entry:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:9001:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:9001:entry:1", price=100.0, qty=1.0)

    wake_calls = []
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
        wake_callback=lambda: wake_calls.append(1),
    )
    worker.run_reconciliation()

    assert len(wake_calls) == 1


def test_run_reconciliation_no_wake_callback_when_no_fill(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # No sent commands → nothing to reconcile
    adapter = FakeAdapter()
    wake_calls = []
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
        wake_callback=lambda: wake_calls.append(1),
    )
    worker.run_reconciliation()

    assert len(wake_calls) == 0


def test_run_reconciliation_no_wake_callback_on_duplicate(ops_db):
    """Second run_reconciliation for same fill must NOT call wake_callback again (idempotency)."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 9002, 42, "PLACE_ENTRY", "tsb:42:9002:entry:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:9002:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:9002:entry:1", price=100.0, qty=1.0)

    wake_calls = []
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
        wake_callback=lambda: wake_calls.append(1),
    )
    worker.run_reconciliation()  # first: inserts event → wake
    worker.run_reconciliation()  # second: cmd is DONE, nothing to poll

    assert len(wake_calls) == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_run_reconciliation_calls_wake_callback_on_fill tests/runtime_v2/execution_gateway/test_event_sync.py::test_run_reconciliation_no_wake_callback_when_no_fill tests/runtime_v2/execution_gateway/test_event_sync.py::test_run_reconciliation_no_wake_callback_on_duplicate -v
```

Expected: FAIL — `ExchangeEventSyncWorker.__init__() got an unexpected keyword argument 'wake_callback'`

- [ ] **Step 3: Implement wake_callback in ExchangeEventSyncWorker**

Replace the entire `ExchangeEventSyncWorker.__init__` and add the callback calls. The wake should fire immediately after each successful `insert_exchange_event` (inserted=True), not at the end of the batch:

```python
class ExchangeEventSyncWorker:
    def __init__(
        self,
        ops_db_path: str,
        adapter: ExecutionAdapter,
        repo: GatewayCommandRepository,
        execution_account_id: str,
        wake_callback=None,
    ) -> None:
        self._ops_db = ops_db_path
        self._adapter = adapter
        self._repo = repo
        self._execution_account_id = execution_account_id
        self._wake_callback = wake_callback

    def _wake(self) -> None:
        if self._wake_callback is not None:
            self._wake_callback()
```

In `run_reconciliation`, add `self._wake()` after each successful `mark_done`:

```python
    def run_reconciliation(self) -> int:
        active = self._repo.get_sent_or_ack()
        processed = 0

        for cmd, client_order_id in active:
            if not client_order_id:
                continue
            try:
                raw = self._adapter.get_order_status(
                    client_order_id=client_order_id,
                    execution_account_id=self._execution_account_id,
                )
                if raw and raw.is_filled:
                    if self._save_fill_event(client_order_id, raw):
                        self._repo.mark_done(cmd.command_id)
                        self._wake()
                        processed += 1
                elif raw and raw.status == "CANCELLED":
                    if self._save_cancelled_event(client_order_id, raw):
                        self._repo.mark_done(cmd.command_id)
                        self._wake()
                        processed += 1
            except Exception:
                logger.exception("reconciliation error for %s", client_order_id)

        return processed
```

In `run_position_reconciliation`, add `self._wake()` after successful insert:

```python
                    inserted = self._repo.insert_exchange_event(
                        chain_id, "CLOSE_FULL_FILLED", payload, idem_key
                    )
                    if inserted:
                        logger.info(
                            "externally closed position detected: chain=%s %s %s qty=%s",
                            chain_id, symbol, side, open_qty,
                        )
                        self._wake()
                        processed += 1
```

In `run_trade_based_reconciliation`, add `self._wake()` after successful insert:

```python
            inserted = self._repo.insert_exchange_event(chain_id, "TP_FILLED", payload, idem_key)
            if inserted:
                logger.info(
                    "TP_FILLED from trade-based reconciliation: chain=%s %s %s",
                    chain_id, symbol, side,
                )
                self._wake()
                processed += 1
```

In `run_protective_orders_reconciliation`, add `self._wake()` after successful insert:

```python
            inserted = self._repo.insert_exchange_event(
                chain_id, "PROTECTIVE_ORDER_CANCELLED", payload, idem_key
            )
            if inserted:
                logger.warning(
                    "PROTECTIVE_ORDER_CANCELLED detected: chain=%s %s %s",
                    chain_id, symbol, side,
                )
                self._wake()
                processed += 1
```

- [ ] **Step 4: Run the new tests — verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_run_reconciliation_calls_wake_callback_on_fill tests/runtime_v2/execution_gateway/test_event_sync.py::test_run_reconciliation_no_wake_callback_when_no_fill tests/runtime_v2/execution_gateway/test_event_sync.py::test_run_reconciliation_no_wake_callback_on_duplicate -v
```

Expected: 3 PASS

- [ ] **Step 5: Run full event_sync test suite**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```

Expected: all PASS (no regressions)

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/event_sync.py tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(event_sync): add wake_callback to ExchangeEventSyncWorker

All 4 reconciliation methods now call wake_callback immediately after a
successful insert_exchange_event. This eliminates the up-to-10s lifecycle
delay caused by REST-detected fills (entry, TP, position close, protective
cancel) that previously had no way to notify the lifecycle worker.

Same pattern as BybitWsFillWatcher._process_batch.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Wire wake_callback in main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Pass wake_callback to ExchangeEventSyncWorker**

In `main.py`, the `sync_worker` is built before `ws_watcher`. The `wake_callback` parameter arrives in `_build_execution_runtime`. Pass it straight through:

```python
    sync_worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db_path,
        adapter=adapter,
        repo=gateway_repo,
        execution_account_id=routing.execution_account_id,
        wake_callback=wake_callback,
    )
```

- [ ] **Step 2: Verify main.py still parses and imports correctly**

```
python -c "import ast; ast.parse(open('main.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run the gateway and event_sync tests to ensure no regressions**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py tests/runtime_v2/execution_gateway/test_gateway.py tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -v --tb=short
```

Expected: all PASS

- [ ] **Step 4: Commit**

```
git add main.py
git commit -m "fix(main): pass wake_callback to ExchangeEventSyncWorker

REST-detected fills now immediately wake the lifecycle worker via the
same _fill_wake_callback already wired into BybitWsFillWatcher.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Fix payload key mismatch in insert_raw_and_classified

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`
- Modify: `tests/runtime_v2/execution_gateway/test_repository_extensions.py`
- Modify: `tests/runtime_v2/execution_gateway/test_event_ingest_integration.py`

**Context:** `insert_raw_and_classified` builds a payload dict with keys `exec_price`/`exec_qty` (normalizer terminology). But `event_processor._process_entry_filled` and `_process_tp_filled` read `fill_price`/`filled_qty`. Every WS-path event was processed with fill=0. Fix: rename the two keys in the payload dict only — no schema change, no column rename.

- [ ] **Step 1: Write a failing test that asserts fill_price/filled_qty keys**

Add to `tests/runtime_v2/execution_gateway/test_repository_extensions.py`:

```python
def test_insert_raw_and_classified_payload_uses_fill_price_filled_qty(tmp_path):
    """Payload written to ops_exchange_events must use fill_price/filled_qty keys
    so event_processor._process_entry_filled / _process_tp_filled can read them."""
    import json as _json
    db_path = make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    raw = _make_raw_event()  # exec_price=50000.0, exec_qty=0.01
    classified = _make_classified(raw=raw, event_type="ENTRY_FILLED")

    repo.insert_raw_and_classified(classified)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT payload_json FROM ops_exchange_events").fetchone()
    conn.close()

    payload = _json.loads(row[0])
    assert "fill_price" in payload, f"expected fill_price, got keys: {list(payload)}"
    assert "filled_qty" in payload, f"expected filled_qty, got keys: {list(payload)}"
    assert "exec_price" not in payload, "exec_price should not be in payload"
    assert "exec_qty" not in payload, "exec_qty should not be in payload"
    assert payload["fill_price"] == 50000.0
    assert payload["filled_qty"] == 0.01
```

- [ ] **Step 2: Run test to confirm it fails**

```
pytest tests/runtime_v2/execution_gateway/test_repository_extensions.py::test_insert_raw_and_classified_payload_uses_fill_price_filled_qty -v
```

Expected: FAIL — `AssertionError: expected fill_price, got keys: ['exec_price', ...]`

- [ ] **Step 3: Fix the payload dict in repositories.py**

In `src/runtime_v2/execution_gateway/repositories.py`, find the `payload` dict inside `insert_raw_and_classified` (around line 490) and rename two keys:

```python
        payload = {
            "fill_price": raw.exec_price,      # was "exec_price"
            "filled_qty": raw.exec_qty,         # was "exec_qty"
            "closed_size": raw.closed_size,
            "pos_qty": raw.pos_qty,
            "symbol": raw.symbol,
            "side": raw.side,
            "source": classified.source,
            "tp_level": classified.tp_level,
            "exchange_event_id": raw.exchange_event_id,
        }
```

Only these two keys change. Everything else stays identical.

- [ ] **Step 4: Run the new test — verify it passes**

```
pytest tests/runtime_v2/execution_gateway/test_repository_extensions.py::test_insert_raw_and_classified_payload_uses_fill_price_filled_qty -v
```

Expected: PASS

- [ ] **Step 5: Run full repository_extensions + event_ingest_integration suites**

```
pytest tests/runtime_v2/execution_gateway/test_repository_extensions.py tests/runtime_v2/execution_gateway/test_event_ingest_integration.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/repositories.py tests/runtime_v2/execution_gateway/test_repository_extensions.py
git commit -m "fix(repositories): rename exec_price/exec_qty to fill_price/filled_qty in WS payload

insert_raw_and_classified was writing exec_price/exec_qty to ops_exchange_events
but event_processor._process_entry_filled and _process_tp_filled read
fill_price/filled_qty. Every WS-classified fill was processed with fill=0,
causing spurious REBUILD_PARTIAL_TPS (qty=0, SUPERSEDED) and wrong
open_position_qty on TP fills.

Only the two payload dict keys are renamed — no schema changes.
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Full regression check

**Files:** none modified — verification only

- [ ] **Step 1: Run the complete runtime_v2 suite (excluding pre-existing ccxt failures)**

```
pytest tests/runtime_v2/ -q --ignore=tests/runtime_v2/test_main_runtime_bootstrap.py --ignore=tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py --ignore=tests/runtime_v2/execution_gateway/test_adapter_factory.py
```

Expected: all PASS (610+ tests), 0 new failures

- [ ] **Step 2: Verify production DB schema unchanged**

```
python -c "
import sqlite3
conn = sqlite3.connect('db/ops.sqlite3')
cols = [r[1] for r in conn.execute('PRAGMA table_info(exchange_raw_events)').fetchall()]
assert 'exec_price' in cols, 'exec_price column must remain in exchange_raw_events (raw data, unchanged)'
assert 'exec_qty' in cols, 'exec_qty column must remain in exchange_raw_events'
print('OK: raw columns untouched')
conn.close()
"
```

Expected: `OK: raw columns untouched`

Note: `exec_price`/`exec_qty` remain as column names in `exchange_raw_events` (they store the raw normalizer data). Only the *payload dict key names* written to `ops_exchange_events` were renamed. These are independent.

- [ ] **Step 3: Smoke-test the full wiring in one shot**

```
python -c "
from collections.abc import Callable
from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
import inspect

sig = inspect.signature(ExchangeEventSyncWorker.__init__)
params = list(sig.parameters.keys())
assert 'wake_callback' in params, 'wake_callback missing from ExchangeEventSyncWorker'
print('OK: ExchangeEventSyncWorker.wake_callback present')

# Verify main.py passes it
import ast
src = open('main.py').read()
assert 'wake_callback=wake_callback' in src.split('ExchangeEventSyncWorker')[1][:300], \
    'wake_callback not passed to ExchangeEventSyncWorker in main.py'
print('OK: main.py wires wake_callback to sync_worker')
"
```

Expected:
```
OK: ExchangeEventSyncWorker.wake_callback present
OK: main.py wires wake_callback to sync_worker
```

- [ ] **Step 4: Commit final verification note**

No code changes in this task — no commit needed.
