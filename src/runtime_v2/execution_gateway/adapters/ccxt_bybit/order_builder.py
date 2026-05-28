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
        if hedge_mode and params.action in {"create_order", "trading_stop_move_sl"}:
            if int(params.extra_params.get("positionIdx", 0)) == 0:
                params.extra_params["positionIdx"] = self._position_idx_for_side(
                    payload.get("side")
                )
            # Bybit hedge mode uses positionIdx to identify the position side; reduceOnly
            # conflicts with positionIdx on the V5 API and must be removed for all order types.
            params.extra_params.pop("reduceOnly", None)
        return params

    @staticmethod
    def _position_idx_for_side(side: str | None) -> int:
        return 1 if side == "LONG" else 2

    def _dispatch(
        self, command_type: str, payload: dict, client_order_id: str
    ) -> BybitOrderParams:
        if command_type == "PLACE_ENTRY":
            return self._place_entry(payload, client_order_id)
        if command_type in {"CLOSE_PARTIAL", "CLOSE_FULL"}:
            return self._close_market(payload, client_order_id)
        if command_type == "CANCEL_PENDING_ENTRY":
            return self._cancel_pending_entry(payload, client_order_id)
        if command_type in {"MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP"}:
            return self._move_stop(command_type, payload)
        if command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL":
            return self._place_entry_with_attached_tpsl(payload, client_order_id)
        if command_type == "SET_POSITION_TPSL_FULL":
            return self._set_position_tpsl_full(payload)
        if command_type == "SET_POSITION_TPSL_PARTIAL":
            return self._set_position_tpsl_partial(payload)
        if command_type == "MOVE_POSITION_STOP":
            return self._move_position_stop(payload)
        if command_type == "REBUILD_PARTIAL_TPS":
            return self._rebuild_partial_tps(payload)
        raise ValueError(f"Unknown command_type: {command_type!r}")

    def _place_entry(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        entry_type = payload["entry_type"]
        order_type = entry_type.lower()
        price = float(payload["price"]) if entry_type == "LIMIT" and payload.get("price") else None

        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type=order_type,
            side=_ENTRY_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=price,
            order_link_id=client_order_id,
            extra_params={},
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
        order_link_id = payload.get("entry_client_order_id") or client_order_id
        return BybitOrderParams(
            action="cancel_by_link",
            symbol=payload["symbol"],
            order_link_id=order_link_id,
        )

    def _move_stop(self, command_type: str, payload: dict) -> BybitOrderParams:
        _ = command_type
        new_trigger_price = float(payload["new_stop_price"])

        protection_style = payload.get("protection_style", "standalone_order")
        if protection_style == "attached_full":
            return BybitOrderParams(
                action="trading_stop_move_sl",
                symbol=payload["symbol"],
                position_side=payload["side"],
                extra_params={
                    "positionIdx": int(payload.get("position_idx", 0)),
                    "stopLoss": str(new_trigger_price),
                },
            )

        return BybitOrderParams(
            action="edit_sl",
            symbol=payload["symbol"],
            new_trigger_price=new_trigger_price,
            position_side=payload["side"],
        )


    def _place_entry_with_attached_tpsl(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        entry_type = payload["entry_type"]
        price = float(payload["price"]) if entry_type == "LIMIT" and payload.get("price") else None
        tpsl = payload["attached_tpsl"]
        mode = tpsl.get("mode", "FULL")

        # positionIdx is not set here — build() sets it from hedge_mode kwarg, same as _place_entry
        extra: dict = {
            "slOrderType": "Market",
            "slTriggerBy": tpsl.get("sl_trigger_by", "MarkPrice"),
        }

        if mode == "SL_ONLY":
            extra["stopLoss"] = float(tpsl["stop_loss"])
        elif mode == "PARTIAL_TP":
            extra.update({
                "takeProfit": float(tpsl["take_profit"]),
                "stopLoss": float(tpsl["stop_loss"]),
                "tpslMode": "Partial",
                "tpOrderType": "Market",
                "tpTriggerBy": tpsl.get("tp_trigger_by", "MarkPrice"),
                "tpSize": str(float(tpsl["tp_qty"])),
            })
        else:  # "FULL"
            extra.update({
                "takeProfit": float(tpsl["take_profit"]),
                "stopLoss": float(tpsl["stop_loss"]),
                "tpslMode": "Full",
                "tpOrderType": "Market",
                "tpTriggerBy": tpsl.get("tp_trigger_by", "MarkPrice"),
            })

        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type=entry_type.lower(),
            side=_ENTRY_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=price,
            order_link_id=client_order_id,
            extra_params=extra,
        )

    def _set_position_tpsl_full(self, payload: dict) -> BybitOrderParams:
        return BybitOrderParams(
            action="trading_stop_full",
            symbol=payload["symbol"],
            position_side=payload["side"],
            extra_params={
                "positionIdx": int(payload.get("position_idx", 0)),
                "tpslMode": "Full",
                "takeProfit": str(float(payload["take_profit"])),
                "stopLoss": str(float(payload["stop_loss"])),
                "tpTriggerBy": payload.get("tp_trigger_by", "MarkPrice"),
                "slTriggerBy": payload.get("sl_trigger_by", "MarkPrice"),
                "tpOrderType": "Market",
                "slOrderType": "Market",
            },
        )

    def _set_position_tpsl_partial(self, payload: dict) -> BybitOrderParams:
        tp_order_type = payload.get("tp_order_type", "Limit")
        preserve_sl = bool(payload.get("preserve_sl", False))
        extra: dict = {
            "positionIdx": int(payload.get("position_idx", 0)),
            "tpslMode": "Partial",
            "takeProfit": str(float(payload["take_profit"])),
            "tpSize": str(float(payload["tp_size"])),
            "tpOrderType": tp_order_type,
            "tpTriggerBy": payload.get("tp_trigger_by", "MarkPrice"),
        }
        if not preserve_sl:
            extra["stopLoss"] = str(float(payload["stop_loss"]))
            extra["slSize"] = str(float(payload["sl_size"]))
            extra["slOrderType"] = payload.get("sl_order_type", "Market")
            extra["slTriggerBy"] = payload.get("sl_trigger_by", "MarkPrice")
        if tp_order_type == "Limit" and payload.get("tp_limit_price"):
            extra["tpLimitPrice"] = str(float(payload["tp_limit_price"]))
        return BybitOrderParams(
            action="trading_stop_partial",
            symbol=payload["symbol"],
            position_side=payload["side"],
            extra_params=extra,
        )

    def _move_position_stop(self, payload: dict) -> BybitOrderParams:
        return BybitOrderParams(
            action="trading_stop_move_sl",
            symbol=payload["symbol"],
            position_side=payload["side"],
            extra_params={
                "positionIdx": int(payload.get("position_idx", 0)),
                "stopLoss": str(float(payload["new_stop_loss"])),
            },
        )

    def _rebuild_partial_tps(self, payload: dict) -> BybitOrderParams:
        return BybitOrderParams(
            action="rebuild_partial_tps",
            symbol=payload["symbol"],
            position_side=payload["side"],
            extra_params={
                "position_idx": int(payload.get("position_idx", 0)),
                "preserve_sl": bool(payload.get("preserve_sl", True)),
                "preserve_full_tp": bool(payload.get("preserve_full_tp", True)),
                "tps": payload["tps"],
            },
        )


__all__ = ["BybitOrderBuilder", "BybitOrderParams"]
