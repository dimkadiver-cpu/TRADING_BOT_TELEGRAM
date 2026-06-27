from __future__ import annotations

import re

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.contracts.entities import SlHitEntities, TpHitEntities
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent

_TARGET_DONE_RE = re.compile(
    r"\b(?P<label>first|one|two|three|all)\s+targets?\s+done\b",
    re.IGNORECASE,
)
_STRUCTURED_REPORT_TP_RE = re.compile(
    r"\b(?P<label>one|two|three|all)\s+targets?\s+done\b",
    re.IGNORECASE,
)
_STOP_LOSS_REPORT_RE = re.compile(r"reaching\s+stop-loss", re.IGNORECASE)

_LEVEL_BY_LABEL = {
    "first": 1,
    "one": 1,
    "two": 2,
    "three": 3,
}


class IntentEntityExtractor:
    def extract(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        intents: list[ParsedIntent] = []
        for ev in evidence:
            if ev.kind != "intent" or ev.suppressed:
                continue
            if ev.name == "TP_HIT":
                intents.append(_build_tp_hit(ev, text))
            elif ev.name == "SL_HIT":
                intents.append(_build_sl_hit(ev, text))
        return _deduplicate(intents)


def _build_tp_hit(ev: MarkerEvidence, text: NormalizedText) -> ParsedIntent:
    match = _TARGET_DONE_RE.search(text.raw_text) or _STRUCTURED_REPORT_TP_RE.search(text.raw_text)
    level = None
    if match is not None:
        level = _LEVEL_BY_LABEL.get(match.group("label").lower())
    confidence = STRONG_WEIGHT if ev.strength == "strong" else WEAK_WEIGHT
    return ParsedIntent(
        type="TP_HIT",
        category=INTENT_CATEGORY_BY_TYPE["TP_HIT"],
        confidence=confidence,
        entities=TpHitEntities(level=level, price=None),
        evidence=[ev],
        raw_fragment=match.group(0) if match is not None else ev.marker,
        span_start=ev.start,
        span_end=ev.end,
        line_index=text.normalized_text[: ev.start].count("\n"),
    )


def _build_sl_hit(ev: MarkerEvidence, text: NormalizedText) -> ParsedIntent:
    confidence = STRONG_WEIGHT if ev.strength == "strong" else WEAK_WEIGHT
    match = _STOP_LOSS_REPORT_RE.search(text.raw_text)
    return ParsedIntent(
        type="SL_HIT",
        category=INTENT_CATEGORY_BY_TYPE["SL_HIT"],
        confidence=confidence,
        entities=SlHitEntities(price=None),
        evidence=[ev],
        raw_fragment=match.group(0) if match is not None else ev.marker,
        span_start=ev.start,
        span_end=ev.end,
        line_index=text.normalized_text[: ev.start].count("\n"),
    )


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

