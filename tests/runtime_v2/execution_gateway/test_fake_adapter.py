from __future__ import annotations

from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter


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


def test_fake_fetch_recent_reduce_trades_empty_by_default():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    trades = a.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert trades == []


def test_fake_simulate_reduce_trade_returned_by_fetch():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    a.simulate_reduce_trade(
        symbol="PHAUSDT", side="SHORT",
        price=0.05754, amount=3871.5, trade_id="t-001",
    )
    trades = a.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert len(trades) == 1
    assert trades[0].trade_id == "t-001"
    assert trades[0].price == 0.05754
    assert trades[0].amount == 3871.5
    assert trades[0].reduce_only is True


def test_fake_simulate_reduce_trade_isolated_by_symbol_side():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    a.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05, 100.0, "t-pha")
    a.simulate_reduce_trade("BTCUSDT", "LONG",  50000.0, 0.01, "t-btc")
    assert len(a.fetch_recent_reduce_trades(symbol="PHAUSDT", side="SHORT", execution_account_id="acc")) == 1
    assert len(a.fetch_recent_reduce_trades(symbol="BTCUSDT", side="LONG",  execution_account_id="acc")) == 1
    assert len(a.fetch_recent_reduce_trades(symbol="ETHUSDT", side="LONG",  execution_account_id="acc")) == 0


def test_fake_fetch_position_details_none_by_default():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    result = a.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert result is None


def test_fake_fetch_position_details_returns_preset():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    a = FakeAdapter()
    a.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=3871.5,
        take_profit=0.05373, stop_loss=0.06908,
    ))
    pos = a.fetch_position_details(symbol="PHAUSDT", side="SHORT", execution_account_id="acc")
    assert pos is not None
    assert pos.take_profit == 0.05373
    assert pos.qty == 3871.5
