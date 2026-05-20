from __future__ import annotations

import logging

from src.runtime_v2.execution_gateway.models import RawAdapterOrder

logger = logging.getLogger(__name__)


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
        mapped_status = StatusMapper._STATUS_MAP.get(raw_status, "OPEN")

        cancel_reason: str | None = None
        if mapped_status == "CANCELLED":
            info = ccxt_order.get("info") or {}
            cancel_type = str(info.get("cancelType") or "").strip()
            reject_reason = str(info.get("rejectReason") or "").strip()
            parts = [p for p in (cancel_type, reject_reason) if p and p != "UNKNOWN"]
            cancel_reason = "|".join(parts) if parts else None
            logger.warning(
                "order CANCELLED coid=%s cancelType=%r rejectReason=%r",
                client_order_id or ccxt_order.get("clientOrderId"),
                cancel_type or None,
                reject_reason or None,
            )

        return RawAdapterOrder(
            client_order_id=client_order_id or str(ccxt_order.get("clientOrderId") or ""),
            exchange_order_id=str(ccxt_order.get("id") or ""),
            status=mapped_status,
            filled_qty=float(ccxt_order.get("filled") or 0.0),
            average_price=float(avg) if avg else None,
            cancel_reason=cancel_reason,
        )


__all__ = ["StatusMapper"]
