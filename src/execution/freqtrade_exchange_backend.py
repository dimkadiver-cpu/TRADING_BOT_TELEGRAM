"""Freqtrade exchange backend adapter for ExchangeGateway.

Wraps a freqtrade Exchange object so it can be used as an ExchangeGatewayBackend
by the ExchangeOrderManager and the reconciliation watchdog.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


class FreqtradeExchangeBackend:
    """Adapter: freqtrade Exchange → ExchangeGatewayBackend protocol.

    Converts between:
    - freqtrade / ccxt field names  ↔  our canonical field names
    - freqtrade pair format ("BTC/USDT:USDT")  ↔  canonical symbol ("BTCUSDT")
    """

    def __init__(self, freqtrade_exchange: Any) -> None:
        self._exchange = freqtrade_exchange

    # ------------------------------------------------------------------
    # ExchangeGatewayBackend protocol
    # ------------------------------------------------------------------

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
    ) -> Any:
        from src.execution.freqtrade_normalizer import canonical_symbol_to_freqtrade_pair

        pair = canonical_symbol_to_freqtrade_pair(symbol) or symbol

        params: dict[str, Any] = {}
        if trigger_price is not None and trigger_price > 0:
            params["stopPrice"] = trigger_price
        if client_order_id:
            params["clientOrderId"] = client_order_id

        raw = self._exchange.create_order(
            pair=pair,
            ordertype=order_type.lower(),
            side=side.lower(),
            amount=qty,
            rate=price or 0.0,
            leverage=1,
            reduceOnly=reduce_only,
            params=params if params else None,
        )
        return _normalize_order(raw, symbol=symbol, client_order_id=client_order_id)

    def cancel_order(self, *, exchange_order_id: str, symbol: str | None = None) -> Any:
        from src.execution.freqtrade_normalizer import canonical_symbol_to_freqtrade_pair

        pair = canonical_symbol_to_freqtrade_pair(symbol) if symbol else None
        try:
            self._exchange.cancel_order(order_id=exchange_order_id, pair=pair or "")
            return {"cancelled": True}
        except Exception as exc:
            _log.warning("cancel_order failed exchange_order_id=%s: %s", exchange_order_id, exc)
            return {"cancelled": False}

    def fetch_open_orders(self, *, symbol: str) -> list[Any]:
        from src.execution.freqtrade_normalizer import canonical_symbol_to_freqtrade_pair

        pair = canonical_symbol_to_freqtrade_pair(symbol) or symbol
        try:
            raw_orders = self._exchange.fetch_open_orders(pair=pair)
        except Exception as exc:
            _log.warning("fetch_open_orders failed symbol=%s: %s", symbol, exc)
            return []
        return [_normalize_order(o, symbol=symbol) for o in (raw_orders or [])]

    def fetch_position(self, *, symbol: str) -> Any:
        from src.execution.freqtrade_normalizer import canonical_symbol_to_freqtrade_pair

        pair = canonical_symbol_to_freqtrade_pair(symbol) or symbol
        try:
            raw = self._exchange.get_positions()
        except Exception as exc:
            _log.warning("fetch_position failed symbol=%s: %s", symbol, exc)
            return None

        # freqtrade returns either dict[symbol, pos] or list[pos]
        positions: list[Any] = list(raw.values()) if isinstance(raw, dict) else list(raw or [])

        for pos in positions:
            if not isinstance(pos, dict):
                continue
            pos_symbol = str(pos.get("symbol") or "")
            if pos_symbol.upper() not in {pair.upper(), symbol.upper()}:
                continue
            size = _float_or_none(pos.get("contracts") or pos.get("positionAmt"))
            if size is None or size <= 0:
                return None
            return {
                "symbol": symbol,
                "side": _string_or_none(pos.get("side")),
                "size": size,
                "entry_price": _float_or_none(pos.get("entryPrice") or pos.get("markPrice")),
            }
        return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _normalize_order(
    raw: Any,
    *,
    symbol: str,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """Map a ccxt order dict to the field names ExchangeGateway._normalize_order_payload expects."""
    if not isinstance(raw, dict):
        return {}
    return {
        "exchange_order_id": raw.get("id") or raw.get("exchange_order_id"),
        "client_order_id": (
            raw.get("clientOrderId")
            or raw.get("client_order_id")
            or client_order_id
        ),
        "symbol": symbol,
        "side": str(raw.get("side") or "").upper(),
        "order_type": str(raw.get("type") or raw.get("order_type") or "").upper(),
        "qty": float(raw.get("amount") or raw.get("qty") or 0.0),
        "price": _float_or_none(raw.get("price")),
        "trigger_price": _float_or_none(
            raw.get("stopPrice")
            or raw.get("triggerPrice")
            or raw.get("trigger_price")
            or raw.get("stop_price")
        ),
        "reduce_only": bool(raw.get("reduceOnly") or raw.get("reduce_only")),
        "status": str(raw.get("status") or "open"),
        "venue_status_raw": str(raw.get("status") or ""),
    }


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
