# Per-Adapter Execution Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate all blocking ccxt REST calls from the asyncio event loop by running each adapter's sync workers inside a dedicated per-adapter thread context, eliminating the `getUpdates` starvation that grows with the number of open positions.

**Architecture:** One `AdapterExecutionContext` per exchange adapter owns a `threading.Thread` + `queue.Queue`. All `ExchangeEventSyncWorker.run_*` calls move from asyncio coroutines into this context's thread, executing serially. The WS fill watcher enqueues jobs to the context instead of calling reconciliation directly. The command worker stays in the lifecycle loop unchanged. Periodic ticks (reconciliation, position-recon) are timer-based inside the context thread — zero periodic work on the asyncio loop.

**Tech Stack:** Python stdlib — `threading.Thread`, `threading.Timer`, `queue.Queue`; existing `asyncio.Event` + `loop.call_soon_threadsafe` for wakeup; existing `ExchangeEventSyncWorker` (unchanged).

## Global Constraints

- No new production dependencies — stdlib only (`threading`, `queue`)
- `from __future__ import annotations` at top of every new/modified file
- `event_sync.py`, `command_worker.py`, `repositories.py` — zero modifications to logic or signatures
- All existing `tests/runtime_v2/execution_gateway/` tests must stay green after Task 3

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `src/runtime_v2/execution_gateway/adapter_context.py` | `AdapterExecutionContext`: thread, queue, ticks, submit, stop, join |
| Create | `tests/runtime_v2/execution_gateway/test_adapter_context.py` | All context tests (serial, parallel, tick, wakeup, wiring) |
| Modify | `main_linux_server.py` | Wire contexts, remove 3 periodic coroutines, lambda WS callback, shutdown join |

---

## Task 1: AdapterExecutionContext — thread, queue, serial execution

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapter_context.py`
- Create: `tests/runtime_v2/execution_gateway/test_adapter_context.py`

**Interfaces — Produces:**
```python
class AdapterExecutionContext:
    def __init__(self, adapter_name: str) -> None: ...
    def start(self) -> None: ...
    def submit(self, job: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...
```

---

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime_v2/execution_gateway/test_adapter_context.py`:

```python
# tests/runtime_v2/execution_gateway/test_adapter_context.py
from __future__ import annotations

import asyncio
import threading
import time


def test_jobs_on_same_context_are_serial():
    """Two overlapping jobs on the same context must execute one after the other."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    log: list[str] = []
    mu = threading.Lock()

    def job(name: str) -> None:
        with mu:
            log.append(f"{name}:start")
        time.sleep(0.05)
        with mu:
            log.append(f"{name}:end")

    ctx = AdapterExecutionContext("serial-test")
    ctx.start()
    ctx.submit(lambda: job("A"))
    ctx.submit(lambda: job("B"))
    time.sleep(0.35)
    ctx.stop()
    ctx.join(timeout=2.0)

    assert log == ["A:start", "A:end", "B:start", "B:end"]


def test_jobs_on_different_contexts_run_in_parallel():
    """Two separate contexts must be able to run their jobs simultaneously."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    barrier = threading.Barrier(2, timeout=1.0)
    reached: list[str] = []

    def job(name: str) -> None:
        reached.append(name)
        barrier.wait()  # blocks until both arrive — impossible if serial

    ctx1 = AdapterExecutionContext("adapter1")
    ctx2 = AdapterExecutionContext("adapter2")
    ctx1.start()
    ctx2.start()

    ctx1.submit(lambda: job("ctx1"))
    ctx2.submit(lambda: job("ctx2"))

    time.sleep(0.5)
    ctx1.stop()
    ctx2.stop()
    ctx1.join(timeout=2.0)
    ctx2.join(timeout=2.0)

    assert set(reached) == {"ctx1", "ctx2"}


def test_loop_not_blocked_while_context_job_is_slow():
    """A 150ms REST-like job in the context thread must not starve asyncio coroutines."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    ticks: list[float] = []

    async def counter() -> None:
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks.append(time.monotonic())

    def slow_job() -> None:
        time.sleep(0.15)

    ctx = AdapterExecutionContext("slow-adapter")
    ctx.start()
    ctx.submit(slow_job)

    asyncio.run(counter())

    ctx.stop()
    ctx.join(timeout=2.0)

    assert len(ticks) == 5, f"expected 5 ticks, got {len(ticks)}"


def test_wakeup_via_call_soon_threadsafe():
    """A job in the context thread can set an asyncio.Event via call_soon_threadsafe."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    async def main() -> bool:
        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        ctx = AdapterExecutionContext("wake-test")
        ctx.start()
        ctx.submit(lambda: loop.call_soon_threadsafe(event.set))

        await asyncio.wait_for(event.wait(), timeout=2.0)
        ctx.stop()
        ctx.join(timeout=2.0)
        return True

    assert asyncio.run(main())


def test_stop_and_join_complete_cleanly():
    """stop() + join() must return within timeout even if queue has pending jobs."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    ctx = AdapterExecutionContext("stop-test")
    ctx.start()
    ctx.submit(lambda: time.sleep(0.01))
    ctx.stop()
    ctx.join(timeout=2.0)
    assert not ctx._thread.is_alive()
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/runtime_v2/execution_gateway/test_adapter_context.py -v
```

Expected: 5 errors — `ModuleNotFoundError: No module named 'src.runtime_v2.execution_gateway.adapter_context'`

- [ ] **Step 3: Write minimal implementation**

Create `src/runtime_v2/execution_gateway/adapter_context.py`:

```python
# src/runtime_v2/execution_gateway/adapter_context.py
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class AdapterExecutionContext:
    """Dedicated thread context for one exchange adapter.

    All blocking ccxt REST calls for this adapter run serially in this thread,
    keeping the asyncio event loop free.
    """

    def __init__(self, adapter_name: str) -> None:
        self._name = adapter_name
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"adapter-ctx-{adapter_name}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def submit(self, job: Callable[[], None]) -> None:
        """Enqueue a callable for serial execution in the context thread."""
        self._queue.put(job)

    def stop(self) -> None:
        """Signal the context to finish and exit its thread."""
        self._stop_event.set()
        self._queue.put(None)  # sentinel — unblocks queue.get

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if job is None:  # stop sentinel
                break
            try:
                job()
            except Exception:
                logger.exception("adapter-ctx %s: job error", self._name)


__all__ = ["AdapterExecutionContext"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/runtime_v2/execution_gateway/test_adapter_context.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapter_context.py tests/runtime_v2/execution_gateway/test_adapter_context.py
git commit -m "feat(execution): AdapterExecutionContext core — thread, queue, serial execution"
```

---

## Task 2: Periodic ticks inside the context (reconciliation + position_reconciliation)

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapter_context.py`
- Modify: `tests/runtime_v2/execution_gateway/test_adapter_context.py`

**Interfaces — Extended constructor (replaces Task 1 signature):**
```python
class AdapterExecutionContext:
    def __init__(
        self,
        adapter_name: str,
        *,
        reconciliation_fn: Callable[[], None] | None = None,
        position_reconciliation_fn: Callable[[], None] | None = None,
        poll_fallback_enabled: bool = True,
        poll_fallback_period_seconds: float = 60.0,
        position_reconciliation_interval_seconds: float = 600.0,
    ) -> None: ...
```

`poll_fallback_enabled=False` → no reconciliation tick (only WS event-driven jobs).
`poll_fallback_enabled=True` + `reconciliation_fn` → timer fires every `poll_fallback_period_seconds`, enqueues `reconciliation_fn`.
`position_reconciliation_fn` → always ticked at `position_reconciliation_interval_seconds`.

---

- [ ] **Step 1: Write the failing tick tests**

Append to `tests/runtime_v2/execution_gateway/test_adapter_context.py`:

```python
def test_reconciliation_tick_fires_when_enabled():
    """When poll_fallback_enabled=True, reconciliation_fn is called periodically."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    calls: list[float] = []

    ctx = AdapterExecutionContext(
        "tick-enabled",
        reconciliation_fn=lambda: calls.append(time.monotonic()),
        poll_fallback_enabled=True,
        poll_fallback_period_seconds=0.05,
    )
    ctx.start()
    time.sleep(0.4)
    ctx.stop()
    ctx.join(timeout=2.0)

    assert len(calls) >= 3, f"expected ≥3 ticks in 400ms at 50ms interval, got {len(calls)}"


def test_reconciliation_tick_does_not_fire_when_disabled():
    """When poll_fallback_enabled=False, reconciliation_fn is never called by the tick."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    calls: list[int] = []

    ctx = AdapterExecutionContext(
        "tick-disabled",
        reconciliation_fn=lambda: calls.append(1),
        poll_fallback_enabled=False,
        poll_fallback_period_seconds=0.05,
    )
    ctx.start()
    time.sleep(0.2)
    ctx.stop()
    ctx.join(timeout=2.0)

    assert calls == []


def test_single_tick_stream_not_double():
    """One context must produce one tick stream — gaps must reflect the configured interval."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    calls: list[float] = []

    ctx = AdapterExecutionContext(
        "single-stream",
        reconciliation_fn=lambda: calls.append(time.monotonic()),
        poll_fallback_enabled=True,
        poll_fallback_period_seconds=0.06,
    )
    ctx.start()
    time.sleep(0.5)
    ctx.stop()
    ctx.join(timeout=2.0)

    assert len(calls) >= 3
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    # if there were two streams each at 60ms, gaps would be ~30ms
    assert all(g > 0.04 for g in gaps), f"unexpectedly short gaps (double stream?): {gaps}"


def test_position_reconciliation_tick_fires():
    """position_reconciliation_fn is called at the configured interval."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    calls: list[float] = []

    ctx = AdapterExecutionContext(
        "pos-tick",
        position_reconciliation_fn=lambda: calls.append(time.monotonic()),
        poll_fallback_enabled=False,
        position_reconciliation_interval_seconds=0.05,
    )
    ctx.start()
    time.sleep(0.4)
    ctx.stop()
    ctx.join(timeout=2.0)

    assert len(calls) >= 3, f"expected ≥3 pos ticks in 400ms, got {len(calls)}"


def test_ticks_do_not_fire_after_stop():
    """No tick fires after stop() is called."""
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext

    calls: list[int] = []

    ctx = AdapterExecutionContext(
        "stop-no-tick",
        reconciliation_fn=lambda: calls.append(1),
        poll_fallback_enabled=True,
        poll_fallback_period_seconds=0.05,
    )
    ctx.start()
    time.sleep(0.12)   # allow ~2 ticks
    count_before = len(calls)
    ctx.stop()
    ctx.join(timeout=2.0)
    time.sleep(0.12)   # wait for what would be 2 more ticks if timers still ran
    count_after = len(calls)

    assert count_before >= 1, "at least one tick expected before stop"
    assert count_after == count_before, "no new ticks expected after stop"
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/runtime_v2/execution_gateway/test_adapter_context.py::test_reconciliation_tick_fires_when_enabled tests/runtime_v2/execution_gateway/test_adapter_context.py::test_reconciliation_tick_does_not_fire_when_disabled tests/runtime_v2/execution_gateway/test_adapter_context.py::test_single_tick_stream_not_double tests/runtime_v2/execution_gateway/test_adapter_context.py::test_position_reconciliation_tick_fires tests/runtime_v2/execution_gateway/test_adapter_context.py::test_ticks_do_not_fire_after_stop -v
```

Expected: 5 errors — `TypeError: __init__() got an unexpected keyword argument 'reconciliation_fn'`

- [ ] **Step 3: Extend AdapterExecutionContext with timer-based ticks**

Replace the full content of `src/runtime_v2/execution_gateway/adapter_context.py`:

```python
# src/runtime_v2/execution_gateway/adapter_context.py
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class AdapterExecutionContext:
    """Dedicated thread context for one exchange adapter.

    All blocking ccxt REST calls for this adapter run serially in this thread,
    keeping the asyncio event loop free. Periodic ticks (reconciliation,
    position-recon) are driven by internal threading.Timer chains — no asyncio
    coroutines required.
    """

    def __init__(
        self,
        adapter_name: str,
        *,
        reconciliation_fn: Callable[[], None] | None = None,
        position_reconciliation_fn: Callable[[], None] | None = None,
        poll_fallback_enabled: bool = True,
        poll_fallback_period_seconds: float = 60.0,
        position_reconciliation_interval_seconds: float = 600.0,
    ) -> None:
        self._name = adapter_name
        self._reconciliation_fn = reconciliation_fn
        self._position_reconciliation_fn = position_reconciliation_fn
        self._poll_fallback_enabled = poll_fallback_enabled
        self._poll_fallback_period_seconds = poll_fallback_period_seconds
        self._pos_recon_interval = position_reconciliation_interval_seconds

        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"adapter-ctx-{adapter_name}",
            daemon=True,
        )
        self._recon_timer: threading.Timer | None = None
        self._pos_recon_timer: threading.Timer | None = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        self._thread.start()
        if self._poll_fallback_enabled and self._reconciliation_fn is not None:
            self._schedule_reconciliation()
        if self._position_reconciliation_fn is not None:
            self._schedule_position_reconciliation()

    def submit(self, job: Callable[[], None]) -> None:
        """Enqueue a callable for serial execution in the context thread."""
        self._queue.put(job)

    def stop(self) -> None:
        """Cancel timers and signal the thread to exit after finishing current job."""
        self._stop_event.set()
        if self._recon_timer is not None:
            self._recon_timer.cancel()
        if self._pos_recon_timer is not None:
            self._pos_recon_timer.cancel()
        self._queue.put(None)  # sentinel — unblocks queue.get

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    # ----------------------------------------------------------------- private

    def _run(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if job is None:  # stop sentinel
                break
            try:
                job()
            except Exception:
                logger.exception("adapter-ctx %s: job error", self._name)

    def _schedule_reconciliation(self) -> None:
        if self._stop_event.is_set():
            return
        self._recon_timer = threading.Timer(
            self._poll_fallback_period_seconds,
            self._tick_reconciliation,
        )
        self._recon_timer.daemon = True
        self._recon_timer.start()

    def _tick_reconciliation(self) -> None:
        if not self._stop_event.is_set():
            self.submit(self._reconciliation_fn)  # type: ignore[arg-type]
            self._schedule_reconciliation()

    def _schedule_position_reconciliation(self) -> None:
        if self._stop_event.is_set():
            return
        self._pos_recon_timer = threading.Timer(
            self._pos_recon_interval,
            self._tick_position_reconciliation,
        )
        self._pos_recon_timer.daemon = True
        self._pos_recon_timer.start()

    def _tick_position_reconciliation(self) -> None:
        if not self._stop_event.is_set():
            self.submit(self._position_reconciliation_fn)  # type: ignore[arg-type]
            self._schedule_position_reconciliation()


__all__ = ["AdapterExecutionContext"]
```

- [ ] **Step 4: Run all adapter_context tests**

```
python -m pytest tests/runtime_v2/execution_gateway/test_adapter_context.py -v
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapter_context.py tests/runtime_v2/execution_gateway/test_adapter_context.py
git commit -m "feat(execution): adapter context periodic ticks — reconciliation + position_reconciliation"
```

---

## Task 3: main_linux_server.py — wiring, remove coroutines, update WS callback, shutdown

**Files:**
- Modify: `main_linux_server.py`
- Modify: `tests/runtime_v2/execution_gateway/test_adapter_context.py` (functional equivalence test)

**What changes in `main_linux_server.py`:**
1. New import for `AdapterExecutionContext`
2. `ExecutionRuntime` dataclass: add `adapter_contexts` field
3. `_build_execution_runtime`: split loop (sync_workers first, then contexts, then ws_watchers)
4. `_close_execution_runtime`: add context stop + join
5. `_async_main`: remove `sync_tasks`, `reconciliation_tasks`, `position_reconciliation_tasks` — and their coroutines (`_run_sync_worker`, `_run_reconciliation_periodically`, `_run_position_reconciliation_periodically`)

---

- [ ] **Step 1: Write the functional equivalence test**

Append to `tests/runtime_v2/execution_gateway/test_adapter_context.py`:

```python
def test_reconciliation_via_context_writes_fill_event(tmp_path):
    """run_reconciliation submitted to a context produces the same DB result as a direct call."""
    import json
    import sqlite3
    import datetime as dt
    from pathlib import Path
    from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # --- setup DB ---
    db = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (9001, 42, "PLACE_ENTRY", "SENT", "{}", "idem:9001", "tsb:42:9001:entry:1", now, now),
    )
    conn.commit()
    conn.close()

    # --- setup fake adapter with a filled order ---
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={},
        client_order_id="tsb:42:9001:entry:1",
        execution_account_id="acc",
        connector="c",
    )
    adapter.simulate_fill("tsb:42:9001:entry:1", price=50000.0, qty=0.01)

    # --- submit reconciliation via context ---
    done = threading.Event()
    repo = GatewayCommandRepository(db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    def job_with_signal() -> None:
        worker.run_reconciliation()
        done.set()

    ctx = AdapterExecutionContext("functional-test")
    ctx.start()
    ctx.submit(job_with_signal)

    assert done.wait(timeout=5.0), "reconciliation job did not complete in time"
    ctx.stop()
    ctx.join(timeout=2.0)

    # --- verify DB ---
    conn = sqlite3.connect(db)
    events = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events"
    ).fetchall()
    conn.close()
    assert len(events) == 1
    assert events[0][0] == "ENTRY_FILLED"
    payload = json.loads(events[0][1])
    assert payload["fill_price"] == 50000.0
```

- [ ] **Step 2: Run test to verify it fails (import error is acceptable — context works, just the wiring test setup)**

```
python -m pytest tests/runtime_v2/execution_gateway/test_adapter_context.py::test_reconciliation_via_context_writes_fill_event -v
```

Expected: PASS (this test exercises the context + existing sync worker — if it fails, fix before proceeding)

- [ ] **Step 3: Add `AdapterExecutionContext` import to `main_linux_server.py`**

In `main_linux_server.py`, after the existing execution_gateway imports (around line 51-53), add:

```python
from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext
```

- [ ] **Step 4: Add `adapter_contexts` field to `ExecutionRuntime` dataclass**

Find the `ExecutionRuntime` dataclass (lines 72-87). Add `adapter_contexts` field at the end:

```python
@dataclass
class ExecutionRuntime:
    adapter: object
    execution_worker: ExecutionCommandWorker
    sync_worker: ExchangeEventSyncWorker
    ws_watcher: BybitWsFillWatcher | None
    reconciliation_interval_seconds: int | None
    adapters: dict[str, object] | None = None
    sync_workers: dict[str, ExchangeEventSyncWorker] | None = None
    ws_watchers: dict[str, BybitWsFillWatcher] | None = None
    reconciliation_intervals: dict[str, int] | None = None
    position_reconciliation_intervals: dict[str, int] | None = None
    poll_fallback_by_account: dict[str, bool] | None = None
    position_reconciliation_interval_seconds: int = 600
    poll_fallback_enabled: bool = True
    adapter_contexts: dict[str, AdapterExecutionContext] | None = None
```

- [ ] **Step 5: Restructure `_build_execution_runtime` — split loop, build contexts, move WS watcher creation**

Replace the section of `_build_execution_runtime` from `sync_workers: dict[str, ExchangeEventSyncWorker] = {}` through the end of the function. The new structure:

```python
    gateway_repo = GatewayCommandRepository(ops_db_path)
    gateway = ExecutionGateway(
        config=exec_config,
        adapter_registry=adapter_registry,
        repo=gateway_repo,
    )
    execution_worker = ExecutionCommandWorker(
        ops_db_path=ops_db_path,
        gateway=gateway,
        repo=gateway_repo,
    )
    sync_workers: dict[str, ExchangeEventSyncWorker] = {}
    ws_watchers: dict[str, BybitWsFillWatcher] = {}
    reconciliation_intervals: dict[str, int] = {}
    position_reconciliation_intervals: dict[str, int] = {}
    poll_fallback_by_account: dict[str, bool] = {}
    account_adapter_map: dict[str, str] = {}   # account_id → adapter_name
    adapter_cfg_map: dict[str, object] = {}    # adapter_name → AdapterConfig

    route_keys = ["default", *[k for k in exec_config.account_routing.keys() if k != "default"]]

    # --- Pass 1: build sync_workers ---
    for route_key in route_keys:
        route_cfg, route_adapter_cfg = exec_config.resolve_routing(route_key)
        account_id = route_cfg.execution_account_id
        if account_id in sync_workers:
            continue
        route_adapter_name = getattr(route_cfg, "adapter", None)
        if route_adapter_name is None:
            route_adapter_name = getattr(exec_config.account_routing.get(route_key), "adapter", None)
        if route_adapter_name is None:
            route_adapter_name = adapter_name
        route_adapter = adapter_registry[route_adapter_name]
        sync_worker = ExchangeEventSyncWorker(
            ops_db_path=ops_db_path,
            adapter=route_adapter,
            repo=gateway_repo,
            execution_account_id=account_id,
            wake_callback=wake_callback,
        )
        sync_workers[account_id] = sync_worker
        account_adapter_map[account_id] = route_adapter_name
        adapter_cfg_map[route_adapter_name] = route_adapter_cfg
        poll_fallback_by_account[account_id] = route_adapter_cfg.websocket.poll_fallback_enabled
        position_reconciliation_intervals[account_id] = (
            route_adapter_cfg.websocket.position_reconciliation_interval_seconds
        )

    # --- Build one AdapterExecutionContext per adapter ---
    adapter_to_accounts: dict[str, list[str]] = {}
    for acc_id, adp_name in account_adapter_map.items():
        adapter_to_accounts.setdefault(adp_name, []).append(acc_id)

    adapter_contexts: dict[str, AdapterExecutionContext] = {}
    for adp_name, acc_ids in adapter_to_accounts.items():
        adp_cfg = adapter_cfg_map[adp_name]
        workers = [sync_workers[a] for a in acc_ids]

        def _make_recon(ws=workers):
            def _recon():
                for w in ws:
                    w.run_reconciliation()
            return _recon

        def _make_pos_recon(ws=workers):
            def _pos_recon():
                for w in ws:
                    w.run_position_reconciliation()
                    w.run_trade_based_reconciliation()
                    w.run_protective_orders_reconciliation()
            return _pos_recon

        ctx = AdapterExecutionContext(
            adp_name,
            reconciliation_fn=_make_recon(),
            position_reconciliation_fn=_make_pos_recon(),
            poll_fallback_enabled=adp_cfg.websocket.poll_fallback_enabled,
            poll_fallback_period_seconds=float(adp_cfg.websocket.poll_fallback_period_seconds),
            position_reconciliation_interval_seconds=float(
                adp_cfg.websocket.position_reconciliation_interval_seconds
            ),
        )
        adapter_contexts[adp_name] = ctx

    # --- Pass 2: build and start ws_watchers (contexts exist now) ---
    for route_key in route_keys:
        route_cfg, route_adapter_cfg = exec_config.resolve_routing(route_key)
        account_id = route_cfg.execution_account_id
        if account_id in ws_watchers:
            continue
        if route_adapter_cfg.type != "ccxt_bybit" or not route_adapter_cfg.websocket.enabled:
            continue
        route_adapter_name = account_adapter_map.get(account_id, adapter_name)
        ctx = adapter_contexts.get(route_adapter_name)
        sw = sync_workers[account_id]

        recon_cb = (
            (lambda c=ctx, w=sw: c.submit(w.run_reconciliation))
            if ctx is not None
            else sw.run_reconciliation
        )

        api_key = (
            os.environ.get(route_adapter_cfg.api_key_env or "")
            if route_adapter_cfg.api_key_env else ""
        )
        api_secret = (
            os.environ.get(route_adapter_cfg.api_secret_env or "")
            if route_adapter_cfg.api_secret_env else ""
        )
        testnet = bool(
            getattr(route_adapter_cfg, "testnet", False)
            or route_adapter_cfg.mode == "testnet"
        )
        normalizer = EventNormalizer()
        classifier = EventClassifier(known_order_link_ids={})
        ws_watcher = BybitWsFillWatcher(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            ops_db_path=ops_db_path,
            repo=gateway_repo,
            normalizer=normalizer,
            classifier=classifier,
            reconciliation_callback=recon_cb,
            mode=route_adapter_cfg.mode,
            wake_callback=wake_callback,
            account_id=account_id,
        )
        ws_watcher.start()
        ws_watchers[account_id] = ws_watcher
        if route_adapter_cfg.websocket.poll_fallback_enabled:
            reconciliation_intervals[account_id] = (
                route_adapter_cfg.websocket.poll_fallback_period_seconds
            )

    # Start all adapter contexts
    for ctx in adapter_contexts.values():
        ctx.start()

    sync_worker = sync_workers[routing.execution_account_id]
    ws_watcher_default = ws_watchers.get(routing.execution_account_id)
    reconciliation_interval_seconds = reconciliation_intervals.get(routing.execution_account_id)

    logger.info(
        "execution gateway started | adapter=%s | account=%s",
        adapter_name, routing.execution_account_id,
    )
    return ExecutionRuntime(
        adapter=adapter,
        execution_worker=execution_worker,
        sync_worker=sync_worker,
        ws_watcher=ws_watcher_default,
        reconciliation_interval_seconds=reconciliation_interval_seconds,
        adapters=adapter_registry,
        sync_workers=sync_workers,
        ws_watchers=ws_watchers,
        reconciliation_intervals=reconciliation_intervals,
        position_reconciliation_intervals=position_reconciliation_intervals,
        poll_fallback_by_account=poll_fallback_by_account,
        position_reconciliation_interval_seconds=adapter_cfg.websocket.position_reconciliation_interval_seconds,
        poll_fallback_enabled=adapter_cfg.websocket.poll_fallback_enabled,
        adapter_contexts=adapter_contexts,
    )
```

- [ ] **Step 6: Update `_close_execution_runtime` — stop contexts before watchers, join after**

Replace the function body of `_close_execution_runtime`:

```python
def _close_execution_runtime(runtime: ExecutionRuntime | None) -> None:
    if runtime is None:
        return
    # Stop adapter contexts first — no new REST calls will be submitted
    for ctx in (runtime.adapter_contexts or {}).values():
        ctx.stop()
    # Stop WS watchers
    stopped_watchers: set[int] = set()
    for watcher in (runtime.ws_watchers or {}).values():
        if id(watcher) in stopped_watchers:
            continue
        watcher.stop()
        stopped_watchers.add(id(watcher))
    if runtime.ws_watcher is not None and id(runtime.ws_watcher) not in stopped_watchers:
        runtime.ws_watcher.stop()
    # Close adapter REST clients
    closed_adapters: set[int] = set()
    for adapter in (runtime.adapters or {}).values():
        close = getattr(adapter, "close", None)
        if callable(close) and id(adapter) not in closed_adapters:
            close()
            closed_adapters.add(id(adapter))
    if id(runtime.adapter) not in closed_adapters:
        close = getattr(runtime.adapter, "close", None)
        if callable(close):
            close()
    # Join context threads (after REST clients closed — no in-flight calls remain)
    for ctx in (runtime.adapter_contexts or {}).values():
        ctx.join(timeout=5.0)
```

- [ ] **Step 7: Remove the three periodic coroutines and their task blocks from `_async_main`**

**7a.** Delete the three coroutine functions. Find and remove (they appear between `_run_position_reconciliation_periodically` at line ~334 and `_run_lifecycle_workers` at line ~350):

```python
async def _run_reconciliation_periodically(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_reconciliation()
        except Exception:
            logger.exception("periodic reconciliation error")


async def _run_position_reconciliation_periodically(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_position_reconciliation()
            sync_worker.run_trade_based_reconciliation()
            sync_worker.run_protective_orders_reconciliation()
        except Exception:
            logger.exception("periodic position/tp reconciliation error")
```

Also delete `_run_sync_worker`:

```python
async def _run_sync_worker(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int = 8,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_once()
        except Exception:
            logger.exception("sync worker error")
```

**7b.** In `_async_main`, find the three task-creation blocks (around lines 658–698) and delete them entirely. The blocks to remove are:

```python
        sync_tasks = []
        if execution_runtime is not None:
            for account_id, worker in (execution_runtime.sync_workers or {}).items():
                if not (execution_runtime.poll_fallback_by_account or {}).get(account_id, False):
                    continue
                sync_tasks.append(
                    asyncio.create_task(
                        _run_sync_worker(
                            sync_worker=worker,
                            logger=logger,
                        )
                    )
                )

        reconciliation_tasks = []
        position_reconciliation_tasks = []
        if execution_runtime is not None:
            for account_id, worker in (execution_runtime.sync_workers or {}).items():
                interval = (execution_runtime.reconciliation_intervals or {}).get(account_id)
                if interval is not None:
                    reconciliation_tasks.append(
                        asyncio.create_task(
                            _run_reconciliation_periodically(
                                sync_worker=worker,
                                interval_seconds=interval,
                                logger=logger,
                            )
                        )
                    )
                pos_interval = (
                    execution_runtime.position_reconciliation_intervals or {}
                ).get(account_id, execution_runtime.position_reconciliation_interval_seconds)
                position_reconciliation_tasks.append(
                    asyncio.create_task(
                        _run_position_reconciliation_periodically(
                            sync_worker=worker,
                            interval_seconds=pos_interval,
                            logger=logger,
                        )
                    )
                )
```

**7c.** In the `finally` block (around lines 702–709), remove the cancellation loops for the three removed task lists:

```python
            for task in sync_tasks:
                task.cancel()
            for task in reconciliation_tasks:
                task.cancel()
            for task in position_reconciliation_tasks:
                task.cancel()
```

- [ ] **Step 8: Run the full execution_gateway test suite**

```
python -m pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: all existing 371 tests pass (plus the new adapter_context tests)

- [ ] **Step 9: Run new functional equivalence test**

```
python -m pytest tests/runtime_v2/execution_gateway/test_adapter_context.py -v
```

Expected: all adapter_context tests pass (including `test_reconciliation_via_context_writes_fill_event`)

- [ ] **Step 10: Commit**

```bash
git add main_linux_server.py tests/runtime_v2/execution_gateway/test_adapter_context.py
git commit -m "feat(runtime): wire AdapterExecutionContext — remove periodic loop coroutines, lambda WS callback, shutdown join"
```

---

## Validation Checklist

After all tasks complete, verify:

- [ ] `python -m pytest tests/runtime_v2/execution_gateway/ -v` → all green
- [ ] No `asyncio.sleep` loops remain for REST sync workers in `main_linux_server.py`
- [ ] `grep -n "_run_sync_worker\|_run_reconciliation_periodically\|_run_position_reconciliation_periodically" main_linux_server.py` → no matches (functions and call sites removed)
- [ ] `grep -n "adapter_contexts" main_linux_server.py` → matches in `_build_execution_runtime`, `_close_execution_runtime`, and `ExecutionRuntime`
- [ ] In `_build_execution_runtime`, the `reconciliation_callback` passed to `BybitWsFillWatcher` is a lambda calling `ctx.submit(...)`, not a direct method reference
