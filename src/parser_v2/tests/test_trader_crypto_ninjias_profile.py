from __future__ import annotations

import pytest

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.trader_crypto_ninjias.profile import TraderCryptoNinjiasProfile

_runtime = UniversalParserRuntime()
_profile = TraderCryptoNinjiasProfile()


def _parse(text: str, reply_to: int | None = None):
    ctx = ParserContext(
        raw_context=RawContext(raw_text=text, reply_to_message_id=reply_to),
        reply_to_message_id=reply_to,
    )
    return _runtime.parse(text, ctx, _profile)


def test_standard_short_signal_parses_two_step_structure():
    result = _parse(
        "🔴 SHORT - $ZEC\n\n"
        "- Entry market: 397.93\n"
        "- Entry limit: 437.82\n"
        "- SL: 481.35\n\n"
        "🎯 TP1: 312\n"
        "🎯 TP2: 244.5\n"
        "🎯 TP3: 155.89\n"
        "🎯 TP4: 39.75\n"
    )
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.signal is not None
    assert result.signal.symbol == "ZECUSDT"
    assert result.signal.side == "SHORT"
    assert result.signal.entry_structure == "TWO_STEP"
    assert len(result.signal.entries) == 2
    assert result.signal.entries[0].entry_type == "MARKET"
    assert result.signal.entries[0].price.value == pytest.approx(397.93)
    assert result.signal.entries[1].entry_type == "LIMIT"
    assert result.signal.stop_loss.price.value == pytest.approx(481.35)
    assert [tp.sequence for tp in result.signal.take_profits] == [1, 2, 3, 4]


def test_long_limit_signal_uses_indexed_limit_entries():
    result = _parse(
        "🟢 LONG LIMIT - $VELO\n\n"
        "- Entry limit 1: 0.003047\n"
        "- Entry limit 2: 0.002836\n"
        "- SL: 0.002658\n\n"
        "🎯 TP1: 0.003420\n"
        "🎯 TP2: 0.003988\n"
        "🎯 TP3: 0.007663\n"
    )
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.signal is not None
    assert result.signal.symbol == "VELOUSDT"
    assert result.signal.entry_structure == "TWO_STEP"
    assert [entry.entry_type for entry in result.signal.entries] == ["LIMIT", "LIMIT"]
    assert result.signal.entries[1].price.value == pytest.approx(0.002836)


def test_inline_signal_without_stop_loss_is_partial():
    result = _parse(
        "SPX short headging entry 0.3239 entry limit 0.3410\n\n"
        "TP1 0.2926\n"
        "TP2 0.2608\n"
        "TP3 0.2252"
    )
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARTIAL"
    assert result.signal is not None
    assert result.signal.symbol == "SPXUSDT"
    assert result.signal.side == "SHORT"
    assert "stop_loss" in result.signal.missing_fields


def test_risk_order_with_entry_and_duplicate_tp_numbers_is_parsed():
    result = _parse(
        "LONG  - $DYDX - RISK ORDER - SMALL VOL\n\n"
        "- Entry: 0.1025\n"
        "- Entry limit: 0.0980\n"
        "- SL: 0.0940\n\n"
        "TP1: 0.1107\n"
        "TP2: 0.1201\n"
        "TP2: 0.1731\n\n"
        "Disclaimer"
    )
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.signal is not None
    assert result.signal.symbol == "DYDXUSDT"
    assert result.signal.side == "LONG"
    assert result.signal.entry_structure == "TWO_STEP"
    assert result.signal.entries[0].price.value == pytest.approx(0.1025)
    assert result.signal.entries[1].price.value == pytest.approx(0.0980)
    assert [tp.sequence for tp in result.signal.take_profits] == [1, 2, 3]
    assert result.signal.take_profits[2].price.value == pytest.approx(0.1731)


def test_risk_order_market_single_entry_single_tp_is_one_shot():
    result = _parse(
        "LONG MARKET - $SEI - RISK ORDER - SMALL VOL\n\n"
        "- Entry: 0.2970\n"
        "- SL: 0.2816\n"
        "TP: 0.4529"
    )
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.signal is not None
    assert result.signal.entry_structure == "ONE_SHOT"
    assert result.signal.entries[0].entry_type == "MARKET"
    assert result.signal.take_profits[0].sequence == 1
    assert result.signal.take_profits[0].price.value == pytest.approx(0.4529)


def test_risk_order_range_entry_becomes_two_step_limits():
    result = _parse(
        "LONG  - $RESOLV - RISK ORDER\n\n"
        "- Entry: 0.1511 - 0.1507\n"
        "- SL: 0.1437\n"
        "TP: 0.1992"
    )
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.signal is not None
    assert result.signal.entry_structure == "TWO_STEP"
    assert [entry.entry_type for entry in result.signal.entries] == ["LIMIT", "LIMIT"]
    assert result.signal.entries[0].price.value == pytest.approx(0.1507)
    assert result.signal.entries[1].price.value == pytest.approx(0.1511)


def test_manual_tp_hit_becomes_report_event():
    result = _parse("SHIBA hit TP2 + 2.7R", reply_to=9355)
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "TP_HIT"
    assert result.report is not None
    assert len(result.report.events) == 1
    assert result.report.events[0].event_type == "TP_HIT"
    assert result.report.events[0].level == 2


def test_full_tp_keeps_event_and_result_summary():
    result = _parse("DOGE hit full TP + 6R")
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "TP_HIT"
    assert result.report is not None
    assert result.report.events[0].event_type == "TP_HIT"
    assert result.report.result is not None
    assert result.report.result.raw_fragment == "DOGE hit full TP + 6R"


def test_move_stop_to_entry_is_update():
    result = _parse("SHIBA move sl to entry then wait", reply_to=9347)
    assert result.primary_class == "UPDATE"
    assert result.primary_intent == "MOVE_STOP_TO_BE"
    assert len(result.target_action_groups) == 1
    assert result.target_action_groups[0].actions[0].action_type == "SET_STOP"
    assert result.target_action_groups[0].actions[0].set_stop.target_type == "ENTRY"


def test_close_at_entry_is_close_full_update():
    result = _parse("DYDX close at entry, wait for new entry", reply_to=9349)
    assert result.primary_class == "UPDATE"
    assert result.primary_intent == "CLOSE_FULL"
    assert len(result.target_action_groups) == 1
    assert result.target_action_groups[0].actions[0].action_type == "CLOSE"
    assert result.target_action_groups[0].actions[0].close.close_scope == "FULL"


def test_cancel_entry_limit_is_cancel_pending_update():
    result = _parse("SOON cancel entry limit and cancel sl then wait", reply_to=9206)
    assert result.primary_class == "UPDATE"
    assert result.primary_intent == "CANCEL_PENDING"
    assert len(result.target_action_groups) == 1
    assert result.target_action_groups[0].actions[0].action_type == "CANCEL_PENDING"
    assert result.target_action_groups[0].actions[0].cancel_pending.cancel_scope_hint == "ALL_PENDING"


def test_hit_be_is_report():
    result = _parse("WLD hit BE", reply_to=9350)
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "EXIT_BE"
    assert result.report is not None
    assert result.report.events[0].event_type == "EXIT_BE"


def test_plain_r_result_becomes_report_result():
    result = _parse("XLM + 3R")
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "REPORT_RESULT"
    assert result.report is not None
    assert result.report.result is not None
    assert result.report.result.raw_fragment == "XLM + 3R"


def test_promo_message_is_info():
    result = _parse(
        "🔴 LIVESTREAM\n\n"
        "Turning $1,000 into $100,000 COPY TRADING CHALLENGE\n\n"
        "Link: https://youtube.com/live/8F5DVC7BH94?feature=share"
    )
    assert result.primary_class == "INFO"
    assert result.parse_status == "PARSED"
