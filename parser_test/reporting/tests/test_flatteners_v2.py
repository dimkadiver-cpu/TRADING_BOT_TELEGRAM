from __future__ import annotations

import json

from parser_test.reporting.flatteners_v2 import ReportRow, flatten_for_scope


def _signal_row() -> ReportRow:
    canonical = {
        "schema_version": "2.0",
        "parser_profile": "trader_a",
        "primary_class": "SIGNAL",
        "parse_status": "PARSED",
        "primary_intent": "NEW_SIGNAL",
        "intents": ["NEW_SIGNAL"],
        "confidence": 0.95,
        "warnings": [],
        "diagnostics": {},
        "signal": {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_structure": "ONE_SHOT",
            "entries": [{"sequence": 1, "entry_type": "LIMIT", "price": {"raw": "30000", "value": 30000.0}, "role": "PRIMARY", "is_optional": False}],
            "stop_loss": {"price": {"raw": "29000", "value": 29000.0}},
            "take_profits": [
                {"sequence": 1, "price": {"raw": "31000", "value": 31000.0}},
                {"sequence": 2, "price": {"raw": "32000", "value": 32000.0}},
            ],
            "risk_hint": {"raw": "1%", "value": 1.0},
            "leverage_hint": None,
            "missing_fields": [],
            "completeness": "COMPLETE",
        },
        "raw_context": {"raw_text": "BUY BTCUSDT @ 30000"},
    }
    return ReportRow(
        run_id=1,
        raw_message_id=10,
        trader_id="trader_a",
        parser_profile="trader_a",
        primary_class="SIGNAL",
        parse_status="PARSED",
        primary_intent="NEW_SIGNAL",
        confidence=0.95,
        canonical_json=json.dumps(canonical),
        warnings_json=None,
        diagnostics_json=None,
        error_status="OK",
        error_message=None,
        telegram_message_id=42,
        source_chat_id="chat1",
        source_topic_id=None,
        reply_to_message_id=None,
        message_ts="2026-05-01T10:00:00",
        raw_text="BUY BTCUSDT @ 30000",
    )


def test_flatten_signal_common_fields():
    row = _signal_row()
    result = flatten_for_scope("NEW_SIGNAL", row)
    assert result["run_id"] == 1
    assert result["raw_message_id"] == 10
    assert result["primary_class"] == "SIGNAL"
    assert result["parse_status"] == "PARSED"
    assert result["trader_id"] == "trader_a"


def test_flatten_signal_symbol_side():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["symbol"] == "BTCUSDT"
    assert result["side"] == "LONG"


def test_flatten_signal_entries():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["entries_count"] == 1
    assert "30000" in result["entries_summary"]


def test_flatten_signal_take_profits():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["take_profit_count"] == 2
    assert "31000" in result["take_profit_prices"]
    assert "32000" in result["take_profit_prices"]


def test_flatten_signal_stop_loss():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["stop_loss_price"] == 29000.0


def test_flatten_all_scope_no_signal_columns():
    result = flatten_for_scope("ALL", _signal_row())
    assert "symbol" not in result
    assert "entries_count" not in result


def test_flatten_errors_scope():
    row = ReportRow(
        run_id=1,
        raw_message_id=5,
        trader_id=None,
        parser_profile=None,
        primary_class=None,
        parse_status=None,
        primary_intent=None,
        confidence=None,
        canonical_json=None,
        warnings_json=None,
        diagnostics_json=None,
        error_status="PARSER_ERROR",
        error_message="boom",
        telegram_message_id=99,
        source_chat_id="chat1",
        source_topic_id=None,
        reply_to_message_id=None,
        message_ts="2026-05-01",
        raw_text="testo",
    )
    result = flatten_for_scope("ERRORS", row)
    assert result["error_status"] == "PARSER_ERROR"
    assert result["error_message"] == "boom"
    assert "symbol" not in result
