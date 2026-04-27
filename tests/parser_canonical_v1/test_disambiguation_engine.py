from __future__ import annotations

import pytest

from src.parser.canonical_v1.intent_candidate import IntentCandidate
from src.parser.shared.disambiguation_engine import (
    DisambiguationResult,
    apply_disambiguation_rules,
)
from src.parser.shared.disambiguation_rules_schema import DisambiguationRule


def _candidate(intent: str, strength: str = "weak") -> IntentCandidate:
    return IntentCandidate(intent=intent, strength=strength, evidence=[f"marker: {intent}"])


def test_prefer_rule_keeps_specific_intent_and_records_rule() -> None:
    result = apply_disambiguation_rules(
        text_normalized="sposta bu in breakeven",
        intent_candidates=[
            _candidate("MOVE_STOP_TO_BE", "strong"),
            _candidate("MOVE_STOP", "weak"),
        ],
        rules=[
            DisambiguationRule(
                name="prefer_be_over_move_stop",
                action="prefer",
                when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
                prefer="MOVE_STOP_TO_BE",
                if_contains_any=["bu", "breakeven"],
            )
        ],
    )

    assert isinstance(result, DisambiguationResult)
    assert [c.intent for c in result.intent_candidates] == ["MOVE_STOP_TO_BE"]
    assert result.detected_intents == ["MOVE_STOP_TO_BE"]
    assert result.applied_rules == ["prefer_be_over_move_stop"]


def test_prefer_rule_does_not_match_without_text_marker() -> None:
    original = [
        _candidate("MOVE_STOP_TO_BE", "strong"),
        _candidate("MOVE_STOP", "weak"),
    ]

    result = apply_disambiguation_rules(
        text_normalized="sposta stop in be",
        intent_candidates=original,
        rules=[
            DisambiguationRule(
                name="prefer_be_over_move_stop",
                action="prefer",
                when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
                prefer="MOVE_STOP_TO_BE",
                if_contains_any=["bu", "breakeven"],
            )
        ],
    )

    assert [c.intent for c in result.intent_candidates] == ["MOVE_STOP_TO_BE", "MOVE_STOP"]
    assert result.detected_intents == ["MOVE_STOP_TO_BE", "MOVE_STOP"]
    assert result.applied_rules == []


def test_suppress_rule_removes_only_requested_intent() -> None:
    result = apply_disambiguation_rules(
        text_normalized="chiudo partial",
        intent_candidates=[
            _candidate("CLOSE_FULL", "weak"),
            _candidate("CLOSE_PARTIAL", "strong"),
        ],
        rules=[
            DisambiguationRule(
                name="suppress_close_full_if_partial",
                action="suppress",
                when_all_detected=["CLOSE_FULL", "CLOSE_PARTIAL"],
                suppress=["CLOSE_FULL"],
                if_contains_any=["partial", "parziale"],
            )
        ],
    )

    assert [c.intent for c in result.intent_candidates] == ["CLOSE_PARTIAL"]
    assert result.detected_intents == ["CLOSE_PARTIAL"]
    assert result.applied_rules == ["suppress_close_full_if_partial"]


def test_keep_multi_rule_preserves_both_intents() -> None:
    result = apply_disambiguation_rules(
        text_normalized="sl e chiusura finale",
        intent_candidates=[
            _candidate("SL_HIT", "strong"),
            _candidate("CLOSE_FULL", "weak"),
        ],
        rules=[
            DisambiguationRule(
                name="keep_sl_and_close",
                action="keep_multi",
                when_all_detected=["SL_HIT", "CLOSE_FULL"],
                keep=["SL_HIT", "CLOSE_FULL"],
            )
        ],
    )

    assert [c.intent for c in result.intent_candidates] == ["SL_HIT", "CLOSE_FULL"]
    assert result.detected_intents == ["SL_HIT", "CLOSE_FULL"]
    assert result.applied_rules == ["keep_sl_and_close"]


def test_unless_contains_any_blocks_rule_application() -> None:
    result = apply_disambiguation_rules(
        text_normalized="partial close requested",
        intent_candidates=[
            _candidate("CLOSE_FULL", "weak"),
            _candidate("CLOSE_PARTIAL", "strong"),
        ],
        rules=[
            DisambiguationRule(
                name="suppress_close_full_unless_partial_absent",
                action="suppress",
                when_any_detected=["CLOSE_FULL"],
                suppress=["CLOSE_FULL"],
                unless_contains_any=["partial"],
            )
        ],
    )

    assert [c.intent for c in result.intent_candidates] == ["CLOSE_FULL", "CLOSE_PARTIAL"]
    assert result.detected_intents == ["CLOSE_FULL", "CLOSE_PARTIAL"]
    assert result.applied_rules == []


def test_rule_order_is_applied_sequentially() -> None:
    result = apply_disambiguation_rules(
        text_normalized="bu + partial",
        intent_candidates=[
            _candidate("MOVE_STOP_TO_BE", "strong"),
            _candidate("MOVE_STOP", "weak"),
            _candidate("CLOSE_FULL", "weak"),
            _candidate("CLOSE_PARTIAL", "strong"),
        ],
        rules=[
            DisambiguationRule(
                name="prefer_be_over_move_stop",
                action="prefer",
                when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
                prefer="MOVE_STOP_TO_BE",
                if_contains_any=["bu"],
            ),
            DisambiguationRule(
                name="suppress_close_full_if_partial",
                action="suppress",
                when_all_detected=["CLOSE_FULL", "CLOSE_PARTIAL"],
                suppress=["CLOSE_FULL"],
                if_contains_any=["partial"],
            ),
        ],
    )

    assert [c.intent for c in result.intent_candidates] == ["MOVE_STOP_TO_BE", "CLOSE_PARTIAL"]
    assert result.detected_intents == ["MOVE_STOP_TO_BE", "CLOSE_PARTIAL"]
    assert result.applied_rules == [
        "prefer_be_over_move_stop",
        "suppress_close_full_if_partial",
    ]


def test_rules_with_target_fields_are_rejected() -> None:
    class BadRule(DisambiguationRule):
        target_ref: str | None = None

    with pytest.raises(ValueError, match="target_ref|target_history"):
        apply_disambiguation_rules(
            text_normalized="bu",
            intent_candidates=[
                _candidate("MOVE_STOP_TO_BE", "strong"),
                _candidate("MOVE_STOP", "weak"),
            ],
            rules=[
                BadRule(
                    name="bad_rule",
                    action="prefer",
                    when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
                    prefer="MOVE_STOP_TO_BE",
                )
            ],
        )
