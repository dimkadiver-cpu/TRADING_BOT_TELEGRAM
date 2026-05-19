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


def test_place_entry_mode_c_multi_tp_uses_attached_payload_fields() -> None:
    params = _builder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "qty": 0.01,
            "price": 50000.0,
            "native_attached_tpsl": True,
            "attached_take_profit": 53000.0,
            "attached_stop_loss": 49000.0,
            "attached_take_profit_qty": 0.004,
            "tp_count": 2,
        },
        "tsb:10:5:entry:modec:1",
    )

    assert params.action == "create_order"
    assert params.side == "buy"
    assert params.extra_params == {
        "takeProfit": 53000.0,
        "stopLoss": 49000.0,
        "tpslMode": "Partial",
        "tpOrderType": "Limit",
        "tpLimitPrice": 53000.0,
        "tpSize": 0.004,
    }


def test_place_entry_mode_c_single_tp_uses_total_qty_for_tp_size() -> None:
    params = _builder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "qty": 0.01,
            "price": 50000.0,
            "native_attached_tpsl": True,
            "attached_take_profit": 51000.0,
            "attached_stop_loss": 49000.0,
            "attached_take_profit_qty": 0.003,
            "tp_count": 1,
        },
        "tsb:10:5:entry:modec:2",
    )

    assert params.extra_params["tpslMode"] == "Partial"
    assert params.extra_params["tpSize"] == 0.01


def test_place_entry_mode_c_short_preserves_sell_entry_side() -> None:
    params = _builder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "SHORT",
            "entry_type": "LIMIT",
            "qty": 0.02,
            "price": 48000.0,
            "native_attached_tpsl": True,
            "attached_take_profit": 47000.0,
            "attached_stop_loss": 49000.0,
            "attached_take_profit_qty": 0.01,
            "tp_count": 2,
        },
        "tsb:10:5:entry:modec:3",
    )

    assert params.side == "sell"
    assert params.extra_params["tpslMode"] == "Partial"


def test_place_entry_mode_c_defaults_tp_count_to_one() -> None:
    params = _builder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "qty": 0.01,
            "price": 50000.0,
            "native_attached_tpsl": True,
            "attached_take_profit": 51000.0,
            "attached_stop_loss": 49000.0,
            "attached_take_profit_qty": 0.002,
        },
        "tsb:10:5:entry:modec:4",
    )

    assert params.extra_params["tpSize"] == 0.01


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


def test_sync_protective_orders_returns_amend_sl_qty() -> None:
    params = _builder().build(
        "SYNC_PROTECTIVE_ORDERS",
        {"symbol": "BTC/USDT:USDT", "side": "LONG"},
        "tsb:10:5:sync:1",
    )

    assert params.action == "amend_sl_qty"
    assert params.symbol == "BTC/USDT:USDT"
    assert params.position_side == "LONG"


def test_cancel_pending_entry_keeps_cancel_by_link_contract() -> None:
    params = _builder().build(
        "CANCEL_PENDING_ENTRY",
        {"symbol": "BTC/USDT:USDT", "side": "LONG"},
        "tsb:10:5:cancel:1",
    )

    assert params.action == "cancel_by_link"
    assert params.symbol == "BTC/USDT:USDT"
    assert params.order_link_id == "tsb:10:5:cancel:1"


@pytest.mark.parametrize(
    ("side", "target_price", "be_buffer_pct", "expected_trigger"),
    [
        ("LONG", 50000.0, 0.0, 50000.0),
        ("LONG", 50000.0, 0.01, 50500.0),
        ("SHORT", 50000.0, 0.0, 50000.0),
        ("SHORT", 50000.0, 0.01, 49500.0),
    ],
)
def test_move_stop_to_breakeven_uses_target_price_and_buffer(
    side: str,
    target_price: float,
    be_buffer_pct: float,
    expected_trigger: float,
) -> None:
    params = _builder().build(
        "MOVE_STOP_TO_BREAKEVEN",
        {
            "symbol": "BTC/USDT:USDT",
            "side": side,
            "target_price": target_price,
            "be_buffer_pct": be_buffer_pct,
        },
        "tsb:10:5:be:1",
    )

    assert params.action == "edit_sl"
    assert params.symbol == "BTC/USDT:USDT"
    assert params.position_side == side
    assert params.new_trigger_price == expected_trigger


def test_move_stop_uses_new_stop_price() -> None:
    params = _builder().build(
        "MOVE_STOP",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "new_stop_price": 50123.0,
        },
        "tsb:10:5:move:1",
    )

    assert params.action == "edit_sl"
    assert params.new_trigger_price == 50123.0
    assert params.position_side == "LONG"


def test_unknown_command_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown command_type"):
        _builder().build("DO_SOMETHING_ELSE", {}, "tsb:10:5:unknown:1")


@pytest.mark.parametrize(
    ("command_type", "payload", "expected_position_idx"),
    [
        (
            "PLACE_ENTRY",
            {
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "entry_type": "LIMIT",
                "qty": 0.01,
                "price": 50000.0,
            },
            1,
        ),
        (
            "PLACE_ENTRY",
            {
                "symbol": "BTC/USDT:USDT",
                "side": "SHORT",
                "entry_type": "LIMIT",
                "qty": 0.01,
                "price": 50000.0,
            },
            2,
        ),
    ],
)
def test_hedge_mode_adds_position_idx_to_entries(
    command_type: str,
    payload: dict,
    expected_position_idx: int,
) -> None:
    params = BybitOrderBuilder().build(
        command_type,
        payload,
        "tsb:1:1:entry:1",
        hedge_mode=True,
    )

    assert params.extra_params.get("positionIdx") == expected_position_idx
    assert "reduceOnly" not in params.extra_params


@pytest.mark.parametrize(
    ("command_type", "payload", "expected_position_idx"),
    [
        (
            "PLACE_PROTECTIVE_STOP",
            {
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "qty": 0.01,
                "stop_price": 45000.0,
            },
            1,
        ),
        (
            "PLACE_TAKE_PROFIT",
            {
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "qty": 0.01,
                "price": 55000.0,
            },
            1,
        ),
        (
            "CLOSE_FULL",
            {
                "symbol": "BTC/USDT:USDT",
                "side": "SHORT",
                "qty": 0.01,
            },
            2,
        ),
    ],
)
def test_hedge_mode_removes_reduce_only_from_closing_orders(
    command_type: str,
    payload: dict,
    expected_position_idx: int,
) -> None:
    params = BybitOrderBuilder().build(
        command_type,
        payload,
        "tsb:1:1:closing:1",
        hedge_mode=True,
    )

    assert params.extra_params.get("positionIdx") == expected_position_idx
    assert "reduceOnly" not in params.extra_params


def test_hedge_mode_false_no_position_idx() -> None:
    params = BybitOrderBuilder().build(
        "PLACE_ENTRY",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "qty": 0.01,
            "price": 50000.0,
        },
        "tsb:1:1:entry:1",
        hedge_mode=False,
    )

    assert "positionIdx" not in params.extra_params


def test_hedge_mode_sync_protective_orders_returns_amend_sl_qty() -> None:
    params = BybitOrderBuilder().build(
        "SYNC_PROTECTIVE_ORDERS",
        {"symbol": "BTC/USDT:USDT", "side": "LONG"},
        "tsb:1:1:sync:1",
        hedge_mode=True,
    )

    assert params.action == "amend_sl_qty"
