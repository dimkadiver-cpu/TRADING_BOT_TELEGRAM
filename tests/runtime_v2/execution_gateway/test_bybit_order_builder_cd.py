from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import (
    BybitOrderBuilder, BybitOrderParams,
)


def _b() -> BybitOrderBuilder:
    return BybitOrderBuilder()


# ── PLACE_ENTRY_WITH_ATTACHED_TPSL ────────────────────────────────────────────

def test_place_entry_with_attached_tpsl_limit():
    params = _b().build(
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "price": 65000.0,
            "qty": 0.01,
            "leverage": 5,
            "hedge_mode": False,
            "position_idx": 0,
            "attached_tpsl": {
                "mode": "FULL",
                "take_profit": 70000.0,
                "stop_loss": 63000.0,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            },
        },
        "tsb:1:1:entry:1",
    )
    assert params.action == "create_order"
    assert params.order_type == "limit"
    assert params.side == "buy"
    assert params.amount == 0.01
    assert params.price == 65000.0
    assert params.extra_params["takeProfit"] == 70000.0
    assert params.extra_params["stopLoss"] == 63000.0
    assert params.extra_params["tpslMode"] == "Full"
    assert params.extra_params["positionIdx"] == 0
    assert params.extra_params["tpTriggerBy"] == "MarkPrice"
    assert params.extra_params["slTriggerBy"] == "MarkPrice"
    assert params.extra_params["tpOrderType"] == "Market"
    assert params.extra_params["slOrderType"] == "Market"


def test_place_entry_with_attached_tpsl_hedge_long():
    params = _b().build(
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "price": 65000.0,
            "qty": 0.01,
            "leverage": 5,
            "hedge_mode": True,
            "position_idx": 1,
            "attached_tpsl": {
                "mode": "FULL",
                "take_profit": 70000.0,
                "stop_loss": 63000.0,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            },
        },
        "tsb:1:1:entry:1",
    )
    assert params.extra_params["positionIdx"] == 1


# ── SET_POSITION_TPSL_FULL ────────────────────────────────────────────────────

def test_set_position_tpsl_full():
    params = _b().build(
        "SET_POSITION_TPSL_FULL",
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_idx": 0,
            "take_profit": 70000.0,
            "stop_loss": 63000.0,
            "tp_trigger_by": "MarkPrice",
            "sl_trigger_by": "MarkPrice",
        },
        "tsb:1:1:tpsl_full:1",
    )
    assert params.action == "trading_stop_full"
    assert params.symbol == "BTCUSDT"
    assert params.extra_params["positionIdx"] == 0
    assert params.extra_params["tpslMode"] == "Full"
    assert params.extra_params["takeProfit"] == "70000.0"
    assert params.extra_params["stopLoss"] == "63000.0"
    assert params.extra_params["tpTriggerBy"] == "MarkPrice"
    assert params.extra_params["slTriggerBy"] == "MarkPrice"
    assert params.extra_params["tpOrderType"] == "Market"
    assert params.extra_params["slOrderType"] == "Market"


# ── SET_POSITION_TPSL_PARTIAL ─────────────────────────────────────────────────

def test_set_position_tpsl_partial():
    params = _b().build(
        "SET_POSITION_TPSL_PARTIAL",
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_idx": 0,
            "tp_sequence": 1,
            "take_profit": 67000.0,
            "stop_loss": 63000.0,
            "tp_size": 0.01,
            "sl_size": 0.01,
            "tp_order_type": "Limit",
            "tp_limit_price": 67000.0,
            "tp_trigger_by": "MarkPrice",
            "sl_trigger_by": "MarkPrice",
        },
        "tsb:1:1:tpsl_partial:1",
    )
    assert params.action == "trading_stop_partial"
    assert params.extra_params["tpslMode"] == "Partial"
    assert params.extra_params["tpSize"] == "0.01"
    assert params.extra_params["slSize"] == "0.01"
    assert params.extra_params["tpOrderType"] == "Limit"
    assert params.extra_params["tpLimitPrice"] == "67000.0"


# ── MOVE_POSITION_STOP ────────────────────────────────────────────────────────

def test_move_position_stop():
    params = _b().build(
        "MOVE_POSITION_STOP",
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_idx": 0,
            "new_stop_loss": 65000.0,
        },
        "tsb:1:1:move_stop:1",
    )
    assert params.action == "trading_stop_move_sl"
    assert params.symbol == "BTCUSDT"
    assert params.extra_params["stopLoss"] == "65000.0"
    assert params.extra_params["positionIdx"] == 0
    # takeProfit must not be present (we only want to move stop, not affect TP)
    assert "takeProfit" not in params.extra_params
