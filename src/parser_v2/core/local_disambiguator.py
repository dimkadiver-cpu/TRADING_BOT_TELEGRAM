from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules


@dataclass(frozen=True)
class LocalDisambiguationResult:
    intents: list[ParsedIntent]
    primary_intent: str | None
    suppressed_intents: list[ParsedIntent]
    diagnostics: dict[str, list[str]]


class LocalDisambiguator:
    def resolve(
        self,
        intents: list[ParsedIntent],
        rules: ParserRules,
        *,
        signal: SignalDraft | None = None,
        normalized: NormalizedText | None = None,
    ) -> LocalDisambiguationResult:
        active = list(intents)
        suppressed: list[ParsedIntent] = []
        applied_rules: list[str] = []

        for rule in rules.disambiguation:
            if _is_context_market_rule(rule):
                removed = _apply_context_market_rule(
                    active,
                    suppressed,
                    rule=rule,
                    signal=signal,
                    normalized=normalized,
                )
                if removed:
                    _append_once(applied_rules, _rule_name(rule))
                continue

            if not _rule_matches(rule, active, normalized):
                continue

            removed = _apply_prefer_suppress_rule(active, suppressed, rule)
            if removed or _rule_action(rule) == "keep_multi":
                _append_once(applied_rules, _rule_name(rule))

        return LocalDisambiguationResult(
            intents=active,
            primary_intent=_select_primary_intent(active, rules),
            suppressed_intents=suppressed,
            diagnostics={
                "applied_disambiguation_rules": applied_rules,
                "suppressed_intents": [_format_intent(intent) for intent in suppressed],
            },
        )


def _apply_prefer_suppress_rule(
    active: list[ParsedIntent],
    suppressed: list[ParsedIntent],
    rule: dict[str, Any],
) -> bool:
    scope = rule.get("scope", "whole_message")
    remove_types: set[str] = set()
    action = _rule_action(rule)

    if action == "suppress":
        remove_types.update(rule.get("suppress") or [])
    else:
        prefer = rule.get("prefer")
        if prefer is None:
            return False
        over = rule.get("over")
        if over is None:
            over = [
                intent_type
                for intent_type in rule.get("when_all_detected", [])
                if intent_type != prefer
            ]
        remove_types.update(over)

    if scope == "whole_message":
        return _remove_types(active, suppressed, remove_types)

    # Find "preferred" intents to determine context
    prefer_type = rule.get("prefer")
    preferred_intents = [i for i in active if i.type == prefer_type] if prefer_type else []

    if not preferred_intents:
        return _remove_types(active, suppressed, remove_types)

    removed_any = False
    for preferred in preferred_intents:
        to_remove = [
            intent for intent in active
            if intent.type in remove_types
            and _scope_matches(preferred, intent, scope)
        ]
        for intent in to_remove:
            active.remove(intent)
            suppressed.append(intent)
            removed_any = True
    return removed_any


def _scope_matches(preferred: ParsedIntent, candidate: ParsedIntent, scope: str) -> bool:
    if scope == "same_span":
        if preferred.span_start is None or preferred.span_end is None:
            return False
        if candidate.span_start is None or candidate.span_end is None:
            return False
        return not (candidate.span_end <= preferred.span_start or candidate.span_start >= preferred.span_end)
    if scope in ("same_line", "same_sentence", "same_target_group"):
        return preferred.line_index is not None and preferred.line_index == candidate.line_index
    return True  # fallback whole_message


def _apply_context_market_rule(
    active: list[ParsedIntent],
    suppressed: list[ParsedIntent],
    *,
    rule: dict[str, Any],
    signal: SignalDraft | None,
    normalized: NormalizedText | None,
) -> bool:
    markers = [
        marker
        for marker in rule.get("when_marker_in", [])
        if isinstance(marker, str)
    ]
    if markers and not _any_marker_present(markers, active, normalized):
        return False

    if signal is None:
        return False

    payload_rule = rule.get("if_signal_payload_present") or {}
    if payload_rule.get("interpret_as") != "ENTRY_TYPE_MARKET":
        return False

    to_remove = [
        intent
        for intent in active
        if intent.type == "MODIFY_ENTRY"
        and getattr(intent.entities, "mode", None) == "MARKET_NOW"
        and (not markers or _intent_has_any_marker(intent, markers))
    ]
    if not to_remove:
        return False

    for intent in to_remove:
        active.remove(intent)
        suppressed.append(intent)
    return True


def _rule_matches(
    rule: dict[str, Any],
    active: list[ParsedIntent],
    normalized: NormalizedText | None,
) -> bool:
    detected = {intent.type for intent in active}

    when_all = rule.get("when_all_detected")
    if when_all is not None and not set(when_all).issubset(detected):
        return False

    when_any = rule.get("when_any_detected")
    if when_any is not None and not set(when_any).intersection(detected):
        return False

    contains_any = rule.get("if_contains_any")
    if contains_any is not None and not _text_contains_any(normalized, contains_any):
        return False

    unless_contains = rule.get("unless_contains_any")
    if unless_contains is not None and _text_contains_any(normalized, unless_contains):
        return False

    return when_all is not None or when_any is not None or contains_any is not None


def _remove_types(
    active: list[ParsedIntent],
    suppressed: list[ParsedIntent],
    remove_types: set[str],
) -> bool:
    removed = [intent for intent in active if intent.type in remove_types]
    if not removed:
        return False

    for intent in removed:
        active.remove(intent)
        suppressed.append(intent)
    return True


def _select_primary_intent(
    active: list[ParsedIntent],
    rules: ParserRules,
) -> str | None:
    if not active:
        return None

    active_types = [intent.type for intent in active]
    for intent_type in rules.primary_intent_precedence:
        if intent_type in active_types:
            return intent_type

    return active[0].type


def _is_context_market_rule(rule: dict[str, Any]) -> bool:
    return "when_marker_in" in rule and (
        "if_signal_payload_present" in rule or "if_signal_payload_absent" in rule
    )


def _any_marker_present(
    markers: list[str],
    active: list[ParsedIntent],
    normalized: NormalizedText | None,
) -> bool:
    return _text_contains_any(normalized, markers) or any(
        _intent_has_any_marker(intent, markers) for intent in active
    )


def _intent_has_any_marker(intent: ParsedIntent, markers: list[str]) -> bool:
    raw_mode_marker = getattr(intent.entities, "raw_mode_marker", None)
    intent_markers = [evidence.marker for evidence in intent.evidence]
    if intent.raw_fragment is not None:
        intent_markers.append(intent.raw_fragment)
    if raw_mode_marker is not None:
        intent_markers.append(raw_mode_marker)
    return any(
        marker in intent_marker
        for marker in markers
        for intent_marker in intent_markers
    )


def _text_contains_any(normalized: NormalizedText | None, markers: list[str]) -> bool:
    if normalized is None:
        return False
    return any(marker in normalized.normalized_text for marker in markers)


def _rule_action(rule: dict[str, Any]) -> str:
    if "action" in rule:
        return str(rule["action"])
    if "suppress" in rule:
        return "suppress"
    if "prefer" in rule:
        return "prefer"
    return "keep_multi"


def _rule_name(rule: dict[str, Any]) -> str:
    return str(rule.get("name") or "unnamed_disambiguation_rule")


def _format_intent(intent: ParsedIntent) -> str:
    if intent.raw_fragment:
        return f"{intent.type}:{intent.raw_fragment}"
    return intent.type


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
