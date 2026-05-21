from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import ccxt

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import BybitOrderBuilder
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder

from src.runtime_v2.execution_gateway import client_order_id as coid_mod

if TYPE_CHECKING:
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


class CcxtBybitAdapter(ExecutionAdapter):
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        connector: str,
        mode: str = "live",
        repo: GatewayCommandRepository | None = None,
        _exchange=None,  # injectable for unit tests
    ) -> None:
        if _exchange is not None:
            self._exchange = _exchange
        else:
            self._exchange = ccxt.bybit({
                "apiKey": api_key,
                "secret": api_secret,
                "options": {"defaultType": "linear"},
            })
            if mode == "demo":
                # Bybit Demo Trading — usa api-demo.bybit.com, non testnet
                self._exchange.enable_demo_trading(True)
            elif mode == "testnet":
                self._exchange.set_sandbox_mode(True)
        self._connector = connector
        self._repo = repo
        self._builder = BybitOrderBuilder()

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
        # Normalize payload: allow callers to pass entry_price as alias for target_price
        # in MOVE_STOP_TO_BREAKEVEN commands (builder expects target_price).
        if command_type == "MOVE_STOP_TO_BREAKEVEN" and "entry_price" in payload and "target_price" not in payload:
            payload = {**payload, "target_price": payload["entry_price"]}

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

            if params.action == "amend_sl_qty":
                return self._handle_amend_sl_qty(params.symbol, params.position_side)

            if params.action in {"trading_stop_full", "trading_stop_partial", "trading_stop_move_sl"}:
                resp = self._exchange.private_post_v5_position_trading_stop({
                    "category": "linear",
                    "symbol": params.symbol,
                    **params.extra_params,
                })
                ret_code = (resp or {}).get("retCode", 0)
                if ret_code != 0:
                    ret_msg = (resp or {}).get("retMsg", "")
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

    def _handle_amend_sl_qty(self, symbol: str, side: str) -> AdapterResult:
        close_side = "sell" if side == "LONG" else "buy"
        position_idx = 1 if side == "LONG" else 2

        try:
            positions = self._exchange.fetch_positions([symbol])
        except Exception as exc:
            return AdapterResult(success=False, error=f"fetch_positions failed: {exc}")

        current_qty = 0.0
        pos_info: dict = {}
        for pos in positions:
            if str(pos.get("side") or "").lower() == side.lower():
                current_qty = float(pos.get("contracts") or 0.0)
                pos_info = pos.get("info") or {}
                break

        try:
            open_orders = self._exchange.fetch_open_orders(symbol)
        except Exception:
            open_orders = []

        if current_qty == 0.0:
            for order in open_orders:
                if order.get("reduceOnly") and order.get("side") == close_side:
                    try:
                        self._exchange.cancel_order(order["id"], symbol)
                    except Exception as exc:
                        logger.warning("cancel residual reduceOnly order failed: %s", exc)
            return AdapterResult(success=True)

        sl_orders = [
            order
            for order in open_orders
            if order.get("reduceOnly") and order.get("stopPrice") and order.get("side") == close_side
        ]
        if sl_orders:
            sl_order = sl_orders[-1]
            try:
                self._exchange.edit_order(
                    sl_order["id"],
                    symbol,
                    sl_order["type"],
                    sl_order["side"],
                    current_qty,
                    params={"triggerPrice": float(sl_order["stopPrice"])},
                )
            except Exception as exc:
                return AdapterResult(success=False, error=f"edit_order sl failed: {exc}")
            return AdapterResult(success=True)

        attached_sl = pos_info.get("stopLoss", "0")
        if attached_sl and float(attached_sl) > 0:
            bybit_symbol = pos_info.get("symbol") or symbol.replace("/", "").replace(":USDT", "")
            try:
                self._exchange.private_post_v5_position_trading_stop(
                    {
                        "category": "linear",
                        "symbol": bybit_symbol,
                        "positionIdx": position_idx,
                        "stopLoss": str(attached_sl),
                        "slSize": str(current_qty),
                    }
                )
            except Exception as exc:
                return AdapterResult(success=False, error=f"trading_stop failed: {exc}")
            return AdapterResult(success=True)

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
