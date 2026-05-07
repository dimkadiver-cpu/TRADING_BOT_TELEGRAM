from __future__ import annotations
import pytest
from src.parser_v2.contracts.markers import MarkerMatch
from src.parser_v2.contracts.rules import MarkerResolutionRules, ParserRules, WeakContextExclusionRule
from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver


def _make_match(name: str, marker: str, strength: str, start: int, end: int) -> MarkerMatch:
    return MarkerMatch(name=name, kind="intent", strength=strength, marker=marker, start=start, end=end)


def _make_rules(exclusions: list[WeakContextExclusionRule]) -> ParserRules:
    return ParserRules(
        marker_resolution=MarkerResolutionRules(weak_context_exclusions=exclusions)
    )


RAW_TEXT_HISTORICAL = "Закрылась в бу, после 1 тейка, конечно же"


def test_weak_marker_suppressed_by_historical_context():
    text = RAW_TEXT_HISTORICAL
    marker_pos = text.find("тейка")
    matches = [_make_match("TP_HIT", "тейка", "weak", marker_pos, marker_pos + 5)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
        reason="historical_context",
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 0
    assert len(result.suppressed_markers) == 1
    assert result.suppressed_markers[0].reason == "historical_context"


def test_strong_marker_never_suppressed():
    text = "после 1 тейка второй тейк взят"
    strong_pos = text.find("тейк взят")
    matches = [_make_match("TP_HIT", "тейк взят", "strong", strong_pos, strong_pos + 9)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейк", "тейк взят"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 1
    assert len(result.suppressed_markers) == 0


def test_unless_prevents_suppression():
    text = "после 1 тейка тейк взят"
    marker_pos = text.find("тейка")
    matches = [_make_match("TP_HIT", "тейка", "weak", marker_pos, marker_pos + 5)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
        unless_contains_any=["тейк взят"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 1  # not suppressed due to "unless"


def test_scope_same_line_only_affects_same_line():
    text = "после 1 тейка закрылась в бу.\n2 тейк взят."
    weak_pos = text.find("тейка")
    strong_pos = text.find("тейк взят")
    matches = [
        _make_match("TP_HIT", "тейка", "weak", weak_pos, weak_pos + 5),
        _make_match("TP_HIT", "тейк взят", "strong", strong_pos, strong_pos + 9),
    ]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_line",
        if_contains_any=["после 1 тейка"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 1
    assert result.evidence[0].marker == "тейк взят"


def test_no_raw_text_skips_exclusions_with_diagnostic():
    matches = [_make_match("TP_HIT", "тейка", "weak", 5, 10)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]))  # no raw_text
    assert len(result.evidence) == 1  # not suppressed
    assert "weak_context_exclusions_skipped_no_text" in result.diagnostics
