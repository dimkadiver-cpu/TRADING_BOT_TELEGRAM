# tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py
"""
Test gated — girano solo con Hummingbot API attiva.
Eseguire con: RUN_HUMMINGBOT_API_TESTS=1 HUMMINGBOT_API_URL=http://localhost:8000 pytest
"""
from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_HUMMINGBOT_API_TESTS"),
    reason="Set RUN_HUMMINGBOT_API_TESTS=1 to run",
)

HUMMINGBOT_URL = os.environ.get("HUMMINGBOT_API_URL", "http://localhost:8000")
CONNECTOR = os.environ.get("HUMMINGBOT_CONNECTOR", "bybit_perpetual_testnet")
ACCOUNT = os.environ.get("HUMMINGBOT_ACCOUNT", "master_account")
SECRET = os.environ.get("HUMMINGBOT_SECRET")


@pytest.fixture
def adapter():
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter
    return HummingbotApiPaperAdapter(base_url=HUMMINGBOT_URL, connector=CONNECTOR, secret=SECRET)


def test_api_reachable(adapter):
    import httpx
    resp = httpx.get(f"{HUMMINGBOT_URL}/docs", timeout=5)
    assert resp.status_code == 200


def test_capabilities_declared(adapter):
    caps = adapter.get_capabilities()
    assert caps.place_entry is True
    assert caps.executor_position is False


def test_place_and_query_order(adapter):
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT", "side": "LONG",
                 "entry_type": "LIMIT", "price": 1.0, "qty": 0.001, "sequence": 1},
        client_order_id="tsb:9999:9999:entry:1",
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
    assert result.success
    status = adapter.get_order_status(
        client_order_id="tsb:9999:9999:entry:1",
        execution_account_id=ACCOUNT,
    )
    assert status is not None
    adapter.cancel_order(
        client_order_id="tsb:9999:9999:entry:1",
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
