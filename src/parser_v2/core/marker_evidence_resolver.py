from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.parser_v2.contracts.markers import MarkerEvidence, MarkerMatch
from src.parser_v2.contracts.rules import ParserRules, WeakContextExclusionRule
from src.parser_v2.contracts.rules import SemanticMarkers


@dataclass(frozen=True)
class MarkerEvidenceResolution:
    evidence: list[MarkerEvidence]
    suppressed_markers: list[MarkerEvidence]
    diagnostics: dict[str, list[str]]


class MarkerEvidenceResolver:
    def resolve(
        self,
        matches: list[MarkerMatch],
        rules: ParserRules,
        *,
        raw_text: str | None = None,
        semantic_markers: SemanticMarkers | None = None,
    ) -> MarkerEvidenceResolution:
        suppressed: dict[int, MarkerEvidence] = {}
        applied_rules: list[str] = []
        diagnostics_extra: dict[str, list[str]] = {}

        marker_resolution = rules.marker_resolution

        # 1. suppress_weak_inside_strong_same_intent
        if marker_resolution.suppress_weak_inside_strong_same_intent:
            for weak_index, weak_match in enumerate(matches):
                if weak_match.kind != "intent" or weak_match.strength != "weak":
                    continue
                for strong_match in _iter_strong_intents(matches):
                    if (
                        weak_match.name == strong_match.name
                        and _contains(strong_match, weak_match)
                    ):
                        suppressed[weak_index] = _suppressed_evidence(
                            weak_match,
                            suppressed_by=strong_match.name,
                            reason="weak_inside_strong_same_intent",
                        )
                        _append_once(applied_rules, "weak_inside_strong_same_intent")
                        break

        # 2. weak_context_exclusions
        if marker_resolution.weak_context_exclusions:
            if raw_text is None:
                diagnostics_extra["weak_context_exclusions_skipped_no_text"] = [
                    r.name for r in marker_resolution.weak_context_exclusions
                ]
            else:
                for weak_index, weak_match in enumerate(matches):
                    if weak_index in suppressed:
                        continue
                    if weak_match.kind != "intent" or weak_match.strength != "weak":
                        continue
                    for rule in marker_resolution.weak_context_exclusions:
                        if rule.intent != weak_match.name:
                            continue
                        if not _rule_markers_match(rule, weak_match, semantic_markers):
                            continue
                        context_text = _extract_context(raw_text, weak_match.start, rule)
                        if _should_suppress_by_context(rule, context_text):
                            suppressed[weak_index] = _suppressed_evidence(
                                weak_match,
                                suppressed_by=rule.name,
                                reason=rule.reason or "weak_context_exclusion",
                            )
                            _append_once(applied_rules, rule.name)
                            break

        # 3. cross_intent_suppression
        for rule in marker_resolution.cross_intent_suppression:
            triggering_strong_matches = [
                match
                for match in _iter_strong_intents(matches)
                if match.name == rule.if_strong
            ]
            if not triggering_strong_matches:
                continue

            for weak_index, weak_match in enumerate(matches):
                if (
                    weak_index in suppressed
                    or weak_match.kind != "intent"
                    or weak_match.strength != "weak"
                    or weak_match.name not in rule.suppress_weak
                ):
                    continue

                for strong_match in triggering_strong_matches:
                    if _contains(strong_match, weak_match):
                        reason = rule.reason or "cross_intent_suppression"
                        suppressed[weak_index] = _suppressed_evidence(
                            weak_match,
                            suppressed_by=strong_match.name,
                            reason=reason,
                        )
                        _append_once(applied_rules, reason)
                        break

        evidence = [
            _clean_evidence(match)
            for index, match in enumerate(matches)
            if index not in suppressed
        ]
        suppressed_markers = [
            suppressed[index]
            for index in range(len(matches))
            if index in suppressed
        ]

        diagnostics: dict[str, list[str]] = {
            "suppressed_markers": [
                _format_marker(marker) for marker in suppressed_markers
            ],
            "applied_marker_rules": applied_rules,
            **diagnostics_extra,
        }

        return MarkerEvidenceResolution(
            evidence=evidence,
            suppressed_markers=suppressed_markers,
            diagnostics=diagnostics,
        )


def _rule_markers_match(
    rule: WeakContextExclusionRule,
    match: MarkerMatch,
    semantic_markers: SemanticMarkers | None,
) -> bool:
    markers = rule.markers
    if isinstance(markers, dict) and markers.get("source") == "intent_weak":
        if semantic_markers is None:
            return True  # fallback: apply the rule
        intent_marker_set = semantic_markers.intent_markers.get(match.name)
        if intent_marker_set is None:
            return False
        return match.marker in intent_marker_set.weak
    return match.marker in markers


def _extract_context(text: str, marker_start: int, rule: WeakContextExclusionRule) -> str:
    scope = rule.scope
    if scope == "whole_message":
        return text
    if scope == "same_line":
        line_start = text.rfind("\n", 0, marker_start)
        line_start = 0 if line_start == -1 else line_start + 1
        line_end = text.find("\n", marker_start)
        line_end = len(text) if line_end == -1 else line_end
        return text[line_start:line_end]
    if scope == "same_sentence":
        sentence_start = max(
            text.rfind(".", 0, marker_start),
            text.rfind("!", 0, marker_start),
            text.rfind("?", 0, marker_start),
            text.rfind("\n", 0, marker_start),
        )
        sentence_start = 0 if sentence_start == -1 else sentence_start + 1
        sentence_end_candidates = [
            pos for pos in [
                text.find(".", marker_start),
                text.find("!", marker_start),
                text.find("?", marker_start),
                text.find("\n", marker_start),
            ]
            if pos != -1
        ]
        sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(text)
        return text[sentence_start:sentence_end]
    if scope == "window":
        chars = rule.window_chars or 50
        return text[max(0, marker_start - chars): marker_start + chars]
    return text


def _should_suppress_by_context(rule: WeakContextExclusionRule, context: str) -> bool:
    condition_met = False
    if rule.if_contains_any:
        condition_met = any(phrase in context for phrase in rule.if_contains_any)
    if not condition_met and rule.if_regex_any:
        condition_met = any(re.search(pattern, context) for pattern in rule.if_regex_any)
    if not condition_met:
        return False
    if rule.unless_contains_any:
        if any(phrase in context for phrase in rule.unless_contains_any):
            return False
    return True


def _iter_strong_intents(matches: list[MarkerMatch]) -> list[MarkerMatch]:
    return [
        match
        for match in matches
        if match.kind == "intent" and match.strength == "strong"
    ]


def _contains(container: MarkerMatch, contained: MarkerMatch) -> bool:
    return container.start <= contained.start and contained.end <= container.end


def _clean_evidence(match: MarkerMatch) -> MarkerEvidence:
    return MarkerEvidence(
        name=match.name,
        kind=match.kind,
        strength=match.strength,
        marker=match.marker,
        start=match.start,
        end=match.end,
    )


def _suppressed_evidence(
    match: MarkerMatch,
    *,
    suppressed_by: str,
    reason: str,
) -> MarkerEvidence:
    return MarkerEvidence(
        name=match.name,
        kind=match.kind,
        strength=match.strength,
        marker=match.marker,
        start=match.start,
        end=match.end,
        suppressed=True,
        suppressed_by=suppressed_by,
        reason=reason,
    )


def _format_marker(marker: MarkerEvidence) -> str:
    return f"{marker.name}/{marker.strength}:{marker.marker}@{marker.start}:{marker.end}"


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
