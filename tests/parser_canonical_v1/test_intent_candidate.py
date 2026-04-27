from __future__ import annotations

import json

import pytest

from src.parser.canonical_v1.intent_candidate import IntentCandidate, IntentStrength


# --- construction ---

def test_strong_candidate() -> None:
    c = IntentCandidate(intent="EXIT_BE", strength="strong", evidence=["marker: закрыта в бу"])
    assert c.intent == "EXIT_BE"
    assert c.strength == "strong"
    assert c.evidence == ["marker: закрыта в бу"]


def test_weak_candidate() -> None:
    c = IntentCandidate(intent="CLOSE_FULL", strength="weak", evidence=["marker: закрыта"])
    assert c.strength == "weak"


def test_empty_evidence_allowed() -> None:
    c = IntentCandidate(intent="INFO_ONLY", strength="weak", evidence=[])
    assert c.evidence == []


# --- is_strong / is_weak ---

def test_is_strong_true_when_strong() -> None:
    c = IntentCandidate(intent="MOVE_STOP_TO_BE", strength="strong", evidence=["x"])
    assert c.is_strong is True
    assert c.is_weak is False


def test_is_weak_true_when_weak() -> None:
    c = IntentCandidate(intent="MOVE_STOP", strength="weak", evidence=["y"])
    assert c.is_weak is True
    assert c.is_strong is False


# --- validation ---

def test_invalid_intent_raises() -> None:
    with pytest.raises(Exception):
        IntentCandidate(intent="NOT_AN_INTENT", strength="strong", evidence=[])


def test_invalid_strength_raises() -> None:
    with pytest.raises(Exception):
        IntentCandidate(intent="TP_HIT", strength="medium", evidence=[])  # type: ignore[arg-type]


# --- JSON round-trip ---

def test_json_roundtrip_preserves_all_fields() -> None:
    original = IntentCandidate(
        intent="SL_HIT",
        strength="strong",
        evidence=["marker: стоп", "context: loss"],
    )
    serialized = original.model_dump_json()
    restored = IntentCandidate.model_validate_json(serialized)
    assert restored == original


def test_json_roundtrip_via_dict() -> None:
    original = IntentCandidate(intent="TP_HIT", strength="weak", evidence=["tp1 touched"])
    data = original.model_dump()
    restored = IntentCandidate.model_validate(data)
    assert restored.intent == original.intent
    assert restored.strength == original.strength
    assert restored.evidence == original.evidence


def test_json_roundtrip_no_extra_fields() -> None:
    original = IntentCandidate(intent="NEW_SETUP", strength="strong", evidence=[])
    raw = json.loads(original.model_dump_json())
    assert set(raw.keys()) == {"intent", "strength", "evidence"}


# --- IntentStrength type alias is exported ---

def test_intent_strength_values() -> None:
    from typing import get_args
    values = set(get_args(IntentStrength))
    assert values == {"strong", "weak"}
