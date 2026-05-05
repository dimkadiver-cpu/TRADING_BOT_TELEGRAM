from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext, TargetHints
from src.parser_v2.contracts.entities import MoveStopToBEEntities
from src.parser_v2.contracts.markers import MarkerEvidence, MarkerMatch, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.core.parsed_message_builder import ParsedMessageBuilder


STOP_TO_BE = "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
BE = "\u0431\u0443"


def _normalized(raw: str = "\u0421\u0442\u043e\u043f \u0432 \u0411\u0423") -> NormalizedText:
    return NormalizedText(raw_text=raw, normalized_text=STOP_TO_BE, lines=[STOP_TO_BE])


def _move_stop_to_be_intent() -> ParsedIntent:
    return ParsedIntent(
        type="MOVE_STOP_TO_BE",
        category="UPDATE",
        confidence=1.0,
        entities=MoveStopToBEEntities(),
        evidence=[
            MarkerEvidence(
                name="MOVE_STOP_TO_BE",
                kind="intent",
                strength="strong",
                marker=STOP_TO_BE,
                start=0,
                end=9,
            )
        ],
        raw_fragment=STOP_TO_BE,
    )


def test_builds_parsed_message_with_diagnostics_warnings_and_target_hints() -> None:
    matched_markers = [
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="strong",
            marker=STOP_TO_BE,
            start=0,
            end=9,
        ),
        MarkerMatch(
            name="EXIT_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=7,
            end=9,
        ),
    ]
    suppressed_markers = [
        MarkerEvidence(
            name="EXIT_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=7,
            end=9,
            suppressed=True,
            suppressed_by="MOVE_STOP_TO_BE",
            reason="command_marker_dominates_be_status_marker",
        )
    ]

    parsed = ParsedMessageBuilder().build(
        parser_profile="trader_a",
        normalized=_normalized(),
        context=ParserContext(reply_to_message_id=42, source_chat_id="-1001"),
        intents=[_move_stop_to_be_intent()],
        primary_intent="MOVE_STOP_TO_BE",
        target_hints=TargetHints(reply_to_message_id=42),
        matched_markers=matched_markers,
        suppressed_markers=suppressed_markers,
        applied_marker_rules=["command_marker_dominates_be_status_marker"],
        applied_disambiguation_rules=["prefer_move_stop_to_be_over_move_stop"],
        warnings=["manual_review_hint"],
    )

    assert parsed.primary_class == "UPDATE"
    assert parsed.parse_status == "PARSED"
    assert parsed.confidence == 1.0
    assert parsed.evidence_status == "RESOLVED"
    assert parsed.primary_intent == "MOVE_STOP_TO_BE"
    assert parsed.target_hints == TargetHints(reply_to_message_id=42)
    assert parsed.warnings == ["manual_review_hint"]
    assert parsed.raw_context.raw_text == "\u0421\u0442\u043e\u043f \u0432 \u0411\u0423"
    assert parsed.raw_context.normalized_text == STOP_TO_BE
    assert parsed.raw_context.reply_to_message_id == 42
    assert parsed.raw_context.source_chat_id == "-1001"
    assert parsed.diagnostics["matched_markers"] == [
        "MOVE_STOP_TO_BE/strong:\u0441\u0442\u043e\u043f \u0432 \u0431\u0443@0:9",
        "EXIT_BE/weak:\u0431\u0443@7:9",
    ]
    assert parsed.diagnostics["suppressed_markers"] == [
        "EXIT_BE/weak:\u0431\u0443@7:9"
    ]
    assert parsed.diagnostics["applied_marker_rules"] == [
        "command_marker_dominates_be_status_marker"
    ]
    assert parsed.diagnostics["applied_disambiguation_rules"] == [
        "prefer_move_stop_to_be_over_move_stop"
    ]
    assert parsed.diagnostics["applied_rules"] == [
        "command_marker_dominates_be_status_marker",
        "prefer_move_stop_to_be_over_move_stop",
    ]
    assert parsed.diagnostics["category_scores"] == {"UPDATE": 1.0}


def test_build_preserves_existing_raw_context_and_fills_normalized_text() -> None:
    parsed = ParsedMessageBuilder().build(
        parser_profile="trader_a",
        normalized=_normalized(),
        context=ParserContext(
            raw_context=RawContext(
                raw_text="\u0421\u0442\u043e\u043f \u0432 \u0411\u0423",
                message_id=100,
                reply_to_message_id=99,
                source_chat_id="-1002",
                extracted_links=["https://t.me/c/1/2"],
            )
        ),
        intents=[_move_stop_to_be_intent()],
        primary_intent="MOVE_STOP_TO_BE",
        target_hints=TargetHints(reply_to_message_id=99),
    )

    assert parsed.raw_context.message_id == 100
    assert parsed.raw_context.reply_to_message_id == 99
    assert parsed.raw_context.source_chat_id == "-1002"
    assert parsed.raw_context.extracted_links == ["https://t.me/c/1/2"]
    assert parsed.raw_context.normalized_text == STOP_TO_BE


def test_no_signal_and_no_intents_builds_unclassified_info_without_db_validation() -> None:
    normalized = NormalizedText(raw_text="asdfgh", normalized_text="asdfgh", lines=["asdfgh"])

    parsed = ParsedMessageBuilder().build(
        parser_profile="trader_a",
        normalized=normalized,
        context=ParserContext(message_id=7),
        intents=[],
        target_hints=None,
    )

    assert parsed.primary_class == "INFO"
    assert parsed.parse_status == "UNCLASSIFIED"
    assert parsed.confidence == 0.0
    assert parsed.evidence_status == "LOW_CONFIDENCE"
    assert parsed.intents == []
    assert parsed.primary_intent is None
    assert parsed.warnings == []
    assert parsed.raw_context.message_id == 7
    assert parsed.diagnostics["matched_markers"] == []
    assert parsed.diagnostics["suppressed_markers"] == []
    assert parsed.diagnostics["applied_rules"] == []
