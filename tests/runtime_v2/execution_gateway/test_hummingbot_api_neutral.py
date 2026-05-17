# tests/runtime_v2/execution_gateway/test_hummingbot_api_neutral.py
from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities


def test_adapter_uses_capabilities_from_config():
    caps = AdapterCapabilities(
        place_entry=True,
        protective_stop_native=False,
        take_profit_native=False,
        bracket_order=False,
        move_stop=False,
        close_partial=True,
        close_full=True,
        executor_position=False,
    )
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
        capabilities=caps,
    )
    assert adapter.get_capabilities().protective_stop_native is False
    assert adapter.get_capabilities().close_full is True


def test_adapter_default_capabilities_when_none_passed():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8000",
        connector="bybit_perpetual_testnet",
    )
    assert adapter.get_capabilities().place_entry is True
    assert adapter.get_capabilities().protective_stop_native is True


def test_auth_headers_no_secret():
    headers = HummingbotApiAdapter._auth_headers(None)
    assert headers == {}


def test_auth_headers_bearer():
    headers = HummingbotApiAdapter._auth_headers("mytoken")
    assert headers == {"Authorization": "Bearer mytoken"}


def test_auth_headers_basic():
    headers = HummingbotApiAdapter._auth_headers("user:pass")
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")


def test_build_order_body_place_entry_limit():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
    )
    body = adapter._build_order_body(
        "PLACE_ENTRY",
        {"symbol": "BTC/USDT", "side": "LONG", "entry_type": "LIMIT", "price": 50000.0, "qty": 0.01},
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="master_account",
    )
    assert body["connector_name"] == "bybit_perpetual_demo"
    assert body["trade_type"] == "BUY"
    assert body["order_type"] == "LIMIT"
    assert body["price"] == 50000.0
    assert body["position_action"] == "OPEN"


def test_build_order_body_place_entry_market():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
    )
    body = adapter._build_order_body(
        "PLACE_ENTRY",
        {"symbol": "ETH/USDT", "side": "SHORT", "entry_type": "MARKET", "qty": 0.1},
        client_order_id="tsb:2:2:entry:1",
        execution_account_id="master_account",
    )
    assert body["trade_type"] == "SELL"
    assert body["order_type"] == "MARKET"
    assert "price" not in body


def test_build_order_body_close_full():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
    )
    body = adapter._build_order_body(
        "CLOSE_FULL",
        {"symbol": "BTC/USDT", "side": "LONG", "qty": 0.01},
        client_order_id="tsb:1:5:entry:1",
        execution_account_id="master_account",
    )
    assert body["trade_type"] == "SELL"
    assert body["order_type"] == "MARKET"
    assert body["reduce_only"] is True
    assert body["position_action"] == "CLOSE"


def test_backward_compat_alias():
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter
    assert HummingbotApiPaperAdapter is HummingbotApiAdapter
