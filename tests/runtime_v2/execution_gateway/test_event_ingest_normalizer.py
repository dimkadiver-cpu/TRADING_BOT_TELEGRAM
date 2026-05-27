# tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py
from __future__ import annotations
import pytest


def test_from_trade_tp_position_level():
    """watchMyTrades TP fill: createType and stopOrderType extracted."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    trade = {
        "id": "exec-001",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 45000.0,
        "amount": 0.01,
        "info": {
            "execId": "exec-001",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "createType": "CreateByTakeProfit",
            "stopOrderType": "TakeProfit",
            "execType": "Trade",
            "closedSize": "0.01",
            "posQty": "0",
            "orderLinkId": "",
            "orderId": "ord-001",
            "seq": "12345",
            "execPrice": "45000",
            "execQty": "0.01",
            "execValue": "450",
            "execFee": "0.18",
            "feeRate": "0.0004",
            "cumExecQty": "0.01",
            "execTime": "1716800000000",
        },
    }
    n = EventNormalizer()
    raw = n.from_trade(trade)
    assert raw is not None
    assert raw.source_stream == "watch_my_trades"
    assert raw.symbol == "BTCUSDT"
    assert raw.side == "Sell"
    assert raw.create_type == "CreateByTakeProfit"
    assert raw.stop_order_type == "TakeProfit"
    assert raw.exec_type == "Trade"
    assert raw.closed_size == 0.01
    assert raw.pos_qty == 0.0
    assert raw.order_link_id == ""
    assert raw.seq == 12345
    assert raw.exec_price == 45000.0
    assert raw.exec_fee == 0.18
    assert raw.idempotency_key == "exec:exec-001"
    assert raw.exchange_time is not None


def test_from_trade_entry_with_order_link_id():
    """watchMyTrades entry fill: our clientOrderId present."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    trade = {
        "id": "exec-002",
        "symbol": "PHA/USDT:USDT",
        "side": "buy",
        "price": 0.15,
        "amount": 100.0,
        "info": {
            "execId": "exec-002",
            "symbol": "PHAUSDT",
            "side": "Buy",
            "createType": "CreateByUser",
            "stopOrderType": "",
            "execType": "Trade",
            "closedSize": "0",
            "posQty": "100",
            "orderLinkId": "tsb:10:5001:entry:1",
            "orderId": "ord-002",
            "seq": "99",
            "execPrice": "0.15",
            "execQty": "100",
            "execValue": "15",
            "execFee": "0.006",
            "feeRate": "0.0004",
            "cumExecQty": "100",
            "execTime": "1716800001000",
        },
    }
    n = EventNormalizer()
    raw = n.from_trade(trade)
    assert raw is not None
    assert raw.symbol == "PHAUSDT"
    assert raw.create_type == "CreateByUser"
    assert raw.stop_order_type == ""
    assert raw.closed_size == 0.0
    assert raw.order_link_id == "tsb:10:5001:entry:1"


def test_from_trade_returns_none_on_missing_id():
    """No execId → return None (skip gracefully)."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    n = EventNormalizer()
    assert n.from_trade({"symbol": "BTC/USDT:USDT", "side": "buy", "info": {}}) is None


def test_from_order_cancelled():
    """watchOrders cancelled entry order."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    order = {
        "id": "ord-cancel-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "status": "canceled",
        "average": None,
        "filled": 0.0,
        "clientOrderId": "tsb:42:999:entry:1",
        "info": {
            "orderId": "ord-cancel-1",
            "orderLinkId": "tsb:42:999:entry:1",
            "orderStatus": "Cancelled",
            "createType": "CreateByUser",
            "stopOrderType": "",
            "side": "Buy",
            "symbol": "BTCUSDT",
            "cumExecQty": "0",
            "leavesQty": "0",
            "updatedTime": "1716800002000",
        },
    }
    n = EventNormalizer()
    raw = n.from_order(order)
    assert raw is not None
    assert raw.source_stream == "watch_orders"
    assert raw.order_status == "Cancelled"
    assert raw.order_link_id == "tsb:42:999:entry:1"
    assert raw.idempotency_key == "order:ord-cancel-1:Cancelled"


def test_from_position_tp_removed():
    """watchPositions: takeProfit field set to 0."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    position = {
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "contracts": 0.01,
        "info": {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "0.01",
            "takeProfit": "0",
            "stopLoss": "42000",
            "seq": "55555",
            "updatedTime": "1716800003000",
        },
    }
    n = EventNormalizer()
    raw = n.from_position(position)
    assert raw is not None
    assert raw.source_stream == "watch_positions"
    assert raw.position_take_profit == 0.0
    assert raw.position_stop_loss == 42000.0
    assert raw.seq == 55555
    assert raw.idempotency_key == "pos:BTCUSDT:Buy:55555"


def test_from_rest_trade_different_idempotency():
    """from_rest_trade uses rest_exec: prefix to coexist with WS in DB."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    trade = {
        "id": "exec-003",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 45000.0,
        "amount": 0.01,
        "info": {
            "execId": "exec-003",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "createType": "CreateByTakeProfit",
            "stopOrderType": "TakeProfit",
            "execType": "Trade",
            "closedSize": "0.01",
            "posQty": "0",
            "orderLinkId": "",
            "orderId": "ord-003",
            "seq": "777",
            "execPrice": "45000",
            "execQty": "0.01",
            "execValue": "450",
            "execFee": "0.18",
            "feeRate": "0.0004",
            "cumExecQty": "0.01",
        },
    }
    n = EventNormalizer()
    raw = n.from_rest_trade(trade)
    assert raw is not None
    assert raw.source_stream == "fetch_my_trades"
    assert raw.idempotency_key == "rest_exec:exec-003"


def test_ccxt_symbol_conversion():
    """PHA/USDT:USDT → PHAUSDT, BTCUSDT stays BTCUSDT."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import _ccxt_symbol_to_raw
    assert _ccxt_symbol_to_raw("PHA/USDT:USDT") == "PHAUSDT"
    assert _ccxt_symbol_to_raw("BTC/USDT:USDT") == "BTCUSDT"
    assert _ccxt_symbol_to_raw("BTCUSDT") == "BTCUSDT"
