from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone

try:
    import ccxt.pro as ccxtpro
except ModuleNotFoundError:  # pragma: no cover - exercised through patched module in unit tests
    ccxtpro = None

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper
from src.runtime_v2.execution_gateway.models import RawAdapterOrder
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BybitWsFillWatcher:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        ops_db_path: str,
        repo: GatewayCommandRepository,
        reconciliation_callback=None,
        mode: str = "live",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._mode = mode
        self._ops_db_path = ops_db_path
        self._repo = repo
        self._reconciliation_callback = reconciliation_callback
        self._stop_event = threading.Event()
        self._loop_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watch_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_in_thread,
            name="bybit-ws-fill-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._loop_ready.wait(timeout=2)
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_watch_task)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run_in_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._loop_ready.set()
        self._watch_task = loop.create_task(self._watch_orders_forever())
        try:
            loop.run_until_complete(self._watch_task)
        except asyncio.CancelledError:
            pass
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._watch_task = None
            self._loop = None
            loop.close()

    def _cancel_watch_task(self) -> None:
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()

    async def _watch_orders_forever(self) -> None:
        exchange = self._build_exchange()
        try:
            while not self._stop_event.is_set():
                try:
                    orders = await exchange.watch_orders()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._stop_event.is_set():
                        break
                    logger.exception("bybit watch_orders failed")
                    await self._run_reconciliation_callback()
                    await asyncio.sleep(5)
                    continue
                self._process_order_batch(orders)
        finally:
            await exchange.close()

    def _build_exchange(self):
        if ccxtpro is None:
            raise RuntimeError("ccxt.pro is not installed")
        exchange = ccxtpro.bybit({
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "options": {"defaultType": "linear"},
        })
        if self._mode == "demo":
            exchange.enable_demo_trading(True)
        elif self._testnet:
            exchange.set_sandbox_mode(True)
        return exchange

    async def _run_reconciliation_callback(self) -> None:
        if self._reconciliation_callback is None:
            return
        result = self._reconciliation_callback()
        if asyncio.iscoroutine(result):
            await result

    def _process_order_batch(self, orders: list[dict] | None) -> None:
        if not orders:
            return
        active_client_order_ids = self._repo.get_active_client_order_ids()
        for order in orders:
            client_order_id = str(order.get("clientOrderId") or "")
            if not client_order_id or client_order_id not in active_client_order_ids:
                continue
            raw = StatusMapper.map(order, client_order_id=client_order_id)
            if raw.is_filled:
                self._save_fill(client_order_id, raw)

    def _save_fill(self, client_order_id: str, raw: RawAdapterOrder) -> None:
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            logger.warning("cannot parse client_order_id from websocket fill: %s", client_order_id)
            return

        exchange_order_id = raw.exchange_order_id or client_order_id
        payload: dict[str, object]

        if coid.role == "entry":
            event_type = "ENTRY_FILLED"
            payload = self._base_fill_payload(raw, coid.command_id)
        elif coid.role == "sl":
            event_type = "SL_FILLED"
            payload = self._base_fill_payload(raw, coid.command_id)
        elif coid.role == "tp":
            event_type = "TP_FILLED"
            payload = {
                "tp_level": coid.sequence,
                "is_final": self._repo.count_active_tps(coid.trade_chain_id) <= 1,
                **self._base_fill_payload(raw, coid.command_id),
            }
        elif coid.role == "exit_partial":
            event_type = "CLOSE_PARTIAL_FILLED"
            payload = self._base_fill_payload(raw, coid.command_id)
        elif coid.role == "exit_full":
            event_type = "CLOSE_FULL_FILLED"
            payload = self._base_fill_payload(raw, coid.command_id)
        else:
            logger.debug("ignoring websocket fill for unsupported role '%s'", coid.role)
            return

        conn = sqlite3.connect(self._ops_db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (
                    coid.trade_chain_id,
                    event_type,
                    json.dumps(payload),
                    "NEW",
                    f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}",
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _base_fill_payload(raw: RawAdapterOrder, command_id: int) -> dict[str, object]:
        return {
            "fill_price": raw.average_price,
            "filled_qty": raw.filled_qty,
            "command_id": command_id,
        }


__all__ = ["BybitWsFillWatcher"]
