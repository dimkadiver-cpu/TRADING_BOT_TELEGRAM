from __future__ import annotations

import re

from src.parser_v2.contracts.entities import (
    CancelPendingEntities,
    CloseFullEntities,
    EntryFilledEntities,
    ExitBeEntities,
    InfoOnlyEntities,
    Price,
    SlHitEntities,
    TpHitEntities,
)
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent

# ── reply-message patterns ────────────────────────────────────────────────────
# "#SYMBOL/USDT Take-Profit target N ✅"
_TP_HIT_RE = re.compile(
    r"take-profit\s+target\s+(?P<level>\d+)",
    re.IGNORECASE,
)
# "#SYMBOL/USDT Entry N ✅\nAverage Entry Price: X"
_ENTRY_FILLED_RE = re.compile(
    r"entry\s+(?P<level>\d+)\s*✅",
    re.IGNORECASE,
)
_AVG_PRICE_RE = re.compile(
    r"average\s+entry\s+price\s*:\s*(?P<price>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
# Profit / Loss percentage lines
_PCT_LINE_RE = re.compile(
    r"(?:profit|loss)\s*:\s*(?P<pct>-?\d[\d,]*(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

_SIGNAL_ANCHORS = ("direction: long", "direction: short", "entry targets:", "take profits:")


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
            if looks_like_signal and ev.name in {"TP_HIT", "SL_HIT", "EXIT_BE", "CLOSE_FULL", "CANCEL_PENDING", "ENTRY_FILLED"}:
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
                    line_index=normalized.normalized_text[: ev.start].count("\n"),
                )
            )
        return _deduplicate(intents)


def _looks_like_signal(text: str) -> bool:
    return any(anchor in text for anchor in _SIGNAL_ANCHORS)


def _tp_hit_entities(ev: MarkerEvidence, normalized: NormalizedText) -> TpHitEntities:
    m = _TP_HIT_RE.search(normalized.raw_text)
    level = int(m.group("level")) if m else None
    return TpHitEntities(level=level, price=None)


def _sl_hit_entities(ev: MarkerEvidence, normalized: NormalizedText) -> SlHitEntities:
    return SlHitEntities(price=None)


def _exit_be_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ExitBeEntities:
    return ExitBeEntities(price=None)


def _close_full_entities(ev: MarkerEvidence, normalized: NormalizedText) -> CloseFullEntities:
    return CloseFullEntities(close_price=None)


def _cancel_pending_entities(ev: MarkerEvidence, normalized: NormalizedText) -> CancelPendingEntities:
    return CancelPendingEntities(cancel_scope_hint="UNKNOWN")


def _entry_filled_entities(ev: MarkerEvidence, normalized: NormalizedText) -> EntryFilledEntities:
    level: int | None = None
    fill_price: Price | None = None

    m_level = _ENTRY_FILLED_RE.search(normalized.raw_text)
    if m_level:
        level = int(m_level.group("level"))

    m_price = _AVG_PRICE_RE.search(normalized.raw_text)
    if m_price:
        raw = m_price.group("price")
        try:
            fill_price = Price(raw=raw, value=float(raw.replace(",", "")))
        except ValueError:
            pass

    return EntryFilledEntities(level=level, fill_price=fill_price)


def _info_only_entities(ev: MarkerEvidence, normalized: NormalizedText) -> InfoOnlyEntities:
    return InfoOnlyEntities(raw_fragment=normalized.raw_text.strip() or None)


_ENTITY_BUILDERS = {
    "TP_HIT": _tp_hit_entities,
    "SL_HIT": _sl_hit_entities,
    "EXIT_BE": _exit_be_entities,
    "CLOSE_FULL": _close_full_entities,
    "CANCEL_PENDING": _cancel_pending_entities,
    "ENTRY_FILLED": _entry_filled_entities,
    "INFO_ONLY": _info_only_entities,
}


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
