from __future__ import annotations

import re
from collections.abc import Callable

from src.parser_v2.contracts.entities import (
    CloseFullEntities,
    InfoOnlyEntities,
    ReportResultEntities,
    SlHitEntities,
    TpHitEntities,
)
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent

_TARGET_LEVEL_RE = re.compile(r"target\s+(?P<level>\d+)\s*:", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_REPORT_INTENTS = {"TP_HIT", "SL_HIT", "REPORT_RESULT"}

EntityBuilder = Callable[[MarkerEvidence, NormalizedText], object]


class IntentEntityExtractor:
    def extract(
        self,
        normalized: NormalizedText,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        looks_like_signal = _looks_like_signal(normalized.normalized_text)
        intents: list[ParsedIntent] = []
        for ev in evidence:
            if ev.kind != "intent" or ev.suppressed:
                continue
            if looks_like_signal and ev.name in _REPORT_INTENTS:
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
        return _deduplicate(intents)


def _looks_like_signal(text: str) -> bool:
    required = ("coin:", "direction:", "entry:", "targets:", "stop loss:")
    return all(marker in text for marker in required)


def _tp_hit_entities(ev: MarkerEvidence, normalized: NormalizedText) -> TpHitEntities:
    level_match = _TARGET_LEVEL_RE.search(ev.marker)
    level = int(level_match.group("level")) if level_match else None
    return TpHitEntities(level=level, price=_first_price_after(normalized.raw_text, ev.end))


def _close_full_entities(ev: MarkerEvidence, normalized: NormalizedText) -> CloseFullEntities:
    return CloseFullEntities(close_price=_first_price_after(normalized.raw_text, ev.end))


def _sl_hit_entities(ev: MarkerEvidence, normalized: NormalizedText) -> SlHitEntities:
    return SlHitEntities(price=_first_price_after(normalized.raw_text, ev.end))


def _report_result_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ReportResultEntities:
    return ReportResultEntities(raw_summary=normalized.raw_text.strip() or None)


def _info_only_entities(ev: MarkerEvidence, normalized: NormalizedText) -> InfoOnlyEntities:
    return InfoOnlyEntities(raw_fragment=normalized.raw_text.strip() or None)


_ENTITY_BUILDERS: dict[str, EntityBuilder] = {
    "CLOSE_FULL": _close_full_entities,
    "TP_HIT": _tp_hit_entities,
    "SL_HIT": _sl_hit_entities,
    "REPORT_RESULT": _report_result_entities,
    "INFO_ONLY": _info_only_entities,
}


def _first_price_after(text: str, offset: int) -> object | None:
    match = _NUMBER_RE.search(text, offset)
    if not match:
        return None
    raw = match.group(0)
    compact = raw.replace(",", "")
    try:
        value = float(compact)
    except ValueError:
        return None
    from src.parser_v2.contracts.entities import Price

    return Price(raw=raw, value=value)


def _deduplicate(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    seen: set[tuple[str, int | None, int | None]] = set()
    result: list[ParsedIntent] = []
    for intent in intents:
        key = (intent.type, intent.span_start, intent.span_end)
        if key in seen:
            continue
        seen.add(key)
        result.append(intent)
    return result
