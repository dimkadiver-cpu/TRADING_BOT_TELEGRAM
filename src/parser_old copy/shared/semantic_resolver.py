from __future__ import annotations

from pydantic import BaseModel, Field

from src.parser.canonical_v1.intent_candidate import IntentCandidate
from src.parser.canonical_v1.intent_taxonomy import STATEFUL_INTENTS, IntentName
from src.parser.shared.compatibility_engine import (
    CompatibilityResult,
    evaluate_intent_compatibility,
)
from src.parser.shared.context_resolution_engine import (
    ContextInput,
    apply_context_resolution_rules,
)
from src.parser.shared.context_resolution_schema import ContextResolutionRule
from src.parser.shared.disambiguation_engine import apply_disambiguation_rules
from src.parser.shared.disambiguation_rules_schema import DisambiguationRule
from src.parser.shared.intent_compatibility_schema import IntentCompatibilityPair
from src.parser.shared.resolution_unit import ResolutionUnit

_PRIMARY_INTENT_PRECEDENCE: tuple[IntentName, ...] = (
    "SL_HIT",
    "EXIT_BE",
    "TP_HIT",
    "REPORT_FINAL_RESULT",
    "REPORT_PARTIAL_RESULT",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING_ORDERS",
    "INVALIDATE_SETUP",
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "UPDATE_TAKE_PROFITS",
    "ADD_ENTRY",
    "REENTER",
    "ENTRY_FILLED",
    "NEW_SETUP",
    "INFO_ONLY",
)


class SemanticResolverInput(BaseModel):
    text_normalized: str
    intent_candidates: list[IntentCandidate]
    context: ContextInput
    resolution_unit: ResolutionUnit


class ResolverDiagnostics(BaseModel):
    intents_before_disambiguation: list[IntentName] = Field(default_factory=list)
    intents_after_disambiguation: list[IntentName] = Field(default_factory=list)
    intents_after_context_resolution: list[IntentName] = Field(default_factory=list)
    applied_disambiguation_rules: list[str] = Field(default_factory=list)
    applied_context_rules: list[str] = Field(default_factory=list)
    primary_intent_reason: str = ""
    unresolved_warnings: list[str] = Field(default_factory=list)


class SemanticResolverOutput(BaseModel):
    primary_intent: IntentName | None = None
    final_intents: list[IntentName] = Field(default_factory=list)
    diagnostics: ResolverDiagnostics


def _needs_context_resolution(
    compatibility: CompatibilityResult,
    intents: list[IntentName],
    context: ContextInput,
    rules: list[ContextResolutionRule],
) -> bool:
    if not rules:
        return False
    if compatibility.requires_context_validation:
        return True
    if any(intent in STATEFUL_INTENTS for intent in intents):
        return True
    return (
        (context.message_type_hint or "").strip().upper() == "UPDATE"
        and not context.has_target_ref
        and bool(intents)
    )


def _has_still_unresolved_conflict(
    compatibility: CompatibilityResult,
    final_intents: list[IntentName],
) -> bool:
    final_set = set(final_intents)
    return any(
        pair.warning_if_unresolved and set(pair.intents).issubset(final_set)
        for pair in compatibility.conflicting_pairs
    )


def _derive_primary_intent_reason(
    intents: list[IntentName],
    primary_intent: IntentName | None,
    compat_result: CompatibilityResult,
) -> str:
    if primary_intent is None:
        return "no_intents"
    if len(intents) == 1:
        return f"single_intent:{primary_intent}"
    for pair in compat_result.conflicting_pairs:
        if pair.preferred == primary_intent and set(pair.intents).issubset(set(intents)):
            return f"compatibility_preferred:{primary_intent}"
    return f"precedence:{primary_intent}"


def select_primary_intent(
    intents: list[IntentName],
    compat_result: CompatibilityResult | object | None,
) -> IntentName | None:
    if not intents:
        return None

    conflict_pairs = getattr(compat_result, "conflicting_pairs", [])
    detected = set(intents)
    for pair in conflict_pairs:
        preferred = getattr(pair, "preferred", None)
        if preferred is not None and set(pair.intents).issubset(detected):
            return preferred

    for candidate in _PRIMARY_INTENT_PRECEDENCE:
        if candidate in detected:
            return candidate

    return sorted(intents)[0]


class SemanticResolver:
    def __init__(
        self,
        *,
        compatibility_pairs: list[IntentCompatibilityPair] | None = None,
        disambiguation_rules: list[DisambiguationRule] | None = None,
        context_resolution_rules: list[ContextResolutionRule] | None = None,
    ) -> None:
        self._compatibility_pairs = list(compatibility_pairs or [])
        self._disambiguation_rules = list(disambiguation_rules or [])
        self._context_resolution_rules = list(context_resolution_rules or [])

    def resolve(self, resolver_input: SemanticResolverInput) -> SemanticResolverOutput:
        intents_before = [
            candidate.intent for candidate in resolver_input.intent_candidates
        ]
        compatibility = evaluate_intent_compatibility(
            intents_before,
            self._compatibility_pairs,
        )

        resolved_local_candidates = list(resolver_input.intent_candidates)
        intents_after_disambiguation = list(intents_before)
        applied_disambiguation_rules: list[str] = []

        if compatibility.requires_local_resolution:
            local_result = apply_disambiguation_rules(
                text_normalized=resolver_input.text_normalized,
                intent_candidates=resolved_local_candidates,
                rules=self._disambiguation_rules,
            )
            resolved_local_candidates = local_result.intent_candidates
            intents_after_disambiguation = local_result.detected_intents
            applied_disambiguation_rules = local_result.applied_rules

        resolved_context_candidates = list(resolved_local_candidates)
        intents_after_context_resolution = list(intents_after_disambiguation)
        applied_context_rules: list[str] = []

        if _needs_context_resolution(
            compatibility,
            intents_after_disambiguation,
            resolver_input.context,
            self._context_resolution_rules,
        ):
            context_result = apply_context_resolution_rules(
                intent_candidates=resolved_context_candidates,
                context=resolver_input.context,
                rules=self._context_resolution_rules,
            )
            resolved_context_candidates = context_result.intent_candidates
            intents_after_context_resolution = context_result.detected_intents
            applied_context_rules = context_result.applied_rules

        final_compatibility = evaluate_intent_compatibility(
            intents_after_context_resolution,
            self._compatibility_pairs,
        )

        unresolved_warnings: list[str] = []
        if _has_still_unresolved_conflict(
            compatibility,
            intents_after_context_resolution,
        ) and not (applied_disambiguation_rules or applied_context_rules):
            unresolved_warnings.append("unresolved_intent_conflict")

        final_compatibility.resolved = not unresolved_warnings
        primary_intent = select_primary_intent(
            intents_after_context_resolution,
            final_compatibility,
        )

        diagnostics = ResolverDiagnostics(
            intents_before_disambiguation=intents_before,
            intents_after_disambiguation=intents_after_disambiguation,
            intents_after_context_resolution=intents_after_context_resolution,
            applied_disambiguation_rules=applied_disambiguation_rules,
            applied_context_rules=applied_context_rules,
            primary_intent_reason=_derive_primary_intent_reason(
                intents_after_context_resolution,
                primary_intent,
                final_compatibility,
            ),
            unresolved_warnings=unresolved_warnings,
        )

        return SemanticResolverOutput(
            primary_intent=primary_intent,
            final_intents=intents_after_context_resolution,
            diagnostics=diagnostics,
        )
