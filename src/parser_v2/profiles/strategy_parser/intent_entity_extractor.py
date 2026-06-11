from __future__ import annotations

import re

from src.parser_v2.contracts.entities import (
    CloseFullEntities,
    InfoOnlyEntities,
    Price,
    ReportResultEntities,
    SlHitEntities,
    TpHitEntities,
)
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent


_NUMBER_PATTERN = r"\d(?:[\d.,]*\d)?"
_PRICE_RE = re.compile(_NUMBER_PATTERN)

# "→ выход 0.16289" — exit price in close messages
_EXIT_PRICE_RE = re.compile(rf"→\s*выход\s+(?P<value>{_NUMBER_PATTERN})", re.IGNORECASE)

# "−1.0R" or "-0.7R" — result in R multiples
_RESULT_R_RE = re.compile(r"[−\-]?\s*\d+(?:[.,]\d+)?\s*R\b", re.IGNORECASE)


class IntentEntityExtractor:
    def extract(
        self,
        normalized: NormalizedText,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        has_strong_info = any(
            ev.kind == "info" and ev.strength == "strong" and not ev.suppressed
            for ev in evidence
        )
        intents: list[ParsedIntent] = []
        for ev in evidence:
            if ev.kind != "intent" or ev.suppressed:
                continue
            if has_strong_info and ev.strength == "weak":
                continue

            confidence = STRONG_WEIGHT if ev.strength == "strong" else WEAK_WEIGHT
            entities = _build_entities(ev, normalized)
            if entities is None:
                continue

            intents.append(
                ParsedIntent(
                    type=ev.name,
                    category=INTENT_CATEGORY_BY_TYPE[ev.name],
                    confidence=confidence,
                    entities=entities,
                    evidence=[ev],
                    raw_fragment=ev.marker,
                    span_start=ev.start,
                    span_end=ev.end,
                    line_index=normalized.normalized_text[: ev.start].count("\n"),
                )
            )

        return _deduplicate_by_span(intents)


def _build_entities(ev: MarkerEvidence, normalized: NormalizedText) -> object | None:
    text = normalized.normalized_text
    name = ev.name

    if name == "TP_HIT":
        return TpHitEntities(level=None, price=_exit_price(text))
    if name == "SL_HIT":
        return SlHitEntities(price=_exit_price(text))
    if name == "CLOSE_FULL":
        return CloseFullEntities(close_price=_exit_price(text))
    if name == "REPORT_RESULT":
        m = _RESULT_R_RE.search(text)
        return ReportResultEntities(raw_summary=m.group(0).strip() if m else normalized.raw_text.strip() or None)
    if name == "INFO_ONLY":
        return InfoOnlyEntities(raw_fragment=normalized.raw_text.strip() or None)

    return None


def _exit_price(text: str) -> Price | None:
    m = _EXIT_PRICE_RE.search(text)
    if not m:
        return None
    return _price_from_raw(m.group("value"))


def _price_from_raw(raw: str | None) -> Price | None:
    value = _float_from_raw(raw)
    if raw is None or value is None:
        return None
    return Price(raw=raw.strip(), value=value)


def _float_from_raw(raw: str | None) -> float | None:
    if not raw:
        return None
    compact = raw.strip().replace(" ", "")
    if not compact:
        return None
    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif "," in compact:
        compact = compact.replace(",", ".")
    try:
        return float(compact)
    except ValueError:
        return None


def _deduplicate_by_span(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    if len(intents) <= 1:
        return intents

    ordered = sorted(intents, key=lambda i: (i.confidence, i.span_end - i.span_start), reverse=True)
    kept: list[ParsedIntent] = []
    for candidate in ordered:
        c0, c1 = candidate.span_start, candidate.span_end
        drop = False
        for keeper in kept:
            k0, k1 = keeper.span_start, keeper.span_end
            contained = k0 <= c0 and c1 <= k1
            same_type_overlap = keeper.type == candidate.type and not (c1 <= k0 or c0 >= k1)
            if contained or same_type_overlap:
                drop = True
                break
        if not drop:
            kept.append(candidate)

    kept.sort(key=lambda i: i.span_start)
    return kept
