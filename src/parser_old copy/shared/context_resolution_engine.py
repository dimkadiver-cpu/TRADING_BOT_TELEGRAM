from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.parser.canonical_v1.intent_candidate import IntentCandidate
from src.parser.canonical_v1.intent_taxonomy import IntentName
from src.parser.shared.context_resolution_schema import ContextResolutionRule, ContextResolutionWhen

ContextRefKind = Literal["reply_id", "telegram_link", "explicit_id", "global_scope", "unknown"]


class ContextInput(BaseModel):
    has_target_ref: bool
    target_ref_kind: ContextRefKind
    target_exists: bool
    target_history_intents: list[IntentName]
    message_type_hint: str | None = None


class ContextResolutionResult(BaseModel):
    intent_candidates: list[IntentCandidate] = Field(default_factory=list)
    detected_intents: list[IntentName] = Field(default_factory=list)
    applied_rules: list[str] = Field(default_factory=list)


def _normalise_hint(value: str | None) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def _rule_trigger_intents(when: ContextResolutionWhen) -> set[IntentName]:
    intents: set[IntentName] = set()
    if when.has_weak_intent is not None:
        intents.add(when.has_weak_intent)
    if when.has_strong_intent is not None:
        intents.add(when.has_strong_intent)
    if when.has_any_intent is not None:
        intents.update(when.has_any_intent)
    return intents


def _rule_matches_intent_signal(
    candidates: list[IntentCandidate],
    when: ContextResolutionWhen,
) -> bool:
    detected = {candidate.intent for candidate in candidates}

    if when.has_weak_intent is not None and not any(
        candidate.intent == when.has_weak_intent and candidate.strength == "weak"
        for candidate in candidates
    ):
        return False

    if when.has_strong_intent is not None and not any(
        candidate.intent == when.has_strong_intent and candidate.strength == "strong"
        for candidate in candidates
    ):
        return False

    if when.has_any_intent is not None and not any(
        intent in detected for intent in when.has_any_intent
    ):
        return False

    return True


def _rule_matches_context(
    context: ContextInput,
    rule: ContextResolutionRule,
) -> bool:
    when = rule.when

    if when.has_target_ref is not None and when.has_target_ref != context.has_target_ref:
        return False

    if when.message_type_hint_in is not None:
        hint = _normalise_hint(context.message_type_hint)
        allowed = {_normalise_hint(item) for item in when.message_type_hint_in}
        if hint not in allowed:
            return False

    if rule.if_target_exists is not None and rule.if_target_exists != context.target_exists:
        return False

    if rule.if_target_history_has_any is not None and not any(
        intent in context.target_history_intents for intent in rule.if_target_history_has_any
    ):
        return False

    if rule.if_target_history_lacks_all is not None and any(
        intent in context.target_history_intents for intent in rule.if_target_history_lacks_all
    ):
        return False

    return True


def _resolve_candidates(
    candidates: list[IntentCandidate],
    rule: ContextResolutionRule,
    *,
    fallback: bool = False,
) -> list[IntentCandidate]:
    if rule.action == "suppress":
        suppress_set = set(rule.suppress or [])
        return [candidate for candidate in candidates if candidate.intent not in suppress_set]

    if rule.action == "set_primary":
        primary = rule.primary
        if primary is None:
            return list(candidates)
        primary_candidates = [candidate for candidate in candidates if candidate.intent == primary]
        if not primary_candidates:
            return list(candidates)
        others = [candidate for candidate in candidates if candidate.intent != primary]
        return [primary_candidates[0], *others]

    trigger_intents = _rule_trigger_intents(rule.when)
    matched = [candidate for candidate in candidates if candidate.intent in trigger_intents]

    if not matched:
        return list(candidates)

    if rule.action == "promote" and not fallback:
        promoted: list[IntentCandidate] = []
        for candidate in candidates:
            if candidate.intent in trigger_intents:
                promoted.append(
                    IntentCandidate(
                        intent=candidate.intent,
                        strength="strong",
                        evidence=list(candidate.evidence),
                    )
                )
            else:
                promoted.append(candidate)
        return promoted

    if rule.action == "resolve_as":
        resolved_intent = rule.otherwise_resolve_as if fallback and rule.otherwise_resolve_as is not None else rule.resolve_as
        if resolved_intent is None:
            return list(candidates)

        resolved_strength = "strong" if not fallback else "weak"
        evidence = [e for candidate in matched for e in candidate.evidence]
        resolved_candidate = IntentCandidate(
            intent=resolved_intent,
            strength=resolved_strength,
            evidence=evidence,
        )
        resolved_candidates: list[IntentCandidate] = []
        inserted = False
        for candidate in candidates:
            if candidate.intent in trigger_intents:
                if not inserted:
                    resolved_candidates.append(resolved_candidate)
                    inserted = True
                continue
            resolved_candidates.append(candidate)
        return resolved_candidates

    return list(candidates)


def apply_context_resolution_rules(
    intent_candidates: list[IntentCandidate],
    context: ContextInput,
    rules: list[ContextResolutionRule],
) -> ContextResolutionResult:
    candidates = list(intent_candidates)
    applied_rules: list[str] = []

    for rule in rules:
        intent_signal_matches = _rule_matches_intent_signal(candidates, rule.when)
        if not intent_signal_matches:
            continue

        context_matches = _rule_matches_context(context, rule)
        if context_matches:
            candidates = _resolve_candidates(candidates, rule)
            applied_rules.append(rule.name)
            continue

        if rule.action == "resolve_as" and rule.otherwise_resolve_as is not None:
            candidates = _resolve_candidates(candidates, rule, fallback=True)
            applied_rules.append(rule.name)

    detected_intents = [candidate.intent for candidate in candidates]
    return ContextResolutionResult(
        intent_candidates=candidates,
        detected_intents=detected_intents,
        applied_rules=applied_rules,
    )
