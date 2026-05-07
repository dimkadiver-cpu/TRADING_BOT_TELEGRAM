from __future__ import annotations
import pytest
from src.parser_v2.contracts.rules import WeakContextExclusionRule, MarkerResolutionRules


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
