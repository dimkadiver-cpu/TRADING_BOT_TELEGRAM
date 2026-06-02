from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper


def _order(status: str, filled: float | str = 0.0, average: float | None = None) -> dict:
    return {"id": "ord123", "status": status, "filled": filled, "average": average}


@pytest.mark.parametrize("ccxt_status,expected_status", [
    ("open", "OPEN"),
    ("partially_filled", "OPEN"),
    ("closed", "FILLED"),
    ("canceled", "CANCELLED"),
    ("cancelled", "CANCELLED"),
    ("expired", "CANCELLED"),
    ("rejected", "FAILED"),
])
def test_status_mapper_status_strings(ccxt_status, expected_status):
    raw = StatusMapper.map(_order(ccxt_status), client_order_id="tsb:1:2:entry:1")
    assert raw.status == expected_status


def test_status_mapper_sets_exchange_order_id():
    raw = StatusMapper.map(_order("closed"), client_order_id="tsb:1:2:entry:1")
    assert raw.exchange_order_id == "ord123"


def test_status_mapper_sets_filled_qty():
    raw = StatusMapper.map(_order("closed", filled=0.05), client_order_id="tsb:1:2:entry:1")
    assert raw.filled_qty == 0.05


def test_status_mapper_sets_average_price():
    raw = StatusMapper.map(_order("closed", filled=0.01, average=50000.0),
                           client_order_id="tsb:1:2:entry:1")
    assert raw.average_price == 50000.0


def test_status_mapper_average_price_none_when_zero():
    raw = StatusMapper.map(_order("open", filled=0.0, average=0.0),
                           client_order_id="tsb:1:2:entry:1")
    assert raw.average_price is None


def test_status_mapper_is_filled_true_on_closed():
    raw = StatusMapper.map(_order("closed", filled=0.01, average=50000.0),
                           client_order_id="tsb:1:2:entry:1")
    assert raw.is_filled is True


def test_status_mapper_is_filled_false_on_open():
    raw = StatusMapper.map(_order("open"), client_order_id="tsb:1:2:entry:1")
    assert raw.is_filled is False


def test_status_mapper_uses_client_order_id():
    raw = StatusMapper.map(_order("open"), client_order_id="tsb:99:88:sl:2")
    assert raw.client_order_id == "tsb:99:88:sl:2"


def test_status_mapper_unknown_status_defaults_open():
    raw = StatusMapper.map(_order("pending"), client_order_id="tsb:1:2:entry:1")
    assert raw.status == "OPEN"


def test_status_mapper_normalizes_uppercase_status():
    raw = StatusMapper.map(_order("CLOSED"), client_order_id="tsb:1:2:entry:1")
    assert raw.status == "FILLED"


@pytest.mark.parametrize("ccxt_order", [
    {"id": "ord123", "filled": 0.0, "average": None},
    {"id": "ord123", "status": None, "filled": 0.0, "average": None},
])
def test_status_mapper_missing_status_defaults_open(ccxt_order):
    raw = StatusMapper.map(ccxt_order, client_order_id="tsb:1:2:entry:1")
    assert raw.status == "OPEN"


def test_status_mapper_uses_fallback_client_order_id():
    raw = StatusMapper.map({"clientOrderId": "tsb:fallback", "status": "open"})
    assert raw.client_order_id == "tsb:fallback"


def test_status_mapper_missing_id_maps_empty_exchange_order_id():
    raw = StatusMapper.map({"status": "open"}, client_order_id="tsb:1:2:entry:1")
    assert raw.exchange_order_id == ""


def test_status_mapper_converts_string_filled_to_float():
    raw = StatusMapper.map(_order("closed", filled="0.05"), client_order_id="tsb:1:2:entry:1")
    assert raw.filled_qty == 0.05


def _order_with_info(status: str, filled: float, average, info: dict) -> dict:
    return {"id": "ord789", "status": status, "filled": filled, "average": average, "info": info}


def test_status_mapper_captures_exec_fee():
    order = _order_with_info("closed", 0.01, 50000.0, {"cumExecFee": "0.275"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.exec_fee == 0.275


def test_status_mapper_captures_exec_value():
    order = _order_with_info("closed", 0.01, 50000.0, {"cumExecValue": "500.0"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.exec_value == 500.0


def test_status_mapper_captures_exchange_time():
    order = _order_with_info("closed", 0.01, 50000.0, {"updatedTime": "1748764800000"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.exchange_time is not None
    assert "2025" in raw.exchange_time or "2026" in raw.exchange_time


def test_status_mapper_captures_leaves_qty():
    order = _order_with_info("closed", 0.01, 50000.0, {"leavesQty": "0.0"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.leaves_qty == 0.0


def test_status_mapper_captures_cum_exec_qty():
    order = _order_with_info("closed", 0.01, 50000.0, {"cumExecQty": "0.01"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.cum_exec_qty == 0.01


def test_status_mapper_none_when_info_missing():
    raw = StatusMapper.map({"id": "x", "status": "closed", "filled": 0.01}, client_order_id="coid")
    assert raw.exec_fee is None
    assert raw.exec_value is None
    assert raw.exchange_time is None
    assert raw.leaves_qty is None
    assert raw.cum_exec_qty is None
