from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.parser_v2.contracts.canonical_message import (
    CanonicalMessage,
    InfoPayload,
    ReportPayload,
    SignalPayload,
    TargetedAction,
    UpdatePayload,
)
from src.parser_v2.contracts.context import ParserContext, RawContext, TargetHints
from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.Legacy.trader_a_legacy.profile import TraderAProfile


STOP_TO_BE = "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
EXIT_BE = "\u0437\u0430\u043a\u0440\u044b\u043b\u0441\u044f \u0432 \u0431\u0443"
TP1_HIT = "\u043f\u0435\u0440\u0432\u044b\u0439 \u0442\u0435\u0439\u043a \u0432\u0437\u044f\u043b\u0438"
SL_HIT = "\u0432\u044b\u0431\u0438\u043b\u043e \u043f\u043e \u0441\u0442\u043e\u043f\u0443"
CLOSE_CURRENT = (
    "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e "
    "\u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c"
)
FIX_50 = "\u0444\u0438\u043a\u0441 50%"
CANCEL_LIMITS = (
    "\u0443\u0431\u0438\u0440\u0430\u0435\u043c "
    "\u043b\u0438\u043c\u0438\u0442\u043a\u0438"
)
REPORT_RESULT = "\u0438\u0442\u043e\u0433 \u043f\u043e \u0441\u0434\u0435\u043b\u043a\u0435"
MARKET_OVERVIEW = "\u043e\u0431\u0437\u043e\u0440 \u0440\u044b\u043d\u043a\u0430"
CLOSE_ALL_REDUNDANT = (
    "\u0432\u0441\u0435\u043c \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e"
)
CLOSE_TAIL = "\u0430 \u0434\u0430\u0432\u0430\u0439\u0442\u0435 \u0438\u0445 \u043f\u0440\u0438\u043a\u0440\u043e\u0435\u043c"
CLOSE_ALL_SHORTS = (
    "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c "
    "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b"
)


def _parse(text: str) -> CanonicalMessage:
    return UniversalParserRuntime().parse(text, ParserContext(), TraderAProfile())


def _signal_text(*, symbol: bool = True, take_profit: bool = True) -> str:
    lines = []
    if symbol:
        lines.append("#ETHUSDT")
    lines.extend(["long", "entry: 2114", "stop: 2100"])
    if take_profit:
        lines.append("tp1: 2200")
    return "\n".join(lines)


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_event"),
    [
        (STOP_TO_BE, "MOVE_STOP_TO_BE", None),
        (EXIT_BE, "EXIT_BE", "EXIT_BE"),
        (TP1_HIT, "TP_HIT", "TP_HIT"),
        (SL_HIT, "SL_HIT", "SL_HIT"),
        (CLOSE_CURRENT, "CLOSE_FULL", None),
        (FIX_50, "CLOSE_PARTIAL", None),
        (CANCEL_LIMITS, "CANCEL_PENDING", None),
        (REPORT_RESULT, "REPORT_RESULT", None),
        (MARKET_OVERVIEW, "INFO_ONLY", None),
    ],
)
def test_phase13_golden_path_messages(
    text: str,
    expected_intent: str,
    expected_event: str | None,
) -> None:
    canonical = _parse(text)

    assert expected_intent in canonical.intents
    if expected_intent in {"MOVE_STOP_TO_BE", "CLOSE_FULL", "CLOSE_PARTIAL", "CANCEL_PENDING"}:
        assert canonical.primary_class == "UPDATE"
        assert canonical.update is not None
        assert canonical.update.operations
    elif expected_intent == "REPORT_RESULT":
        assert canonical.primary_class == "REPORT"
        assert canonical.report is not None
        assert canonical.report.result is not None
    elif expected_intent == "INFO_ONLY":
        assert canonical.primary_class == "INFO"
        assert canonical.info is not None
    else:
        assert canonical.primary_class == "REPORT"
        assert canonical.report is not None
        assert canonical.report.events[0].event_type == expected_event


def test_phase13_complete_signal_is_signal_parsed() -> None:
    canonical = _parse(_signal_text())

    assert canonical.primary_class == "SIGNAL"
    assert canonical.parse_status == "PARSED"
    assert canonical.signal is not None
    assert canonical.signal.missing_fields == []


def test_phase13_partial_signal_without_take_profit_keeps_missing_field() -> None:
    canonical = _parse(_signal_text(take_profit=False))

    assert canonical.primary_class == "SIGNAL"
    assert canonical.parse_status == "PARTIAL"
    assert canonical.signal is not None
    assert canonical.signal.missing_fields == ["take_profits"]


def test_phase13_partial_signal_without_symbol_stays_signal_partial() -> None:
    canonical = _parse(_signal_text(symbol=False))

    assert canonical.primary_class == "SIGNAL"
    assert canonical.parse_status == "PARTIAL"
    assert canonical.signal is not None
    assert "symbol" in canonical.signal.missing_fields


def test_phase13_stop_to_be_does_not_emit_exit_be() -> None:
    canonical = _parse(STOP_TO_BE)

    assert canonical.primary_class == "UPDATE"
    assert canonical.intents == ["MOVE_STOP_TO_BE"]
    assert canonical.update.operations[0].set_stop.target_type == "ENTRY"
    assert "EXIT_BE" not in canonical.intents


def test_phase13_close_partial_extracts_fraction() -> None:
    canonical = _parse(FIX_50)

    assert canonical.primary_class == "UPDATE"
    assert canonical.update.operations[0].close.close_scope == "PARTIAL"
    assert canonical.update.operations[0].close.fraction == 0.5


def test_phase13_tp_and_stop_to_be_composite_keeps_update_and_report() -> None:
    canonical = _parse(f"{TP1_HIT}, {STOP_TO_BE}")

    assert canonical.primary_class == "UPDATE"
    assert "MOVE_STOP_TO_BE" in canonical.intents
    assert canonical.update.operations[0].source_intent == "MOVE_STOP_TO_BE"
    assert canonical.report is not None
    assert canonical.report.events[0].event_type == "TP_HIT"
    assert canonical.report.events[0].level == 1


def test_phase13_sl_hit_with_redundant_close_full_stays_report_with_warning() -> None:
    canonical = _parse(f"{SL_HIT}, {CLOSE_ALL_REDUNDANT}")

    assert canonical.primary_class == "REPORT"
    assert canonical.primary_intent == "SL_HIT"
    assert canonical.report.events[0].event_type == "SL_HIT"
    assert canonical.update is None
    assert "close_full_redundant_with_sl_hit" in canonical.warnings


def test_phase13_unknown_and_empty_inputs_are_unclassified_info() -> None:
    for text in ["asdfgh", "", "   \n  ", "\U0001f680\U0001f525"]:
        canonical = _parse(text)

        assert canonical.primary_class == "INFO"
        assert canonical.parse_status == "UNCLASSIFIED"
        assert canonical.intents == []


def test_phase13_numeric_or_symbol_only_does_not_create_signal() -> None:
    for text in ["2114", "ETHUSDT"]:
        canonical = _parse(text)

        assert canonical.primary_class == "INFO"
        assert canonical.parse_status == "UNCLASSIFIED"
        assert canonical.signal is None


def test_phase13_long_and_mixed_unicode_text_does_not_crash_matcher() -> None:
    canonical = _parse("\U0001f680 \u5e02\u573a " + ("nonsense " * 800))

    assert canonical.primary_class == "INFO"
    assert canonical.parse_status == "UNCLASSIFIED"


def test_phase13_weak_marker_with_ignore_marker_emits_no_intent() -> None:
    canonical = _parse("#admin \u0431\u0443")

    assert canonical.primary_class == "INFO"
    assert canonical.parse_status == "UNCLASSIFIED"
    assert canonical.intents == []


def test_phase13_signal_with_update_marker_keeps_signal_and_warning() -> None:
    canonical = _parse(f"{_signal_text()}\n{CLOSE_CURRENT}")

    assert canonical.primary_class == "SIGNAL"
    assert canonical.parse_status == "PARSED"
    assert canonical.update is None
    assert "update_intents_dropped_in_signal_message" in canonical.warnings


def test_phase13_reply_context_without_command_text_does_not_parse_replied_message() -> None:
    canonical = UniversalParserRuntime().parse(
        "\U0001f680",
        ParserContext(reply_to_message_id=123),
        TraderAProfile(),
    )

    assert canonical.primary_class == "INFO"
    assert canonical.parse_status == "UNCLASSIFIED"
    assert canonical.target_hints.reply_to_message_id == 123


@pytest.mark.parametrize("raw_price", ["90 000,5", "90,000.5", "90.000,5"])
def test_phase13_signal_price_locales_normalize_to_same_value(raw_price: str) -> None:
    canonical = _parse(
        "\n".join(
            [
                "#BTCUSDT",
                "long",
                f"entry: {raw_price}",
                "stop: 89000",
                "tp1: 91000",
            ]
        )
    )

    assert canonical.primary_class == "SIGNAL"
    assert canonical.signal.entries[0].price.value == 90000.5


def test_phase13_multi_ref_same_intent_groups_targeted_action() -> None:
    canonical = _parse(
        "\n".join(
            [
                f"LINK - https://t.me/c/123/978 - {STOP_TO_BE}",
                f"ALGO - https://t.me/c/123/1002 - {STOP_TO_BE}",
            ]
        )
    )

    assert canonical.primary_class == "UPDATE"
    assert canonical.update.operations == []
    assert canonical.targeted_actions[0].action_type == "SET_STOP"
    assert canonical.targeted_actions[0].target_hints.telegram_message_ids == [978, 1002]


def test_phase13_multi_ref_tail_command_groups_close_full_action() -> None:
    canonical = _parse(
        "\n".join(
            [
                "XRP - https://t.me/c/123/1015",
                "ADA - https://t.me/c/123/1017",
                "SOL - https://t.me/c/123/1019",
                "",
                CLOSE_TAIL,
            ]
        )
    )

    assert canonical.primary_class == "UPDATE"
    assert canonical.primary_intent == "CLOSE_FULL"
    assert canonical.update.operations == []
    assert canonical.targeted_actions[0].action_type == "CLOSE"
    assert canonical.targeted_actions[0].params == {"close_scope": "FULL"}
    assert canonical.targeted_actions[0].target_hints.telegram_message_ids == [1015, 1017, 1019]


def test_phase13_global_selector_groups_close_full_action() -> None:
    canonical = _parse(CLOSE_ALL_SHORTS)

    assert canonical.primary_class == "UPDATE"
    assert canonical.primary_intent == "CLOSE_FULL"
    assert canonical.update.operations == []
    assert canonical.targeted_actions[0].action_type == "CLOSE"
    assert canonical.targeted_actions[0].target_hints.scope_hint == "ALL_SHORT"


def test_phase13_mixed_multi_ref_is_partial_non_executable() -> None:
    canonical = _parse(
        "\n".join(
            [
                f"https://t.me/c/123/111 {STOP_TO_BE}",
                f"https://t.me/c/123/222 {CLOSE_CURRENT}",
            ]
        )
    )

    assert canonical.primary_class == "UPDATE"
    assert canonical.parse_status == "PARTIAL"
    assert canonical.update.operations == []
    assert canonical.targeted_actions == []
    assert "multi_ref_mixed_intents_not_supported" in canonical.warnings


def test_phase13_schema_validation_cases() -> None:
    raw = RawContext(raw_text="raw")
    signal = SignalPayload(
        symbol="ETHUSDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[EntryLeg(sequence=1, entry_type="MARKET")],
        stop_loss=StopLoss(price=Price(raw="2100", value=2100.0)),
        take_profits=[TakeProfit(sequence=1, price=Price(raw="2200", value=2200.0))],
        completeness="COMPLETE",
    )

    with pytest.raises(ValidationError, match="SIGNAL forbids update"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=1.0,
            signal=signal,
            update=UpdatePayload(),
            raw_context=raw,
        )

    with pytest.raises(ValidationError, match="operation or targeted_action"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="UPDATE",
            parse_status="PARSED",
            confidence=1.0,
            update=UpdatePayload(),
            raw_context=raw,
        )

    partial = CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARTIAL",
        confidence=0.6,
        update=UpdatePayload(),
        warnings=["multi_ref_mixed_intents_not_supported"],
        raw_context=raw,
    )
    assert partial.parse_status == "PARTIAL"

    with pytest.raises(ValidationError, match="REPORT requires report payload"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="REPORT",
            parse_status="PARSED",
            confidence=1.0,
            raw_context=raw,
        )

    with pytest.raises(ValidationError, match="INFO forbids"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="INFO",
            parse_status="PARSED",
            confidence=1.0,
            info=InfoPayload(raw_fragment="info"),
            report=ReportPayload(),
            targeted_actions=[
                TargetedAction(
                    action_type="CLOSE",
                    params={"close_scope": "FULL"},
                    target_hints=TargetHints(scope_hint="ALL_SHORT"),
                    source_intent="CLOSE_FULL",
                )
            ],
            raw_context=raw,
        )
