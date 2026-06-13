from __future__ import annotations

import re
from collections.abc import Callable

from src.parser_v2.contracts.entities import InfoOnlyEntities, ReportResultEntities, SlHitEntities, TpHitEntities
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.core.parsing_utils import deduplicate_by_span as _deduplicate_by_span

_DONE_TARGETS_RE = re.compile(r"targets?\s+(?P<body>[\d,\s]+)\s+done", re.IGNORECASE)
_DONE_NUMBER_RE = re.compile(r"\d+")

EntityBuilder = Callable[[MarkerEvidence, NormalizedText], object]


class IntentEntityExtractor:
    def extract(
        self,
        normalized: NormalizedText,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        if _looks_like_signal(normalized.normalized_text):
            return []

        intents: list[ParsedIntent] = []
        for ev in evidence:
            if ev.kind != "intent" or ev.suppressed:
                continue
            builder = _ENTITY_BUILDERS.get(ev.name)
            if builder is None:
                continue
            confidence = STRONG_WEIGHT if ev.strength == "strong" else WEAK_WEIGHT
            intents.append(
                ParsedIntent(
                    type=ev.name,
                    category=INTENT_CATEGORY_BY_TYPE[ev.name],
                    confidence=confidence,
                    entities=builder(ev, normalized),
                    evidence=[ev],
                    raw_fragment=ev.marker,
                    span_start=ev.start,
                    span_end=ev.end,
                    line_index=normalized.normalized_text[:ev.start].count("\n"),
                )
            )
        return _deduplicate_by_span(intents)


def _looks_like_signal(text: str) -> bool:
    return (
        "coin" in text
        and "entry:" in text
        and "stoploss:" in text
        and "target 1:" in text
    )


def _tp_hit_entities(ev: MarkerEvidence, normalized: NormalizedText) -> TpHitEntities:
    match = _DONE_TARGETS_RE.search(normalized.raw_text)
    if match is None:
        return TpHitEntities()
    levels = [int(value) for value in _DONE_NUMBER_RE.findall(match.group("body"))]
    return TpHitEntities(level=max(levels) if levels else None)


def _report_result_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ReportResultEntities:
    return ReportResultEntities(raw_summary=normalized.raw_text.strip() or None)


def _info_only_entities(ev: MarkerEvidence, normalized: NormalizedText) -> InfoOnlyEntities:
    return InfoOnlyEntities(raw_fragment=normalized.raw_text.strip() or None)


_ENTITY_BUILDERS: dict[str, EntityBuilder] = {
    "TP_HIT": _tp_hit_entities,
    "SL_HIT": lambda ev, normalized: SlHitEntities(),
    "REPORT_RESULT": _report_result_entities,
    "INFO_ONLY": _info_only_entities,
}
