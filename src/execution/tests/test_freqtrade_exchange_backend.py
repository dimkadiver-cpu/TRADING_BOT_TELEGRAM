"""Tests for FreqtradeExchangeBackend adapter (GAP-03)."""

from __future__ import annotations

from typing import Any

import pytest

from src.execution.freqtrade_exchange_backend import FreqtradeExchangeBackend, _normalize_order


# ---------------------------------------------------------------------------
# Fake freqtrade exchange
# ---------------------------------------------------------------------------


class _FakeFreqtradeExchange:
    """Minimal fake that mimics the freqtrade exchange API used by the backend."""

    def __init__(self) -> None:
        self.created_orders: list[dict[str, Any]] = []
        self.cancelled_order_ids: list[str] = []
        self.open_orders_by_pair: dict[str, list[dict[str, Any]]] = {}
        self.positions: dict[str, dict[str, Any]] = {}

    def create_order(
        self,
        pair: str,
        ordertype: str,
        side: str,
        amount: float,
        rate: float | None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        client_id = params.get("clientOrderId", f"cid-{len(self.created_orders)}")
        order = {
            "id": f"ex-{client_id}",
            "clientOrderId": client_id,
            "symbol": pair,
            "side": side,
            "type": ordertype,
            "amount": amount,
            "price": rate,
            "stopPrice": params.get("stopPrice"),
            "reduceOnly": params.get("reduceOnly", False),
            "status": "open",
        }
        self.created_orders.append(order)
        self.open_orders_by_pair.setdefault(pair, []).append(order)
        return order

    def cancel_order(self, order_id: str, pair: str = "") -> dict[str, Any]:
        self.cancelled_order_ids.append(order_id)
        for p_orders in self.open_orders_by_pair.values():
            for i, o in enumerate(p_orders):
                if o["id"] == order_id:
                    p_orders.pop(i)
                    break
        return {"id": order_id, "status": "canceled"}

    def fetch_open_orders(self, pair: str, since: int | None = None, params: dict | None = None) -> list[dict[str, Any]]:
        return list(self.open_orders_by_pair.get(pair, []))

    def get_positions(self) -> dict[str, dict[str, Any]]:
        return dict(self.positions)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_backend(exchange: _FakeFreqtradeExchange | None = None) -> tuple[FreqtradeExchangeBackend, _FakeFreqtradeExchange]:
    ex = exchange or _FakeFreqtradeExchange()
    return FreqtradeExchangeBackend(ex), ex


# ---------------------------------------------------------------------------
# _normalize_order
# ---------------------------------------------------------------------------


class TestNormalizeOrder:
    def test_maps_ccxt_fields_to_canonical(self) -> None:
        raw = {
            "id": "ex-123",
            "clientOrderId": "mykey:SL:0",
            "side": "sell",
            "type": "stop",
            "amount": 2.0,
            "price": None,
            "stopPrice": 57000.0,
            "reduceOnly": True,
            "status": "open",
        }
        result = _normalize_order(raw, symbol="BTCUSDT")
        assert result["exchange_order_id"] == "ex-123"
        assert result["client_order_id"] == "mykey:SL:0"
        assert result["symbol"] == "BTCUSDT"
        assert result["side"] == "SELL"
        assert result["order_type"] == "STOP"
        assert result["qty"] == 2.0
        assert result["trigger_price"] == 57000.0
        assert result["reduce_only"] is True
        assert result["status"] == "open"

    def test_client_order_id_fallback(self) -> None:
        raw = {"id": "ex-456", "side": "buy", "type": "limit", "amount": 1.0, "status": "open"}
        result = _normalize_order(raw, symbol="ETHUSDT", client_order_id="fallback-cid")
        assert result["client_order_id"] == "fallback-cid"

    def test_trigger_price_alternative_field_names(self) -> None:
        raw = {"id": "x", "side": "sell", "type": "stop", "amount": 1.0, "triggerPrice": 100.0, "status": "open"}
        assert _normalize_order(raw, symbol="X")["trigger_price"] == 100.0

    def test_non_dict_returns_empty(self) -> None:
        assert _normalize_order(None, symbol="X") == {}
        assert _normalize_order("string", symbol="X") == {}


# ---------------------------------------------------------------------------
# create_order
# ---------------------------------------------------------------------------


class TestCreateOrder:
    def test_creates_stop_order_on_exchange(self) -> None:
        backend, ex = _make_backend()
        result = backend.create_order(
            symbol="BTCUSDT",
            side="SELL",
            order_type="STOP",
            qty=2.0,
            trigger_price=57000.0,
            reduce_only=True,
            client_order_id="atk:SL:0",
        )
        assert len(ex.created_orders) == 1
        order = ex.created_orders[0]
        assert order["side"] == "sell"
        assert order["type"] == "stop"
        assert order["amount"] == 2.0
        assert order["stopPrice"] == 57000.0
        assert result["exchange_order_id"] == "ex-atk:SL:0"
        assert result["client_order_id"] == "atk:SL:0"

    def test_creates_limit_tp_order(self) -> None:
        backend, ex = _make_backend()
        backend.create_order(
            symbol="BTCUSDT",
            side="SELL",
            order_type="LIMIT",
            qty=0.6,
            price=65000.0,
            reduce_only=True,
            client_order_id="atk:TP:0",
        )
        order = ex.created_orders[0]
        assert order["type"] == "limit"
        assert order["price"] == 65000.0
        assert order["amount"] == 0.6

    def test_converts_symbol_to_freqtrade_pair(self) -> None:
        backend, ex = _make_backend()
        backend.create_order(symbol="ETHUSDT", side="SELL", order_type="LIMIT", qty=1.0, reduce_only=True)
        assert ex.created_orders[0]["symbol"] == "ETH/USDT:USDT"


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


class TestCancelOrder:
    def test_cancel_returns_true_on_success(self) -> None:
        backend, ex = _make_backend()
        ex.open_orders_by_pair["BTC/USDT:USDT"] = [
            {"id": "ex-abc", "clientOrderId": "atk:SL:0", "symbol": "BTC/USDT:USDT", "status": "open"}
        ]
        result = backend.cancel_order(exchange_order_id="ex-abc", symbol="BTCUSDT")
        assert result == {"cancelled": True}
        assert "ex-abc" in ex.cancelled_order_ids

    def test_cancel_returns_false_on_exception(self) -> None:
        class _BrokenExchange(_FakeFreqtradeExchange):
            def cancel_order(self, order_id: str, pair: str = "") -> dict[str, Any]:
                raise RuntimeError("network error")

        backend = FreqtradeExchangeBackend(_BrokenExchange())
        result = backend.cancel_order(exchange_order_id="ex-xyz", symbol="BTCUSDT")
        assert result == {"cancelled": False}


# ---------------------------------------------------------------------------
# fetch_open_orders
# ---------------------------------------------------------------------------


class TestFetchOpenOrders:
    def test_returns_normalized_orders(self) -> None:
        backend, ex = _make_backend()
        ex.open_orders_by_pair["BTC/USDT:USDT"] = [
            {
                "id": "ex-sl",
                "clientOrderId": "atk:SL:0",
                "symbol": "BTC/USDT:USDT",
                "side": "sell",
                "type": "stop",
                "amount": 2.0,
                "price": None,
                "stopPrice": 57000.0,
                "reduceOnly": True,
                "status": "open",
            },
        ]
        orders = backend.fetch_open_orders(symbol="BTCUSDT")
        assert len(orders) == 1
        assert orders[0]["exchange_order_id"] == "ex-sl"
        assert orders[0]["client_order_id"] == "atk:SL:0"
        assert orders[0]["symbol"] == "BTCUSDT"
        assert orders[0]["trigger_price"] == 57000.0

    def test_returns_empty_list_on_exception(self) -> None:
        class _BrokenExchange(_FakeFreqtradeExchange):
            def fetch_open_orders(self, pair: str, **kwargs: Any) -> list:
                raise RuntimeError("timeout")

        backend = FreqtradeExchangeBackend(_BrokenExchange())
        assert backend.fetch_open_orders(symbol="BTCUSDT") == []

    def test_returns_empty_list_when_no_orders(self) -> None:
        backend, _ = _make_backend()
        assert backend.fetch_open_orders(symbol="SOLUSDT") == []


# ---------------------------------------------------------------------------
# fetch_position
# ---------------------------------------------------------------------------


class TestFetchPosition:
    def test_returns_position_dict_format(self) -> None:
        backend, ex = _make_backend()
        ex.positions["BTC/USDT:USDT"] = {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 2.0,
            "entryPrice": 60000.0,
        }
        pos = backend.fetch_position(symbol="BTCUSDT")
        assert pos is not None
        assert pos["symbol"] == "BTCUSDT"
        assert pos["side"] == "long"
        assert pos["size"] == 2.0
        assert pos["entry_price"] == 60000.0

    def test_returns_none_when_position_size_zero(self) -> None:
        backend, ex = _make_backend()
        ex.positions["BTC/USDT:USDT"] = {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.0, "entryPrice": 60000.0}
        assert backend.fetch_position(symbol="BTCUSDT") is None

    def test_returns_none_when_symbol_not_found(self) -> None:
        backend, ex = _make_backend()
        ex.positions["ETH/USDT:USDT"] = {"symbol": "ETH/USDT:USDT", "side": "long", "contracts": 1.0, "entryPrice": 3000.0}
        assert backend.fetch_position(symbol="BTCUSDT") is None

    def test_returns_none_on_exception(self) -> None:
        class _BrokenExchange(_FakeFreqtradeExchange):
            def get_positions(self) -> dict:
                raise RuntimeError("connection reset")

        backend = FreqtradeExchangeBackend(_BrokenExchange())
        assert backend.fetch_position(symbol="BTCUSDT") is None

    def test_handles_list_format_positions(self) -> None:
        """Some freqtrade versions may return a list instead of dict."""
        class _ListPositionExchange(_FakeFreqtradeExchange):
            def get_positions(self) -> list:  # type: ignore[override]
                return [{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 1.5, "entryPrice": 55000.0}]

        backend = FreqtradeExchangeBackend(_ListPositionExchange())
        pos = backend.fetch_position(symbol="BTCUSDT")
        assert pos is not None
        assert pos["size"] == 1.5


# ---------------------------------------------------------------------------
# Integration: backend feeds gateway normalizer
# ---------------------------------------------------------------------------


class TestBackendGatewayIntegration:
    def test_fetch_open_orders_via_gateway(self) -> None:
        from src.execution.exchange_gateway import ExchangeGateway

        backend, ex = _make_backend()
        ex.open_orders_by_pair["BTC/USDT:USDT"] = [
            {
                "id": "ex-tp0",
                "clientOrderId": "atk:TP:0",
                "symbol": "BTC/USDT:USDT",
                "side": "sell",
                "type": "limit",
                "amount": 0.6,
                "price": 65000.0,
                "reduceOnly": True,
                "status": "open",
            }
        ]
        gateway = ExchangeGateway(backend)
        orders = gateway.fetch_open_orders(symbol="BTCUSDT")
        assert len(orders) == 1
        o = orders[0]
        assert o.exchange_order_id == "ex-tp0"
        assert o.client_order_id == "atk:TP:0"
        assert o.qty == 0.6
        assert o.price == 65000.0
        assert o.status == "OPEN"

    def test_fetch_position_via_gateway(self) -> None:
        from src.execution.exchange_gateway import ExchangeGateway

        backend, ex = _make_backend()
        ex.positions["BTC/USDT:USDT"] = {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 2.0,
            "entryPrice": 60000.0,
        }
        gateway = ExchangeGateway(backend)
        pos = gateway.fetch_position(symbol="BTCUSDT")
        assert pos is not None
        assert pos.size == 2.0
        assert pos.entry_price == 60000.0
