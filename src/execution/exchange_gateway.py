"""Exchange-backed gateway wrapper used by the execution manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ExchangeGatewayBackend(Protocol):
    """Backend contract hidden behind the exchange gateway."""

    def create_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: float | None = None,
        trigger_price: float | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> Any: ...

    def cancel_order(self, *, exchange_order_id: str, symbol: str | None = None) -> Any: ...

    def fetch_open_orders(self, *, symbol: str) -> list[Any]: ...

    def fetch_position(self, *, symbol: str) -> Any: ...

    def fetch_ticker(self, *, symbol: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class ExchangeOrder:
    exchange_order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    order_type: str
    qty: float
    price: float | None
    trigger_price: float | None
    reduce_only: bool
    status: str
    average_fill_price: float | None = None
    venue_status_raw: str | None = None
    raw_payload: Any | None = None


@dataclass(frozen=True, slots=True)
class ExchangePosition:
    symbol: str
    side: str | None
    size: float
    entry_price: float | None
    raw_payload: Any | None = None


class ExchangeGateway:
    """Single venue wrapper consumed by the rest of the execution layer."""

    def __init__(self, backend: ExchangeGatewayBackend) -> None:
        self._backend = backend

    def create_reduce_only_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        client_order_id: str | None = None,
    ) -> ExchangeOrder:
        payload = self._backend.create_order(
            symbol=symbol,
            side=side,
            order_type="LIMIT",
            qty=float(qty),
            price=float(price),
            trigger_price=None,
            reduce_only=True,
            client_order_id=client_order_id,
        )
        return self._normalize_order_payload(payload, default_client_order_id=client_order_id)

    def create_reduce_only_stop_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        trigger_price: float,
        client_order_id: str | None = None,
    ) -> ExchangeOrder:
        payload = self._backend.create_order(
            symbol=symbol,
            side=side,
            order_type="STOP",
            qty=float(qty),
            price=None,
            trigger_price=float(trigger_price),
            reduce_only=True,
            client_order_id=client_order_id,
        )
        return self._normalize_order_payload(payload, default_client_order_id=client_order_id)

    def create_entry_market_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        client_order_id: str | None = None,
    ) -> ExchangeOrder:
        payload = self._backend.create_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            qty=float(qty),
            price=None,
            trigger_price=None,
            reduce_only=False,
            client_order_id=client_order_id,
        )
        return self._normalize_order_payload(payload, default_client_order_id=client_order_id)

    def create_reduce_only_market_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        client_order_id: str | None = None,
    ) -> ExchangeOrder:
        payload = self._backend.create_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            qty=float(qty),
            price=None,
            trigger_price=None,
            reduce_only=True,
            client_order_id=client_order_id,
        )
        return self._normalize_order_payload(payload, default_client_order_id=client_order_id)

    def cancel_order(self, *, exchange_order_id: str, symbol: str | None = None) -> bool:
        payload = self._backend.cancel_order(
            exchange_order_id=exchange_order_id,
            symbol=symbol,
        )
        if isinstance(payload, bool):
            return payload
        if isinstance(payload, dict):
            cancelled = payload.get("cancelled")
            if isinstance(cancelled, bool):
                return cancelled
        return True

    def fetch_current_price(self, *, symbol: str) -> float | None:
        """Return the current market price for qty sizing, or None on failure."""
        try:
            ticker = self._backend.fetch_ticker(symbol=symbol)
        except Exception:
            return None
        if ticker is None:
            return None
        if isinstance(ticker, dict):
            price = (
                _float_or_none(ticker.get("last"))
                or _float_or_none(ticker.get("bid"))
                or _float_or_none(ticker.get("ask"))
            )
            return price if price is not None and price > 0 else None
        return None

    def fetch_open_orders(self, *, symbol: str) -> list[ExchangeOrder]:
        return [
            self._normalize_order_payload(item)
            for item in self._backend.fetch_open_orders(symbol=symbol)
        ]

    def fetch_position(self, *, symbol: str) -> ExchangePosition | None:
        payload = self._backend.fetch_position(symbol=symbol)
        if payload is None:
            return None
        if isinstance(payload, ExchangePosition):
            return payload
        if not isinstance(payload, dict):
            raise TypeError("exchange position payload must be ExchangePosition, dict, or None")
        return ExchangePosition(
            symbol=str(payload.get("symbol") or symbol),
            side=_string_or_none(payload.get("side")),
            size=float(payload.get("size") or 0.0),
            entry_price=_float_or_none(payload.get("entry_price")),
            raw_payload=payload,
        )

    @staticmethod
    def _normalize_order_payload(
        payload: Any,
        *,
        default_client_order_id: str | None = None,
    ) -> ExchangeOrder:
        if isinstance(payload, ExchangeOrder):
            return payload
        if not isinstance(payload, dict):
            raise TypeError("exchange order payload must be ExchangeOrder or dict")

        exchange_order_id = (
            payload.get("exchange_order_id")
            or payload.get("id")
            or payload.get("order_id")
        )
        if not exchange_order_id:
            raise ValueError("exchange order payload missing exchange_order_id")

        raw_status = _string_or_none(payload.get("status")) or _string_or_none(payload.get("venue_status_raw"))
        return ExchangeOrder(
            exchange_order_id=str(exchange_order_id),
            client_order_id=_string_or_none(payload.get("client_order_id")) or default_client_order_id,
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or ""),
            order_type=str(payload.get("order_type") or payload.get("type") or ""),
            qty=float(payload.get("qty") or payload.get("amount") or 0.0),
            price=_float_or_none(payload.get("price")),
            trigger_price=_float_or_none(payload.get("trigger_price") or payload.get("stop_price")),
            reduce_only=bool(payload.get("reduce_only", False)),
            status=_normalize_order_status(raw_status),
            average_fill_price=_float_or_none(payload.get("average")),
            venue_status_raw=raw_status,
            raw_payload=payload,
        )


def _normalize_order_status(raw_status: str | None) -> str:
    if not raw_status:
        return "OPEN"

    normalized = raw_status.strip().upper()
    mapping = {
        "NEW": "NEW",
        "CREATED": "NEW",
        "OPEN": "OPEN",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "PARTIALLYFILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
        "REJECTED": "REJECTED",
        "EXPIRED": "EXPIRED",
    }
    return mapping.get(normalized, "OPEN")


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
