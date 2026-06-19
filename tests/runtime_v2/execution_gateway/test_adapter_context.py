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
