# tests/runtime_v2/control_plane/test_clean_log_formatter.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log


def test_signal_accepted():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "trader_id": "trader_a",
    })
    assert "#145" in text
    assert "SIGNAL ACCEPTED" in text
    assert "BTC/USDT" in text
    assert "📈" in text          # LONG side emoji
    assert "Source:" in text


def test_review_required():
    text = format_clean_log("REVIEW_REQUIRED", {
        "chain_id": 147, "symbol": "ETH/USDT", "side": "SHORT",
        "reason": "ambiguous_entry_zone",
    })
    assert "REVIEW REQUIRED" in text
    assert "📉" in text          # SHORT side emoji
    assert "ambiguous_entry_zone" in text


def test_entry_opened():
    text = format_clean_log("ENTRY_OPENED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 65020.0, "filled_qty": 0.004,
    })
    assert "ENTRY OPENED" in text
    assert "65,020" in text or "65020" in text


def test_tp_filled():
    text = format_clean_log("TP_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG", "tp_level": 1,
    })
    assert "TP" in text and "FILLED" in text


def test_sl_filled_marks_closed():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
    })
    assert "SL FILLED" in text
    assert "POSITION CLOSED" in text
    assert "🛑" in text


def test_position_closed():
    text = format_clean_log("POSITION_CLOSED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
    })
    assert "POSITION CLOSED" in text


def test_unknown_type_has_safe_fallback():
    text = format_clean_log("WAT", {"chain_id": 1, "symbol": "X/Y", "side": "LONG"})
    assert "#1" in text
    assert "WAT" in text
