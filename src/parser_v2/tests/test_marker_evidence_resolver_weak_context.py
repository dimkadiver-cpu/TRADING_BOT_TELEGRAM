from __future__ import annotations
import pytest
from src.parser_v2.contracts.markers import MarkerMatch
from src.parser_v2.contracts.rules import MarkerContextExclusionRule, MarkerResolutionRules, ParserRules, WeakContextExclusionRule
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
    result = resolver.resolve(matches, _make_rules([rule]), text=text)
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
    result = resolver.resolve(matches, _make_rules([rule]), text=text)
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
    result = resolver.resolve(matches, _make_rules([rule]), text=text)
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
    result = resolver.resolve(matches, _make_rules([rule]), text=text)
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


def _make_match_kind(
    name: str, marker: str, strength: str, start: int, end: int, kind: str = "intent"
) -> MarkerMatch:
    return MarkerMatch(name=name, kind=kind, strength=strength, marker=marker, start=start, end=end)


def _make_rules_ctx(exclusions: list[MarkerContextExclusionRule]) -> ParserRules:
    return ParserRules(
        marker_resolution=MarkerResolutionRules(marker_context_exclusions=exclusions)
    )


def test_strong_marker_suppressed_by_context():
    text = "закрываю по текущим. p.s. у вас прибыль по шортам будет больше"
    marker_text = "по шортам"
    pos = text.find(marker_text)
    matches = [_make_match_kind("ALL_SHORT", marker_text, "strong", pos, pos + len(marker_text), kind="target_hint")]
    rule = MarkerContextExclusionRule(
        name="all_short_ps",
        strength="strong",
        marker_name="ALL_SHORT",
        markers=[marker_text],
        scope="whole_message",
        if_contains_any=["p.s."],
        reason="scope_hint_in_postscript",
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 0
    assert len(result.suppressed_markers) == 1
    assert result.suppressed_markers[0].reason == "scope_hint_in_postscript"


def test_strong_marker_not_suppressed_if_condition_not_met():
    text = "закрываю по шортам по текущим"
    marker_text = "по шортам"
    pos = text.find(marker_text)
    matches = [_make_match_kind("ALL_SHORT", marker_text, "strong", pos, pos + len(marker_text), kind="target_hint")]
    rule = MarkerContextExclusionRule(
        name="all_short_ps",
        strength="strong",
        marker_name="ALL_SHORT",
        markers=[marker_text],
        scope="whole_message",
        if_contains_any=["p.s."],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 1
    assert len(result.suppressed_markers) == 0


def test_list_marker_name_suppresses_all_matching():
    text = "фактически в бу закрылись"
    pos = text.find("бу")
    matches = [
        _make_match("EXIT_BE", "бу", "weak", pos, pos + 2),
        _make_match("MOVE_STOP_TO_BE", "бу", "weak", pos, pos + 2),
    ]
    rule = MarkerContextExclusionRule(
        name="be_context",
        strength="weak",
        marker_name=["EXIT_BE", "MOVE_STOP_TO_BE"],
        markers=["бу"],
        scope="same_sentence",
        if_contains_any=["фактически в бу"],
        reason="be_false_positive",
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 0
    assert len(result.suppressed_markers) == 2
    assert all(m.reason == "be_false_positive" for m in result.suppressed_markers)


def test_marker_context_exclusion_does_not_affect_wrong_strength():
    text = "фактически в бу закрылись"
    pos = text.find("бу")
    matches = [_make_match("EXIT_BE", "бу", "strong", pos, pos + 2)]
    rule = MarkerContextExclusionRule(
        name="be_context_weak_only",
        strength="weak",
        marker_name="EXIT_BE",
        markers=["бу"],
        scope="same_sentence",
        if_contains_any=["фактически в бу"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 1


def test_backward_compat_weak_context_exclusions_unchanged():
    text = "после 1 тейка закрылась в бу"
    pos = text.find("тейка")
    matches = [_make_match("TP_HIT", "тейка", "weak", pos, pos + 5)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
        reason="historical_context",
    )
    rules = ParserRules(marker_resolution=MarkerResolutionRules(weak_context_exclusions=[rule]))
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, rules, text=text)
    assert len(result.evidence) == 0
    assert result.suppressed_markers[0].reason == "historical_context"
