from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.parser.shared.disambiguation_rules_schema import (
    DisambiguationAction,
    DisambiguationRule,
    DisambiguationRulesBlock,
)


# --- DisambiguationAction ---

def test_disambiguation_action_values() -> None:
    from typing import get_args
    values = set(get_args(DisambiguationAction))
    assert values == {"prefer", "suppress", "keep_multi"}


# --- DisambiguationRule construction: valid cases ---

def test_prefer_rule_valid() -> None:
    rule = DisambiguationRule(
        name="prefer_be_over_move_stop",
        action="prefer",
        when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
        prefer="MOVE_STOP_TO_BE",
        if_contains_any=["bu", "breakeven"],
    )
    assert rule.name == "prefer_be_over_move_stop"
    assert rule.action == "prefer"
    assert rule.prefer == "MOVE_STOP_TO_BE"
    assert rule.when_all_detected == ["MOVE_STOP_TO_BE", "MOVE_STOP"]
    assert rule.if_contains_any == ["bu", "breakeven"]


def test_suppress_rule_valid() -> None:
    rule = DisambiguationRule(
        name="suppress_close_full_if_partial",
        action="suppress",
        when_all_detected=["CLOSE_FULL", "CLOSE_PARTIAL"],
        suppress=["CLOSE_FULL"],
    )
    assert rule.action == "suppress"
    assert rule.suppress == ["CLOSE_FULL"]


def test_keep_multi_rule_valid() -> None:
    rule = DisambiguationRule(
        name="keep_sl_and_close",
        action="keep_multi",
        when_all_detected=["SL_HIT", "CLOSE_FULL"],
        keep=["SL_HIT", "CLOSE_FULL"],
    )
    assert rule.action == "keep_multi"
    assert rule.keep == ["SL_HIT", "CLOSE_FULL"]


def test_when_any_detected_accepted() -> None:
    rule = DisambiguationRule(
        name="prefer_tp_hit",
        action="prefer",
        when_any_detected=["TP_HIT", "SL_HIT"],
        prefer="TP_HIT",
    )
    assert rule.when_any_detected == ["TP_HIT", "SL_HIT"]
    assert rule.when_all_detected is None


def test_unless_contains_any_accepted() -> None:
    rule = DisambiguationRule(
        name="suppress_unless_partial",
        action="suppress",
        when_any_detected=["CLOSE_FULL"],
        suppress=["CLOSE_FULL"],
        unless_contains_any=["partial", "частично"],
    )
    assert rule.unless_contains_any == ["partial", "частично"]


def test_optional_fields_default_to_none() -> None:
    rule = DisambiguationRule(
        name="minimal_keep",
        action="keep_multi",
        when_any_detected=["TP_HIT"],
    )
    assert rule.when_all_detected is None
    assert rule.if_contains_any is None
    assert rule.unless_contains_any is None
    assert rule.prefer is None
    assert rule.suppress is None
    assert rule.keep is None


# --- DisambiguationRule validators ---

def test_missing_both_when_raises() -> None:
    """At least one of when_all_detected / when_any_detected is required."""
    with pytest.raises(ValidationError):
        DisambiguationRule(
            name="bad_rule",
            action="prefer",
            prefer="MOVE_STOP_TO_BE",
        )


def test_prefer_action_without_prefer_field_raises() -> None:
    with pytest.raises(ValidationError):
        DisambiguationRule(
            name="missing_prefer",
            action="prefer",
            when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
            # prefer field missing
        )


def test_suppress_action_without_suppress_field_raises() -> None:
    with pytest.raises(ValidationError):
        DisambiguationRule(
            name="missing_suppress",
            action="suppress",
            when_all_detected=["CLOSE_FULL", "CLOSE_PARTIAL"],
            # suppress field missing
        )


def test_invalid_intent_name_in_when_all_detected_raises() -> None:
    with pytest.raises(ValidationError):
        DisambiguationRule(
            name="bad_intent",
            action="keep_multi",
            when_all_detected=["NOT_VALID_INTENT", "MOVE_STOP"],
        )


def test_invalid_intent_name_in_prefer_raises() -> None:
    with pytest.raises(ValidationError):
        DisambiguationRule(
            name="bad_prefer",
            action="prefer",
            when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
            prefer="TOTALLY_WRONG",
        )


def test_invalid_action_raises() -> None:
    with pytest.raises(ValidationError):
        DisambiguationRule(
            name="bad_action",
            action="unknown_action",  # type: ignore[arg-type]
            when_any_detected=["TP_HIT"],
        )


# --- DisambiguationRulesBlock ---

def test_block_empty_rules() -> None:
    block = DisambiguationRulesBlock(rules=[])
    assert block.rules == []


def test_block_with_multiple_rules() -> None:
    block = DisambiguationRulesBlock(rules=[
        DisambiguationRule(
            name="r1",
            action="prefer",
            when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
            prefer="MOVE_STOP_TO_BE",
        ),
        DisambiguationRule(
            name="r2",
            action="keep_multi",
            when_all_detected=["SL_HIT", "CLOSE_FULL"],
        ),
    ])
    assert len(block.rules) == 2


# --- Proposal example: prefer_be_over_move_stop ---

PROPOSAL_PREFER_BE = {
    "disambiguation_rules": {
        "rules": [
            {
                "name": "prefer_be_over_move_stop",
                "action": "prefer",
                "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
                "prefer": "MOVE_STOP_TO_BE",
                "if_contains_any": ["bu", "breakeven"],
            }
        ]
    }
}


def test_proposal_prefer_be_loads_and_validates() -> None:
    raw = PROPOSAL_PREFER_BE["disambiguation_rules"]
    block = DisambiguationRulesBlock.model_validate(raw)
    assert len(block.rules) == 1
    rule = block.rules[0]
    assert rule.name == "prefer_be_over_move_stop"
    assert rule.action == "prefer"
    assert rule.prefer == "MOVE_STOP_TO_BE"
    assert rule.when_all_detected == ["MOVE_STOP_TO_BE", "MOVE_STOP"]
    assert rule.if_contains_any == ["bu", "breakeven"]


def test_proposal_prefer_be_json_roundtrip() -> None:
    raw = PROPOSAL_PREFER_BE["disambiguation_rules"]
    block = DisambiguationRulesBlock.model_validate(raw)
    serialized = block.model_dump_json()
    restored = DisambiguationRulesBlock.model_validate_json(serialized)
    assert restored == block


def test_proposal_prefer_be_from_json_string() -> None:
    json_str = json.dumps(PROPOSAL_PREFER_BE["disambiguation_rules"])
    block = DisambiguationRulesBlock.model_validate_json(json_str)
    assert len(block.rules) == 1
    assert block.rules[0].name == "prefer_be_over_move_stop"
