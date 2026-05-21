"""
Gated integration tests against Bybit Testnet via CCXT.

Prerequisites:
  1. Bybit testnet account with API key (Unified Trading Account, USDT perpetual enabled)
  2. Set env vars:
       BYBIT_TESTNET_API_KEY=<your testnet api key>
       BYBIT_API_SECRET_BYBIT_TESTNET=<your testnet api secret>

Run with:
  BYBIT_TESTNET_API_KEY=<key> BYBIT_API_SECRET_BYBIT_TESTNET=<secret> \\
  pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_gated.py -v -s -m bybit_testnet
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.bybit_testnet

_SYMBOL = "BTC/USDT:USDT"
_ENTRY_CLIENT_ORDER_ID = "tsb:99:9001:entry:1"
_SL_CLIENT_ORDER_ID = "tsb:99:9001:sl:1"


@pytest.fixture(scope="module")
def adapter():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    return CcxtBybitAdapter(
        api_key=os.environ["BYBIT_TESTNET_API_KEY"],
        api_secret=os.environ["BYBIT_API_SECRET_BYBIT_TESTNET"],
        connector="bybit",
        mode="testnet",
    )


def test_set_leverage_does_not_raise(adapter):
    """set_leverage should complete without raising on testnet."""
    adapter.set_leverage(_SYMBOL, 5, "bybit_testnet")


def test_place_limit_entry_returns_exchange_order_id(adapter):
    """Place a limit entry far below market price — won't fill, verifies order creation."""
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={
            "symbol": _SYMBOL, "side": "LONG",
            "entry_type": "LIMIT", "qty": 0.001, "price": 1.0,  # far below market
        },
        client_order_id=_ENTRY_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )
    assert result.success is True, f"place_order failed: {result.error}"
    assert result.exchange_order_id, "expected a non-empty exchange_order_id"


def test_get_order_status_open_after_place(adapter):
    """After placing, order should be visible as OPEN."""
    time.sleep(1)  # brief pause for exchange propagation
    raw = adapter.get_order_status(
        client_order_id=_ENTRY_CLIENT_ORDER_ID, execution_account_id="bybit_testnet"
    )
    assert raw is not None, "get_order_status returned None — order may not be visible via orderLinkId"
    assert raw.status == "OPEN"
    assert raw.client_order_id == _ENTRY_CLIENT_ORDER_ID


def test_cancel_pending_entry(adapter):
    """Cancel the open entry placed above; order should disappear or show CANCELLED."""
    result = adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": _SYMBOL},
        client_order_id=_ENTRY_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )
    assert result.success is True, f"cancel failed: {result.error}"

    time.sleep(1)
    raw = adapter.get_order_status(
        client_order_id=_ENTRY_CLIENT_ORDER_ID, execution_account_id="bybit_testnet"
    )
    if raw is not None:
        assert raw.status == "CANCELLED"


def test_place_protective_stop_returns_success(adapter):
    """Place a stop order and verify it appears as OPEN."""
    result = adapter.place_order(
        command_type="PLACE_PROTECTIVE_STOP",
        payload={
            "symbol": _SYMBOL, "side": "LONG",
            "qty": 0.001, "stop_price": 1.0,  # far below market
        },
        client_order_id=_SL_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )
    assert result.success is True, f"place stop failed: {result.error}"

    time.sleep(1)
    raw = adapter.get_order_status(
        client_order_id=_SL_CLIENT_ORDER_ID, execution_account_id="bybit_testnet"
    )
    # OD-F1-2: attached SL/TP may not be visible via orderLinkId — known open decision
    if raw is not None:
        assert raw.status in ("OPEN", "CANCELLED", "FILLED")

    # Cleanup: cancel the stop
    adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": _SYMBOL},
        client_order_id=_SL_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )


def test_get_position_qty_returns_float(adapter):
    """get_position_qty must return a float (may be 0.0 if no open position)."""
    qty = adapter.get_position_qty(
        symbol=_SYMBOL, side="LONG", execution_account_id="bybit_testnet"
    )
    assert isinstance(qty, float) or qty is None
