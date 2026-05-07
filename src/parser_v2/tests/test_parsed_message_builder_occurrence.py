from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.core.parsed_message_builder import ParsedMessageBuilder


def _make_intent(type_: str) -> ParsedIntent:
    return ParsedIntent(type=type_, category="UPDATE", confidence=0.9)


def _build(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    normalized = NormalizedText(raw_text="test", normalized_text="test")
    context = ParserContext(raw_context=RawContext(raw_text="test"))
    msg = ParsedMessageBuilder().build(
        parser_profile="test",
        normalized=normalized,
        context=context,
        intents=intents,
    )
    return msg.intents


def test_single_intent_gets_occurrence_index_zero():
    intents = _build([_make_intent("MOVE_STOP_TO_BE")])
    assert intents[0].occurrence_index == 0
    assert intents[0].intent_id == "MOVE_STOP_TO_BE#0"


def test_two_same_type_get_sequential_indices():
    intents = _build([
        _make_intent("MOVE_STOP_TO_BE"),
        _make_intent("MOVE_STOP_TO_BE"),
    ])
    assert intents[0].occurrence_index == 0
    assert intents[0].intent_id == "MOVE_STOP_TO_BE#0"
    assert intents[1].occurrence_index == 1
    assert intents[1].intent_id == "MOVE_STOP_TO_BE#1"


def test_different_types_each_start_at_zero():
    intents = _build([
        _make_intent("MOVE_STOP_TO_BE"),
        _make_intent("CANCEL_PENDING"),
        _make_intent("MOVE_STOP_TO_BE"),
    ])
    assert intents[0].intent_id == "MOVE_STOP_TO_BE#0"
    assert intents[1].intent_id == "CANCEL_PENDING#0"
    assert intents[2].intent_id == "MOVE_STOP_TO_BE#1"


def test_empty_intents_ok():
    intents = _build([])
    assert intents == []
