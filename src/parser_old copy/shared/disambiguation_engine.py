from __future__ import annotations

from pydantic import BaseModel, Field

from src.parser.canonical_v1.intent_candidate import IntentCandidate
from src.parser.canonical_v1.intent_taxonomy import IntentName
from src.parser.shared.disambiguation_rules_schema import DisambiguationRule


class DisambiguationResult(BaseModel):
    intent_candidates: list[IntentCandidate] = Field(default_factory=list)
    detected_intents: list[IntentName] = Field(default_factory=list)
    applied_rules: list[str] = Field(default_factory=list)


def _rule_matches(
    text_normalized: str,
    intent_candidates: list[IntentCandidate],
    rule: DisambiguationRule,
) -> bool:
    detected = [candidate.intent for candidate in intent_candidates]
    detected_set = set(detected)

    if rule.when_all_detected is not None and not set(rule.when_all_detected).issubset(
        detected_set
    ):
        return False

    if rule.when_any_detected is not None and not any(
        intent in detected_set for intent in rule.when_any_detected
    ):
        return False

    if rule.if_contains_any is not None and not any(
        token in text_normalized for token in rule.if_contains_any
    ):
        return False

    if rule.unless_contains_any is not None and any(
        token in text_normalized for token in rule.unless_contains_any
    ):
        return False

    return True


def _ensure_local_only_rule(rule: DisambiguationRule) -> None:
    if hasattr(rule, "target_ref") or hasattr(rule, "target_history"):
        raise ValueError(
            "disambiguation rules must not access target_ref or target_history"
        )


def apply_disambiguation_rules(
    text_normalized: str,
    intent_candidates: list[IntentCandidate],
    rules: list[DisambiguationRule],
) -> DisambiguationResult:
    candidates = list(intent_candidates)
    applied_rules: list[str] = []

    for rule in rules:
        _ensure_local_only_rule(rule)
        if not _rule_matches(text_normalized, candidates, rule):
            continue

        detected = [candidate.intent for candidate in candidates]
        allowed_intents = set(rule.when_any_detected or [])
        matched_intents = (
            list(rule.when_all_detected)
            if rule.when_all_detected is not None
            else [intent for intent in detected if intent in allowed_intents]
        )

        if rule.action == "prefer":
            if rule.prefer is not None:
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.intent == rule.prefer
                    or candidate.intent not in matched_intents
                ]
        elif rule.action == "suppress":
            suppress_set = set(rule.suppress or [])
            candidates = [
                candidate for candidate in candidates if candidate.intent not in suppress_set
            ]
        elif rule.action == "keep_multi":
            pass
        else:  # pragma: no cover - DisambiguationAction is validated upstream
            raise ValueError(f"unsupported disambiguation action: {rule.action}")

        applied_rules.append(rule.name)

    detected_intents = [candidate.intent for candidate in candidates]
    return DisambiguationResult(
        intent_candidates=candidates,
        detected_intents=detected_intents,
        applied_rules=applied_rules,
    )
