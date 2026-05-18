from __future__ import annotations

import pytest

from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import (
    BybitOrderBuilder,
    BybitOrderParams,
)


def _builder() -> BybitOrderBuilder:
    return BybitOrderBuilder()


@pytest.mark.parametrize(
    ("side", "qty", "price", "expected_side", "client_order_id"),
    [
        ("LONG", 0.01, 50000.0, "buy", "tsb:10:5:entry:1"),
        ("SHORT", 0.02, 48000.0, "sell", "tsb:10:5:entry:2"),
    ],
)
def test_place_entry_limit_builds_create_order(
    side: str,
    qty: float,
    price: float,
    expected_side: str,
    client_order_id: str,
) -> None:
    params = _builder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": side,
            "entry_type": "LIMIT",
            "qty": qty,
            "price": price,
        },
        client_order_id,
    )

    assert isinstance(params, BybitOrderParams)
    assert params.action == "create_order"
    assert params.order_type == "limit"
    assert params.side == expected_side
    assert params.symbol == "BTC/USDT:USDT"
    assert params.amount == qty
    assert params.price == price
    assert params.order_link_id == client_order_id


@pytest.mark.parametrize(
    ("side", "qty", "expected_side", "client_order_id"),
    [
        ("LONG", 0.01, "buy", "tsb:10:5:entry:3"),
        ("SHORT", 0.02, "sell", "tsb:10:5:entry:4"),
    ],
)
def test_place_entry_market_builds_create_order_without_price(
    side: str,
    qty: float,
    expected_side: str,
    client_order_id: str,
) -> None:
    params = _builder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": side,
            "entry_type": "MARKET",
            "qty": qty,
        },
        client_order_id,
    )

    assert params.action == "create_order"
    assert params.order_type == "market"
    assert params.side == expected_side
    assert params.symbol == "BTC/USDT:USDT"
    assert params.amount == qty
    assert params.price is None
    assert params.order_link_id == client_order_id


def test_place_entry_without_native_attached_tpsl_uses_empty_extra_params() -> None:
    params = _builder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "qty": 0.01,
            "price": 50000.0,
        },
        "tsb:10:5:entry:5",
    )

    assert params.extra_params == {}


@pytest.mark.parametrize(
    ("side", "qty", "stop_price", "expected_side", "client_order_id"),
    [
        ("LONG", 0.01, 49000.0, "sell", "tsb:10:5:sl:1"),
        ("SHORT", 0.02, 51000.0, "buy", "tsb:10:5:sl:2"),
    ],
)
def test_place_protective_stop_builds_stop_reduce_only_order(
    side: str,
    qty: float,
    stop_price: float,
    expected_side: str,
    client_order_id: str,
) -> None:
    params = _builder().build(
        "PLACE_PROTECTIVE_STOP",
        {
            "symbol": "BTC/USDT:USDT",
            "side": side,
            "qty": qty,
            "stop_price": stop_price,
        },
        client_order_id,
    )

    assert params.action == "create_order"
    assert params.order_type == "stop"
    assert params.side == expected_side
    assert params.symbol == "BTC/USDT:USDT"
    assert params.amount == qty
    assert params.price is None
    assert params.order_link_id == client_order_id
    assert params.extra_params == {
        "reduceOnly": True,
        "triggerPrice": stop_price,
        "triggerBy": "LastPrice",
    }


@pytest.mark.parametrize(
    ("side", "qty", "price", "expected_side", "client_order_id"),
    [
        ("LONG", 0.01, 52000.0, "sell", "tsb:10:5:tp:1"),
        ("SHORT", 0.02, 47000.0, "buy", "tsb:10:5:tp:2"),
    ],
)
def test_place_take_profit_builds_limit_reduce_only_order(
    side: str,
    qty: float,
    price: float,
    expected_side: str,
    client_order_id: str,
) -> None:
    params = _builder().build(
        "PLACE_TAKE_PROFIT",
        {
            "symbol": "BTC/USDT:USDT",
            "side": side,
            "qty": qty,
            "price": price,
        },
        client_order_id,
    )

    assert params.action == "create_order"
    assert params.order_type == "limit"
    assert params.side == expected_side
    assert params.symbol == "BTC/USDT:USDT"
    assert params.amount == qty
    assert params.price == price
    assert params.order_link_id == client_order_id
    assert params.extra_params == {"reduceOnly": True}


@pytest.mark.parametrize(
    ("command_type", "side", "qty", "expected_side", "client_order_id"),
    [
        ("CLOSE_PARTIAL", "LONG", 0.005, "sell", "tsb:10:5:close:1"),
        ("CLOSE_PARTIAL", "SHORT", 0.005, "buy", "tsb:10:5:close:2"),
        ("CLOSE_FULL", "LONG", 0.01, "sell", "tsb:10:5:close:3"),
        ("CLOSE_FULL", "SHORT", 0.01, "buy", "tsb:10:5:close:4"),
    ],
)
def test_close_commands_build_market_reduce_only_orders(
    command_type: str,
    side: str,
    qty: float,
    expected_side: str,
    client_order_id: str,
) -> None:
    params = _builder().build(
        command_type,
        {
            "symbol": "BTC/USDT:USDT",
            "side": side,
            "qty": qty,
        },
        client_order_id,
    )

    assert params.action == "create_order"
    assert params.order_type == "market"
    assert params.side == expected_side
    assert params.symbol == "BTC/USDT:USDT"
    assert params.amount == qty
    assert params.price is None
    assert params.order_link_id == client_order_id
    assert params.extra_params == {"reduceOnly": True}


def test_sync_protective_orders_returns_noop() -> None:
    params = _builder().build("SYNC_PROTECTIVE_ORDERS", {}, "tsb:10:5:sync:1")

    assert params.action == "noop"


def test_unknown_command_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown command_type"):
        _builder().build("DO_SOMETHING_ELSE", {}, "tsb:10:5:unknown:1")
