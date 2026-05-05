from __future__ import annotations

import re
from collections.abc import Callable

from src.parser_v2.contracts.entities import (
    AddEntryEntities,
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    EntryLeg,
    ExitBeEntities,
    InfoOnlyEntities,
    InvalidateSetupEntities,
    ModifyEntryEntities,
    ModifyTargetsEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    Price,
    ReenterEntities,
    ReportResultEntities,
    SlHitEntities,
    TpHitEntities,
)
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent


_NUMBER_PATTERN = r"\d(?:[\d.,]*\d)?"
_PRICE_RE = re.compile(_NUMBER_PATTERN)

# Mini-regex used only for entity extraction from the matched marker text
# Handles "tpN"/"тпN", "N тейк", and ordinals "первый"/"второй"/"третий"
_RE_TP_LEVEL = re.compile(
    r"(?:tp|тп)\s*(?P<n1>[123])"
    r"|(?P<n2>[123])\s*тейк"
    r"|(?P<ord>перв|втор|треть)",
    re.IGNORECASE,
)
_RE_PCT = re.compile(r"(?P<pct>\d+(?:[.,]\d+)?)\s*%")
_RE_HALF = re.compile(r"\bhalf\b|половин", re.IGNORECASE)
_RE_TP1 = re.compile(r"первый|тп\s*1|tp\s*1|1\s*тейк", re.IGNORECASE)
_RE_MARKET_NOW = re.compile(r"рынк|market", re.IGNORECASE)
_RE_REMOVE = re.compile(r"убира|remove|delete", re.IGNORECASE)

_TP_ORDINAL_MAP = {"перв": 1, "втор": 2, "треть": 3}

EntityBuilder = Callable[[MarkerEvidence, NormalizedText], object]


class IntentEntityExtractor:
    """Extracts typed entities for each intent already detected by MarkerMatcher.

    Intent detection (which IntentType is present) is driven by semantic_markers.json
    via MarkerMatcher → MarkerEvidenceResolver. This class only handles the
    entity-extraction step: given a detected intent marker at a known position,
    parse prices, levels, and percentages from the surrounding text.
    """

    def extract(
        self,
        normalized: NormalizedText,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        # A strong INFO marker (e.g. admin message) suppresses all weak intent markers:
        # admin/schedule/greeting messages don't carry trading intents.
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
                )
            )
        return _deduplicate_by_span(intents)


# ---------------------------------------------------------------------------
# Entity builders — one per IntentType
# Each receives the MarkerEvidence (which carries the matched marker text and
# its span) and the full NormalizedText. They must NOT re-detect the intent;
# they only extract numeric/textual entities from the surrounding context.
# ---------------------------------------------------------------------------


def _move_stop_entities(ev: MarkerEvidence, normalized: NormalizedText) -> MoveStopEntities:
    m = _RE_TP_LEVEL.search(ev.marker)
    if m:
        level = _tp_level_from_match(m)
        if level is not None:
            return MoveStopEntities(stop_to_tp_level=level)
    price = _first_price_after(normalized.normalized_text, ev.end)
    return MoveStopEntities(new_stop_price=price)


def _tp_level_from_match(m: re.Match[str]) -> int | None:
    if m.group("n1"):
        return int(m.group("n1"))
    if m.group("n2"):
        return int(m.group("n2"))
    if m.group("ord"):
        prefix = m.group("ord").lower()
        for k, v in _TP_ORDINAL_MAP.items():
            if prefix.startswith(k):
                return v
    return None


def _close_full_entities(ev: MarkerEvidence, normalized: NormalizedText) -> CloseFullEntities:
    return CloseFullEntities(close_price=_first_price_after(normalized.normalized_text, ev.end))


def _close_partial_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ClosePartialEntities:
    fraction = None
    m_pct = _RE_PCT.search(ev.marker)
    if m_pct:
        value = _float_from_raw(m_pct.group("pct"))
        if value is not None:
            fraction = value / 100.0
    elif _RE_HALF.search(ev.marker):
        fraction = 0.5
    return ClosePartialEntities(
        fraction=fraction,
        close_price=_first_price_after(normalized.normalized_text, ev.end),
    )


def _cancel_pending_entities(ev: MarkerEvidence, normalized: NormalizedText) -> CancelPendingEntities:
    text = normalized.normalized_text
    if any(kw in text for kw in ("limit", "pending", "лимит")):
        return CancelPendingEntities(cancel_scope_hint="ALL_PENDING")
    return CancelPendingEntities()


def _reenter_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ReenterEntities:
    prices = _prices_after(normalized.normalized_text, ev.end)
    return ReenterEntities(
        entries=prices,
        entry_type="LIMIT" if prices else None,
        entry_structure="ONE_SHOT" if len(prices) == 1 else "LADDER" if len(prices) > 1 else None,
    )


def _add_entry_entities(ev: MarkerEvidence, normalized: NormalizedText) -> AddEntryEntities:
    price = _first_price_after(normalized.normalized_text, ev.end)
    return AddEntryEntities(entry_price=price, entry_type="LIMIT" if price is not None else None)


def _modify_entry_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ModifyEntryEntities:
    entries: list[EntryLeg] = []
    if _RE_MARKET_NOW.search(ev.marker):
        mode = "MARKET_NOW"
        entries.append(EntryLeg(sequence=1, entry_type="MARKET", role="PRIMARY"))
    elif _RE_REMOVE.search(ev.marker):
        mode = "REMOVE"
    else:
        mode = "UPDATE_PRICE"
        price = _first_price_after(normalized.normalized_text, ev.end)
        if price is not None:
            entries.append(EntryLeg(sequence=1, entry_type="LIMIT", price=price, role="PRIMARY"))
    return ModifyEntryEntities(mode=mode, entries=entries, raw_mode_marker=ev.marker)


def _modify_targets_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ModifyTargetsEntities:
    return ModifyTargetsEntities(
        take_profits=_prices_after(normalized.normalized_text, ev.end),
        mode="UNKNOWN",
    )


def _entry_filled_entities(ev: MarkerEvidence, normalized: NormalizedText) -> EntryFilledEntities:
    return EntryFilledEntities(fill_price=_first_price_after(normalized.normalized_text, ev.end))


def _tp_hit_entities(ev: MarkerEvidence, normalized: NormalizedText) -> TpHitEntities:
    level = 1 if _RE_TP1.search(ev.marker) else None
    return TpHitEntities(
        level=level,
        price=_first_price_after(normalized.normalized_text, ev.end),
    )


def _sl_hit_entities(ev: MarkerEvidence, normalized: NormalizedText) -> SlHitEntities:
    return SlHitEntities(price=_first_price_after(normalized.normalized_text, ev.end))


def _raw_summary_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ReportResultEntities:
    return ReportResultEntities(raw_summary=normalized.raw_text.strip() or None)


def _info_only_entities(ev: MarkerEvidence, normalized: NormalizedText) -> InfoOnlyEntities:
    return InfoOnlyEntities(raw_fragment=normalized.raw_text.strip() or None)


_ENTITY_BUILDERS: dict[str, EntityBuilder] = {
    "MOVE_STOP_TO_BE": lambda ev, n: MoveStopToBEEntities(),
    "MOVE_STOP": _move_stop_entities,
    "CLOSE_FULL": _close_full_entities,
    "CLOSE_PARTIAL": _close_partial_entities,
    "CANCEL_PENDING": _cancel_pending_entities,
    "INVALIDATE_SETUP": lambda ev, n: InvalidateSetupEntities(reason_text=n.raw_text.strip() or None),
    "REENTER": _reenter_entities,
    "ADD_ENTRY": _add_entry_entities,
    "MODIFY_ENTRY": _modify_entry_entities,
    "MODIFY_TARGETS": _modify_targets_entities,
    "ENTRY_FILLED": _entry_filled_entities,
    "TP_HIT": _tp_hit_entities,
    "SL_HIT": _sl_hit_entities,
    "EXIT_BE": lambda ev, n: ExitBeEntities(price=_first_price_after(n.normalized_text, ev.end)),
    "REPORT_RESULT": _raw_summary_entities,
    "INFO_ONLY": _info_only_entities,
}


# ---------------------------------------------------------------------------
# Span deduplication
# ---------------------------------------------------------------------------


def _deduplicate_by_span(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    """Remove intents subsumed by a stronger/equal match covering the same span.

    Two cases handled:
    - Containment: a weak intent whose span sits entirely inside a stronger intent's
      span is dropped (e.g. weak "стоп" inside strong "стоп в бу").
    - Same-type overlap: when two intents of the same type have overlapping spans,
      keep the one with higher confidence, breaking ties by longer span.
    """
    if len(intents) <= 1:
        return intents

    # Process in priority order: higher confidence first, longer span as tiebreaker
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


# ---------------------------------------------------------------------------
# Price parsing helpers
# ---------------------------------------------------------------------------


def _first_price_after(text: str, offset: int) -> Price | None:
    prices = _prices_after(text, offset)
    return prices[0] if prices else None


def _prices_after(text: str, offset: int) -> list[Price]:
    prices: list[Price] = []
    for match in _PRICE_RE.finditer(text, offset):
        price = _price_from_raw(match.group(0))
        if price is not None:
            prices.append(price)
    return prices


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
