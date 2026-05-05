from __future__ import annotations

from src.parser_v2.contracts.markers import MarkerEvidence, MarkerMatch
from src.parser_v2.contracts.rules import (
    CrossIntentSuppressionRule,
    MarkerResolutionRules,
    ParserRules,
)
from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver


STOP_TO_BE = "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
BE = "\u0431\u0443"


def test_required_stop_to_be_keeps_only_strong_move_stop_to_be() -> None:
    matches = [
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="strong",
            marker=STOP_TO_BE,
            start=0,
            end=9,
        ),
        MarkerMatch(
            name="EXIT_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=7,
            end=9,
        ),
    ]
    rules = ParserRules(
        marker_resolution=MarkerResolutionRules(
            cross_intent_suppression=[
                CrossIntentSuppressionRule(
                    if_strong="MOVE_STOP_TO_BE",
                    suppress_weak=["EXIT_BE"],
                    reason="command_marker_dominates_be_status_marker",
                )
            ]
        )
    )

    result = MarkerEvidenceResolver().resolve(matches, rules)

    assert result.evidence == [
        MarkerEvidence(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="strong",
            marker=STOP_TO_BE,
            start=0,
            end=9,
        )
    ]
    assert [marker.name for marker in result.suppressed_markers] == ["EXIT_BE"]
    assert result.suppressed_markers[0].suppressed_by == "MOVE_STOP_TO_BE"
    assert result.suppressed_markers[0].reason == "command_marker_dominates_be_status_marker"
    assert result.diagnostics["suppressed_markers"] == [
        "EXIT_BE/weak:\u0431\u0443@7:9"
    ]


def test_suppresses_weak_inside_strong_same_intent_when_enabled() -> None:
    matches = [
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="strong",
            marker=STOP_TO_BE,
            start=0,
            end=9,
        ),
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=7,
            end=9,
        ),
    ]
    rules = ParserRules(
        marker_resolution=MarkerResolutionRules(
            suppress_weak_inside_strong_same_intent=True
        )
    )

    result = MarkerEvidenceResolver().resolve(matches, rules)

    assert [marker.name for marker in result.evidence] == ["MOVE_STOP_TO_BE"]
    assert len(result.suppressed_markers) == 1
    assert result.suppressed_markers[0].reason == "weak_inside_strong_same_intent"
    assert result.diagnostics["applied_marker_rules"] == [
        "weak_inside_strong_same_intent"
    ]


def test_cross_intent_suppression_keeps_non_overlapping_weak_markers() -> None:
    matches = [
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="strong",
            marker=STOP_TO_BE,
            start=0,
            end=9,
        ),
        MarkerMatch(
            name="EXIT_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=20,
            end=22,
        ),
    ]
    rules = ParserRules(
        marker_resolution=MarkerResolutionRules(
            cross_intent_suppression=[
                CrossIntentSuppressionRule(
                    if_strong="MOVE_STOP_TO_BE",
                    suppress_weak=["EXIT_BE"],
                )
            ]
        )
    )

    result = MarkerEvidenceResolver().resolve(matches, rules)

    assert [marker.name for marker in result.evidence] == [
        "MOVE_STOP_TO_BE",
        "EXIT_BE",
    ]
    assert result.suppressed_markers == []
    assert result.diagnostics["suppressed_markers"] == []
