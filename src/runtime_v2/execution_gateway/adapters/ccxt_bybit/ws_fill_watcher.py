from __future__ import annotations

import asyncio
import dataclasses
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

try:
    import ccxt.pro as ccxtpro
except ModuleNotFoundError:  # pragma: no cover - exercised through patched module in unit tests
    ccxtpro = None

from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event
from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

if TYPE_CHECKING:
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer

logger = logging.getLogger(__name__)

_WS_FAIL_NOTIFY_AFTER = 3
_WS_FAIL_DEDUPE_WINDOW = 600  # seconds — re-notifies after reconnect + new failure window


class BybitWsFillWatcher:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        ops_db_path: str,
        repo: GatewayCommandRepository,
        normalizer: "EventNormalizer",
        classifier: "EventClassifier",
        reconciliation_callback=None,
        mode: str = "live",
        wake_callback: Callable[[], None] | None = None,
        account_id: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._mode = mode
        self._ops_db_path = ops_db_path
        self._repo = repo
        # Logical account this watcher serves: scopes symbol+side chain resolution
        # so two accounts holding the same symbol+side don't look ambiguous.
        self._account_id = account_id
        self._normalizer = normalizer
        self._classifier = classifier  # reserved: batch processing re-creates EventClassifier with fresh data
        self._reconciliation_callback = reconciliation_callback
        self._wake_callback = wake_callback
        self._stop_event = threading.Event()
        self._loop_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watch_orders_task: asyncio.Task | None = None
        self._watch_trades_task: asyncio.Task | None = None
        self._watch_positions_task: asyncio.Task | None = None

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
        self._watch_orders_task = loop.create_task(self._watch_orders_forever())
        self._watch_trades_task = loop.create_task(self._watch_trades_forever())
        self._watch_positions_task = loop.create_task(self._watch_positions_forever())
        self._loop_ready.set()
        try:
            loop.run_until_complete(
                asyncio.gather(
                    self._watch_orders_task,
                    self._watch_trades_task,
                    self._watch_positions_task,
                    return_exceptions=True,
                )
            )
        except asyncio.CancelledError:
            pass
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._watch_orders_task = None
            self._watch_trades_task = None
            self._watch_positions_task = None
            self._loop = None
            loop.close()

    def _cancel_watch_task(self) -> None:
        for task in (self._watch_orders_task, self._watch_trades_task, self._watch_positions_task):
            if task is not None and not task.done():
                task.cancel()

    def _notify_ws_failure(self, stream_name: str) -> None:
        bucket = int(time.time()) // _WS_FAIL_DEDUPE_WINDOW
        try:
            conn = sqlite3.connect(self._ops_db_path)
            try:
                with conn:
                    write_tech_log_event(
                        conn,
                        notification_type="WS_RECONNECT",
                        payload={
                            "level": "WARNING",
                            "category": "Exchange",
                            "title": f"ws_{stream_name}_disconnected",
                            "description": (
                                f"WebSocket stream '{stream_name}' ha perso la connessione "
                                f"{_WS_FAIL_NOTIFY_AFTER} volte consecutive. Reconnessione automatica in corso."
                            ),
                            "context": {"stream": stream_name, "account_id": self._account_id},
                            "action": "Verifica la connessione exchange. Se persiste, controlla i log.",
                            "source": "ws_fill_watcher",
                        },
                        dedupe_key=f"ws_fail:{stream_name}:{self._account_id}:{bucket}",
                        priority="HIGH",
                    )
            finally:
                conn.close()
        except Exception:
            logger.exception("failed to write ws disconnect tech_log for stream=%s", stream_name)

    async def _watch_orders_forever(self) -> None:
        exchange = self._build_exchange()
        consecutive_failures = 0
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
                    consecutive_failures += 1
                    if consecutive_failures >= _WS_FAIL_NOTIFY_AFTER:
                        self._notify_ws_failure("watch_orders")
                    await self._run_reconciliation_callback()
                    try:
                        await exchange.close()
                    except Exception:
                        pass
                    exchange = self._build_exchange()
                    await asyncio.sleep(min(5 * consecutive_failures, 60))
                    continue
                consecutive_failures = 0
                self._process_batch(orders, self._normalizer.from_order)
        finally:
            await exchange.close()

    async def _watch_trades_forever(self) -> None:
        exchange = self._build_exchange()
        consecutive_failures = 0
        try:
            while not self._stop_event.is_set():
                try:
                    trades = await exchange.watch_my_trades()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._stop_event.is_set():
                        break
                    logger.exception("bybit watch_my_trades failed")
                    consecutive_failures += 1
                    if consecutive_failures >= _WS_FAIL_NOTIFY_AFTER:
                        self._notify_ws_failure("watch_trades")
                    await self._run_reconciliation_callback()
                    try:
                        await exchange.close()
                    except Exception:
                        pass
                    exchange = self._build_exchange()
                    await asyncio.sleep(min(5 * consecutive_failures, 60))
                    continue
                consecutive_failures = 0
                self._process_batch(trades, self._normalizer.from_trade)
        finally:
            await exchange.close()

    async def _watch_positions_forever(self) -> None:
        exchange = self._build_exchange()
        consecutive_failures = 0
        try:
            while not self._stop_event.is_set():
                try:
                    positions = await exchange.watch_positions()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._stop_event.is_set():
                        break
                    logger.exception("bybit watch_positions failed")
                    consecutive_failures += 1
                    if consecutive_failures >= _WS_FAIL_NOTIFY_AFTER:
                        self._notify_ws_failure("watch_positions")
                    await self._run_reconciliation_callback()
                    try:
                        await exchange.close()
                    except Exception:
                        pass
                    exchange = self._build_exchange()
                    await asyncio.sleep(min(5 * consecutive_failures, 60))
                    continue
                consecutive_failures = 0
                self._process_batch(positions, self._normalizer.from_position)
        finally:
            await exchange.close()

    def _process_batch(
        self,
        items: list[dict] | None,
        normalize_fn,  # callable: dict → ExchangeRawEvent | None
    ) -> None:
        """Generic batch processor: normalize → classify → enrich → persist."""
        if not items:
            return
        # Refresh known_order_link_ids once per batch for efficiency
        known = self._repo.get_known_order_link_ids()
        # Update classifier's known map (re-create classifier with fresh data)
        classifier = EventClassifier(known_order_link_ids=known)
        for item in items:
            try:
                raw = normalize_fn(item)
                if raw is None:
                    continue
                classified = classifier.classify(raw)

                # Post-classification enrichment: attribute TP/SL fills that Bybit
                # does not tag with orderLinkId (position-level attached orders).
                # "Sell" fill closes a LONG; "Buy" fill closes a SHORT.
                if (
                    classified.event_type in ("TP_FILLED", "SL_FILLED")
                    and classified.trade_chain_id is None
                ):
                    fill_side = (raw.side or "").strip()
                    position_side = "LONG" if fill_side.lower() == "sell" else "SHORT"
                    chain_id = self._repo.resolve_chain_for_fill(
                        raw.symbol, position_side, self._account_id
                    )
                    if chain_id is not None:
                        classified = dataclasses.replace(classified, trade_chain_id=chain_id)

                # Manual closes (CreateByUser, no orderLinkId): same side inversion as TP/SL.
                # "Sell" fill closes a LONG; "Buy" fill closes a SHORT.
                if (
                    classified.event_type in ("MANUAL_CLOSE_FULL", "MANUAL_CLOSE_PARTIAL")
                    and classified.trade_chain_id is None
                ):
                    fill_side = (raw.side or "").strip()
                    position_side = "LONG" if fill_side.lower() == "sell" else "SHORT"
                    chain_id = self._repo.resolve_chain_for_fill(
                        raw.symbol, position_side, self._account_id
                    )
                    if chain_id is not None:
                        classified = dataclasses.replace(classified, trade_chain_id=chain_id)
                    else:
                        logger.warning(
                            "manual close %s (%s %s account=%s) not attributable: "
                            "0 or >1 open chains for symbol+side",
                            raw.exchange_event_id, raw.symbol, position_side, self._account_id,
                        )

                # Funding events: resolve chain by symbol + side (Bybit side is position side,
                # not fill direction — "Buy" = LONG position, "Sell" = SHORT position).
                if (
                    classified.event_type == "FUNDING_SETTLED"
                    and classified.trade_chain_id is None
                ):
                    funding_side = (raw.side or "").strip()
                    position_side = "LONG" if funding_side.lower() in ("buy", "long") else "SHORT"
                    chain_id = self._repo.resolve_chain_for_fill(
                        raw.symbol, position_side, self._account_id
                    )
                    if chain_id is not None:
                        classified = dataclasses.replace(classified, trade_chain_id=chain_id)
                    else:
                        logger.warning(
                            "funding execution %s (%s %s account=%s, exec_fee=%s) not attributable: "
                            "0 or >1 open chains for symbol+side — cumulative_funding will not be updated",
                            raw.exchange_event_id, raw.symbol, position_side,
                            self._account_id, raw.exec_fee,
                        )

                inserted = self._repo.insert_raw_and_classified(classified)
                if inserted and classified.should_forward_to_lifecycle and self._wake_callback:
                    self._wake_callback()
            except Exception:
                item_id = item.get("id", repr(item)) if isinstance(item, dict) else repr(item)
                logger.exception("error processing item %s", item_id)

    def _build_exchange(self):
        if ccxtpro is None:
            raise RuntimeError("ccxt.pro is not installed")
        exchange = ccxtpro.bybit({
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "options": {
                "defaultType": "linear",
                # ccxt default filterExecTypes excludes "Funding": without this
                # override watch_my_trades silently drops funding fee executions
                # and FUNDING_SETTLED never reaches the lifecycle.
                "watchMyTrades": {
                    "filterExecTypes": ["Trade", "AdlTrade", "BustTrade", "Settle", "Funding"],
                },
            },
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


__all__ = ["BybitWsFillWatcher"]
