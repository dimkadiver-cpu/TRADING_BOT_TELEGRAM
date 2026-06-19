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
