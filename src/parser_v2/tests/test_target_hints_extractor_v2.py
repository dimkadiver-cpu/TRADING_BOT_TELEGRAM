from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext, TargetExtractionResult
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.rules import SemanticMarkers
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor


def _extract(text: str, reply_id: int | None = None) -> TargetExtractionResult:
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower())
    return TargetHintsExtractor().extract(normalized, context, SemanticMarkers())


def test_extract_returns_extraction_result():
    result = _extract("стоп в бу")
    assert isinstance(result, TargetExtractionResult)


def test_extract_message_target_hints_preserved():
    result = _extract("стоп в бу", reply_id=100)
    assert result.message_target_hints.reply_to_message_id == 100


def test_extract_telegram_link_becomes_candidate_with_position():
    text = "https://t.me/c/777/111 стоп в бу"
    result = _extract(text)
    assert len(result.candidates) >= 1
    link_candidate = next(
        (c for c in result.candidates if c.source == "MESSAGE_TEXT_LINK"), None
    )
    assert link_candidate is not None
    assert link_candidate.value == 111
    assert link_candidate.start == 0
    assert link_candidate.line_index == 0


def test_extract_reply_becomes_candidate():
    result = _extract("стоп в бу", reply_id=100)
    reply_candidate = next(
        (c for c in result.candidates if c.source == "REPLY"), None
    )
    assert reply_candidate is not None
    assert reply_candidate.value == 100


def test_extract_multiline_links_have_correct_line_index():
    text = "https://t.me/c/777/111 стоп\nhttps://t.me/c/777/222 закрываю"
    result = _extract(text)
    link_candidates = [c for c in result.candidates if c.source == "MESSAGE_TEXT_LINK"]
    assert len(link_candidates) == 2
    line_indices = {c.value: c.line_index for c in link_candidates}
    assert line_indices[111] == 0
    assert line_indices[222] == 1


def test_extract_target_source_set_on_message_hints():
    text = "https://t.me/c/777/111 стоп"
    result = _extract(text)
    assert result.message_target_hints.target_source == "MESSAGE_TEXT_LINK"


def test_extract_reply_target_source_when_no_link():
    result = _extract("стоп в бу", reply_id=100)
    assert result.message_target_hints.target_source == "REPLY"
