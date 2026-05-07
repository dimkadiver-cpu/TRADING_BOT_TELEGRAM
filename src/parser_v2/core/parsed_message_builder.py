from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, TypeVar

_T = TypeVar("_T")

from src.parser_v2.contracts.context import ParserContext, RawContext, TargetHints
from src.parser_v2.contracts.enums import (
    EvidenceStatus,
    INTENT_CATEGORY_BY_TYPE,
    IntentType,
    STRONG_WEIGHT,
    WEAK_WEIGHT,
)
from src.parser_v2.contracts.markers import MarkerEvidence, MarkerMatch, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage, SignalDraft
from src.parser_v2.core.classification_resolver import ClassificationResolver


class ParsedMessageBuilder:
    def build(
        self,
        *,
        parser_profile: str,
        normalized: NormalizedText,
        context: ParserContext | None = None,
        signal: SignalDraft | None = None,
        intents: list[ParsedIntent] | None = None,
        primary_intent: IntentType | None = None,
        target_hints: TargetHints | None = None,
        matched_markers: list[MarkerMatch] | None = None,
        suppressed_markers: list[MarkerEvidence] | None = None,
        applied_marker_rules: list[str] | None = None,
        applied_disambiguation_rules: list[str] | None = None,
        warnings: list[str] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> ParsedMessage:
        final_intents = _assign_occurrence_ids(intents or [])
        classification = ClassificationResolver().resolve(
            signal=signal,
            intents=final_intents,
            target_hints=target_hints,
        )
        category_scores = _category_scores(final_intents)
        confidence = _confidence(
            signal=signal,
            category_scores=category_scores,
        )
        merged_warnings = _dedup(
            [*classification.warnings, *(warnings or [])]
        )

        return ParsedMessage(
            parser_profile=parser_profile,
            primary_class=classification.primary_class,
            parse_status=classification.parse_status,
            confidence=confidence,
            signal=signal,
            intents=final_intents,
            primary_intent=primary_intent,
            evidence_status=_evidence_status(
                final_intents,
                confidence=confidence,
                primary_intent=primary_intent,
            ),
            target_hints=target_hints,
            warnings=merged_warnings,
            diagnostics=_diagnostics(
                base=diagnostics,
                matched_markers=matched_markers or [],
                suppressed_markers=suppressed_markers or [],
                applied_marker_rules=applied_marker_rules or [],
                applied_disambiguation_rules=applied_disambiguation_rules or [],
                signal=signal,
                category_scores=category_scores,
            ),
            raw_context=_raw_context(normalized, context, target_hints),
        )


def _confidence(
    *,
    signal: SignalDraft | None,
    category_scores: dict[str, float],
) -> float:
    if signal is not None:
        signal_score = 1.0 if signal.completeness == "COMPLETE" else 0.6
        return max(signal_score, _max_score(category_scores))

    return _max_score(category_scores)


def _max_score(category_scores: dict[str, float]) -> float:
    if not category_scores:
        return 0.0
    return min(1.0, max(category_scores.values()))


def _category_scores(intents: list[ParsedIntent]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for intent in intents:
        category = INTENT_CATEGORY_BY_TYPE.get(intent.type, intent.category)
        score = _intent_evidence_score(intent)
        scores[category] = min(1.0, scores.get(category, 0.0) + score)
    return scores


def _intent_evidence_score(intent: ParsedIntent) -> float:
    if not intent.evidence:
        return intent.confidence

    score = 0.0
    for evidence in intent.evidence:
        if evidence.strength == "strong":
            score += STRONG_WEIGHT
        else:
            score += WEAK_WEIGHT
    return score


def _evidence_status(
    intents: list[ParsedIntent],
    *,
    confidence: float,
    primary_intent: IntentType | None,
) -> EvidenceStatus:
    if confidence < 0.5:
        return "LOW_CONFIDENCE"
    if intents and all(_intent_is_weak_only(intent) for intent in intents):
        return "LOW_CONFIDENCE"
    if len(intents) > 1 and primary_intent is None:
        return "AMBIGUOUS"
    return "RESOLVED"


def _intent_is_weak_only(intent: ParsedIntent) -> bool:
    return bool(intent.evidence) and all(
        evidence.strength == "weak" for evidence in intent.evidence
    )


def _diagnostics(
    *,
    base: Mapping[str, Any] | None,
    matched_markers: list[MarkerMatch],
    suppressed_markers: list[MarkerEvidence],
    applied_marker_rules: list[str],
    applied_disambiguation_rules: list[str],
    signal: SignalDraft | None,
    category_scores: dict[str, float],
) -> dict[str, Any]:
    result: dict[str, Any] = dict(base or {})
    result["matched_markers"] = _format_markers(matched_markers)
    result["suppressed_markers"] = _format_markers(suppressed_markers)
    result["applied_marker_rules"] = list(applied_marker_rules)
    result["applied_disambiguation_rules"] = list(applied_disambiguation_rules)
    result["applied_rules"] = _dedup(
        [*applied_marker_rules, *applied_disambiguation_rules]
    )
    result["category_scores"] = dict(category_scores)

    if signal is not None:
        result["signal_missing_fields"] = list(signal.missing_fields)
        result["signal_entry_count"] = len(signal.entries)
        result["signal_tp_count"] = len(signal.take_profits)

    return result


def _raw_context(
    normalized: NormalizedText,
    context: ParserContext | None,
    target_hints: TargetHints | None,
) -> RawContext:
    if context is not None and context.raw_context is not None:
        raw = context.raw_context.model_copy(deep=True)
        if raw.normalized_text is None:
            raw.normalized_text = normalized.normalized_text
        if not raw.extracted_links and target_hints is not None:
            raw.extracted_links = list(target_hints.telegram_links)
        return raw

    return RawContext(
        raw_text=normalized.raw_text,
        normalized_text=normalized.normalized_text,
        message_id=None if context is None else context.message_id,
        reply_to_message_id=None if context is None else context.reply_to_message_id,
        source_chat_id=None if context is None else context.source_chat_id,
        source_topic_id=None if context is None else context.source_topic_id,
        extracted_links=[] if target_hints is None else list(target_hints.telegram_links),
    )


def _format_markers(
    markers: Iterable[MarkerMatch] | Iterable[MarkerEvidence],
) -> list[str]:
    return [
        f"{marker.name}/{marker.strength}:{marker.marker}@{marker.start}:{marker.end}"
        for marker in markers
    ]


def _assign_occurrence_ids(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    counters: dict[str, int] = {}
    result: list[ParsedIntent] = []
    for intent in intents:
        idx = counters.get(intent.type, 0)
        counters[intent.type] = idx + 1
        result.append(intent.model_copy(update={
            "occurrence_index": idx,
            "intent_id": f"{intent.type}#{idx}",
        }))
    return result


def _dedup(values: Iterable[_T]) -> list[_T]:
    seen: set[T] = set()
    result: list[T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
