from __future__ import annotations

from dataclasses import dataclass

from src.parser_v2.contracts.markers import MarkerEvidence, MarkerMatch
from src.parser_v2.contracts.rules import ParserRules


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
    ) -> MarkerEvidenceResolution:
        suppressed: dict[int, MarkerEvidence] = {}
        applied_rules: list[str] = []

        marker_resolution = rules.marker_resolution

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

        return MarkerEvidenceResolution(
            evidence=evidence,
            suppressed_markers=suppressed_markers,
            diagnostics={
                "suppressed_markers": [
                    _format_marker(marker) for marker in suppressed_markers
                ],
                "applied_marker_rules": applied_rules,
            },
        )


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
