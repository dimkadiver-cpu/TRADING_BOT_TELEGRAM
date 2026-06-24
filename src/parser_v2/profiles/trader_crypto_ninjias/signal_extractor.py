from __future__ import annotations

import re

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import SignalDraft
from src.parser_v2.core.symbol_normalizer import normalize_symbol

_STANDARD_HEADER_RE = re.compile(
    r"^(?:[^\w\s]+\s*)?(?P<side>long(?:\s+(?:limit|market|swing))?|short(?:\s+(?:limit|market|swing))?)\s*-\s*\$(?P<symbol>[a-z0-9]{1,20})\b",
    re.IGNORECASE,
)
_INLINE_SIGNAL_RE = re.compile(
    r"^(?P<symbol>[a-z0-9]{1,20})\s+(?P<side>long|short)\b",
    re.IGNORECASE,
)
_ENTRY_RE = re.compile(r"entry\s*:?\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_ENTRY_RANGE_RE = re.compile(
    r"entry\s*:?\s*(?P<low>\d[\d,]*(?:\.\d+)?)\s*-\s*(?P<high>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_ENTRY_MARKET_RE = re.compile(r"entry\s+market\s*:?\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_ENTRY_LIMIT_RE = re.compile(r"entry\s+limit\s*:?\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_ENTRY_LIMIT_INDEXED_RE = re.compile(
    r"entry\s+limit\s*(?P<index>\d+)\s*:?\s*(?P<price>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_STOP_LOSS_RE = re.compile(r"\bsl\s*:?\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_TP_RE = re.compile(r"\btp(?P<level>\d+)\s*:?\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_TP_SINGLE_RE = re.compile(r"\btp\s*:?\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)


class SignalExtractor:
    def extract(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> SignalDraft | None:
        raw = text.raw_text
        if not _looks_like_signal(raw):
            return None

        symbol = _extract_symbol(raw)
        side = _extract_side(raw)
        entries = _extract_entries(raw)
        stop_loss = _extract_stop_loss(raw)
        take_profits = _extract_take_profits(raw)

        missing = _missing_fields(
            symbol=symbol,
            side=side,
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
        )

        return SignalDraft(
            symbol=symbol,
            side=side,
            entry_structure=_entry_structure(entries),
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
            risk_hint=None,
            leverage_hint=None,
            missing_fields=missing,
            completeness="COMPLETE" if not missing else "INCOMPLETE",
        )


def _looks_like_signal(text: str) -> bool:
    if _STANDARD_HEADER_RE.search(text):
        return True
    return bool(_INLINE_SIGNAL_RE.search(text) and (_TP_RE.search(text) or _TP_SINGLE_RE.search(text)))


def _extract_symbol(text: str) -> str | None:
    for pattern in (_STANDARD_HEADER_RE, _INLINE_SIGNAL_RE):
        match = pattern.search(text)
        if match:
            symbol = match.group("symbol").upper()
            if not symbol.endswith("USDT"):
                symbol = f"{symbol}USDT"
            return normalize_symbol(symbol)
    return None


def _extract_side(text: str) -> str | None:
    for pattern in (_STANDARD_HEADER_RE, _INLINE_SIGNAL_RE):
        match = pattern.search(text)
        if match:
            return "LONG" if "long" in match.group("side").lower() else "SHORT"
    return None


def _extract_entries(text: str) -> list[EntryLeg]:
    indexed_limit_matches = list(_ENTRY_LIMIT_INDEXED_RE.finditer(text))
    if indexed_limit_matches:
        entries: list[EntryLeg] = []
        for sequence, match in enumerate(indexed_limit_matches, start=1):
            price = _price(match.group("price"))
            if price is None:
                continue
            entries.append(
                EntryLeg(
                    sequence=sequence,
                    entry_type="LIMIT",
                    price=price,
                    role="PRIMARY" if sequence == 1 else "AVERAGING",
                    is_optional=False,
                )
            )
        return entries

    range_match = _ENTRY_RANGE_RE.search(text)
    if range_match:
        low = _price(range_match.group("low"))
        high = _price(range_match.group("high"))
        if low is not None and high is not None:
            ordered = sorted((low, high), key=lambda item: item.value)
            return [
                EntryLeg(
                    sequence=1,
                    entry_type="LIMIT",
                    price=ordered[0],
                    role="PRIMARY",
                    is_optional=False,
                ),
                EntryLeg(
                    sequence=2,
                    entry_type="LIMIT",
                    price=ordered[1],
                    role="AVERAGING",
                    is_optional=False,
                ),
            ]

    entries: list[EntryLeg] = []
    market_match = _ENTRY_MARKET_RE.search(text)
    if market_match:
        price = _price(market_match.group("price"))
        entries.append(
            EntryLeg(
                sequence=1,
                entry_type="MARKET",
                price=price,
                role="PRIMARY",
                is_optional=False,
            )
        )
    else:
        entry_match = _ENTRY_RE.search(text)
        if entry_match:
            price = _price(entry_match.group("price"))
            if price is not None:
                entries.append(
                    EntryLeg(
                        sequence=1,
                        entry_type=_default_entry_type_from_header(text),
                        price=price,
                        role="PRIMARY",
                        is_optional=False,
                    )
                )

    limit_match = _ENTRY_LIMIT_RE.search(text)
    if limit_match:
        price = _price(limit_match.group("price"))
        if price is not None:
            entries.append(
                EntryLeg(
                    sequence=len(entries) + 1,
                    entry_type="LIMIT",
                    price=price,
                    role="AVERAGING" if entries else "PRIMARY",
                    is_optional=False,
                )
            )

    return entries


def _default_entry_type_from_header(text: str) -> str:
    header_match = _STANDARD_HEADER_RE.search(text)
    if header_match is None:
        return "LIMIT"
    header_side = header_match.group("side").lower()
    return "MARKET" if "market" in header_side else "LIMIT"


def _extract_stop_loss(text: str) -> StopLoss | None:
    match = _STOP_LOSS_RE.search(text)
    if not match:
        return None
    price = _price(match.group("price"))
    return StopLoss(price=price) if price is not None else None


def _extract_take_profits(text: str) -> list[TakeProfit]:
    take_profits: list[TakeProfit] = []
    for match in _TP_RE.finditer(text):
        price = _price(match.group("price"))
        if price is None:
            continue
        level = int(match.group("level"))
        take_profits.append(TakeProfit(sequence=level, price=price, label=f"TP{level}"))

    if not take_profits:
        single_tp = _TP_SINGLE_RE.search(text)
        if single_tp:
            price = _price(single_tp.group("price"))
            if price is not None:
                take_profits.append(TakeProfit(sequence=1, price=price, label="TP1"))

    take_profits = _normalize_take_profit_sequences(take_profits)
    take_profits.sort(key=lambda tp: tp.sequence)
    return take_profits


def _normalize_take_profit_sequences(take_profits: list[TakeProfit]) -> list[TakeProfit]:
    normalized: list[TakeProfit] = []
    next_sequence = 1
    seen: set[int] = set()
    for tp in take_profits:
        sequence = tp.sequence
        if sequence in seen or sequence < next_sequence:
            sequence = next_sequence
        normalized.append(tp.model_copy(update={"sequence": sequence, "label": f"TP{sequence}"}))
        seen.add(sequence)
        next_sequence = sequence + 1
    return normalized


def _entry_structure(entries: list[EntryLeg]) -> str | None:
    count = len(entries)
    if count == 0:
        return None
    if count == 1:
        return "ONE_SHOT"
    if count == 2:
        return "TWO_STEP"
    return "LADDER"


def _missing_fields(
    *,
    symbol: str | None,
    side: str | None,
    entries: list[EntryLeg],
    stop_loss: StopLoss | None,
    take_profits: list[TakeProfit],
) -> list[str]:
    missing: list[str] = []
    if symbol is None:
        missing.append("symbol")
    if side is None:
        missing.append("side")
    if not entries:
        missing.append("entries")
    if stop_loss is None:
        missing.append("stop_loss")
    if not take_profits:
        missing.append("take_profits")
    return missing


def _price(raw: str | None) -> Price | None:
    if not raw:
        return None
    compact = raw.strip().replace(",", "")
    try:
        return Price(raw=raw.strip(), value=float(compact))
    except ValueError:
        return None
