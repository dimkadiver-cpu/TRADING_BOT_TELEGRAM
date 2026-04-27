from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.parser.shared.intent_compatibility_schema import (
    IntentCompatibilityBlock,
    IntentCompatibilityPair,
    RelationType,
)


# --- RelationType ---

def test_relation_type_values() -> None:
    from typing import get_args
    values = set(get_args(RelationType))
    assert values == {"compatible", "exclusive", "specific_vs_generic", "stateful_requires_context"}


# --- IntentCompatibilityPair construction ---

def test_compatible_pair_no_preferred() -> None:
    pair = IntentCompatibilityPair(
        intents=["TP_HIT", "REPORT_FINAL_RESULT"],
        relation="compatible",
        requires_resolution=False,
    )
    assert pair.intents == ["TP_HIT", "REPORT_FINAL_RESULT"]
    assert pair.relation == "compatible"
    assert pair.preferred is None
    assert pair.requires_resolution is False
    assert pair.requires_context_validation is False
    assert pair.warning_if_unresolved is True


def test_specific_vs_generic_with_preferred() -> None:
    pair = IntentCompatibilityPair(
        intents=["MOVE_STOP_TO_BE", "MOVE_STOP"],
        relation="specific_vs_generic",
        preferred="MOVE_STOP_TO_BE",
        requires_resolution=True,
    )
    assert pair.preferred == "MOVE_STOP_TO_BE"
    assert pair.requires_resolution is True


def test_exclusive_pair() -> None:
    pair = IntentCompatibilityPair(
        intents=["SL_HIT", "CLOSE_FULL"],
        relation="exclusive",
        requires_resolution=True,
    )
    assert pair.relation == "exclusive"


def test_requires_context_validation_explicit_true() -> None:
    pair = IntentCompatibilityPair(
        intents=["EXIT_BE", "CLOSE_FULL"],
        relation="specific_vs_generic",
        preferred="EXIT_BE",
        requires_resolution=True,
        requires_context_validation=True,
    )
    assert pair.requires_context_validation is True


def test_warning_if_unresolved_explicit_false() -> None:
    pair = IntentCompatibilityPair(
        intents=["TP_HIT", "REPORT_FINAL_RESULT"],
        relation="compatible",
        requires_resolution=False,
        warning_if_unresolved=False,
    )
    assert pair.warning_if_unresolved is False


# --- IntentCompatibilityPair validators ---

def test_intents_must_have_exactly_2() -> None:
    with pytest.raises(ValidationError):
        IntentCompatibilityPair(
            intents=["MOVE_STOP_TO_BE"],
            relation="specific_vs_generic",
            requires_resolution=True,
        )


def test_intents_3_raises() -> None:
    with pytest.raises(ValidationError):
        IntentCompatibilityPair(
            intents=["MOVE_STOP_TO_BE", "MOVE_STOP", "EXIT_BE"],
            relation="specific_vs_generic",
            requires_resolution=True,
        )


def test_invalid_intent_name_raises() -> None:
    with pytest.raises(ValidationError):
        IntentCompatibilityPair(
            intents=["NOT_VALID", "MOVE_STOP"],
            relation="exclusive",
            requires_resolution=True,
        )


def test_preferred_not_in_intents_raises() -> None:
    with pytest.raises(ValidationError):
        IntentCompatibilityPair(
            intents=["MOVE_STOP_TO_BE", "MOVE_STOP"],
            relation="specific_vs_generic",
            preferred="EXIT_BE",  # not in intents
            requires_resolution=True,
        )


def test_preferred_in_intents_accepted() -> None:
    pair = IntentCompatibilityPair(
        intents=["EXIT_BE", "CLOSE_FULL"],
        relation="specific_vs_generic",
        preferred="EXIT_BE",
        requires_resolution=True,
        requires_context_validation=True,
    )
    assert pair.preferred == "EXIT_BE"


def test_invalid_relation_raises() -> None:
    with pytest.raises(ValidationError):
        IntentCompatibilityPair(
            intents=["MOVE_STOP_TO_BE", "MOVE_STOP"],
            relation="unknown_relation",  # type: ignore[arg-type]
            requires_resolution=True,
        )


# --- IntentCompatibilityBlock ---

def test_block_with_empty_pairs() -> None:
    block = IntentCompatibilityBlock(pairs=[])
    assert block.pairs == []


def test_block_with_multiple_pairs() -> None:
    block = IntentCompatibilityBlock(pairs=[
        IntentCompatibilityPair(
            intents=["MOVE_STOP_TO_BE", "MOVE_STOP"],
            relation="specific_vs_generic",
            preferred="MOVE_STOP_TO_BE",
            requires_resolution=True,
        ),
        IntentCompatibilityPair(
            intents=["TP_HIT", "REPORT_FINAL_RESULT"],
            relation="compatible",
            requires_resolution=False,
        ),
    ])
    assert len(block.pairs) == 2


# --- Proposal example shape loads and validates ---

PROPOSAL_EXAMPLE = {
    "intent_compatibility": {
        "pairs": [
            {
                "intents": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
                "relation": "specific_vs_generic",
                "preferred": "MOVE_STOP_TO_BE",
                "requires_resolution": True,
            },
            {
                "intents": ["EXIT_BE", "CLOSE_FULL"],
                "relation": "specific_vs_generic",
                "preferred": "EXIT_BE",
                "requires_resolution": True,
                "requires_context_validation": True,
            },
            {
                "intents": ["TP_HIT", "REPORT_FINAL_RESULT"],
                "relation": "compatible",
                "requires_resolution": False,
            },
            {
                "intents": ["SL_HIT", "CLOSE_FULL"],
                "relation": "exclusive",
                "requires_resolution": True,
            },
        ]
    }
}


def test_proposal_example_loads_and_validates() -> None:
    raw = PROPOSAL_EXAMPLE["intent_compatibility"]
    block = IntentCompatibilityBlock.model_validate(raw)
    assert len(block.pairs) == 4
    assert block.pairs[0].preferred == "MOVE_STOP_TO_BE"
    assert block.pairs[1].requires_context_validation is True
    assert block.pairs[2].requires_resolution is False
    assert block.pairs[3].relation == "exclusive"


def test_proposal_example_json_roundtrip() -> None:
    raw = PROPOSAL_EXAMPLE["intent_compatibility"]
    block = IntentCompatibilityBlock.model_validate(raw)
    serialized = block.model_dump_json()
    restored = IntentCompatibilityBlock.model_validate_json(serialized)
    assert restored == block


def test_proposal_example_parsed_from_json_string() -> None:
    json_str = json.dumps(PROPOSAL_EXAMPLE["intent_compatibility"])
    block = IntentCompatibilityBlock.model_validate_json(json_str)
    assert len(block.pairs) == 4
