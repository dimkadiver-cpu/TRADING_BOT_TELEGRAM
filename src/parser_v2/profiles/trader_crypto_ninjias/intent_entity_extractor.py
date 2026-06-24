from __future__ import annotations

import re

from src.parser_v2.contracts.entities import (
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    ExitBeEntities,
    InfoOnlyEntities,
    MoveStopToBEEntities,
    ReportResultEntities,
    SlHitEntities,
    TpHitEntities,
)
from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent

_MANUAL_TP_LEVEL_RE = re.compile(r"\bhit\s+tp(?P<level>\d+)\b", re.IGNORECASE)
_AUTO_TP_LEVEL_RE = re.compile(r"take-profit\s+target\s+(?P<level>\d+)", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?P<pct>\d+(?:[.,]\d+)?)\s*%")


class IntentEntityExtractor:
    def extract(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        looks_like_signal = _looks_like_signal(text.normalized_text)
        intents: list[ParsedIntent] = []

        for ev in evidence:
            if ev.kind != "intent" or ev.suppressed:
                continue
            if looks_like_signal and ev.name in {"TP_HIT", "SL_HIT", "EXIT_BE", "REPORT_RESULT"}:
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
                    entities=builder(ev, text),
                    evidence=[ev],
                    raw_fragment=ev.marker,
                    span_start=ev.start,
                    span_end=ev.end,
                    line_index=text.normalized_text[: ev.start].count("\n"),
                )
            )

        return _deduplicate(intents)


def _looks_like_signal(text: str) -> bool:
    return (
        ("long -" in text or "short -" in text or " long " in text or " short " in text)
        and "tp1" in text
        and ("entry market" in text or "entry limit" in text or " entry " in text)
    )


def _tp_hit_entities(ev: MarkerEvidence, text: NormalizedText) -> TpHitEntities:
    level_match = _MANUAL_TP_LEVEL_RE.search(text.raw_text) or _AUTO_TP_LEVEL_RE.search(text.raw_text)
    level = int(level_match.group("level")) if level_match else None
    return TpHitEntities(level=level, price=None)


def _sl_hit_entities(ev: MarkerEvidence, text: NormalizedText) -> SlHitEntities:
    return SlHitEntities(price=None)


def _exit_be_entities(ev: MarkerEvidence, text: NormalizedText) -> ExitBeEntities:
    return ExitBeEntities(price=None)


def _close_full_entities(ev: MarkerEvidence, text: NormalizedText) -> CloseFullEntities:
    return CloseFullEntities(close_price=None)


def _close_partial_entities(ev: MarkerEvidence, text: NormalizedText) -> ClosePartialEntities:
    fraction = None
    match = _PERCENT_RE.search(ev.marker)
    if match is not None:
        raw_value = match.group("pct").replace(",", ".")
        fraction = float(raw_value) / 100.0
    return ClosePartialEntities(fraction=fraction, close_price=None)


def _move_stop_to_be_entities(ev: MarkerEvidence, text: NormalizedText) -> MoveStopToBEEntities:
    return MoveStopToBEEntities()


def _cancel_pending_entities(ev: MarkerEvidence, text: NormalizedText) -> CancelPendingEntities:
    normalized = text.normalized_text
    if any(token in normalized for token in ("entry limit", "limit", "pending")):
        return CancelPendingEntities(cancel_scope_hint="ALL_PENDING")
    return CancelPendingEntities()


def _report_result_entities(ev: MarkerEvidence, text: NormalizedText) -> ReportResultEntities:
    return ReportResultEntities(raw_summary=text.raw_text.strip() or None)


def _info_only_entities(ev: MarkerEvidence, text: NormalizedText) -> InfoOnlyEntities:
    return InfoOnlyEntities(raw_fragment=text.raw_text.strip() or None)


_ENTITY_BUILDERS = {
    "MOVE_STOP_TO_BE": _move_stop_to_be_entities,
    "CLOSE_PARTIAL": _close_partial_entities,
    "CANCEL_PENDING": _cancel_pending_entities,
    "TP_HIT": _tp_hit_entities,
    "SL_HIT": _sl_hit_entities,
    "EXIT_BE": _exit_be_entities,
    "CLOSE_FULL": _close_full_entities,
    "REPORT_RESULT": _report_result_entities,
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
