from __future__ import annotations

import re
import pytest

from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.core.marker_matcher import MarkerMatcher


# ── helpers ─────────────────────────────────────────────────────────────────

def _text(s: str) -> NormalizedText:
    return NormalizedText(raw_text=s, normalized_text=s)


def _field_markers(**kwargs) -> SemanticMarkers:
    return SemanticMarkers(field_markers={"take_profit": MarkerSet(**kwargs)})


# ── Task 1: MarkerSet compila i pattern ─────────────────────────────────────

def test_markerset_compiles_strong_patterns():
    ms = MarkerSet(strong_patterns=["(?i)тп\\s*[1-5]:"])
    assert len(ms._strong_compiled) == 1
    assert isinstance(ms._strong_compiled[0], re.Pattern)
    assert ms._strong_compiled[0].search("тп 1: сигнал") is not None


def test_markerset_compiles_weak_patterns():
    ms = MarkerSet(weak_patterns=["риск\\s*%"])
    assert len(ms._weak_compiled) == 1
    assert isinstance(ms._weak_compiled[0], re.Pattern)


def test_markerset_empty_patterns_by_default():
    ms = MarkerSet(strong=["тейки"])
    assert ms.strong_patterns == []
    assert ms._strong_compiled == []
    assert ms._weak_compiled == []


def test_markerset_invalid_pattern_raises():
    with pytest.raises(ValueError, match="strong_patterns"):
        MarkerSet(strong_patterns=["[invalid"])


# ── Task 2: MarkerMatcher usa i pattern ─────────────────────────────────────

def test_pattern_produces_marker_match_with_matched_text():
    matcher = MarkerMatcher()
    result = matcher.match(_text("тп1: 100"), _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:"]))
    assert len(result) == 1
    assert result[0].name == "take_profit"
    assert result[0].kind == "field"
    assert result[0].strength == "strong"
    assert result[0].marker == "тп1:"
    assert result[0].start == 0
    assert result[0].end == 4


def test_pattern_case_insensitive():
    matcher = MarkerMatcher()
    result = matcher.match(_text("ТП1: 100"), _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:"]))
    assert len(result) == 1
    assert result[0].marker == "ТП1:"


def test_pattern_no_match_returns_empty():
    matcher = MarkerMatcher()
    result = matcher.match(_text("нет тейков здесь"), _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:"]))
    assert result == []


def test_two_patterns_two_occurrences_ordered_by_position():
    matcher = MarkerMatcher()
    result = matcher.match(
        _text("тп1: 100\nцель2: 200"),
        _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:", "(?i)цель\\s*[1-5]:"]),
    )
    assert len(result) == 2
    assert result[0].marker == "тп1:"
    assert result[1].marker == "цель2:"


def test_weak_pattern_produces_weak_strength():
    matcher = MarkerMatcher()
    result = matcher.match(_text("риск% небольшой"), _field_markers(weak_patterns=["риск%"]))
    assert len(result) == 1
    assert result[0].strength == "weak"
    assert result[0].marker == "риск%"


def test_literal_and_pattern_same_span_deduped_to_one():
    matcher = MarkerMatcher()
    # literal "тп1:" e pattern "(?i)тп\s*[1-5]:" matchano lo stesso span
    result = matcher.match(
        _text("тп1: 100"),
        _field_markers(strong=["тп1:"], strong_patterns=["(?i)тп\\s*[1-5]:"]),
    )
    assert len(result) == 1
    assert result[0].marker == "тп1:"


def test_literal_and_pattern_different_spans_both_kept():
    matcher = MarkerMatcher()
    result = matcher.match(
        _text("тейки: 100\nтп1: 200"),
        _field_markers(strong=["тейки:"], strong_patterns=["(?i)тп\\s*[1-5]:"]),
    )
    assert len(result) == 2


def test_pattern_feeds_into_marker_evidence_resolver():
    from src.parser_v2.contracts.rules import MarkerResolutionRules, ParserRules
    from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver

    matcher = MarkerMatcher()
    text = "стоп в безубыток и бу"
    markers = SemanticMarkers(
        intent_markers={
            "MOVE_STOP_TO_BE": MarkerSet(
                strong_patterns=["стоп в безубыток"],
                weak=["бу"],
            )
        }
    )
    matches = matcher.match(_text(text), markers)
    assert len(matches) == 2  # strong pattern + weak literal

    rules = ParserRules(
        marker_resolution=MarkerResolutionRules(suppress_weak_inside_strong_same_intent=True)
    )
    result = MarkerEvidenceResolver().resolve(matches, rules, text=text)
    assert len(result.evidence) == 1
    assert result.evidence[0].strength == "strong"
    assert len(result.suppressed_markers) == 1
