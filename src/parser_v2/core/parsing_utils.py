from __future__ import annotations

import re

from src.parser_v2.contracts.entities import Price
from src.parser_v2.contracts.markers import MarkerEvidence
from src.parser_v2.contracts.parsed_message import ParsedIntent

# Side keywords shared across all profiles.
# Covers Russian (лонг/шорт) and English (long/short).
# Profiles may extend this by pre-processing text (e.g. translating labels) before calling.
_SIDE_LONG_RE = re.compile(r"\b(?:лонг|long)\b", re.IGNORECASE)
_SIDE_SHORT_RE = re.compile(r"\b(?:шорт|short)\b", re.IGNORECASE)


def extract_side_from_text(text: str) -> str | None:
    """Return 'LONG', 'SHORT', or None based on side keywords in *text*."""
    if _SIDE_LONG_RE.search(text):
        return "LONG"
    if _SIDE_SHORT_RE.search(text):
        return "SHORT"
    return None


def float_from_raw(raw: str | None) -> float | None:
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


def price_from_raw(raw: str | None) -> Price | None:
    value = float_from_raw(raw)
    if raw is None or value is None:
        return None
    return Price(raw=raw.strip(), value=value)


def deduplicate_by_span(intents: list[ParsedIntent]) -> list[ParsedIntent]:
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


def resolve_market_hint(evidence: list[MarkerEvidence], default_entry_type: str | None) -> bool:
    has_limit = any(e.kind == "entry_type" and e.name == "LIMIT" and not e.suppressed for e in evidence)
    has_market = any(e.kind == "entry_type" and e.name == "MARKET" and not e.suppressed for e in evidence)
    if has_limit:
        return False
    if has_market:
        return True
    return default_entry_type == "MARKET"


__all__ = ["float_from_raw", "price_from_raw", "deduplicate_by_span", "resolve_market_hint", "extract_side_from_text"]
