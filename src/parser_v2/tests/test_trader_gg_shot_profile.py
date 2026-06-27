from __future__ import annotations

import pytest

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.trader_gg_shot.profile import TraderGgShotProfile

_runtime = UniversalParserRuntime()
_profile = TraderGgShotProfile()


def _parse(text: str, reply_to: int | None = None):
    ctx = ParserContext(
        raw_context=RawContext(raw_text=text, reply_to_message_id=reply_to),
        reply_to_message_id=reply_to,
    )
    return _runtime.parse(text, ctx, _profile)


def test_entry_zone_signal_parses_two_step_limit_signal():
    result = _parse(
        "📩 #SEIUSDT 30m | Mid-Term\n"
        "📉 Short Entry Zone: 0.05272-0.05547\n\n"
        "🎯 Strategy Accuracy: 97%\n"
        "Shorts: 97% | Longs: 97%\n\n"
        "⏳ Signal Details:\n"
        "Target 1: 0.05124\n"
        "Target 2: 0.04977\n"
        "Target 3: 0.04829\n"
        "Target 4: 0.04386\n\n"
        "🔺 Stop-Loss: 0.05702\n"
        "💡 After reaching the first target you can put the rest of the position to breakeven.\n"
        "🔎 Signal ID: #ID4684806824"
    )
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.signal is not None
    assert result.signal.symbol == "SEIUSDT"
    assert result.signal.side == "SHORT"
    assert result.signal.entry_structure == "TWO_STEP"
    assert [entry.entry_type for entry in result.signal.entries] == ["LIMIT", "LIMIT"]
    assert result.signal.entries[0].price.value == pytest.approx(0.05272)
    assert result.signal.entries[1].price.value == pytest.approx(0.05547)
    assert result.signal.stop_loss is not None
    assert result.signal.stop_loss.price.value == pytest.approx(0.05702)
    assert [tp.sequence for tp in result.signal.take_profits] == [1, 2, 3, 4]


def test_structured_report_stop_loss_becomes_sl_hit():
    result = _parse(
        "📬 Report on #BNBUSDT 30m | Mid-Term\n"
        "📈 Long was opened at - 600.62\n"
        "📆 Time: 22/06/2026 12:39 UTC\n"
        "🕞 Duration: 17h 24m\n\n"
        "❌ Reaching Stop-Loss: -6% (x10lev)\n\n"
        "🔎 Signal ID: #ID1319403855 | #Report"
    )
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "SL_HIT"
    assert result.report is not None
    assert len(result.report.events) == 1
    assert result.report.events[0].event_type == "SL_HIT"


def test_compact_reply_target_done_becomes_tp_hit():
    result = _parse(
        "#CAKE two targets done ✅\n\n"
        "This FREE signal so far printed:\n\n"
        "+52% profit (10x lev)\n"
        "+104% profit (20x lev)",
        reply_to=8328,
    )
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "TP_HIT"
    assert result.report is not None
    assert len(result.report.events) == 1
    assert result.report.events[0].event_type == "TP_HIT"
    assert result.report.events[0].level == 2


def test_market_analysis_message_is_info():
    result = _parse(
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 BTC - Market Analysis 📉 Bearish\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Levels to watch:\n\n"
        "- Key: 67,255\n"
        "- Support: 65,081 / 63,500\n"
        "- Resistance: 67,255\n\n"
        "BTC 65,781 - the book is loaded one way and the path of least resistance runs against it.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 VΛLIDATOR #MA #2026061706\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    assert result.primary_class == "INFO"
    assert result.parse_status == "PARSED"


def test_two_day_analysis_message_is_info():
    result = _parse(
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 BTC - 2-Day Analysis 📉 Bearish\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Levels to watch:\n\n"
        "- Key: 58,030\n"
        "- Support: 58,675 / 58,030\n"
        "- Resistance: 61,642 / 62,319\n\n"
        "BTC 59,807 - sellers still own the tape over the next 48h.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 VΛLIDATOR #MA #2026062606\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    assert result.primary_class == "INFO"
    assert result.parse_status == "PARSED"


def test_closed_at_the_entrance_is_close_full_update():
    result = _parse("Closed at the entrance #NEO", reply_to=8245)
    assert result.primary_class == "UPDATE"
    assert result.primary_intent == "CLOSE_FULL"
    assert len(result.target_action_groups) == 1
    assert result.target_action_groups[0].targeting.reply_to_message_id == 8245
    assert result.target_action_groups[0].actions[0].action_type == "CLOSE"
    assert result.target_action_groups[0].actions[0].close is not None
    assert result.target_action_groups[0].actions[0].close.close_scope == "FULL"
