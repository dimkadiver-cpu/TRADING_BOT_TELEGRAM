from __future__ import annotations

from src.parser_v2.contracts.context import TargetHints
from src.parser_v2.contracts.entities import (
    EntryLeg,
    ExitBeEntities,
    InfoOnlyEntities,
    MoveStopToBEEntities,
    Price,
    StopLoss,
    TakeProfit,
)
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.core.classification_resolver import ClassificationResolver


def _intent(intent_type: str, entities=None) -> ParsedIntent:
    category = {
        "MOVE_STOP_TO_BE": "UPDATE",
        "EXIT_BE": "REPORT",
        "INFO_ONLY": "INFO",
    }[intent_type]
    return ParsedIntent(
        type=intent_type,
        category=category,
        confidence=1.0,
        entities=entities or {},
        raw_fragment=intent_type.lower(),
    )


def _signal(*, completeness: str, missing_fields: list[str] | None = None) -> SignalDraft:
    return SignalDraft(
        symbol="BTCUSDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[EntryLeg(sequence=1, entry_type="MARKET", role="PRIMARY")],
        stop_loss=StopLoss(price=Price(raw="61000", value=61000.0)),
        take_profits=(
            [TakeProfit(sequence=1, price=Price(raw="63000", value=63000.0))]
            if completeness == "COMPLETE"
            else []
        ),
        missing_fields=missing_fields or [],
        completeness=completeness,
    )


def test_signal_partial_stays_signal_partial() -> None:
    result = ClassificationResolver().resolve(
        signal=_signal(completeness="INCOMPLETE", missing_fields=["take_profits"]),
        intents=[_intent("MOVE_STOP_TO_BE", MoveStopToBEEntities())],
    )

    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARTIAL"


def test_complete_signal_is_signal_parsed() -> None:
    result = ClassificationResolver().resolve(
        signal=_signal(completeness="COMPLETE"),
        intents=[],
    )

    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"


def test_update_without_target_stays_update_with_warning() -> None:
    result = ClassificationResolver().resolve(
        signal=None,
        intents=[_intent("MOVE_STOP_TO_BE", MoveStopToBEEntities())],
        target_hints=None,
    )

    assert result.primary_class == "UPDATE"
    assert result.parse_status == "PARSED"
    assert result.warnings == ["update_without_target_hint"]


def test_update_with_target_hint_does_not_emit_target_warning() -> None:
    result = ClassificationResolver().resolve(
        signal=None,
        intents=[_intent("MOVE_STOP_TO_BE", MoveStopToBEEntities())],
        target_hints=TargetHints(reply_to_message_id=123),
    )

    assert result.primary_class == "UPDATE"
    assert result.parse_status == "PARSED"
    assert result.warnings == []


def test_report_does_not_become_update() -> None:
    result = ClassificationResolver().resolve(
        signal=None,
        intents=[_intent("EXIT_BE", ExitBeEntities())],
    )

    assert result.primary_class == "REPORT"
    assert result.parse_status == "PARSED"
    assert result.warnings == []


def test_update_dominates_report_in_composite_message() -> None:
    result = ClassificationResolver().resolve(
        signal=None,
        intents=[
            _intent("EXIT_BE", ExitBeEntities()),
            _intent("MOVE_STOP_TO_BE", MoveStopToBEEntities()),
        ],
    )

    assert result.primary_class == "UPDATE"
    assert result.parse_status == "PARSED"


def test_info_marker_creates_info_parsed() -> None:
    result = ClassificationResolver().resolve(
        signal=None,
        intents=[_intent("INFO_ONLY", InfoOnlyEntities(raw_fragment="market overview"))],
    )

    assert result.primary_class == "INFO"
    assert result.parse_status == "PARSED"
    assert result.warnings == []


def test_empty_or_no_markers_is_info_unclassified() -> None:
    result = ClassificationResolver().resolve(signal=None, intents=[])

    assert result.primary_class == "INFO"
    assert result.parse_status == "UNCLASSIFIED"
    assert result.warnings == []
