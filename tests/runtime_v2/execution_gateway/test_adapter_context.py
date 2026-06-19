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
