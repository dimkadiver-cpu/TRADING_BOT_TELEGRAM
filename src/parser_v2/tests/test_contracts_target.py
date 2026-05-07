from __future__ import annotations
import pytest
from src.parser_v2.contracts.enums import TargetSource
from src.parser_v2.contracts.context import TargetHints, TargetCandidate, TargetExtractionResult


def test_target_source_values():
    valid: list[TargetSource] = [
        "LOCAL_TEXT_LINK", "LOCAL_EXPLICIT_ID",
        "MESSAGE_TEXT_LINK", "MESSAGE_EXPLICIT_ID",
        "REPLY", "SYMBOL", "GLOBAL_SCOPE", "UNKNOWN",
    ]
    assert len(valid) == 8


def test_target_hints_has_target_source_default():
    hints = TargetHints()
    assert hints.target_source == "UNKNOWN"


def test_target_hints_target_source_persists():
    hints = TargetHints(target_source="REPLY", reply_to_message_id=42)
    assert hints.target_source == "REPLY"
    assert hints.reply_to_message_id == 42


def test_target_candidate_fields():
    candidate = TargetCandidate(
        source="MESSAGE_TEXT_LINK",
        value=111,
        start=5,
        end=30,
        line_index=0,
    )
    assert candidate.source == "MESSAGE_TEXT_LINK"
    assert candidate.value == 111
    assert candidate.line_index == 0


def test_target_extraction_result_structure():
    hints = TargetHints()
    result = TargetExtractionResult(message_target_hints=hints)
    assert result.candidates == []


def test_target_extraction_result_with_candidates():
    candidate = TargetCandidate(source="REPLY", value=100)
    result = TargetExtractionResult(
        message_target_hints=TargetHints(target_source="REPLY", reply_to_message_id=100),
        candidates=[candidate],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].source == "REPLY"
