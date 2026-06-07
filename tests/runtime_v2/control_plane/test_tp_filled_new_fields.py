from __future__ import annotations
from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log


def _tp_payload(fee_rate=None, exec_value=None, include_fee_rate=True, include_exec_value=True):
    p = {
        "chain_id": 1,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "tp_level": 1,
        "tp_price": 55000.0,
        "fill_price": 55000.0,
        "closed_pct": 50.0,
        "pnl": 5.0,
        "fee": 0.275,
        "remaining_pct": 50.0,
        "sl_current": 49000.0,
        "source": "watch_my_trades",
    }
    if include_fee_rate:
        p["fee_rate"] = fee_rate
    if include_exec_value:
        p["exec_value"] = exec_value
    return p


def test_tp_filled_shows_fee_rate_when_present():
    p = _tp_payload(fee_rate=0.00055, exec_value=550.0)
    text = format_clean_log("TP_FILLED", p)
    assert "Fee rate:" in text
    assert "0.055%" in text


def test_tp_filled_shows_exec_value_when_present():
    p = _tp_payload(fee_rate=0.00055, exec_value=550.0)
    text = format_clean_log("TP_FILLED", p)
    assert "Value:" in text
    assert "550" in text


def test_tp_filled_shows_na_fee_rate_for_rest_path():
    """REST path: fee_rate is None -> show 'n/a'."""
    p = _tp_payload(fee_rate=None, exec_value=500.0)
    text = format_clean_log("TP_FILLED", p)
    assert "Fee rate: n/a" in text


def test_tp_filled_shows_na_exec_value_for_rest_path():
    """REST path: exec_value is None -> show 'n/a'."""
    p = _tp_payload(fee_rate=None, exec_value=None)
    text = format_clean_log("TP_FILLED", p)
    assert "Value: n/a" in text


def test_tp_filled_renders_remaining_section_when_payload_has_values():
    p = _tp_payload(fee_rate=0.00055, exec_value=550.0)
    p["remaining_qty"] = 0.005
    p["avg_entry"] = 50000.0
    p["remaining_risk"] = 5.0
    text = format_clean_log("TP_FILLED", p)
    assert "Remaining:" in text
    assert "Qty: 0.005" in text
    assert "Avg entry: 50,000" in text
    assert "Risk: 5.00 USDT" in text
