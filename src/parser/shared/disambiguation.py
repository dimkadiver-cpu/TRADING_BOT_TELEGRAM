from __future__ import annotations

from pydantic import BaseModel, Field

from src.parser.parsed_message import IntentResult, ParsedMessage
from src.parser.shared.disambiguation_rules_schema import DisambiguationRule


class DisambiguationResult(BaseModel):
    parsed_message: ParsedMessage
    applied_rules: list[str] = Field(default_factory=list)


def apply_disambiguation_rules(
    *,
    parsed_message: ParsedMessage,
    rules: list[dict] | list[DisambiguationRule],
) -> DisambiguationResult:
    normalized_rules = [
        rule if isinstance(rule, DisambiguationRule) else DisambiguationRule.model_validate(rule)
        for rule in rules
    ]
    normalized_rules.sort(key=lambda item: item.priority, reverse=True)

    updated = parsed_message.model_copy(deep=True)
    applied_rules: list[str] = []

    for rule in normalized_rules:
        if not _rule_matches(updated, rule):
            continue
        updated.intents = _apply_rule(updated.intents, rule)
        applied_rules.append(rule.name)

    if updated.primary_intent is not None and all(intent.type != updated.primary_intent for intent in updated.intents):
        updated.primary_intent = updated.intents[0].type if updated.intents else None
    updated.composite = len({intent.category for intent in updated.intents}) > 1
    updated.diagnostics["applied_disambiguation_rules"] = applied_rules
    return DisambiguationResult(parsed_message=updated, applied_rules=applied_rules)


def _rule_matches(parsed_message: ParsedMessage, rule: DisambiguationRule) -> bool:
    intents = _available_intents(parsed_message)
    intent_names = {intent.type.value for intent in intents}
    strong_names = {intent.type.value for intent in intents if intent.detection_strength == "strong"}
    weak_names = {intent.type.value for intent in intents if intent.detection_strength == "weak"}
    text = (parsed_message.raw_context.raw_text or "").lower()

    if rule.when_all_detected is not None and not set(rule.when_all_detected).issubset(intent_names):
        return False
    if rule.when_any_detected is not None and not any(name in intent_names for name in rule.when_any_detected):
        return False
    if rule.if_contains_any is not None and not any(token.lower() in text for token in rule.if_contains_any):
        return False
    if rule.unless_contains_any is not None and any(token.lower() in text for token in rule.unless_contains_any):
        return False
    if rule.when_strong and not set(rule.when_strong).issubset(strong_names):
        return False
    if rule.when_weak and not set(rule.when_weak).issubset(weak_names):
        return False
    if rule.text_any and not any(token.lower() in text for token in rule.text_any):
        return False
    if rule.text_none and any(token.lower() in text for token in rule.text_none):
        return False
    if rule.message_composite is not None and parsed_message.composite != rule.message_composite:
        return False

    has_targeting = parsed_message.targeting is not None and bool(parsed_message.targeting.refs)
    if rule.message_has_targeting is not None and has_targeting != rule.message_has_targeting:
        return False

    if any(not _entity_path_present(intents, path) for path in rule.entities_present):
        return False
    if any(_entity_path_present(intents, path) for path in rule.entities_absent):
        return False

    return True


def _apply_rule(intents: list[IntentResult], rule: DisambiguationRule) -> list[IntentResult]:
    if rule.action == "keep_multi":
        return list(intents)
    if rule.action == "suppress":
        suppress_set = set(rule.suppress or [])
        return [intent for intent in intents if intent.type.value not in suppress_set]
    if rule.action == "prefer":
        if rule.over:
            over_set = set(rule.over)
        else:
            over_set = set(rule.when_all_detected or [])
            over_set.update(rule.when_any_detected or [])
            over_set.discard(rule.prefer)
        return [
            intent
            for intent in intents
            if intent.type == rule.prefer or intent.type.value not in over_set
        ]
    raise ValueError(f"unsupported disambiguation action: {rule.action}")


def _available_intents(parsed_message: ParsedMessage) -> list[IntentResult]:
    confirmed = [intent for intent in parsed_message.intents if intent.status == "CONFIRMED"]
    return confirmed or [intent for intent in parsed_message.intents if intent.status != "INVALID"]


def _entity_path_present(intents: list[IntentResult], path: str) -> bool:
    if "." not in path:
        return False
    intent_name, field_name = path.split(".", 1)
    for intent in intents:
        if intent.type.value != intent_name:
            continue
        if getattr(intent.entities, field_name, None) is not None:
            return True
    return False
