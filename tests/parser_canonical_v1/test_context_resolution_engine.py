from __future__ import annotations

from src.parser.canonical_v1.intent_candidate import IntentCandidate
from src.parser.shared.context_resolution_engine import (
    ContextInput,
    ContextResolutionResult,
    apply_context_resolution_rules,
)
from src.parser.shared.context_resolution_schema import ContextResolutionRule


def _candidate(intent: str, strength: str = "weak") -> IntentCandidate:
    return IntentCandidate(intent=intent, strength=strength, evidence=[f"marker: {intent}"])


def test_context_input_exposes_expected_fields() -> None:
    context = ContextInput(
        has_target_ref=True,
        target_ref_kind="reply_id",
        target_exists=True,
        target_history_intents=["MOVE_STOP_TO_BE"],
        message_type_hint="UPDATE",
    )

    assert context.has_target_ref is True
    assert context.target_ref_kind == "reply_id"
    assert context.target_exists is True
    assert context.target_history_intents == ["MOVE_STOP_TO_BE"]
    assert context.message_type_hint == "UPDATE"


def test_exit_be_is_confirmed_when_target_history_is_coherent() -> None:
    result = apply_context_resolution_rules(
        intent_candidates=[_candidate("EXIT_BE")],
        context=ContextInput(
            has_target_ref=True,
            target_ref_kind="reply_id",
            target_exists=True,
            target_history_intents=["MOVE_STOP_TO_BE"],
            message_type_hint="INFO_ONLY",
        ),
        rules=[
            ContextResolutionRule(
                name="exit_be_requires_history",
                action="resolve_as",
                when={"has_weak_intent": "EXIT_BE", "has_target_ref": True},
                if_target_history_has_any=["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"],
                resolve_as="EXIT_BE",
                otherwise_resolve_as="INFO_ONLY",
            )
        ],
    )

    assert result.detected_intents == ["EXIT_BE"]
    assert result.applied_rules == ["exit_be_requires_history"]


def test_exit_be_falls_back_to_info_only_without_target() -> None:
    result = apply_context_resolution_rules(
        intent_candidates=[_candidate("EXIT_BE")],
        context=ContextInput(
            has_target_ref=False,
            target_ref_kind="unknown",
            target_exists=False,
            target_history_intents=[],
            message_type_hint="INFO_ONLY",
        ),
        rules=[
            ContextResolutionRule(
                name="exit_be_requires_history",
                action="resolve_as",
                when={"has_weak_intent": "EXIT_BE", "has_target_ref": True},
                if_target_history_has_any=["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"],
                resolve_as="EXIT_BE",
                otherwise_resolve_as="INFO_ONLY",
            )
        ],
    )

    assert result.detected_intents == ["INFO_ONLY"]
    assert result.applied_rules == ["exit_be_requires_history"]


def test_update_like_message_without_target_degrades_to_info_only() -> None:
    result = apply_context_resolution_rules(
        intent_candidates=[_candidate("MOVE_STOP_TO_BE")],
        context=ContextInput(
            has_target_ref=False,
            target_ref_kind="unknown",
            target_exists=False,
            target_history_intents=[],
            message_type_hint="UPDATE",
        ),
        rules=[
            ContextResolutionRule(
                name="update_requires_target",
                action="resolve_as",
                when={
                    "has_any_intent": ["MOVE_STOP_TO_BE"],
                    "has_target_ref": True,
                    "message_type_hint_in": ["UPDATE"],
                },
                resolve_as="MOVE_STOP_TO_BE",
                otherwise_resolve_as="INFO_ONLY",
            )
        ],
    )

    assert result.detected_intents == ["INFO_ONLY"]
    assert result.applied_rules == ["update_requires_target"]


def test_tp_hit_is_suppressed_when_target_does_not_exist() -> None:
    result = apply_context_resolution_rules(
        intent_candidates=[_candidate("TP_HIT", "strong")],
        context=ContextInput(
            has_target_ref=True,
            target_ref_kind="telegram_link",
            target_exists=False,
            target_history_intents=["CLOSE_FULL"],
            message_type_hint="REPORT",
        ),
        rules=[
            ContextResolutionRule(
                name="suppress_tp_when_target_closed",
                action="suppress",
                when={"has_strong_intent": "TP_HIT"},
                if_target_exists=False,
                suppress=["TP_HIT"],
            )
        ],
    )

    assert result.detected_intents == []
    assert result.applied_rules == ["suppress_tp_when_target_closed"]


def test_context_resolution_result_is_serializable() -> None:
    result = ContextResolutionResult(
        intent_candidates=[_candidate("EXIT_BE")],
        detected_intents=["EXIT_BE"],
        applied_rules=["exit_be_requires_history"],
    )

    restored = ContextResolutionResult.model_validate_json(result.model_dump_json())
    assert restored.detected_intents == ["EXIT_BE"]
    assert restored.applied_rules == ["exit_be_requires_history"]
