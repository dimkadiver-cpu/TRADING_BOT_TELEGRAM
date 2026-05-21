# tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import ccxt
from unittest.mock import MagicMock


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


def test_place_protective_stop_calls_create_order():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "exch_sl"}
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="PLACE_PROTECTIVE_STOP",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG",
                 "qty": 0.01, "stop_price": 49000.0},
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    args, kwargs = exchange.create_order.call_args
    assert args[1] == "market"
    assert args[2] == "sell"
    assert kwargs["params"]["triggerPrice"] == 49000.0
    assert kwargs["params"]["reduceOnly"] is True


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
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG", "entry_price": 50000.0},
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_called_once()
    edit_args, edit_kwargs = exchange.edit_order.call_args
    assert edit_args[0] == "sl_ord_1"
    assert edit_kwargs["params"]["triggerPrice"] == 50000.0


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
    assert caps.sync_protective_orders is True
