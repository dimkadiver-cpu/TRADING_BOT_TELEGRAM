from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import (
    TargetCandidate, TargetExtractionResult, TargetHints,
)
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.core.target_binding_resolver import TargetBindingResolver


def _make_intent(type_: str, line_index: int = 0, occurrence_index: int = 0) -> ParsedIntent:
    return ParsedIntent(
        type=type_,
        category="UPDATE",
        confidence=0.9,
        line_index=line_index,
        occurrence_index=occurrence_index,
        intent_id=f"{type_}#{occurrence_index}",
    )


def _make_link_candidate(msg_id: int, line_index: int, start: int = 0) -> TargetCandidate:
    return TargetCandidate(
        source="MESSAGE_TEXT_LINK",
        value=msg_id,
        start=start,
        end=start + 25,
        line_index=line_index,
    )


def _make_extraction(
    candidates: list[TargetCandidate],
    reply_id: int | None = None,
    msg_ids: list[int] | None = None,
) -> TargetExtractionResult:
    target_source = "UNKNOWN"
    if msg_ids:
        target_source = "MESSAGE_TEXT_LINK"
    elif reply_id:
        target_source = "REPLY"
    hints = TargetHints(
        target_source=target_source,
        reply_to_message_id=reply_id,
        telegram_message_ids=msg_ids or [],
    )
    return TargetExtractionResult(message_target_hints=hints, candidates=candidates)


def test_reply_no_local_binding():
    intents = [_make_intent("MOVE_STOP_TO_BE", line_index=0)]
    extraction = _make_extraction(
        candidates=[TargetCandidate(source="REPLY", value=100)],
        reply_id=100,
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.message_target_hints.reply_to_message_id == 100
    assert result.message_target_hints.target_source == "REPLY"
    assert result.intents[0].target_hints is None


def test_global_links_no_local_binding():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=2, occurrence_index=0),
        _make_intent("CANCEL_PENDING", line_index=3, occurrence_index=0),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0),
            _make_link_candidate(222, line_index=1),
        ],
        msg_ids=[111, 222],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.intents[0].target_hints is None
    assert result.intents[1].target_hints is None
    assert result.message_target_hints.telegram_message_ids == [111, 222]


def test_line_level_one_to_one_binding():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0, occurrence_index=0),
        _make_intent("CLOSE_FULL", line_index=1, occurrence_index=0),
        _make_intent("CANCEL_PENDING", line_index=2, occurrence_index=0),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0),
            _make_link_candidate(222, line_index=1),
            _make_link_candidate(333, line_index=2),
        ],
        msg_ids=[111, 222, 333],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.intents[0].target_hints.telegram_message_ids == [111]
    assert result.intents[0].target_hints.target_source == "LOCAL_TEXT_LINK"
    assert result.intents[1].target_hints.telegram_message_ids == [222]
    assert result.intents[2].target_hints.telegram_message_ids == [333]


def test_two_same_intents_two_links_binds_one_to_one():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP_TO_BE", line_index=1, occurrence_index=1),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0),
            _make_link_candidate(222, line_index=1),
        ],
        msg_ids=[111, 222],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.intents[0].target_hints.telegram_message_ids == [111]
    assert result.intents[1].target_hints.telegram_message_ids == [222]


def test_ambiguous_binding_produces_partial_warning():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0, occurrence_index=0),
        _make_intent("CLOSE_FULL", line_index=0, occurrence_index=0),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0, start=0),
            _make_link_candidate(222, line_index=0, start=30),
        ],
        msg_ids=[111, 222],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert "ambiguous_target_intent_binding" in result.warnings


def test_text_link_wins_over_reply():
    intents = [_make_intent("MOVE_STOP_TO_BE", line_index=0)]
    extraction = TargetExtractionResult(
        message_target_hints=TargetHints(
            target_source="MESSAGE_TEXT_LINK",
            reply_to_message_id=100,
            telegram_message_ids=[222],
        ),
        candidates=[
            TargetCandidate(source="REPLY", value=100),
            TargetCandidate(source="MESSAGE_TEXT_LINK", value=222, start=0, end=25, line_index=0),
        ],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.message_target_hints.target_source == "MESSAGE_TEXT_LINK"
    assert result.diagnostics.get("ignored_reply_to_message_id") == 100
