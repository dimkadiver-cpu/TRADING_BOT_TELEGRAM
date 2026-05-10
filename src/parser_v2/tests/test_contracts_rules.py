from __future__ import annotations
import pytest
from src.parser_v2.contracts.rules import WeakContextExclusionRule, MarkerResolutionRules, MarkerContextExclusionRule


def test_weak_context_exclusion_rule_basic():
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейк", "тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    assert rule.name == "tp_historical"
    assert rule.scope == "same_sentence"
    assert rule.markers == ["тейк", "тейка"]


def test_weak_context_exclusion_rule_with_source():
    rule = WeakContextExclusionRule(
        name="tp_future",
        intent="TP_HIT",
        markers={"source": "intent_weak"},
        scope="same_sentence",
        if_regex_any=["дойд[её]т\\s+до\\s+тейк"],
    )
    assert rule.markers == {"source": "intent_weak"}


def test_weak_context_exclusion_requires_condition():
    with pytest.raises(Exception):
        WeakContextExclusionRule(
            name="invalid",
            intent="TP_HIT",
            markers=["тейк"],
            scope="same_sentence",
            # no if_contains_any nor if_regex_any
        )


def test_marker_resolution_rules_has_weak_context_exclusions():
    rules = MarkerResolutionRules()
    assert rules.weak_context_exclusions == []


def test_marker_resolution_rules_with_exclusion():
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейк"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    rules = MarkerResolutionRules(weak_context_exclusions=[rule])
    assert len(rules.weak_context_exclusions) == 1


def test_marker_context_exclusion_rule_strong_basic():
    rule = MarkerContextExclusionRule(
        name="all_short_ps",
        strength="strong",
        marker_name="ALL_SHORT",
        markers={"source": "semantic"},
        scope="same_sentence",
        if_contains_any=["p.s."],
        reason="scope_hint_in_postscript",
    )
    assert rule.strength == "strong"
    assert rule.marker_name == "ALL_SHORT"
    assert rule.reason == "scope_hint_in_postscript"


def test_marker_context_exclusion_rule_list_marker_name():
    rule = MarkerContextExclusionRule(
        name="be_context",
        strength="weak",
        marker_name=["EXIT_BE", "MOVE_STOP_TO_BE"],
        markers=["бу"],
        scope="same_sentence",
        if_contains_any=["фактически в бу"],
    )
    assert rule.marker_name == ["EXIT_BE", "MOVE_STOP_TO_BE"]


def test_marker_context_exclusion_rule_requires_condition():
    with pytest.raises(Exception):
        MarkerContextExclusionRule(
            name="invalid",
            strength="weak",
            marker_name="EXIT_BE",
            markers=["бу"],
            scope="same_sentence",
            # нет if_contains_any ни if_regex_any
        )


def test_marker_resolution_rules_has_marker_context_exclusions():
    rules = MarkerResolutionRules()
    assert rules.marker_context_exclusions == []


def test_marker_resolution_rules_accepts_marker_context_exclusion():
    rule = MarkerContextExclusionRule(
        name="test",
        strength="strong",
        marker_name="ALL_SHORT",
        markers=["по шортам"],
        scope="same_sentence",
        if_contains_any=["p.s."],
    )
    rules = MarkerResolutionRules(marker_context_exclusions=[rule])
    assert len(rules.marker_context_exclusions) == 1
