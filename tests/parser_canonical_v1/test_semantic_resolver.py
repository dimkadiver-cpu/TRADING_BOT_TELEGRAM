from __future__ import annotations

import pytest

from src.parser.canonical_v1.intent_candidate import IntentCandidate
from src.parser.canonical_v1.intent_taxonomy import IntentName
from src.parser.shared.compatibility_engine import (
    CompatibilityResult,
    evaluate_intent_compatibility,
)
from src.parser.shared.context_resolution_engine import (
    ContextInput,
    ContextResolutionResult,
    apply_context_resolution_rules,
)
from src.parser.shared.context_resolution_schema import ContextResolutionRule
from src.parser.shared.disambiguation_engine import (
    DisambiguationResult,
    apply_disambiguation_rules,
)
from src.parser.shared.disambiguation_rules_schema import DisambiguationRule
from src.parser.shared.intent_compatibility_schema import IntentCompatibilityPair
from src.parser.shared.semantic_resolver import (
    ResolverDiagnostics,
    SemanticResolver,
    SemanticResolverInput,
    SemanticResolverOutput,
    select_primary_intent,
)


def _candidate(intent: IntentName, strength: str = "weak") -> IntentCandidate:
    return IntentCandidate(intent=intent, strength=strength, evidence=[f"marker: {intent}"])


PROPOSAL_PAIRS = [
    IntentCompatibilityPair(
        intents=["MOVE_STOP_TO_BE", "MOVE_STOP"],
        relation="specific_vs_generic",
        preferred="MOVE_STOP_TO_BE",
        requires_resolution=True,
    ),
    IntentCompatibilityPair(
        intents=["EXIT_BE", "CLOSE_FULL"],
        relation="specific_vs_generic",
        preferred="EXIT_BE",
        requires_resolution=True,
        requires_context_validation=True,
    ),
    IntentCompatibilityPair(
        intents=["TP_HIT", "REPORT_FINAL_RESULT"],
        relation="compatible",
        requires_resolution=False,
    ),
    IntentCompatibilityPair(
        intents=["SL_HIT", "CLOSE_FULL"],
        relation="exclusive",
        requires_resolution=True,
    ),
]

DISAMBIGUATION_RULES = [
    DisambiguationRule(
        name="prefer_be_over_move_stop",
        action="prefer",
        when_all_detected=["MOVE_STOP_TO_BE", "MOVE_STOP"],
        prefer="MOVE_STOP_TO_BE",
        if_contains_any=["bu", "breakeven"],
    ),
    DisambiguationRule(
        name="prefer_exit_be_over_close_full",
        action="prefer",
        when_all_detected=["EXIT_BE", "CLOSE_FULL"],
        prefer="EXIT_BE",
        if_contains_any=["bu", "breakeven"],
    ),
    DisambiguationRule(
        name="suppress_close_full_if_partial",
        action="suppress",
        when_all_detected=["CLOSE_FULL", "CLOSE_PARTIAL"],
        suppress=["CLOSE_FULL"],
        if_contains_any=["partial", "parziale"],
        unless_contains_any=["mantieni"],
    ),
    DisambiguationRule(
        name="keep_sl_and_close",
        action="keep_multi",
        when_all_detected=["SL_HIT", "CLOSE_FULL"],
        keep=["SL_HIT", "CLOSE_FULL"],
    ),
]

CONTEXT_RULES = [
    ContextResolutionRule(
        name="exit_be_requires_history",
        action="resolve_as",
        when={"has_weak_intent": "EXIT_BE", "has_target_ref": True},
        if_target_history_has_any=["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"],
        resolve_as="EXIT_BE",
        otherwise_resolve_as="INFO_ONLY",
    ),
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
    ),
    ContextResolutionRule(
        name="suppress_tp_when_target_closed",
        action="suppress",
        when={"has_strong_intent": "TP_HIT"},
        if_target_exists=False,
        suppress=["TP_HIT"],
    ),
    ContextResolutionRule(
        name="promote_partial_close_with_target",
        action="promote",
        when={"has_weak_intent": "CLOSE_PARTIAL", "has_target_ref": True},
        if_target_exists=True,
        intent="CLOSE_PARTIAL",
    ),
    ContextResolutionRule(
        name="set_report_primary",
        action="set_primary",
        when={"has_any_intent": ["REPORT_FINAL_RESULT"]},
        primary="REPORT_FINAL_RESULT",
    ),
]

STRONG_EXIT_BE_RULE = ContextResolutionRule(
    name="exit_be_strong_without_history_falls_back",
    action="resolve_as",
    when={"has_strong_intent": "EXIT_BE", "has_target_ref": True},
    if_target_history_has_any=["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"],
    resolve_as="EXIT_BE",
    otherwise_resolve_as="INFO_ONLY",
)


def _context(
    *,
    has_target_ref: bool = True,
    target_ref_kind: str = "reply_id",
    target_exists: bool = True,
    target_history_intents: list[IntentName] | None = None,
    message_type_hint: str | None = "UPDATE",
) -> ContextInput:
    return ContextInput(
        has_target_ref=has_target_ref,
        target_ref_kind=target_ref_kind,  # type: ignore[arg-type]
        target_exists=target_exists,
        target_history_intents=list(target_history_intents or []),
        message_type_hint=message_type_hint,
    )


def _resolver(
    *,
    compatibility_pairs: list[IntentCompatibilityPair] | None = None,
    disambiguation_rules: list[DisambiguationRule] | None = None,
    context_resolution_rules: list[ContextResolutionRule] | None = None,
) -> SemanticResolver:
    return SemanticResolver(
        compatibility_pairs=PROPOSAL_PAIRS if compatibility_pairs is None else compatibility_pairs,
        disambiguation_rules=DISAMBIGUATION_RULES if disambiguation_rules is None else disambiguation_rules,
        context_resolution_rules=CONTEXT_RULES if context_resolution_rules is None else context_resolution_rules,
    )


@pytest.mark.parametrize(
    ("detected", "expected_local", "expected_context", "expected_conflicts"),
    [
        (["MOVE_STOP_TO_BE", "MOVE_STOP"], True, False, [("MOVE_STOP_TO_BE", "MOVE_STOP")]),
        (["TP_HIT", "REPORT_FINAL_RESULT"], False, False, []),
        (["EXIT_BE", "CLOSE_FULL"], True, True, [("EXIT_BE", "CLOSE_FULL")]),
        (["SL_HIT", "CLOSE_FULL"], True, False, [("SL_HIT", "CLOSE_FULL")]),
        (["NEW_SETUP", "INFO_ONLY"], False, False, []),
        (["MOVE_STOP_TO_BE", "MOVE_STOP", "INFO_ONLY"], True, False, [("MOVE_STOP_TO_BE", "MOVE_STOP")]),
        (["EXIT_BE", "CLOSE_FULL", "INFO_ONLY"], True, True, [("EXIT_BE", "CLOSE_FULL")]),
        (
            ["MOVE_STOP_TO_BE", "MOVE_STOP", "EXIT_BE", "CLOSE_FULL"],
            True,
            True,
            [("MOVE_STOP_TO_BE", "MOVE_STOP"), ("EXIT_BE", "CLOSE_FULL")],
        ),
    ],
)
def test_intent_compatibility_regression_table(
    detected: list[IntentName],
    expected_local: bool,
    expected_context: bool,
    expected_conflicts: list[tuple[IntentName, IntentName]],
) -> None:
    result = evaluate_intent_compatibility(detected, PROPOSAL_PAIRS)

    assert isinstance(result, CompatibilityResult)
    assert result.requires_local_resolution is expected_local
    assert result.requires_context_validation is expected_context
    assert result.resolved is False
    assert [tuple(pair.intents) for pair in result.conflicting_pairs] == expected_conflicts


@pytest.mark.parametrize(
    ("text", "candidates", "expected_intents", "expected_rules"),
    [
        (
            "sposta in bu",
            [_candidate("MOVE_STOP_TO_BE", "strong"), _candidate("MOVE_STOP", "weak")],
            ["MOVE_STOP_TO_BE"],
            ["prefer_be_over_move_stop"],
        ),
        (
            "sposta stop normale",
            [_candidate("MOVE_STOP_TO_BE", "strong"), _candidate("MOVE_STOP", "weak")],
            ["MOVE_STOP_TO_BE", "MOVE_STOP"],
            [],
        ),
        (
            "chiudo partial",
            [_candidate("CLOSE_FULL", "weak"), _candidate("CLOSE_PARTIAL", "strong")],
            ["CLOSE_PARTIAL"],
            ["suppress_close_full_if_partial"],
        ),
        (
            "chiudo partial ma mantieni full",
            [_candidate("CLOSE_FULL", "weak"), _candidate("CLOSE_PARTIAL", "strong")],
            ["CLOSE_FULL", "CLOSE_PARTIAL"],
            [],
        ),
        (
            "sl e chiusura finale",
            [_candidate("SL_HIT", "strong"), _candidate("CLOSE_FULL", "weak")],
            ["SL_HIT", "CLOSE_FULL"],
            ["keep_sl_and_close"],
        ),
        (
            "chiusa in breakeven",
            [_candidate("EXIT_BE", "weak"), _candidate("CLOSE_FULL", "strong")],
            ["EXIT_BE"],
            ["prefer_exit_be_over_close_full"],
        ),
    ],
)
def test_disambiguation_rules_regression_table(
    text: str,
    candidates: list[IntentCandidate],
    expected_intents: list[IntentName],
    expected_rules: list[str],
) -> None:
    result = apply_disambiguation_rules(
        text_normalized=text,
        intent_candidates=candidates,
        rules=DISAMBIGUATION_RULES,
    )

    assert isinstance(result, DisambiguationResult)
    assert [candidate.intent for candidate in result.intent_candidates] == expected_intents
    assert result.detected_intents == expected_intents
    assert result.applied_rules == expected_rules


@pytest.mark.parametrize(
    ("candidates", "context", "rules", "expected_intents", "expected_rules", "expected_strengths"),
    [
        (
            [_candidate("EXIT_BE", "weak")],
            _context(target_history_intents=["MOVE_STOP_TO_BE"]),
            CONTEXT_RULES,
            ["EXIT_BE"],
            ["exit_be_requires_history"],
            ["strong"],
        ),
        (
            [_candidate("EXIT_BE", "weak")],
            _context(
                has_target_ref=False,
                target_ref_kind="unknown",
                target_exists=False,
                target_history_intents=[],
                message_type_hint="INFO_ONLY",
            ),
            CONTEXT_RULES,
            ["INFO_ONLY"],
            ["exit_be_requires_history"],
            ["weak"],
        ),
        (
            [_candidate("EXIT_BE", "strong")],
            _context(target_history_intents=[]),
            [STRONG_EXIT_BE_RULE],
            ["INFO_ONLY"],
            ["exit_be_strong_without_history_falls_back"],
            ["weak"],
        ),
        (
            [_candidate("MOVE_STOP_TO_BE", "weak")],
            _context(
                has_target_ref=False,
                target_ref_kind="unknown",
                target_exists=False,
                target_history_intents=[],
                message_type_hint="UPDATE",
            ),
            CONTEXT_RULES,
            ["INFO_ONLY"],
            ["update_requires_target"],
            ["weak"],
        ),
        (
            [_candidate("TP_HIT", "strong")],
            _context(
                target_ref_kind="telegram_link",
                target_exists=False,
                target_history_intents=["CLOSE_FULL"],
                message_type_hint="REPORT",
            ),
            CONTEXT_RULES,
            [],
            ["suppress_tp_when_target_closed"],
            [],
        ),
        (
            [_candidate("CLOSE_PARTIAL", "weak")],
            _context(
                target_history_intents=["NEW_SETUP"],
                message_type_hint="UPDATE",
            ),
            CONTEXT_RULES,
            ["CLOSE_PARTIAL"],
            ["promote_partial_close_with_target"],
            ["strong"],
        ),
    ],
)
def test_context_resolution_rules_regression_table(
    candidates: list[IntentCandidate],
    context: ContextInput,
    rules: list[ContextResolutionRule],
    expected_intents: list[IntentName],
    expected_rules: list[str],
    expected_strengths: list[str],
) -> None:
    result = apply_context_resolution_rules(
        intent_candidates=candidates,
        context=context,
        rules=rules,
    )

    assert isinstance(result, ContextResolutionResult)
    assert result.detected_intents == expected_intents
    assert result.applied_rules == expected_rules
    assert [candidate.strength for candidate in result.intent_candidates] == expected_strengths


@pytest.mark.parametrize(
    ("text", "candidates", "context", "expected_primary", "expected_final", "expected_disamb", "expected_context"),
    [
        (
            "sposta in bu",
            [_candidate("MOVE_STOP_TO_BE", "strong"), _candidate("MOVE_STOP", "weak")],
            _context(target_history_intents=["NEW_SETUP"]),
            "MOVE_STOP_TO_BE",
            ["MOVE_STOP_TO_BE"],
            ["prefer_be_over_move_stop"],
            [],
        ),
        (
            "chiusa in bu",
            [_candidate("EXIT_BE"), _candidate("CLOSE_FULL")],
            _context(target_history_intents=["MOVE_STOP_TO_BE"]),
            "EXIT_BE",
            ["EXIT_BE"],
            ["prefer_exit_be_over_close_full"],
            ["exit_be_requires_history"],
        ),
        (
            "chiusa in bu",
            [_candidate("EXIT_BE"), _candidate("CLOSE_FULL")],
            _context(
                has_target_ref=False,
                target_ref_kind="unknown",
                target_exists=False,
                target_history_intents=[],
                message_type_hint="INFO_ONLY",
            ),
            "INFO_ONLY",
            ["INFO_ONLY"],
            ["prefer_exit_be_over_close_full"],
            ["exit_be_requires_history"],
        ),
        (
            "stop colpito e chiusura finale",
            [_candidate("SL_HIT", "strong"), _candidate("CLOSE_FULL", "weak")],
            _context(
                target_ref_kind="telegram_link",
                target_exists=True,
                target_history_intents=["NEW_SETUP"],
                message_type_hint="REPORT",
            ),
            "SL_HIT",
            ["SL_HIT", "CLOSE_FULL"],
            ["keep_sl_and_close"],
            [],
        ),
        (
            "sposta stop in be",
            [_candidate("MOVE_STOP_TO_BE", "weak")],
            _context(
                has_target_ref=False,
                target_ref_kind="unknown",
                target_exists=False,
                target_history_intents=[],
                message_type_hint="UPDATE",
            ),
            "INFO_ONLY",
            ["INFO_ONLY"],
            [],
            ["update_requires_target"],
        ),
    ],
)
def test_semantic_resolver_end_to_end_guidance_cases(
    text: str,
    candidates: list[IntentCandidate],
    context: ContextInput,
    expected_primary: IntentName,
    expected_final: list[IntentName],
    expected_disamb: list[str],
    expected_context: list[str],
) -> None:
    result = _resolver().resolve(
        SemanticResolverInput(
            text_normalized=text,
            intent_candidates=candidates,
            context=context,
            resolution_unit="MESSAGE_WIDE",
        )
    )

    assert isinstance(result, SemanticResolverOutput)
    assert result.primary_intent == expected_primary
    assert result.final_intents == expected_final
    assert result.diagnostics.applied_disambiguation_rules == expected_disamb
    assert result.diagnostics.applied_context_rules == expected_context


def test_unresolved_conflict_emits_warning_when_no_rule_matches() -> None:
    resolver = _resolver(disambiguation_rules=[], context_resolution_rules=[])

    result = resolver.resolve(
        SemanticResolverInput(
            text_normalized="sposta stop",
            intent_candidates=[
                _candidate("MOVE_STOP_TO_BE", "strong"),
                _candidate("MOVE_STOP", "weak"),
            ],
            context=_context(target_history_intents=["NEW_SETUP"]),
            resolution_unit="MESSAGE_WIDE",
        )
    )

    assert result.primary_intent == "MOVE_STOP_TO_BE"
    assert result.final_intents == ["MOVE_STOP_TO_BE", "MOVE_STOP"]
    assert result.diagnostics.unresolved_warnings == ["unresolved_intent_conflict"]


def test_no_op_when_all_intents_are_compatible_keeps_diagnostics_clean() -> None:
    resolver = _resolver(
        compatibility_pairs=PROPOSAL_PAIRS,
        disambiguation_rules=[],
        context_resolution_rules=[],
    )

    result = resolver.resolve(
        SemanticResolverInput(
            text_normalized="tp hit finale",
            intent_candidates=[
                _candidate("TP_HIT", "strong"),
                _candidate("REPORT_FINAL_RESULT", "weak"),
            ],
            context=_context(message_type_hint="REPORT"),
            resolution_unit="MESSAGE_WIDE",
        )
    )

    assert result.final_intents == ["TP_HIT", "REPORT_FINAL_RESULT"]
    assert result.diagnostics.applied_disambiguation_rules == []
    assert result.diagnostics.applied_context_rules == []
    assert result.diagnostics.unresolved_warnings == []
    assert result.diagnostics.intents_before_disambiguation == [
        "TP_HIT",
        "REPORT_FINAL_RESULT",
    ]
    assert result.diagnostics.intents_after_disambiguation == [
        "TP_HIT",
        "REPORT_FINAL_RESULT",
    ]
    assert result.diagnostics.intents_after_context_resolution == [
        "TP_HIT",
        "REPORT_FINAL_RESULT",
    ]


def test_same_input_produces_same_output() -> None:
    resolver = _resolver()
    resolver_input = SemanticResolverInput(
        text_normalized="chiusa in bu",
        intent_candidates=[
            _candidate("EXIT_BE"),
            _candidate("CLOSE_FULL"),
        ],
        context=_context(target_history_intents=["MOVE_STOP_TO_BE"]),
        resolution_unit="MESSAGE_WIDE",
    )

    first = resolver.resolve(resolver_input)
    second = resolver.resolve(resolver_input)

    assert first.model_dump() == second.model_dump()


def test_select_primary_intent_uses_preferred_pair_then_precedence() -> None:
    compat = type("CompatStub", (), {"conflicting_pairs": PROPOSAL_PAIRS})()

    assert select_primary_intent(["EXIT_BE", "CLOSE_FULL"], compat) == "EXIT_BE"
    assert select_primary_intent(["SL_HIT", "CLOSE_FULL"], compat) == "SL_HIT"
    assert select_primary_intent([], compat) is None


def test_diagnostics_model_round_trip() -> None:
    diagnostics = ResolverDiagnostics(
        intents_before_disambiguation=["MOVE_STOP_TO_BE", "MOVE_STOP"],
        intents_after_disambiguation=["MOVE_STOP_TO_BE"],
        intents_after_context_resolution=["MOVE_STOP_TO_BE"],
        applied_disambiguation_rules=["prefer_be_over_move_stop"],
        applied_context_rules=[],
        primary_intent_reason="single_intent:MOVE_STOP_TO_BE",
        unresolved_warnings=[],
    )

    restored = ResolverDiagnostics.model_validate_json(diagnostics.model_dump_json())
    assert restored.primary_intent_reason == "single_intent:MOVE_STOP_TO_BE"
