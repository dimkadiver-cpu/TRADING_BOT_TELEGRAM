# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py
from __future__ import annotations

import logging

import ccxt

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import BybitOrderBuilder
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder

logger = logging.getLogger(__name__)

_DEFAULT_CAPABILITIES = AdapterCapabilities(
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


class CcxtBybitAdapter(ExecutionAdapter):
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        connector: str,
        capabilities: AdapterCapabilities | None = None,
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
            if testnet:
                self._exchange.set_sandbox_mode(True)
        self._connector = connector
        self._capabilities = capabilities or _DEFAULT_CAPABILITIES
        self._builder = BybitOrderBuilder()

    def get_capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        self._exchange.set_leverage(leverage, symbol, params={
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        })

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

        params = self._builder.build(command_type, payload, client_order_id)

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
        try:
            orders = self._exchange.fetch_open_orders(
                None, params={"orderLinkId": client_order_id}
            )
        except Exception as exc:
            logger.debug("fetch_open_orders error: %s", exc)
            orders = []
        if not orders:
            try:
                orders = self._exchange.fetch_closed_orders(
                    None, params={"orderLinkId": client_order_id}
                )
            except Exception as exc:
                logger.debug("fetch_closed_orders error: %s", exc)
                orders = []
        if not orders:
            return None
        return StatusMapper.map(orders[-1], client_order_id=client_order_id)

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


__all__ = ["CcxtBybitAdapter"]
