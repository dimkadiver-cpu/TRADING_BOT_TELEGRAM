from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.parser.shared.context_resolution_schema import (
    ContextResolutionAction,
    ContextResolutionRule,
    ContextResolutionRulesBlock,
    ContextResolutionWhen,
)


# ---------------------------------------------------------------------------
# ContextResolutionAction
# ---------------------------------------------------------------------------


def test_action_values_are_correct() -> None:
    valid = {"promote", "resolve_as", "set_primary", "suppress"}
    from typing import get_args

    assert set(get_args(ContextResolutionAction)) == valid


# ---------------------------------------------------------------------------
# ContextResolutionWhen — costruzione valida
# ---------------------------------------------------------------------------


def test_when_with_has_weak_intent_only() -> None:
    w = ContextResolutionWhen(has_weak_intent="EXIT_BE")
    assert w.has_weak_intent == "EXIT_BE"
    assert w.has_strong_intent is None
    assert w.has_any_intent is None


def test_when_with_has_strong_intent_only() -> None:
    w = ContextResolutionWhen(has_strong_intent="TP_HIT")
    assert w.has_strong_intent == "TP_HIT"


def test_when_with_has_any_intent_only() -> None:
    w = ContextResolutionWhen(has_any_intent=["SL_HIT", "CLOSE_FULL"])
    assert w.has_any_intent == ["SL_HIT", "CLOSE_FULL"]


def test_when_with_has_target_ref_alongside_intent() -> None:
    w = ContextResolutionWhen(has_weak_intent="EXIT_BE", has_target_ref=True)
    assert w.has_target_ref is True


def test_when_with_message_type_hint_in() -> None:
    w = ContextResolutionWhen(
        has_strong_intent="SL_HIT",
        message_type_hint_in=["UPDATE", "INFO"],
    )
    assert w.message_type_hint_in == ["UPDATE", "INFO"]


# ---------------------------------------------------------------------------
# ContextResolutionWhen — validator: almeno un has_*_intent richiesto
# ---------------------------------------------------------------------------


def test_when_requires_at_least_one_intent_signal() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ContextResolutionWhen(has_target_ref=True)


def test_when_allows_all_intent_signals_together() -> None:
    w = ContextResolutionWhen(
        has_weak_intent="EXIT_BE",
        has_strong_intent="CLOSE_FULL",
        has_any_intent=["SL_HIT"],
    )
    assert w.has_weak_intent == "EXIT_BE"


def test_when_rejects_invalid_intent_in_has_weak() -> None:
    with pytest.raises(ValidationError):
        ContextResolutionWhen(has_weak_intent="NONEXISTENT_INTENT")


def test_when_rejects_invalid_intent_in_has_any() -> None:
    with pytest.raises(ValidationError):
        ContextResolutionWhen(has_any_intent=["CLOSE_FULL", "BOGUS"])


# ---------------------------------------------------------------------------
# ContextResolutionRule — costruzione valida per ogni azione
# ---------------------------------------------------------------------------

_WHEN_WEAK_EXIT_BE = {"has_weak_intent": "EXIT_BE", "has_target_ref": True}


def test_rule_action_resolve_as() -> None:
    rule = ContextResolutionRule(
        name="exit_be_requires_history",
        action="resolve_as",
        when=_WHEN_WEAK_EXIT_BE,
        if_target_history_has_any=["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"],
        resolve_as="EXIT_BE",
        otherwise_resolve_as="INFO_ONLY",
    )
    assert rule.name == "exit_be_requires_history"
    assert rule.resolve_as == "EXIT_BE"
    assert rule.otherwise_resolve_as == "INFO_ONLY"
    assert rule.if_target_history_has_any == ["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"]


def test_rule_action_promote() -> None:
    rule = ContextResolutionRule(
        name="promote_exit_be",
        action="promote",
        when={"has_weak_intent": "EXIT_BE"},
        intent="EXIT_BE",
    )
    assert rule.intent == "EXIT_BE"


def test_rule_action_set_primary() -> None:
    rule = ContextResolutionRule(
        name="sl_hit_is_primary",
        action="set_primary",
        when={"has_any_intent": ["SL_HIT", "CLOSE_FULL"]},
        primary="SL_HIT",
    )
    assert rule.primary == "SL_HIT"


def test_rule_action_suppress() -> None:
    rule = ContextResolutionRule(
        name="suppress_tp_if_target_closed",
        action="suppress",
        when={"has_strong_intent": "TP_HIT"},
        if_target_exists=False,
        suppress=["TP_HIT"],
    )
    assert rule.suppress == ["TP_HIT"]
    assert rule.if_target_exists is False


def test_rule_if_target_history_lacks_all() -> None:
    rule = ContextResolutionRule(
        name="no_history_fallback",
        action="resolve_as",
        when={"has_weak_intent": "EXIT_BE"},
        if_target_history_lacks_all=["MOVE_STOP_TO_BE"],
        resolve_as="INFO_ONLY",
    )
    assert rule.if_target_history_lacks_all == ["MOVE_STOP_TO_BE"]


# ---------------------------------------------------------------------------
# ContextResolutionRule — validatori per azione (campi obbligatori)
# ---------------------------------------------------------------------------


def test_resolve_as_requires_resolve_as_field() -> None:
    with pytest.raises(ValidationError, match="resolve_as"):
        ContextResolutionRule(
            name="bad",
            action="resolve_as",
            when=_WHEN_WEAK_EXIT_BE,
        )


def test_promote_requires_intent_field() -> None:
    with pytest.raises(ValidationError, match="intent"):
        ContextResolutionRule(
            name="bad",
            action="promote",
            when={"has_weak_intent": "EXIT_BE"},
        )


def test_set_primary_requires_primary_field() -> None:
    with pytest.raises(ValidationError, match="primary"):
        ContextResolutionRule(
            name="bad",
            action="set_primary",
            when={"has_any_intent": ["SL_HIT"]},
        )


def test_suppress_requires_suppress_field() -> None:
    with pytest.raises(ValidationError, match="suppress"):
        ContextResolutionRule(
            name="bad",
            action="suppress",
            when={"has_strong_intent": "TP_HIT"},
        )


def test_otherwise_resolve_as_only_valid_with_resolve_as_action() -> None:
    with pytest.raises(ValidationError, match="otherwise_resolve_as"):
        ContextResolutionRule(
            name="bad",
            action="promote",
            when={"has_weak_intent": "EXIT_BE"},
            intent="EXIT_BE",
            otherwise_resolve_as="INFO_ONLY",
        )


# ---------------------------------------------------------------------------
# Proposta example — exit_be_requires_history si carica e valida
# ---------------------------------------------------------------------------


_PROPOSAL_JSON = json.dumps(
    [
        {
            "name": "exit_be_requires_history",
            "action": "resolve_as",
            "when": {
                "has_weak_intent": "EXIT_BE",
                "has_target_ref": True,
            },
            "if_target_history_has_any": ["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"],
            "resolve_as": "EXIT_BE",
            "otherwise_resolve_as": "INFO_ONLY",
        }
    ]
)


def test_proposal_example_loads_and_validates() -> None:
    block = ContextResolutionRulesBlock.model_validate({"rules": json.loads(_PROPOSAL_JSON)})
    assert len(block.rules) == 1
    rule = block.rules[0]
    assert rule.name == "exit_be_requires_history"
    assert rule.action == "resolve_as"
    assert rule.when.has_weak_intent == "EXIT_BE"
    assert rule.when.has_target_ref is True
    assert "MOVE_STOP_TO_BE" in (rule.if_target_history_has_any or [])
    assert rule.resolve_as == "EXIT_BE"
    assert rule.otherwise_resolve_as == "INFO_ONLY"


# ---------------------------------------------------------------------------
# Round-trip JSON
# ---------------------------------------------------------------------------


def test_round_trip_json() -> None:
    block = ContextResolutionRulesBlock.model_validate({"rules": json.loads(_PROPOSAL_JSON)})
    serialized = block.model_dump_json()
    restored = ContextResolutionRulesBlock.model_validate_json(serialized)
    assert restored.rules[0].name == block.rules[0].name
    assert restored.rules[0].resolve_as == block.rules[0].resolve_as
    assert restored.rules[0].when.has_weak_intent == block.rules[0].when.has_weak_intent


def test_round_trip_dict() -> None:
    block = ContextResolutionRulesBlock.model_validate({"rules": json.loads(_PROPOSAL_JSON)})
    d = block.model_dump()
    restored = ContextResolutionRulesBlock.model_validate(d)
    assert restored.rules[0].otherwise_resolve_as == "INFO_ONLY"
