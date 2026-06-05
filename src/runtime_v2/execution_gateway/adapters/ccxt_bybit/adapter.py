from __future__ import annotations

import math
import logging
import time
from typing import TYPE_CHECKING

import ccxt

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import BybitOrderBuilder
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper
from src.runtime_v2.execution_gateway.models import (
    AdapterCapabilities, AdapterResult, RawAdapterOrder,
    RawAdapterTrade, RawPositionDetails,
)

from src.runtime_v2.execution_gateway import client_order_id as coid_mod

if TYPE_CHECKING:
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


class CcxtBybitAdapter(ExecutionAdapter):
    @staticmethod
    def _normalize_bybit_symbol(symbol: str) -> str:
        return symbol.replace("/", "").replace(":USDT", "")

    @staticmethod
    def _parse_trading_stop_retcode(resp: dict | None) -> tuple[int, str]:
        raw_ret_code = (resp or {}).get("retCode", 0)
        try:
            ret_code = int(raw_ret_code)
        except (TypeError, ValueError):
            ret_code = -1
        return ret_code, str((resp or {}).get("retMsg", ""))

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        connector: str,
        mode: str = "live",
        adjust_for_time_difference: bool = True,
        recv_window_ms: int = 10000,
        time_sync_on_startup: bool = True,
        repo: GatewayCommandRepository | None = None,
        _exchange=None,  # injectable for unit tests
    ) -> None:
        if _exchange is not None:
            self._exchange = _exchange
        else:
            self._exchange = ccxt.bybit({
                "apiKey": api_key,
                "secret": api_secret,
                "options": {
                    "defaultType": "linear",
                    "adjustForTimeDifference": adjust_for_time_difference,
                    "recvWindow": recv_window_ms,
                    "recv_window": recv_window_ms,
                },
            })
            if mode == "demo":
                # Bybit Demo Trading — usa api-demo.bybit.com, non testnet
                self._exchange.enable_demo_trading(True)
            elif mode == "testnet":
                self._exchange.set_sandbox_mode(True)
            self._configure_time_sync(
                adjust_for_time_difference=adjust_for_time_difference,
                recv_window_ms=recv_window_ms,
                time_sync_on_startup=time_sync_on_startup,
            )
        self._connector = connector
        self._repo = repo
        self._builder = BybitOrderBuilder()

    def _configure_time_sync(
        self,
        *,
        adjust_for_time_difference: bool,
        recv_window_ms: int,
        time_sync_on_startup: bool,
    ) -> None:
        options = getattr(self._exchange, "options", None)
        if isinstance(options, dict):
            options["adjustForTimeDifference"] = adjust_for_time_difference
            options["recvWindow"] = recv_window_ms
            options["recv_window"] = recv_window_ms

        if hasattr(self._exchange, "recvWindow"):
            try:
                self._exchange.recvWindow = recv_window_ms
            except Exception:
                logger.debug("unable to set exchange recvWindow", exc_info=True)

        if not (adjust_for_time_difference and time_sync_on_startup):
            return

        load_time_difference = getattr(self._exchange, "load_time_difference", None)
        if not callable(load_time_difference):
            return

        try:
            delta_ms = load_time_difference()
            if delta_ms is not None:
                logger.info("Bybit time sync delta_ms=%s", delta_ms)
        except Exception as exc:
            logger.warning("Bybit time sync bootstrap failed: %s", exc)

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            place_entry=True,
            protective_stop_native=True,
            take_profit_native=True,
            bracket_order=False,
            move_stop=True,
            close_partial=True,
            close_full=True,
            executor_position=False,
            sync_protective_orders=True,
        )

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str,
                     *, position_idx: int = 0) -> None:
        extra = {
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        }
        if position_idx != 0:
            extra["positionIdx"] = position_idx
        try:
            self._exchange.set_leverage(leverage, symbol, params=extra)
        except Exception as e:
            # retCode 110043 = "leverage not modified" — already at target, treat as success
            if "110043" in str(e):
                return
            raise

    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        # Legacy compatibility: older callers may still send entry_price for breakeven moves.
        # Normal flow should provide lifecycle-computed new_stop_price directly.
        if (
            command_type == "MOVE_STOP_TO_BREAKEVEN"
            and "new_stop_price" not in payload
            and "entry_price" in payload
        ):
            payload = {**payload, "new_stop_price": payload["entry_price"]}
        if (
            command_type in {"MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP"}
            and "new_stop_price" in payload
            and payload["new_stop_price"] in (None, "")
        ):
            return AdapterResult(
                success=False,
                reason="invalid_payload",
                error="new_stop_price is required",
            )

        hedge_mode = bool(payload.get("hedge_mode", False))

        params = self._builder.build(
            command_type,
            payload,
            client_order_id,
            hedge_mode=hedge_mode,
        )

        if params.action == "noop":
            return AdapterResult(success=True)

        try:
            if params.action == "create_order":
                resp = self._exchange.create_order(
                    params.symbol,
                    params.order_type,
                    params.side,
                    params.amount,
                    params.price,
                    params={"orderLinkId": params.order_link_id, **params.extra_params},
                )
                return AdapterResult(
                    success=True,
                    exchange_order_id=str(resp.get("id") or ""),
                )

            if params.action == "cancel_by_link":
                orders = self._exchange.fetch_open_orders(
                    params.symbol, params={"orderLinkId": params.order_link_id}
                )
                # Bybit enforces one pending entry per orderLinkId at a time, so we only cancel the last order.
                # This is safe even if multiple orders exist (should not happen, but defensive).
                if orders:
                    self._exchange.cancel_order(orders[-1]["id"], params.symbol)
                return AdapterResult(success=True)

            if params.action == "edit_sl":
                close_side = "sell" if params.position_side == "LONG" else "buy"
                orders = self._exchange.fetch_open_orders(params.symbol)
                sl_orders = [
                    o for o in orders
                    if o.get("reduceOnly") and o.get("stopPrice") and o["side"] == close_side
                ]
                if not sl_orders:
                    return AdapterResult(success=False, reason="sl_order_not_found")
                sl = sl_orders[-1]
                self._exchange.edit_order(
                    sl["id"], params.symbol, sl["type"], sl["side"], sl["amount"],
                    params={"triggerPrice": params.new_trigger_price},
                )
                return AdapterResult(success=True)

            if params.action == "rebuild_partial_tps":
                return self._handle_rebuild_partial_tps(
                    params.symbol,
                    params.position_side,
                    params.extra_params,
                )

            if params.action in {"trading_stop_full", "trading_stop_partial", "trading_stop_move_sl"}:
                resp = self._exchange.private_post_v5_position_trading_stop({
                    "category": "linear",
                    "symbol": self._normalize_bybit_symbol(params.symbol),
                    **params.extra_params,
                })
                ret_code, ret_msg = self._parse_trading_stop_retcode(resp)
                if ret_code != 0:
                    logger.warning("trading_stop retCode=%s msg=%s", ret_code, ret_msg)
                    return AdapterResult(success=False, error=f"retCode={ret_code}: {ret_msg}")
                return AdapterResult(success=True)

        except ccxt.InvalidOrder as e:
            return AdapterResult(success=False, reason="invalid_order", error=str(e))
        except ccxt.InsufficientFunds as e:
            return AdapterResult(success=False, reason="insufficient_funds", error=str(e))
        except (ccxt.NetworkError, ccxt.RateLimitExceeded):
            raise
        except ccxt.BaseError as e:
            return AdapterResult(success=False, error=str(e))

        return AdapterResult(success=False, error=f"unhandled action: {params.action!r}")

    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        logger.warning("cancel_order called — delegating to CANCEL_PENDING_ENTRY command; this no-op is expected")
        return AdapterResult(success=True)

    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None:
        orders = []
        try:
            orders = self._exchange.fetch_open_orders(
                None, params={"orderLinkId": client_order_id}
            )
        except Exception as exc:
            logger.debug("fetch_open_orders error: %s", exc)
        if not orders:
            try:
                orders = self._exchange.fetch_closed_orders(
                    None, params={"orderLinkId": client_order_id}
                )
            except Exception as exc:
                logger.debug("fetch_closed_orders error: %s", exc)
        if orders:
            order = orders[-1]
            returned_coid = str(order.get("clientOrderId") or "")
            if returned_coid != client_order_id:
                logger.warning(
                    "get_order_status: orderLinkId filter ignored by Bybit — "
                    "requested=%s got=%s — skipping",
                    client_order_id, returned_coid,
                )
                return None
            return StatusMapper.map(order, client_order_id=client_order_id)
        return self._get_attached_order_status_from_positions(client_order_id)

    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None:
        try:
            positions = self._exchange.fetch_positions([symbol])
            for pos in positions:
                if str(pos.get("side") or "").lower() == side.lower():
                    return float(pos.get("contracts") or 0.0)
            return 0.0
        except Exception:
            logger.warning("get_position_qty failed for %s %s", symbol, side)
            return None

    def fetch_mark_price(self, symbol: str, execution_account_id: str) -> float | None:
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            mark = ticker.get("markPrice") or ticker.get("last")
            return float(mark) if mark is not None else None
        except Exception as exc:
            logger.warning("fetch_mark_price failed for %s: %s", symbol, exc)
            return None

    def load_known_symbols(self) -> frozenset[str] | None:
        try:
            markets = self._exchange.load_markets()
            # CCXT keys are "BTC/USDT:USDT" but the bot uses Bybit raw ids (e.g. "BTCUSDT").
            # Collect both the ccxt key and the exchange-native market id so either format matches.
            ids: set[str] = set()
            for key, mkt in markets.items():
                ids.add(key)
                raw_id = mkt.get("id")
                if raw_id:
                    ids.add(raw_id)
            return frozenset(ids)
        except Exception as exc:
            logger.warning("load_known_symbols failed: %s", exc)
            return None

    def fetch_recent_reduce_trades(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
        limit: int = 50,
    ) -> list[RawAdapterTrade]:
        """Return recent position-closing fills (reduceOnly=True) for symbol+side.

        Uses REST fetch_my_trades() — catches fills missed by WS or during bot downtime.
        symbol: Bybit raw format (e.g. PHAUSDT). ccxt accepts raw format for linear futures.
        """
        since_ms = int((time.time() - 86400) * 1000)  # last 24h
        try:
            raw_trades = self._exchange.fetch_my_trades(symbol, since=since_ms, limit=limit)
        except Exception as exc:
            logger.warning("fetch_my_trades failed for %s: %s", symbol, exc)
            return []

        result: list[RawAdapterTrade] = []
        for t in raw_trades:
            info = t.get("info") or {}
            reduce_only = bool(info.get("reduceOnly", False))
            if not reduce_only:
                continue
            trade_symbol = self._normalize_bybit_symbol(t.get("symbol") or symbol)
            try:
                result.append(RawAdapterTrade(
                    trade_id=str(t["id"]),
                    symbol=trade_symbol,
                    price=float(t["price"]),
                    amount=float(t["amount"]),
                    reduce_only=True,
                ))
            except Exception:
                logger.debug("skipping malformed trade %s", t.get("id"))
        return result

    def fetch_position_details(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> RawPositionDetails | None:
        """Return TP/SL levels currently set on the exchange for symbol+side.

        Uses fetch_positions() — detects if protective orders were externally cancelled.
        Returns None if position not found or on error.
        """
        try:
            positions = self._exchange.fetch_positions([symbol])
        except Exception as exc:
            logger.warning("fetch_position_details failed for %s %s: %s", symbol, side, exc)
            return None

        for pos in positions:
            if str(pos.get("side") or "").lower() != side.lower():
                continue
            info = pos.get("info") or {}
            raw_symbol = info.get("symbol") or self._normalize_bybit_symbol(
                pos.get("symbol") or symbol
            )

            def _parse_price(val: object) -> float | None:
                if val is None:
                    return None
                s = str(val).strip()
                if s == "":
                    return 0.0
                try:
                    return float(s)
                except ValueError:
                    return None

            return RawPositionDetails(
                symbol=raw_symbol,
                side=side.upper(),
                qty=float(pos.get("contracts") or 0.0),
                take_profit=_parse_price(info.get("takeProfit")),
                stop_loss=_parse_price(info.get("stopLoss")),
            )
        return None

    def _handle_rebuild_partial_tps(
        self,
        symbol: str,
        side: str,
        extra_params: dict,
    ) -> AdapterResult:
        def _safe_float(value: object) -> float | None:
            if value is None:
                return None
            text = str(value).strip()
            if text == "":
                return None
            try:
                return float(text)
            except (TypeError, ValueError):
                return None

        close_side = "sell" if side == "LONG" else "buy"
        position_idx = int(extra_params.get("position_idx", 0))
        tps = list(extra_params.get("tps") or [])
        preserve_sl = bool(extra_params.get("preserve_sl", True))
        preserve_full_tp = bool(extra_params.get("preserve_full_tp", True))

        try:
            positions = self._exchange.fetch_positions([symbol])
        except Exception as exc:
            return AdapterResult(success=False, error=f"fetch_positions failed: {exc}")

        full_qty = 0.0
        active_stop_loss: float | None = None
        for pos in positions:
            if str(pos.get("side") or "").lower() != side.lower():
                continue
            full_qty = float(pos.get("contracts") or 0.0)
            pos_info = pos.get("info") or {}
            active_stop_loss = _safe_float(pos_info.get("stopLoss"))
            break

        try:
            open_orders = self._exchange.fetch_open_orders(symbol)
        except Exception as exc:
            return AdapterResult(success=False, error=f"fetch_open_orders failed: {exc}")

        candidate_orders = [
            order
            for order in open_orders
            if order.get("reduceOnly")
            and order.get("stopPrice")
            and order.get("side") == close_side
        ]
        for order in candidate_orders:
            order_amount = float(order.get("amount") or 0.0)
            order_stop_price = _safe_float(order.get("stopPrice"))
            if (
                preserve_sl
                and active_stop_loss is not None
                and order_stop_price is not None
                and order_stop_price == active_stop_loss
            ):
                continue
            if preserve_full_tp and math.isclose(order_amount, full_qty, rel_tol=1e-9, abs_tol=1e-9):
                continue
            try:
                self._exchange.cancel_order(order["id"], symbol)
            except Exception as exc:
                logger.warning("cancel partial tp order failed: %s", exc)

        for tp in sorted(tps, key=lambda item: int(item.get("sequence", 0))):
            sequence = int(tp.get("sequence", 0))
            tp_order_type = str(tp.get("order_type", "Limit"))
            body = {
                "category": "linear",
                "symbol": self._normalize_bybit_symbol(symbol),
                "positionIdx": position_idx,
                "tpslMode": "Partial",
                "takeProfit": str(float(tp["price"])),
                "tpSize": str(float(tp["qty"])),
                "tpOrderType": tp_order_type,
                "tpTriggerBy": tp.get("trigger_by", "MarkPrice"),
            }
            if tp_order_type == "Limit" and tp.get("limit_price") is not None:
                body["tpLimitPrice"] = str(float(tp["limit_price"]))
            try:
                resp = self._exchange.private_post_v5_position_trading_stop(body)
            except Exception as exc:
                return AdapterResult(success=False, error=f"tp{sequence}: {exc}")
            ret_code, ret_msg = self._parse_trading_stop_retcode(resp)
            if ret_code != 0:
                logger.warning("trading_stop retCode=%s msg=%s", ret_code, ret_msg)
                return AdapterResult(
                    success=False,
                    error=f"tp{sequence}: retCode={ret_code}: {ret_msg}",
                )

        return AdapterResult(success=True)

    def _get_attached_order_status_from_positions(
        self,
        client_order_id: str,
    ) -> RawAdapterOrder | None:
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            return None
        if coid.role not in {"sl", "tp"} or self._repo is None:
            return None

        payload = self._repo.get_payload_by_client_order_id(client_order_id)
        if payload is None:
            return None

        symbol = payload.get("symbol")
        side = payload.get("side")
        if not symbol or not side:
            return None

        try:
            positions = self._exchange.fetch_positions([symbol])
        except Exception as exc:
            logger.debug("fetch_positions fallback error: %s", exc)
            return None

        matched_side = False
        current_qty = 0.0
        for pos in positions:
            if str(pos.get("side") or "").lower() == str(side).lower():
                matched_side = True
                current_qty = float(pos.get("contracts") or 0.0)
                break

        if matched_side and current_qty == 0.0:
            return RawAdapterOrder(
                client_order_id=client_order_id,
                status="FILLED",
            )
        return None


__all__ = ["CcxtBybitAdapter"]
