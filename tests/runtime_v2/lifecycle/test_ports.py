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


def test_static_port_symbol_exists_bare_symbol_matches_usdt_future():
    # "HYPE" from Telegram must match "HYPEUSDT" loaded from exchange (HYPE/USDT:USDT ccxt key)
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    port = StaticExchangeDataPort(known_symbols=frozenset({"HYPE/USDT:USDT", "BTC/USDT:USDT"}))

    assert port.symbol_exists("acc_1", "HYPE") is True
    assert port.symbol_exists("acc_1", "BTC") is True
    assert port.symbol_exists("acc_1", "SOL") is False


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


def test_live_port_uses_routed_adapter_snapshots():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import (
        AccountRoutingEntry,
        AdapterConfig,
        ExecutionConfig,
        RawAccountSnapshot,
        RawMarketSnapshot,
    )
    from src.runtime_v2.lifecycle.live_exchange_data_port import LiveExchangeDataPort

    adapter = FakeAdapter(
        account_snapshot=RawAccountSnapshot(
            equity_usdt=1111.0,
            available_balance_usdt=999.0,
            total_margin_used_usdt=123.0,
            source="fake_live",
        ),
        market_snapshots={
            "BTCUSDT": RawMarketSnapshot(
                symbol="BTCUSDT",
                mark_price=50000.0,
                bid=49999.0,
                ask=50001.0,
                min_order_size=0.001,
                price_precision=1,
                qty_precision=3,
                source="fake_live",
            )
        },
    )
    config = ExecutionConfig(
        default_adapter="demo",
        account_routing={
            "default": AccountRoutingEntry(adapter="demo", execution_account_id="main"),
            "acc_1": AccountRoutingEntry(adapter="demo", execution_account_id="main"),
        },
        adapters={"demo": AdapterConfig(type="fake", mode="demo", connector="fake")},
    )

    port = LiveExchangeDataPort(
        execution_config=config,
        adapter_registry={"demo": adapter},
        known_symbols=frozenset({"BTC/USDT:USDT"}),
    )

    account = port.get_account_state("acc_1")
    market = port.get_symbol_market_state("acc_1", "BTCUSDT")

    assert account.equity_usdt == 1111.0
    assert account.available_balance_usdt == 999.0
    assert account.total_margin_used_usdt == 123.0
    assert account.source == "fake_live"
    assert market.mark_price == 50000.0
    assert market.bid == 49999.0
    assert market.ask == 50001.0
    assert market.min_order_size == 0.001
    assert market.price_precision == 1
    assert market.qty_precision == 3
    assert market.source == "fake_live"


def test_live_port_falls_back_to_static_defaults_when_adapter_has_no_snapshot():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import AccountRoutingEntry, AdapterConfig, ExecutionConfig
    from src.runtime_v2.lifecycle.live_exchange_data_port import LiveExchangeDataPort

    config = ExecutionConfig(
        default_adapter="demo",
        account_routing={
            "default": AccountRoutingEntry(adapter="demo", execution_account_id="main"),
            "acc_1": AccountRoutingEntry(adapter="demo", execution_account_id="main"),
        },
        adapters={"demo": AdapterConfig(type="fake", mode="demo", connector="fake")},
    )
    port = LiveExchangeDataPort(
        execution_config=config,
        adapter_registry={"demo": FakeAdapter()},
        known_symbols=frozenset({"ETH/USDT:USDT"}),
    )

    account = port.get_account_state("acc_1")
    market = port.get_symbol_market_state("acc_1", "ETHUSDT")

    assert account.account_id == "acc_1"
    assert account.source == "static_default"
    assert account.equity_usdt is None
    assert market.symbol == "ETHUSDT"
    assert market.source == "static_default"
    assert market.mark_price is None
