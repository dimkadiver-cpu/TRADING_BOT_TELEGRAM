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
