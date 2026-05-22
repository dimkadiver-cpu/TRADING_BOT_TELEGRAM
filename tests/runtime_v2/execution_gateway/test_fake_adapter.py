from __future__ import annotations

from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter


def test_sync_protective_orders_is_immediately_filled():
    adapter = FakeAdapter()
    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT", "side": "LONG"},
        client_order_id="tsb:10:7:sync:1",
        execution_account_id="acc1",
        connector="fake",
    )
    assert result.success is True
    order = adapter.get_order_status(
        client_order_id="tsb:10:7:sync:1",
        execution_account_id="acc1",
    )
    assert order is not None
    assert order.is_filled is True


def test_normal_order_is_not_immediately_filled():
    adapter = FakeAdapter()
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT", "side": "LONG", "qty": 0.01, "price": 50000.0},
        client_order_id="tsb:10:1:entry:1",
        execution_account_id="acc1",
        connector="fake",
    )
    assert result.success is True
    order = adapter.get_order_status(
        client_order_id="tsb:10:1:entry:1",
        execution_account_id="acc1",
    )
    assert order is not None
    assert order.is_filled is False


def test_fake_adapter_fetch_mark_price_configured():
    adapter = FakeAdapter(mark_prices={"BTC/USDT": 50000.0})
    assert adapter.fetch_mark_price("BTC/USDT", "acc1") == 50000.0


def test_fake_adapter_fetch_mark_price_missing_returns_none():
    adapter = FakeAdapter()
    assert adapter.fetch_mark_price("BTC/USDT", "acc1") is None


def test_fake_adapter_set_mark_price():
    adapter = FakeAdapter()
    adapter.set_mark_price("ETH/USDT", 3000.0)
    assert adapter.fetch_mark_price("ETH/USDT", "acc1") == 3000.0
