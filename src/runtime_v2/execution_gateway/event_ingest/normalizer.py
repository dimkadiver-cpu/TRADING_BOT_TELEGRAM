# src/runtime_v2/execution_gateway/event_ingest/normalizer.py
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(val: object) -> float | None:
    try:
        return float(val) if val is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _i(val: object) -> int | None:
    try:
        return int(val) if val is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _s(val: object) -> str | None:
    return str(val) if val is not None else None


def _ccxt_symbol_to_raw(symbol: str) -> str:
    """'PHA/USDT:USDT' → 'PHAUSDT'"""
    if "/" not in symbol:
        return symbol
    base, rest = symbol.split("/", 1)
    return base + rest.split(":")[0]


class EventNormalizer:
    """Converts raw CCXT dicts to ExchangeRawEvent. Zero business logic, zero DB."""

    def from_trade(self, trade: dict) -> ExchangeRawEvent | None:
        """From watchMyTrades / fetchMyTrades."""
        info = trade.get("info") or {}
        exec_id = _s(trade.get("id") or info.get("execId"))
        if not exec_id:
            return None
        symbol = _ccxt_symbol_to_raw(info.get("symbol") or trade.get("symbol") or "")
        side = _s(info.get("side") or trade.get("side") or "")
        if not symbol or not side:
            return None

        exec_time_ms = info.get("execTime")
        exchange_time = (
            datetime.fromtimestamp(int(exec_time_ms) / 1000, tz=timezone.utc).isoformat()
            if exec_time_ms else None
        )

        return ExchangeRawEvent(
            source_stream     = "watch_my_trades",
            exchange_event_id = exec_id,
            idempotency_key   = f"exec:{exec_id}",
            symbol            = symbol,
            side              = side,
            create_type       = _s(info.get("createType")),
            stop_order_type   = _s(info.get("stopOrderType")),
            exec_type         = _s(info.get("execType") or trade.get("type")),
            order_status      = None,
            order_link_id     = _s(info["orderLinkId"] if "orderLinkId" in info else trade.get("clientOrderId")),
            order_id          = _s(info.get("orderId") or trade.get("order")),
            seq               = _i(info.get("seq")),
            exec_price        = _f(info.get("execPrice") or trade.get("price")),
            exec_qty          = _f(info.get("execQty") or trade.get("amount")),
            closed_size       = _f(info.get("closedSize")),
            leaves_qty        = _f(info.get("leavesQty")),
            pos_qty           = _f(info.get("posQty")),
            exec_value        = _f(info.get("execValue")),
            exec_fee          = _f(info.get("execFee") or (trade.get("fee") or {}).get("cost")),
            fee_rate          = _f(info.get("feeRate")),
            cum_exec_qty      = _f(info.get("cumExecQty")),
            exchange_time     = exchange_time,
            received_at       = _now(),
            raw_info          = dict(info),
        )

    def from_order(self, order: dict) -> ExchangeRawEvent | None:
        """From watchOrders / fetchOpenOrders."""
        info = order.get("info") or {}
        order_id = _s(order.get("id") or info.get("orderId"))
        if not order_id:
            return None
        order_status = _s(
            order.get("status") or info.get("orderStatus") or ""
        )
        # Normalise ccxt status strings: "canceled" → "Cancelled"
        if order_status and order_status.lower() == "canceled":
            order_status = "Cancelled"
        symbol = _ccxt_symbol_to_raw(info.get("symbol") or order.get("symbol") or "")
        side = _s(info.get("side") or order.get("side") or "")
        if not symbol or not side:
            return None

        updated_time_ms = info.get("updatedTime")
        exchange_time = (
            datetime.fromtimestamp(int(updated_time_ms) / 1000, tz=timezone.utc).isoformat()
            if updated_time_ms else None
        )

        return ExchangeRawEvent(
            source_stream     = "watch_orders",
            exchange_event_id = order_id,
            idempotency_key   = f"order:{order_id}:{order_status}",
            symbol            = symbol,
            side              = side,
            create_type       = _s(info.get("createType")),
            stop_order_type   = _s(info.get("stopOrderType")),
            exec_type         = None,
            order_status      = order_status,
            order_link_id     = _s(info.get("orderLinkId") or order.get("clientOrderId")),
            order_id          = order_id,
            seq               = None,
            exec_price        = _f(order.get("average") or info.get("avgPrice")),
            exec_qty          = _f(order.get("filled") or info.get("cumExecQty")),
            closed_size       = _f(info.get("closedSize")),
            leaves_qty        = _f(info.get("leavesQty")),
            pos_qty           = None,
            exec_value        = _f(info.get("cumExecValue")),
            exec_fee          = _f(info.get("cumExecFee")),
            fee_rate          = None,
            cum_exec_qty      = _f(info.get("cumExecQty")),
            exchange_time     = exchange_time,
            received_at       = _now(),
            raw_info          = dict(info),
        )

    def from_position(self, position: dict) -> ExchangeRawEvent | None:
        """From watchPositions. Detects TP/SL field changes."""
        info = position.get("info") or {}
        symbol = _ccxt_symbol_to_raw(
            position.get("symbol") or info.get("symbol") or ""
        )
        side = _s(info.get("side") or position.get("side") or "")
        if not symbol or not side:
            return None

        seq = _i(info.get("seq"))
        updated_time_ms = info.get("updatedTime")
        exchange_time = (
            datetime.fromtimestamp(int(updated_time_ms) / 1000, tz=timezone.utc).isoformat()
            if updated_time_ms else None
        )
        seq_key = seq if seq is not None else (updated_time_ms or _now())

        return ExchangeRawEvent(
            source_stream        = "watch_positions",
            exchange_event_id    = f"pos:{symbol}:{side}:{seq_key}",
            idempotency_key      = f"pos:{symbol}:{side}:{seq_key}",
            symbol               = symbol,
            side                 = side,
            create_type          = None,
            stop_order_type      = None,
            exec_type            = None,
            order_status         = _s(info.get("positionStatus")),
            order_link_id        = None,
            order_id             = None,
            seq                  = seq,
            exec_price           = None,
            exec_qty             = None,
            closed_size          = None,
            leaves_qty           = None,
            pos_qty              = _f(info.get("size") or position.get("contracts")),
            exec_value           = None,
            exec_fee             = None,
            fee_rate             = None,
            cum_exec_qty         = None,
            position_take_profit = _f(info.get("takeProfit")),
            position_stop_loss   = _f(info.get("stopLoss")),
            exchange_time        = exchange_time,
            received_at          = _now(),
            raw_info             = dict(info),
        )

    def from_rest_trade(self, trade: dict) -> ExchangeRawEvent | None:
        """From fetchMyTrades (REST). Same as from_trade, different source/key."""
        raw = self.from_trade(trade)
        if raw is None:
            return None
        raw.source_stream   = "fetch_my_trades"
        raw.idempotency_key = f"rest_exec:{raw.exchange_event_id}"
        return raw
