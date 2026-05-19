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

    @staticmethod
    def map(ccxt_order: dict, *, client_order_id: str = "") -> RawAdapterOrder:
        raw_status = str(ccxt_order.get("status") or "open").lower()
        avg = ccxt_order.get("average")

        return RawAdapterOrder(
            client_order_id=client_order_id or str(ccxt_order.get("clientOrderId") or ""),
            exchange_order_id=str(ccxt_order.get("id") or ""),
            status=StatusMapper._STATUS_MAP.get(raw_status, "OPEN"),
            filled_qty=float(ccxt_order.get("filled") or 0.0),
            average_price=float(avg) if avg else None,
        )


__all__ = ["StatusMapper"]
