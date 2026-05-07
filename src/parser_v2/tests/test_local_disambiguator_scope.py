from __future__ import annotations
import pytest
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.contracts.rules import ParserRules
from src.parser_v2.core.local_disambiguator import LocalDisambiguator


def _make_intent(type_: str, span_start: int = 0, span_end: int = 10, line_index: int = 0, occurrence_index: int = 0) -> ParsedIntent:
    return ParsedIntent(
        type=type_,
        category="UPDATE",
        confidence=0.9,
        span_start=span_start,
        span_end=span_end,
        line_index=line_index,
        occurrence_index=occurrence_index,
        intent_id=f"{type_}#{occurrence_index}",
    )


def _make_rules(rules_list: list[dict]) -> ParserRules:
    return ParserRules(disambiguation=rules_list)


def test_scope_whole_message_suppresses_all_occurrences():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", span_start=0, span_end=5, line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP_TO_BE", span_start=10, span_end=15, line_index=1, occurrence_index=1),
        _make_intent("MOVE_STOP", span_start=0, span_end=5, line_index=0, occurrence_index=0),
    ]
    rule = {
        "name": "prefer_be_over_stop",
        "scope": "whole_message",
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    assert "MOVE_STOP" not in types
    assert types.count("MOVE_STOP_TO_BE") == 2


def test_scope_same_span_only_suppresses_overlapping():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", span_start=0, span_end=10, line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP", span_start=2, span_end=8, line_index=0, occurrence_index=0),  # overlapping
        _make_intent("MOVE_STOP", span_start=20, span_end=30, line_index=1, occurrence_index=1),  # separate
    ]
    rule = {
        "name": "prefer_be_over_stop",
        "scope": "same_span",
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    assert types.count("MOVE_STOP") == 1
    assert result.intents[-1].line_index == 1


def test_scope_same_line_only_suppresses_same_line():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", span_start=0, span_end=10, line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP", span_start=2, span_end=8, line_index=0, occurrence_index=0),  # same line
        _make_intent("MOVE_STOP", span_start=20, span_end=30, line_index=1, occurrence_index=1),  # different line
    ]
    rule = {
        "name": "prefer_be_over_stop",
        "scope": "same_line",
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    assert types.count("MOVE_STOP") == 1
    assert result.intents[-1].line_index == 1


def test_default_scope_is_whole_message():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0),
        _make_intent("MOVE_STOP", line_index=1),
    ]
    rule = {
        "name": "prefer_be_over_stop",
        # no scope field
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    assert "MOVE_STOP" not in types
