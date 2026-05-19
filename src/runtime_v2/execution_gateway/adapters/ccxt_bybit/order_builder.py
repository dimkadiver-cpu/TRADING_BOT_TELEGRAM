from __future__ import annotations

from dataclasses import dataclass, field


_ENTRY_SIDE = {"LONG": "buy", "SHORT": "sell"}
_CLOSE_SIDE = {"LONG": "sell", "SHORT": "buy"}


@dataclass
class BybitOrderParams:
    action: str
    symbol: str = ""
    order_type: str = ""
    side: str = ""
    amount: float = 0.0
    price: float | None = None
    order_link_id: str = ""
    extra_params: dict = field(default_factory=dict)
    new_trigger_price: float | None = None
    position_side: str = ""


class BybitOrderBuilder:
    def build(
        self,
        command_type: str,
        payload: dict,
        client_order_id: str,
        *,
        hedge_mode: bool = False,
    ) -> BybitOrderParams:
        params = self._dispatch(command_type, payload, client_order_id)
        if hedge_mode and params.action == "create_order":
            params.extra_params["positionIdx"] = (
                1 if payload.get("side") == "LONG" else 2
            )
        return params

    def _dispatch(
        self, command_type: str, payload: dict, client_order_id: str
    ) -> BybitOrderParams:
        if command_type == "PLACE_ENTRY":
            return self._place_entry(payload, client_order_id)
        if command_type == "PLACE_PROTECTIVE_STOP":
            return self._place_protective_stop(payload, client_order_id)
        if command_type == "PLACE_TAKE_PROFIT":
            return self._place_take_profit(payload, client_order_id)
        if command_type in {"CLOSE_PARTIAL", "CLOSE_FULL"}:
            return self._close_market(payload, client_order_id)
        if command_type == "CANCEL_PENDING_ENTRY":
            return self._cancel_pending_entry(payload, client_order_id)
        if command_type in {"MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP"}:
            return self._move_stop(command_type, payload)
        if command_type == "SYNC_PROTECTIVE_ORDERS":
            return BybitOrderParams(
                action="amend_sl_qty",
                symbol=payload["symbol"],
                position_side=payload["side"],
            )
        raise ValueError(f"Unknown command_type: {command_type!r}")

    def _place_entry(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        entry_type = payload["entry_type"]
        order_type = entry_type.lower()
        price = float(payload["price"]) if entry_type == "LIMIT" and payload.get("price") else None
        extra_params = self._mode_c_params(payload) if payload.get("native_attached_tpsl") else {}

        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type=order_type,
            side=_ENTRY_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=price,
            order_link_id=client_order_id,
            extra_params=extra_params,
        )

    def _mode_c_params(self, payload: dict) -> dict:
        tp_count = int(payload.get("tp_count", 1))
        total_qty = float(payload["qty"])
        tp_size = float(payload["attached_take_profit_qty"]) if tp_count > 1 else total_qty

        return {
            "takeProfit": float(payload["attached_take_profit"]),
            "stopLoss": float(payload["attached_stop_loss"]),
            "tpslMode": "Partial",
            "tpOrderType": "Limit",
            "tpLimitPrice": float(payload["attached_take_profit"]),
            "tpSize": tp_size,
        }

    def _place_protective_stop(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type="stop",
            side=_CLOSE_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=None,
            order_link_id=client_order_id,
            extra_params={
                "reduceOnly": True,
                "triggerPrice": float(payload["stop_price"]),
                "triggerBy": "LastPrice",
            },
        )

    def _place_take_profit(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type="limit",
            side=_CLOSE_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=float(payload["price"]),
            order_link_id=client_order_id,
            extra_params={"reduceOnly": True},
        )

    def _close_market(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type="market",
            side=_CLOSE_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=None,
            order_link_id=client_order_id,
            extra_params={"reduceOnly": True},
        )

    def _cancel_pending_entry(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="cancel_by_link",
            symbol=payload["symbol"],
            order_link_id=client_order_id,
        )

    def _move_stop(self, command_type: str, payload: dict) -> BybitOrderParams:
        if command_type == "MOVE_STOP_TO_BREAKEVEN":
            target_price = float(payload["target_price"])
            buffer_pct = float(payload.get("be_buffer_pct") or 0.0)
            if payload["side"] == "LONG":
                new_trigger_price = target_price * (1 + buffer_pct)
            else:
                new_trigger_price = target_price * (1 - buffer_pct)
        else:
            new_trigger_price = float(payload["new_stop_price"])

        return BybitOrderParams(
            action="edit_sl",
            symbol=payload["symbol"],
            new_trigger_price=new_trigger_price,
            position_side=payload["side"],
        )


__all__ = ["BybitOrderBuilder", "BybitOrderParams"]
