from __future__ import annotations

import pytest

from src.parser_v2.contracts.context import TargetHints
from src.parser_v2.contracts.parsed_message import ParsedIntent


def _make_intent(**kwargs) -> ParsedIntent:
    defaults = {
        "type": "MOVE_STOP_TO_BE",
        "category": "UPDATE",
        "confidence": 0.9,
    }
    return ParsedIntent(**{**defaults, **kwargs})


def test_parsed_intent_has_intent_id():
    intent = _make_intent()
    assert intent.intent_id is None


def test_parsed_intent_has_occurrence_index():
    intent = _make_intent()
    assert intent.occurrence_index is None


def test_parsed_intent_has_target_hints():
    intent = _make_intent()
    assert intent.target_hints is None


def test_parsed_intent_stores_intent_id():
    intent = _make_intent(intent_id="MOVE_STOP_TO_BE#0", occurrence_index=0)
    assert intent.intent_id == "MOVE_STOP_TO_BE#0"
    assert intent.occurrence_index == 0


def test_parsed_intent_stores_target_hints():
    hints = TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[111])
    intent = _make_intent(target_hints=hints)
    assert intent.target_hints is not None
    assert intent.target_hints.telegram_message_ids == [111]
    assert intent.target_hints.target_source == "LOCAL_TEXT_LINK"


def test_parsed_intent_rejects_negative_occurrence_index():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _make_intent(occurrence_index=-1)
