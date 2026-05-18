from __future__ import annotations

from src.runtime_v2.execution_gateway.models import RawAdapterOrder


class StatusMapper:
    _STATUS_MAP = {
        "open": "OPEN",
        "partially_filled": "OPEN",
        "closed": "FILLED",
        "canceled": "CANCELLED",
        "cancelled": "CANCELLED",
        "expired": "CANCELLED",
        "rejected": "FAILED",
    }

    @classmethod
    def map(cls, ccxt_order: dict, client_order_id: str | None = None) -> RawAdapterOrder:
        average = ccxt_order.get("average")

        return RawAdapterOrder(
            client_order_id=client_order_id or ccxt_order.get("clientOrderId"),
            exchange_order_id=ccxt_order.get("id"),
            status=cls._STATUS_MAP.get(ccxt_order.get("status"), "OPEN"),
            filled_qty=ccxt_order.get("filled", 0.0),
            average_price=float(average) if average else None,
        )


__all__ = ["StatusMapper"]
