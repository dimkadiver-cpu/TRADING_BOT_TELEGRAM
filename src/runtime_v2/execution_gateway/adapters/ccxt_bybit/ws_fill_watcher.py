from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
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


def _ccxt_symbol_to_raw(symbol: str) -> str:
    """Convert ccxt unified format to Bybit raw format.

    Examples:
        "PHA/USDT:USDT"  →  "PHAUSDT"
        "BTC/USDT:USDT"  →  "BTCUSDT"
        "PHAUSDT"        →  "PHAUSDT"   (pass-through, already raw)
    """
    if "/" not in symbol:
        return symbol
    base, rest = symbol.split("/", 1)
    quote = rest.split(":")[0]
    return base + quote


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
        wake_callback: Callable[[], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._mode = mode
        self._ops_db_path = ops_db_path
        self._repo = repo
        self._reconciliation_callback = reconciliation_callback
        self._wake_callback = wake_callback
        self._stop_event = threading.Event()
        self._loop_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watch_orders_task: asyncio.Task | None = None
        self._watch_trades_task: asyncio.Task | None = None

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
        self._loop_ready.set()
        try:
            loop.run_until_complete(
                asyncio.gather(
                    self._watch_orders_task,
                    self._watch_trades_task,
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
            self._loop = None
            loop.close()

    def _cancel_watch_task(self) -> None:
        for task in (self._watch_orders_task, self._watch_trades_task):
            if task is not None and not task.done():
                task.cancel()

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

    async def _watch_trades_forever(self) -> None:
        """Ascolta watchMyTrades per rilevare fill TP position-level (Mode C).

        I TP impostati via SET_POSITION_TPSL_* non hanno clientOrderId e non
        sono visibili in watchOrders. watchMyTrades riceve tutti i fill inclusi
        quelli position-level.
        """
        exchange = self._build_exchange()
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
                    await asyncio.sleep(5)
                    continue
                self._process_trade_batch(trades)
        finally:
            await exchange.close()

    def _save_tp_fill_from_trade(
        self,
        chain_id: int,
        tp_level: int,
        fill_price: float,
        filled_qty: float,
        is_final: bool,
        exchange_trade_id: str,
    ) -> None:
        """INSERT OR IGNORE di TP_FILLED con dati reali da watchMyTrades."""
        idempotency_key = f"TP_FILLED:{chain_id}:level:{tp_level}"
        payload = json.dumps({
            "tp_level": tp_level,
            "is_final": is_final,
            "fill_price": fill_price,
            "filled_qty": filled_qty,
            "source": "watch_my_trades",
            "exchange_trade_id": exchange_trade_id,
        })
        inserted = self._repo.insert_exchange_event(chain_id, "TP_FILLED", payload, idempotency_key)
        logger.debug(
            "TP_FILLED inserted from watch_my_trades: chain=%d level=%d price=%.4f trade_id=%s",
            chain_id, tp_level, fill_price, exchange_trade_id,
        )
        if inserted and self._wake_callback:
            self._wake_callback()

    def _process_trade_batch(self, trades: list[dict] | None) -> None:
        """Elabora batch di trade da watchMyTrades. Inserisce TP_FILLED per fill position-level.

        Logica:
        1. Filtra su reduceOnly=True (solo chiusure di posizione, non entry/SL)
        2. Determina il lato della posizione (fill sell → posizione LONG)
        3. Cerca chain aperte con stesso symbol+side
        4. Confronta fill price con TP attivi (tolerance ±1%)
        5. Match univoco → INSERT TP_FILLED. Match ambiguo → skip.
        """
        if not trades:
            return
        for trade in trades:
            try:
                self._match_and_save_tp_fill(trade)
            except Exception:
                logger.exception("error processing trade %s", trade.get("id"))

    def _match_and_save_tp_fill(self, trade: dict) -> None:
        """Matching singolo trade → chain + tp_level. Inserisce TP_FILLED se match univoco.

        Known limitation: Stop-loss fills on Bybit are also tagged reduceOnly=True.
        The ±1% price tolerance provides de-facto filtering (SL price typically differs
        from TP price by >1%), but in extreme market conditions a spurious TP_FILLED
        could be emitted for an SL hit. The downstream lifecycle handler is idempotent
        and the reconciliation poller provides a safety net.
        """
        # Solo fill che chiudono posizione (TP chiude, entry/SL apre o aggiusta)
        if not trade.get("reduceOnly", False):
            return

        symbol = _ccxt_symbol_to_raw(trade.get("symbol", ""))
        side = trade.get("side", "")  # "sell" per close LONG, "buy" per close SHORT
        fill_price_raw = trade.get("price")
        filled_qty_raw = trade.get("amount")
        if not symbol or not side or fill_price_raw is None:
            return

        fill_price = float(fill_price_raw)
        filled_qty = float(filled_qty_raw or 0.0)
        if fill_price <= 0.0:
            return

        # Lato della posizione (opposto al lato del fill di chiusura)
        chain_side = "LONG" if side.lower() == "sell" else "SHORT"

        # Trova chain aperte con stesso symbol+side
        open_chain_ids = self._repo.get_open_chains_for_symbol(symbol, chain_side)
        if not open_chain_ids:
            return

        # Confronta fill price con TP attivi di ogni chain (tolerance ±1%)
        matches: list[tuple[int, int]] = []  # (chain_id, tp_level)
        for chain_id in open_chain_ids:
            tp_commands = self._repo.get_active_tp_commands(chain_id)
            for cmd_payload in tp_commands:
                tp_price_raw = cmd_payload.get("take_profit")
                if tp_price_raw is None:
                    continue
                tp_price = float(tp_price_raw)
                if tp_price <= 0.0:
                    continue
                if abs(fill_price - tp_price) / tp_price <= 0.01:  # ±1% tolerance
                    tp_level = int(cmd_payload.get("tp_sequence", 1))
                    matches.append((chain_id, tp_level))

        if len(matches) != 1:
            if len(matches) > 1:
                logger.warning(
                    "ambiguous TP fill match: symbol=%s price=%.4f matches=%s — skipping",
                    symbol, fill_price, matches,
                )
            return

        chain_id, tp_level = matches[0]

        # is_final da posQty Bybit se disponibile, fallback False (conservativo)
        pos_qty_raw = trade.get("info", {}).get("posQty")
        if pos_qty_raw is not None:
            try:
                is_final = float(pos_qty_raw) == 0.0
            except (ValueError, TypeError):
                is_final = False
        else:
            is_final = False

        exchange_trade_id = str(trade.get("id") or "")
        self._save_tp_fill_from_trade(
            chain_id=chain_id,
            tp_level=tp_level,
            fill_price=fill_price,
            filled_qty=filled_qty,
            is_final=is_final,
            exchange_trade_id=exchange_trade_id,
        )

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

        if event_type == "TP_FILLED":
            idempotency_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
        else:
            idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"

        conn = sqlite3.connect(self._ops_db_path)
        inserted = False
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (
                    coid.trade_chain_id,
                    event_type,
                    json.dumps(payload),
                    "NEW",
                    idempotency_key,
                    _now(),
                ),
            )
            conn.commit()
            inserted = cursor.rowcount > 0
        finally:
            conn.close()

        if inserted and self._wake_callback:
            self._wake_callback()

    @staticmethod
    def _base_fill_payload(raw: RawAdapterOrder, command_id: int) -> dict[str, object]:
        return {
            "fill_price": raw.average_price,
            "filled_qty": raw.filled_qty,
            "command_id": command_id,
        }


__all__ = ["BybitWsFillWatcher"]
