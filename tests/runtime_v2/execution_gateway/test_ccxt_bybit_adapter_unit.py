# tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
import ccxt
from unittest.mock import MagicMock, call


def _make_adapter(exchange, hedge_mode=False):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    return CcxtBybitAdapter(
        api_key="key", api_secret="secret", connector="bybit",
        _exchange=exchange,
    )


def _make_adapter_with_repo(exchange, repo, hedge_mode=False):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    return CcxtBybitAdapter(
        api_key="key", api_secret="secret", connector="bybit",
        repo=repo,
        _exchange=exchange,
    )


def _place_entry(adapter, symbol="BTC/USDT:USDT", side="LONG", qty=0.01, price=50000.0):
    return adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": symbol, "side": side, "entry_type": "LIMIT",
                 "qty": qty, "price": price},
        client_order_id="tsb:10:5:entry:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


# --- place_order: create_order happy path ---

def test_place_entry_calls_create_order_with_correct_params():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "exch_12345"}
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is True
    assert result.exchange_order_id == "exch_12345"
    exchange.create_order.assert_called_once()
    args, kwargs = exchange.create_order.call_args
    assert args[0] == "BTC/USDT:USDT"   # symbol
    assert args[1] == "limit"            # order_type
    assert args[2] == "buy"              # side (LONG entry)
    assert args[3] == 0.01              # amount
    assert args[4] == 50000.0           # price
    assert kwargs["params"]["orderLinkId"] == "tsb:10:5:entry:1"


def test_place_entry_short_uses_sell_side():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "exch_99"}
    adapter = _make_adapter(exchange)

    _place_entry(adapter, side="SHORT")

    args, _ = exchange.create_order.call_args
    assert args[2] == "sell"


def test_hedge_mode_place_entry_adds_position_idx():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "123"}
    adapter = _make_adapter(exchange)

    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG",
                 "entry_type": "LIMIT", "qty": 0.01, "price": 50000.0,
                 "hedge_mode": True},
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="main",
        connector="bybit",
    )

    call_params = exchange.create_order.call_args[1]["params"]
    assert call_params.get("positionIdx") == 1
    assert "reduceOnly" not in call_params


# --- place_order: SYNC_PROTECTIVE_ORDERS ---

def test_sync_protective_orders_mode_b_amends_sl_qty():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "side": "long",
            "contracts": 0.5,
            "info": {"symbol": "BTCUSDT", "stopLoss": "0"},
        }
    ]
    exchange.fetch_open_orders.return_value = [
        {
            "id": "sl-order-1",
            "side": "sell",
            "type": "stop",
            "amount": 1.0,
            "reduceOnly": True,
            "stopPrice": "45000.0",
            "info": {},
        }
    ]
    exchange.edit_order.return_value = {"id": "sl-order-1"}
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_called_once_with(
        "sl-order-1",
        "BTC/USDT:USDT",
        "stop",
        "sell",
        0.5,
        params={"triggerPrice": 45000.0},
    )


def test_sync_protective_orders_mode_c_calls_trading_stop():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "side": "long",
            "contracts": 0.7,
            "info": {"symbol": "BTCUSDT", "stopLoss": "45000.0"},
        }
    ]
    exchange.fetch_open_orders.return_value = []
    exchange.private_post_v5_position_trading_stop = MagicMock(return_value={})
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.private_post_v5_position_trading_stop.assert_called_once_with(
        {
            "category": "linear",
            "symbol": "BTCUSDT",
            "positionIdx": 1,
            "stopLoss": "45000.0",
            "slSize": "0.7",
        }
    )


def test_sync_protective_orders_qty_zero_cancels_reduce_only():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "side": "long",
            "contracts": 0.0,
            "info": {"symbol": "BTCUSDT", "stopLoss": "0"},
        }
    ]
    exchange.fetch_open_orders.return_value = [
        {"id": "sl-1", "side": "sell", "reduceOnly": True, "stopPrice": "45000"},
        {"id": "tp-1", "side": "sell", "reduceOnly": True, "stopPrice": None},
    ]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    assert exchange.cancel_order.call_count == 2


def test_sync_protective_orders_no_sl_found_returns_success():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "side": "long",
            "contracts": 0.5,
            "info": {"symbol": "BTCUSDT", "stopLoss": "0"},
        }
    ]
    exchange.fetch_open_orders.return_value = []
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_not_called()


# --- place_order: cancel_by_link (CANCEL_PENDING_ENTRY) ---

def test_cancel_pending_entry_fetches_and_cancels_open_order():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = [{"id": "open_ord_1", "side": "buy"}]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": "BTC/USDT:USDT"},
        client_order_id="tsb:10:5:entry:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.fetch_open_orders.assert_called_once_with(
        "BTC/USDT:USDT", params={"orderLinkId": "tsb:10:5:entry:1"}
    )
    exchange.cancel_order.assert_called_once_with("open_ord_1", "BTC/USDT:USDT")


def test_cancel_pending_entry_no_open_order_still_succeeds():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": "BTC/USDT:USDT"},
        client_order_id="tsb:10:5:entry:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.cancel_order.assert_not_called()


# --- place_order: edit_sl (MOVE_STOP_TO_BREAKEVEN) ---

def test_move_stop_to_breakeven_edits_sl_order():
    exchange = MagicMock()
    sl_order = {
        "id": "sl_ord_1", "side": "sell",
        "type": "stop", "amount": 0.01,
        "reduceOnly": True, "stopPrice": 49000.0,
    }
    exchange.fetch_open_orders.return_value = [sl_order]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "target_price": 50000.0,
            "be_buffer_pct": 0.0,
            "new_stop_price": 50010.0,
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_called_once()
    edit_args, edit_kwargs = exchange.edit_order.call_args
    assert edit_args[0] == "sl_ord_1"
    assert edit_kwargs["params"]["triggerPrice"] == 50010.0


def test_move_stop_to_breakeven_legacy_entry_price_bridge_still_works():
    exchange = MagicMock()
    sl_order = {
        "id": "sl_ord_1", "side": "sell",
        "type": "stop", "amount": 0.01,
        "reduceOnly": True, "stopPrice": 49000.0,
    }
    exchange.fetch_open_orders.return_value = [sl_order]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG", "entry_price": 50000.0},
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_called_once()
    _, edit_kwargs = exchange.edit_order.call_args
    assert edit_kwargs["params"]["triggerPrice"] == 50000.0


def test_move_stop_to_breakeven_rejects_null_new_stop_price_payload():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": 50000.0,
            "new_stop_price": None,
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is False
    assert result.reason == "invalid_payload"
    assert result.error == "new_stop_price is required"
    exchange.edit_order.assert_not_called()


def test_move_stop_sl_not_found_returns_failed():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG", "new_stop_price": 51000.0},
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is False
    assert result.reason == "sl_order_not_found"


def test_move_stop_be_attached_calls_trading_stop_api():
    """Attached/full BE move must call private_post_v5_position_trading_stop, not edit_order."""
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop = MagicMock(
        return_value={"retCode": 0}
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "target_price": 50000.0,
            "be_buffer_pct": 0.0,
            "new_stop_price": 50010.0,
            "protection_style": "attached_full",
            "position_idx": 0,
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_not_called()
    exchange.fetch_open_orders.assert_not_called()
    exchange.private_post_v5_position_trading_stop.assert_called_once()
    call_body = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert call_body["category"] == "linear"
    assert call_body["symbol"] == "BTCUSDT"
    assert call_body["stopLoss"] == "50010.0"
    assert call_body["positionIdx"] == 0


def test_move_stop_be_attached_hedge_mode_long_position_idx_1():
    """Hedge mode LONG should use positionIdx=1 in trading_stop payload."""
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop = MagicMock(
        return_value={"retCode": 0}
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "XRP/USDT:USDT",
            "side": "LONG",
            "target_price": 0.5,
            "be_buffer_pct": 0.0,
            "new_stop_price": 0.5003,
            "protection_style": "attached_full",
            "position_idx": 1,
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    call_body = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert call_body["positionIdx"] == 1
    assert call_body["symbol"] == "XRPUSDT"


def test_move_stop_be_attached_hedge_mode_infers_position_idx_when_missing():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop = MagicMock(
        return_value={"retCode": 0}
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "ETH/USDT:USDT",
            "side": "SHORT",
            "new_stop_price": 2999.5,
            "protection_style": "attached_full",
            "hedge_mode": True,
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    call_body = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert call_body["positionIdx"] == 2
    assert call_body["symbol"] == "ETHUSDT"


def test_move_stop_be_attached_hedge_mode_infers_long_position_idx_when_missing():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop = MagicMock(
        return_value={"retCode": 0}
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "new_stop_price": 50010.0,
            "protection_style": "attached_full",
            "hedge_mode": True,
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    call_body = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert call_body["positionIdx"] == 1
    assert call_body["symbol"] == "BTCUSDT"


def test_move_stop_be_standalone_still_calls_edit_order():
    """Legacy standalone flow must still use edit_order path."""
    exchange = MagicMock()
    sl_order = {
        "id": "sl_ord_1", "side": "sell",
        "type": "stop", "amount": 0.01, "reduceOnly": True, "stopPrice": "49000",
    }
    exchange.fetch_open_orders.return_value = [sl_order]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "target_price": 50000.0,
            "new_stop_price": 50020.0,
            "protection_style": "standalone_order",
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_called_once()
    _, edit_kwargs = exchange.edit_order.call_args
    assert edit_kwargs["params"]["triggerPrice"] == 50020.0
    exchange.private_post_v5_position_trading_stop.assert_not_called()


def test_sync_protective_orders_mode_c_surfaces_trading_stop_retcode_failure():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "side": "long",
            "contracts": 0.7,
            "info": {"symbol": "BTCUSDT", "stopLoss": "45000.0"},
        }
    ]
    exchange.fetch_open_orders.return_value = []
    exchange.private_post_v5_position_trading_stop = MagicMock(
        return_value={"retCode": 110001, "retMsg": "bad stop"}
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is False
    assert result.error == "retCode=110001: bad stop"


def test_move_stop_be_attached_surfaces_trading_stop_retcode_failure():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop = MagicMock(
        return_value={"retCode": 110001, "retMsg": "bad stop"}
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "new_stop_price": 50010.0,
            "protection_style": "attached_full",
            "position_idx": 0,
        },
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is False
    assert result.error == "retCode=110001: bad stop"


# --- place_order: error handling ---

def test_invalid_order_returns_failed_with_reason():
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.InvalidOrder("order params bad")
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is False
    assert result.reason == "invalid_order"
    assert "order params bad" in (result.error or "")


def test_insufficient_funds_returns_failed_with_reason():
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.InsufficientFunds("no money")
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is False
    assert result.reason == "insufficient_funds"


def test_network_error_propagates():
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.NetworkError("timeout")
    adapter = _make_adapter(exchange)

    with pytest.raises(ccxt.NetworkError):
        _place_entry(adapter)


def test_rate_limit_exceeded_propagates():
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.RateLimitExceeded("slow down")
    adapter = _make_adapter(exchange)

    with pytest.raises(ccxt.RateLimitExceeded):
        _place_entry(adapter)


def test_other_base_error_returns_failed():
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.ExchangeError("generic exchange error")
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is False
    assert "generic exchange error" in (result.error or "")


# --- get_order_status ---

def test_get_order_status_finds_open_order():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = [{
        "id": "exch_123", "clientOrderId": "tsb:10:5:entry:1",
        "status": "open", "filled": 0.0, "average": None,
    }]
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "OPEN"
    assert raw.exchange_order_id == "exch_123"
    assert raw.client_order_id == "tsb:10:5:entry:1"


def test_get_order_status_falls_back_to_closed_orders():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = [{
        "id": "exch_456", "clientOrderId": "tsb:10:5:entry:1",
        "status": "closed", "filled": 0.01, "average": 50000.0,
    }]
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "FILLED"
    assert raw.filled_qty == 0.01
    assert raw.average_price == 50000.0


def test_get_order_status_not_found_returns_none():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is None


def test_get_order_status_open_orders_exception_falls_back():
    exchange = MagicMock()
    exchange.fetch_open_orders.side_effect = Exception("network blip")
    exchange.fetch_closed_orders.return_value = [{
        "id": "exch_789", "clientOrderId": "tsb:10:5:entry:1",
        "status": "closed", "filled": 0.005, "average": 48000.0,
    }]
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "FILLED"


def test_get_order_status_skips_closed_order_without_matching_client_order_id():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = [{
        "id": "unrelated_closed_order",
        "status": "closed",
        "filled": 500.0,
        "average": 1.3702,
    }]
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is None


def test_od_f1_2_fallback_returns_filled_when_position_closed():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.0, "info": {}}
    ]
    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
    }
    adapter = _make_adapter_with_repo(exchange, repo)

    raw = adapter.get_order_status(
        client_order_id="tsb:1:1:sl:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "FILLED"
    exchange.fetch_positions.assert_called_once_with(["BTC/USDT:USDT"])


def test_od_f1_2_fallback_returns_none_when_position_still_open():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.5, "info": {}}
    ]
    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
    }
    adapter = _make_adapter_with_repo(exchange, repo)

    raw = adapter.get_order_status(
        client_order_id="tsb:1:1:sl:1", execution_account_id="bybit_main"
    )

    assert raw is None


def test_od_f1_2_fallback_returns_none_when_fetch_positions_empty():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    exchange.fetch_positions.return_value = []
    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
    }
    adapter = _make_adapter_with_repo(exchange, repo)

    raw = adapter.get_order_status(
        client_order_id="tsb:1:1:sl:1", execution_account_id="bybit_main"
    )

    assert raw is None


def test_od_f1_2_fallback_returns_none_when_only_opposite_side_exists():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    exchange.fetch_positions.return_value = [
        {"side": "short", "contracts": 0.0, "info": {}}
    ]
    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
    }
    adapter = _make_adapter_with_repo(exchange, repo)

    raw = adapter.get_order_status(
        client_order_id="tsb:1:1:sl:1", execution_account_id="bybit_main"
    )

    assert raw is None


def test_od_f1_2_fallback_skipped_for_entry_role():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
    }
    adapter = _make_adapter_with_repo(exchange, repo)

    raw = adapter.get_order_status(
        client_order_id="tsb:1:1:entry:1", execution_account_id="bybit_main"
    )

    assert raw is None
    exchange.fetch_positions.assert_not_called()


def test_od_f1_2_fallback_recovers_when_open_and_closed_polling_fail():
    exchange = MagicMock()
    exchange.fetch_open_orders.side_effect = Exception("open failed")
    exchange.fetch_closed_orders.side_effect = Exception("closed failed")
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.0, "info": {}}
    ]
    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
    }
    adapter = _make_adapter_with_repo(exchange, repo)

    raw = adapter.get_order_status(
        client_order_id="tsb:1:1:sl:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "FILLED"


def test_get_payload_by_client_order_id_returns_payload_dict(ops_db):
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            1001,
            42,
            "PLACE_PROTECTIVE_STOP",
            "SENT",
            json.dumps({"symbol": "BTC/USDT:USDT", "side": "LONG"}),
            "idem:1001",
            "tsb:42:1001:sl:1",
            "2026-05-19T00:00:00+00:00",
            "2026-05-19T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(ops_db)

    payload = repo.get_payload_by_client_order_id("tsb:42:1001:sl:1")

    assert payload == {"symbol": "BTC/USDT:USDT", "side": "LONG"}


# --- get_position_qty ---

def test_get_position_qty_long():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.03},
        {"side": "short", "contracts": 0.0},
    ]
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="LONG", execution_account_id="bybit_main"
    )

    assert qty == 0.03
    exchange.fetch_positions.assert_called_once_with(["BTC/USDT:USDT"])


def test_get_position_qty_short():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.01},
        {"side": "short", "contracts": 0.05},
    ]
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="SHORT", execution_account_id="bybit_main"
    )

    assert qty == 0.05


def test_get_position_qty_no_matching_side_returns_zero():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [{"side": "short", "contracts": 0.02}]
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="LONG", execution_account_id="bybit_main"
    )

    assert qty == 0.0


def test_get_position_qty_exception_returns_none():
    exchange = MagicMock()
    exchange.fetch_positions.side_effect = Exception("API error")
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="LONG", execution_account_id="bybit_main"
    )

    assert qty is None


# --- set_leverage ---

def test_set_leverage_calls_exchange_with_buy_sell_params():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)

    adapter.set_leverage("BTC/USDT:USDT", 10, "bybit_main")

    exchange.set_leverage.assert_called_once_with(
        10, "BTC/USDT:USDT",
        params={"buyLeverage": "10", "sellLeverage": "10"},
    )


def test_hedge_mode_set_leverage_passes_position_idx_zero():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)

    # position_idx=0 is the default — should NOT add positionIdx to params
    adapter.set_leverage("BTC/USDT:USDT", 10, "main", position_idx=0)

    call_params = exchange.set_leverage.call_args[1]["params"]
    assert "positionIdx" not in call_params


def test_set_leverage_with_nonzero_position_idx():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)

    adapter.set_leverage("BTC/USDT:USDT", 10, "main", position_idx=1)

    call_params = exchange.set_leverage.call_args[1]["params"]
    assert call_params.get("positionIdx") == 1


def test_one_way_mode_set_leverage_no_position_idx():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)

    adapter.set_leverage("BTC/USDT:USDT", 10, "main")

    call_params = exchange.set_leverage.call_args[1]["params"]
    assert "positionIdx" not in call_params


def test_testnet_mode_enables_ccxt_sandbox(monkeypatch):
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter as amod

    exchange = MagicMock()
    exchange.options = {}
    exchange.load_time_difference.return_value = 123
    monkeypatch.setattr(amod.ccxt, "bybit", MagicMock(return_value=exchange))

    amod.CcxtBybitAdapter(
        api_key="key",
        api_secret="secret",
        connector="bybit",
        mode="testnet",
    )

    exchange.set_sandbox_mode.assert_called_once_with(True)
    exchange.enable_demo_trading.assert_not_called()
    exchange.load_time_difference.assert_called_once_with()
    assert exchange.options["adjustForTimeDifference"] is True
    assert exchange.options["recvWindow"] == 10000
    assert exchange.options["recv_window"] == 10000


def test_demo_mode_enables_time_sync_and_recv_window(monkeypatch):
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter as amod

    exchange = MagicMock()
    exchange.options = {}
    exchange.load_time_difference.return_value = 321
    monkeypatch.setattr(amod.ccxt, "bybit", MagicMock(return_value=exchange))

    amod.CcxtBybitAdapter(
        api_key="key",
        api_secret="secret",
        connector="bybit",
        mode="demo",
        adjust_for_time_difference=True,
        recv_window_ms=15000,
        time_sync_on_startup=True,
    )

    exchange.enable_demo_trading.assert_called_once_with(True)
    exchange.load_time_difference.assert_called_once_with()
    assert exchange.options["recvWindow"] == 15000
    assert exchange.options["recv_window"] == 15000


def test_time_sync_can_be_disabled(monkeypatch):
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter as amod

    exchange = MagicMock()
    exchange.options = {}
    monkeypatch.setattr(amod.ccxt, "bybit", MagicMock(return_value=exchange))

    amod.CcxtBybitAdapter(
        api_key="key",
        api_secret="secret",
        connector="bybit",
        adjust_for_time_difference=False,
        time_sync_on_startup=False,
    )

    exchange.load_time_difference.assert_not_called()
    assert exchange.options["adjustForTimeDifference"] is False


# --- place_order: trading_stop actions ---

def test_place_entry_with_attached_tpsl_calls_create_order():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "ord123"}
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    adapter = CcxtBybitAdapter(api_key="", api_secret="", connector="bybit", _exchange=exchange)
    result = adapter.place_order(
        command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
        payload={
            "symbol": "BTC/USDT:USDT", "side": "LONG", "entry_type": "LIMIT",
            "price": 65000.0, "qty": 0.01, "leverage": 5,
            "hedge_mode": False, "position_idx": 0,
            "attached_tpsl": {
                "mode": "FULL", "take_profit": 70000.0, "stop_loss": 63000.0,
                "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
            },
        },
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="main",
        connector="bybit",
    )
    assert result.success is True
    exchange.create_order.assert_called_once()


def test_set_position_tpsl_full_calls_trading_stop():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop.return_value = {"retCode": 0}
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    adapter = CcxtBybitAdapter(api_key="", api_secret="", connector="bybit", _exchange=exchange)
    result = adapter.place_order(
        command_type="SET_POSITION_TPSL_FULL",
        payload={
            "symbol": "BTCUSDT", "side": "LONG", "position_idx": 0,
            "take_profit": 70000.0, "stop_loss": 63000.0,
            "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
        },
        client_order_id="tsb:1:1:tpsl_full:1",
        execution_account_id="main",
        connector="bybit",
    )
    assert result.success is True
    exchange.private_post_v5_position_trading_stop.assert_called_once()
    call_args = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert call_args["tpslMode"] == "Full"
    assert call_args["positionIdx"] == 0


def test_set_position_tpsl_partial_calls_trading_stop():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop.return_value = {"retCode": 0}
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    adapter = CcxtBybitAdapter(api_key="", api_secret="", connector="bybit", _exchange=exchange)
    result = adapter.place_order(
        command_type="SET_POSITION_TPSL_PARTIAL",
        payload={
            "symbol": "BTCUSDT", "side": "LONG", "position_idx": 0,
            "take_profit": 67000.0, "stop_loss": 63000.0,
            "tp_size": 0.01, "sl_size": 0.01,
            "tp_order_type": "Limit", "tp_limit_price": 67000.0,
            "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
        },
        client_order_id="tsb:1:1:tpsl_partial:1",
        execution_account_id="main",
        connector="bybit",
    )
    assert result.success is True
    exchange.private_post_v5_position_trading_stop.assert_called_once()
    call_args = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert call_args["tpslMode"] == "Partial"
    assert call_args["tpSize"] == "0.01"
    assert call_args["slSize"] == "0.01"
    assert call_args["tpLimitPrice"] == "67000.0"


def test_move_position_stop_calls_trading_stop_only_sl():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop.return_value = {"retCode": 0}
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    adapter = CcxtBybitAdapter(api_key="", api_secret="", connector="bybit", _exchange=exchange)
    adapter.place_order(
        command_type="MOVE_POSITION_STOP",
        payload={
            "symbol": "BTCUSDT", "side": "LONG", "position_idx": 0,
            "new_stop_loss": 65000.0,
        },
        client_order_id="tsb:1:1:move_stop:1",
        execution_account_id="main",
        connector="bybit",
    )
    call_args = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert "stopLoss" in call_args
    assert "takeProfit" not in call_args


def test_rebuild_partial_tps_cancels_only_non_full_qty_orders_and_recreates_each_level():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.01, "info": {"symbol": "BTCUSDT"}}
    ]
    exchange.fetch_open_orders.return_value = [
        {
            "id": "tp-partial-1",
            "side": "sell",
            "amount": 0.003,
            "reduceOnly": True,
            "stopPrice": "51000",
        },
        {
            "id": "tp-full",
            "side": "sell",
            "amount": 0.01,
            "reduceOnly": True,
            "stopPrice": "53000",
        },
        {
            "id": "entry-like",
            "side": "buy",
            "amount": 0.01,
            "reduceOnly": True,
            "stopPrice": "50000",
        },
    ]
    exchange.private_post_v5_position_trading_stop = MagicMock(
        side_effect=[{"retCode": 0}, {"retCode": 0}]
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "position_idx": 1,
            "preserve_full_tp": True,
            "tps": [
                {
                    "sequence": 1,
                    "price": 51000.0,
                    "qty": 0.003,
                    "order_type": "Limit",
                    "limit_price": 51000.0,
                    "trigger_by": "MarkPrice",
                },
                {
                    "sequence": 2,
                    "price": 52000.0,
                    "qty": 0.004,
                    "order_type": "Market",
                    "trigger_by": "LastPrice",
                },
            ],
        },
        client_order_id="tsb:1:1:rebuild:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.cancel_order.assert_called_once_with("tp-partial-1", "BTC/USDT:USDT")
    assert exchange.private_post_v5_position_trading_stop.call_count == 2
    assert exchange.private_post_v5_position_trading_stop.call_args_list == [
        call(
            {
                "category": "linear",
                "symbol": "BTCUSDT",
                "positionIdx": 1,
                "tpslMode": "Partial",
                "takeProfit": "51000.0",
                "tpSize": "0.003",
                "tpOrderType": "Limit",
                "tpTriggerBy": "MarkPrice",
                "tpLimitPrice": "51000.0",
            }
        ),
        call(
            {
                "category": "linear",
                "symbol": "BTCUSDT",
                "positionIdx": 1,
                "tpslMode": "Partial",
                "takeProfit": "52000.0",
                "tpSize": "0.004",
                "tpOrderType": "Market",
                "tpTriggerBy": "LastPrice",
            }
        ),
    ]


def test_rebuild_partial_tps_surfaces_retcode_failure_with_level():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.01, "info": {"symbol": "BTCUSDT"}}
    ]
    exchange.fetch_open_orders.return_value = []
    exchange.private_post_v5_position_trading_stop = MagicMock(
        side_effect=[{"retCode": 0}, {"retCode": 110001, "retMsg": "bad tp"}]
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "position_idx": 1,
            "tps": [
                {"sequence": 1, "price": 51000.0, "qty": 0.003},
                {"sequence": 2, "price": 52000.0, "qty": 0.004},
            ],
        },
        client_order_id="tsb:1:1:rebuild:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is False
    assert result.error == "tp2: retCode=110001: bad tp"


def test_rebuild_partial_tps_survives_cancel_order_exception(caplog):
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.01, "info": {"symbol": "BTCUSDT"}}
    ]
    exchange.fetch_open_orders.return_value = [
        {
            "id": "tp-partial-1",
            "side": "sell",
            "amount": 0.003,
            "reduceOnly": True,
            "stopPrice": "51000",
        }
    ]
    exchange.cancel_order.side_effect = RuntimeError("cancel exploded")
    exchange.private_post_v5_position_trading_stop = MagicMock(return_value={"retCode": 0})
    adapter = _make_adapter(exchange)

    with caplog.at_level(logging.WARNING):
        result = adapter.place_order(
            command_type="REBUILD_PARTIAL_TPS",
            payload={
                "symbol": "BTC/USDT:USDT",
                "side": "LONG",
                "position_idx": 1,
                "tps": [
                    {"sequence": 1, "price": 51000.0, "qty": 0.003},
                ],
            },
            client_order_id="tsb:1:1:rebuild:1",
            execution_account_id="main",
            connector="bybit",
        )

    assert result.success is True
    assert "cancel exploded" in caplog.text
    exchange.private_post_v5_position_trading_stop.assert_called_once()


def test_rebuild_partial_tps_preserve_sl_keeps_matching_stop_loss_order():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "side": "long",
            "contracts": 0.01,
            "info": {"symbol": "BTCUSDT", "stopLoss": "49500.0"},
        }
    ]
    exchange.fetch_open_orders.return_value = [
        {
            "id": "sl-order",
            "side": "sell",
            "amount": 0.009,
            "reduceOnly": True,
            "stopPrice": "49500.000",
        },
        {
            "id": "tp-partial-1",
            "side": "sell",
            "amount": 0.003,
            "reduceOnly": True,
            "stopPrice": "51000",
        },
        {
            "id": "tp-full",
            "side": "sell",
            "amount": 0.01,
            "reduceOnly": True,
            "stopPrice": "53000",
        },
    ]
    exchange.private_post_v5_position_trading_stop = MagicMock(return_value={"retCode": 0})
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "position_idx": 1,
            "preserve_sl": True,
            "preserve_full_tp": True,
            "tps": [
                {"sequence": 1, "price": 51000.0, "qty": 0.003},
            ],
        },
        client_order_id="tsb:1:1:rebuild:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.cancel_order.assert_called_once_with("tp-partial-1", "BTC/USDT:USDT")
    cancelled_ids = [args[0] for args, _ in exchange.cancel_order.call_args_list]
    assert "sl-order" not in cancelled_ids
    assert "tp-full" not in cancelled_ids


def test_rebuild_partial_tps_preserve_full_tp_uses_tolerant_amount_match():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.01, "info": {"symbol": "BTCUSDT"}}
    ]
    exchange.fetch_open_orders.return_value = [
        {
            "id": "tp-full-fuzzy",
            "side": "sell",
            "amount": 0.0100000001,
            "reduceOnly": True,
            "stopPrice": "53000",
        },
        {
            "id": "tp-partial-1",
            "side": "sell",
            "amount": 0.003,
            "reduceOnly": True,
            "stopPrice": "51000",
        },
    ]
    exchange.private_post_v5_position_trading_stop = MagicMock(return_value={"retCode": 0})
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "position_idx": 1,
            "preserve_full_tp": True,
            "tps": [
                {"sequence": 1, "price": 51000.0, "qty": 0.003},
            ],
        },
        client_order_id="tsb:1:1:rebuild:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.cancel_order.assert_called_once_with("tp-partial-1", "BTC/USDT:USDT")
    cancelled_ids = [args[0] for args, _ in exchange.cancel_order.call_args_list]
    assert "tp-full-fuzzy" not in cancelled_ids


def test_rebuild_partial_tps_surfaces_api_exception_with_level():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.01, "info": {"symbol": "BTCUSDT"}}
    ]
    exchange.fetch_open_orders.return_value = []
    exchange.private_post_v5_position_trading_stop = MagicMock(
        side_effect=ccxt.BaseError("boom")
    )
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "position_idx": 1,
            "tps": [
                {"sequence": 3, "price": 53000.0, "qty": 0.01},
            ],
        },
        client_order_id="tsb:1:1:rebuild:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is False
    assert result.error == "tp3: boom"


# --- get_capabilities ---

def test_get_capabilities_returns_correct_flags():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)
    caps = adapter.get_capabilities()

    assert caps.place_entry is True
    assert caps.protective_stop_native is True
    assert caps.take_profit_native is True
    assert caps.bracket_order is False
    assert caps.move_stop is True
    assert caps.close_partial is True
    assert caps.close_full is True


# --- fetch_mark_price ---

def test_fetch_mark_price_returns_mark_price():
    """fetch_mark_price ritorna markPrice dal ticker."""
    exchange = MagicMock()
    exchange.fetch_ticker.return_value = {"markPrice": 50123.45, "last": 50100.0}
    adapter = _make_adapter(exchange)
    result = adapter.fetch_mark_price("BTC/USDT", "acc1")
    assert result == 50123.45


def test_fetch_mark_price_falls_back_to_last():
    """fetch_mark_price usa 'last' se markPrice è assente."""
    exchange = MagicMock()
    exchange.fetch_ticker.return_value = {"last": 50100.0}
    adapter = _make_adapter(exchange)
    result = adapter.fetch_mark_price("BTC/USDT", "acc1")
    assert result == 50100.0


def test_fetch_mark_price_returns_none_on_error():
    """fetch_mark_price ritorna None se ccxt solleva eccezione."""
    exchange = MagicMock()
    exchange.fetch_ticker.side_effect = Exception("network error")
    adapter = _make_adapter(exchange)
    result = adapter.fetch_mark_price("BTC/USDT", "acc1")
    assert result is None


def test_fetch_recent_reduce_trades_returns_reduce_only_fills():
    """fetch_recent_reduce_trades filters to reduceOnly trades, normalizes symbol to raw."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_my_trades.return_value = [
        {
            "id": "trade-001",
            "symbol": "PHA/USDT:USDT",
            "side": "buy",
            "price": 0.05754,
            "amount": 3871.5,
            "info": {"reduceOnly": True},
        },
        {
            "id": "trade-002",
            "symbol": "PHA/USDT:USDT",
            "side": "sell",
            "price": 0.06000,
            "amount": 7743.0,
            "info": {"reduceOnly": False},  # entry fill -> excluded
        },
    ]
    adapter = CcxtBybitAdapter(
        api_key="k", api_secret="s", connector="c", _exchange=exchange
    )
    trades = adapter.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert len(trades) == 1
    assert trades[0].trade_id == "trade-001"
    assert trades[0].symbol == "PHAUSDT"   # raw format
    assert trades[0].price == 0.05754
    assert trades[0].amount == 3871.5
    assert trades[0].reduce_only is True


def test_fetch_recent_reduce_trades_returns_empty_on_exception():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_my_trades.side_effect = RuntimeError("network error")
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    trades = adapter.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert trades == []


def test_fetch_position_details_returns_tp_sl_from_info():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "PHA/USDT:USDT",
            "side": "short",
            "contracts": 3871.5,
            "info": {
                "symbol": "PHAUSDT",
                "side": "Sell",
                "takeProfit": "0.05373",
                "stopLoss": "0.06908",
            },
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    pos = adapter.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert pos is not None
    assert pos.symbol == "PHAUSDT"
    assert pos.qty == 3871.5
    assert pos.take_profit == 0.05373
    assert pos.stop_loss == 0.06908


def test_fetch_position_details_tp_zero_when_empty_string():
    """Bybit sets takeProfit='' when not configured; should map to 0.0."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "PHA/USDT:USDT",
            "side": "short",
            "contracts": 7743.0,
            "info": {"takeProfit": "", "stopLoss": "0.06908"},
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    pos = adapter.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert pos is not None
    assert pos.take_profit == 0.0   # empty string -> 0.0


def test_fetch_position_details_returns_none_when_not_found():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = []
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    pos = adapter.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert pos is None
