"""
Gated integration tests against live Hummingbot demo stack + Bybit Main Demo.

Run with:
  RUN_HUMMINGBOT_DEMO_TESTS=1 \
  HUMMINGBOT_DEMO_API_URL=http://localhost:8001 \
  HUMMINGBOT_DEMO_CONNECTOR=bybit_perpetual_demo \
  HUMMINGBOT_DEMO_ACCOUNT=master_account \
  pytest tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py -v -s

Requirements before running:
  1. docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
  2. Hummingbot demo configured with Bybit Demo API keys
  3. bybit_perpetual_demo connector active in Hummingbot demo container
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_HUMMINGBOT_DEMO_TESTS"),
    reason="Set RUN_HUMMINGBOT_DEMO_TESTS=1 to run",
)

DEMO_URL = os.environ.get("HUMMINGBOT_DEMO_API_URL", "http://localhost:8001")
CONNECTOR = os.environ.get("HUMMINGBOT_DEMO_CONNECTOR", "bybit_perpetual_demo")
ACCOUNT = os.environ.get("HUMMINGBOT_DEMO_ACCOUNT", "master_account")
SECRET = os.environ.get("HUMMINGBOT_SECRET")

_TEST_SYMBOL = "BTC/USDT"
_TEST_CLIENT_ORDER_ID = "tsb:demo:9999:entry:1"


@pytest.fixture(scope="module")
def adapter():
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
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
    return HummingbotApiAdapter(
        base_url=DEMO_URL,
        connector=CONNECTOR,
        capabilities=caps,
        secret=SECRET,
    )


def test_01_api_reachable():
    import httpx
    resp = httpx.get(f"{DEMO_URL}/docs", timeout=5)
    assert resp.status_code == 200, f"Hummingbot demo API not reachable at {DEMO_URL}"


def test_02_connector_available(adapter):
    caps = adapter.get_capabilities()
    assert caps.place_entry is True
    assert caps.protective_stop_native is False


def test_03_set_leverage(adapter):
    adapter.set_leverage(_TEST_SYMBOL, 1, ACCOUNT)


def test_04_place_entry_limit(adapter):
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={
            "symbol": _TEST_SYMBOL,
            "side": "LONG",
            "entry_type": "LIMIT",
            "price": 1.0,
            "qty": 0.001,
            "sequence": 1,
        },
        client_order_id=_TEST_CLIENT_ORDER_ID,
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
    assert result.success, f"place_order failed: {result.error}"


def test_05_get_order_status(adapter):
    time.sleep(1)
    status = adapter.get_order_status(
        client_order_id=_TEST_CLIENT_ORDER_ID,
        execution_account_id=ACCOUNT,
    )
    assert status is not None, "Order not found after place"
    assert status.client_order_id == _TEST_CLIENT_ORDER_ID


def test_06_cancel_order(adapter):
    result = adapter.cancel_order(
        client_order_id=_TEST_CLIENT_ORDER_ID,
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
    assert result.success, f"cancel_order failed: {result.error}"


def test_07_get_position(adapter):
    qty = adapter.get_position_qty(
        symbol=_TEST_SYMBOL,
        side="LONG",
        execution_account_id=ACCOUNT,
    )
    assert qty is None or qty >= 0.0
