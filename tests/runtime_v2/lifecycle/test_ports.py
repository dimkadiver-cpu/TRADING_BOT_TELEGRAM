from __future__ import annotations

from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_static_port_returns_default_account():
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    port = StaticExchangeDataPort()
    snap = port.get_account_state("acc_1")
    assert snap.account_id == "acc_1"
    assert snap.source == "static_default"


def test_static_port_returns_configured_account():
    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    acc = AccountStateSnapshot(
        account_id="acc_1", equity_usdt=10000.0,
        captured_at=_now(), source="static_test",
    )
    port = StaticExchangeDataPort(account_snapshot=acc)
    snap = port.get_account_state("acc_1")
    assert snap.equity_usdt == 10000.0


def test_static_port_returns_configured_market():
    from src.runtime_v2.lifecycle.ports import SymbolMarketSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    mkt = SymbolMarketSnapshot(
        symbol="BTC/USDT", mark_price=50000.0,
        captured_at=_now(), source="static_test",
    )
    port = StaticExchangeDataPort(market_snapshots={"BTC/USDT": mkt})
    snap = port.get_symbol_market_state("acc_1", "BTC/USDT")
    assert snap.mark_price == 50000.0
    assert snap.symbol == "BTCUSDT"


def test_static_port_returns_default_market_for_unknown_symbol():
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    port = StaticExchangeDataPort()
    snap = port.get_symbol_market_state("acc_1", "ETH/USDT")
    assert snap.symbol == "ETHUSDT"
    assert snap.mark_price is None


def test_static_port_get_symbol_market_state_accepts_raw_symbol():
    from src.runtime_v2.lifecycle.ports import SymbolMarketSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    mkt = SymbolMarketSnapshot(
        symbol="BTC/USDT", mark_price=50000.0,
        captured_at=_now(), source="static_test",
    )
    port = StaticExchangeDataPort(market_snapshots={"BTC/USDT": mkt})

    snap = port.get_symbol_market_state("acc_1", "BTCUSDT")

    assert snap.mark_price == 50000.0
    assert snap.symbol == "BTCUSDT"


def test_static_port_symbol_exists_accepts_raw_symbol():
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    port = StaticExchangeDataPort(known_symbols=frozenset({"BTC/USDT", "ETH/USDT"}))

    assert port.symbol_exists("acc_1", "BTCUSDT") is True
    assert port.symbol_exists("acc_1", "ETHUSDT") is True
    assert port.symbol_exists("acc_1", "SOLUSDT") is False


def test_static_port_filters_orders_by_symbol():
    from src.runtime_v2.lifecycle.ports import OrderSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    orders = [
        OrderSnapshot(symbol="BTC/USDT", side="LONG", order_role="ENTRY", status="OPEN"),
        OrderSnapshot(symbol="ETH/USDT", side="LONG", order_role="ENTRY", status="OPEN"),
    ]
    port = StaticExchangeDataPort(orders=orders)
    assert len(port.get_open_orders("acc_1", "BTC/USDT")) == 1
    assert len(port.get_open_orders("acc_1")) == 2


def test_static_port_returns_position():
    from src.runtime_v2.lifecycle.ports import PositionSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    pos = PositionSnapshot(symbol="BTC/USDT", side="LONG", status="OPEN", qty_open=0.1)
    port = StaticExchangeDataPort(positions=[pos])
    assert port.get_open_position("acc_1", "BTC/USDT", "LONG") is pos
    assert port.get_open_position("acc_1", "BTC/USDT", "SHORT") is None
