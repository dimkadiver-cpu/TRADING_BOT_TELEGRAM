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


def test_place_entry_uses_empty_extra_params() -> None:
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


def test_move_stop_be_attached_flow_routes_to_trading_stop_move_sl() -> None:
    """C/D flows with attached TPSL must use trading_stop, not edit_order."""
    params = _builder().build(
        "MOVE_STOP_TO_BREAKEVEN",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "target_price": 50000.0,
            "be_buffer_pct": 0.0,
            "protection_style": "attached_full",
            "position_idx": 0,
        },
        "tsb:10:5:sl:1",
    )

    assert params.action == "trading_stop_move_sl"
    assert params.symbol == "BTC/USDT:USDT"
    assert params.position_side == "LONG"
    assert params.extra_params["stopLoss"] == "50000.0"
    assert params.extra_params["positionIdx"] == 0


def test_move_stop_be_attached_flow_long_applies_buffer() -> None:
    """Buffer is still applied before routing to trading_stop_move_sl."""
    params = _builder().build(
        "MOVE_STOP_TO_BREAKEVEN",
        {
            "symbol": "ETH/USDT:USDT",
            "side": "LONG",
            "target_price": 3000.0,
            "be_buffer_pct": 0.002,
            "protection_style": "attached_full",
            "position_idx": 1,
        },
        "tsb:10:5:sl:1",
    )

    assert params.action == "trading_stop_move_sl"
    assert params.extra_params["stopLoss"] == "3006.0"
    assert params.extra_params["positionIdx"] == 1


def test_move_stop_be_standalone_flow_still_uses_edit_sl() -> None:
    """Legacy flows with protection_style=standalone_order keep edit_sl path."""
    params = _builder().build(
        "MOVE_STOP_TO_BREAKEVEN",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "target_price": 50000.0,
            "be_buffer_pct": 0.0,
            "protection_style": "standalone_order",
        },
        "tsb:10:5:sl:1",
    )

    assert params.action == "edit_sl"
    assert params.new_trigger_price == 50000.0


def test_move_stop_be_no_protection_style_defaults_to_edit_sl() -> None:
    """Payload without protection_style defaults to standalone (backward-compatible)."""
    params = _builder().build(
        "MOVE_STOP_TO_BREAKEVEN",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "target_price": 50000.0,
        },
        "tsb:10:5:sl:1",
    )

    assert params.action == "edit_sl"
    assert params.new_trigger_price == 50000.0
