from __future__ import annotations

import re

from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import SignalDraft
from src.parser_v2.core.symbol_normalizer import normalize_symbol
from src.parser_v2.core.parsing_utils import float_from_raw as _float_from_raw, price_from_raw as _price_from_raw


_NUMBER_PATTERN = r"\d(?:[\d \t.,]*\d)?"

# "по HYPE", "по SUI" — symbol word immediately after Cyrillic preposition "по"
_SYMBOL_RE = re.compile(r"\bпо\s+(?P<symbol>[A-Z][A-Z0-9]{0,19})\b", re.IGNORECASE)

# "Вход 54.69"
_ENTRY_RE = re.compile(rf"\bвход\s+(?P<value>{_NUMBER_PATTERN})(?!\s*%)", re.IGNORECASE)

# "стоп 53.32"
_STOP_RE = re.compile(rf"\bстоп\s+(?P<value>{_NUMBER_PATTERN})(?!\s*%)", re.IGNORECASE)

# "цель 59.46"
_TP_RE = re.compile(rf"\bцель\s+(?P<value>{_NUMBER_PATTERN})(?!\s*%)", re.IGNORECASE)

# Guard: close messages carry historical "вход/стоп/цель" phrases; skip signal extraction entirely
_CLOSE_GUARD_RE = re.compile(r"\bзакрыла\b", re.IGNORECASE)


class SignalExtractor:
    def extract(self, normalized: NormalizedText, market_hint: bool = False) -> SignalDraft | None:
        text = normalized.raw_text

        if _CLOSE_GUARD_RE.search(text):
            return None

        symbol = _extract_symbol(text)
        side = _extract_side(normalized.normalized_text)
        entry = _extract_entry(text)
        stop_loss = _extract_stop_loss(text)
        take_profits = _extract_take_profits(text)

        if not any((entry, stop_loss, take_profits)):
            return None

        entries = (
            [
                EntryLeg(
                    sequence=1,
                    entry_type="MARKET" if market_hint else "LIMIT",
                    price=entry,
                    role="PRIMARY",
                    is_optional=False,
                )
            ]
            if entry is not None
            else []
        )

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
            entry_structure="ONE_SHOT" if entries else None,
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
            risk_hint=None,
            missing_fields=missing,
            completeness="COMPLETE" if not missing else "INCOMPLETE",
        )


def _extract_symbol(text: str) -> str | None:
    m = _SYMBOL_RE.search(text)
    if not m:
        return None
    raw = m.group("symbol").upper()
    return normalize_symbol(raw)


def _extract_side(normalized_text: str) -> str | None:
    if "лонг" in normalized_text or "long" in normalized_text:
        return "LONG"
    if "шорт" in normalized_text or "short" in normalized_text:
        return "SHORT"
    return None


def _extract_entry(text: str) -> Price | None:
    m = _ENTRY_RE.search(text)
    return _price_from_raw(m.group("value")) if m else None


def _extract_stop_loss(text: str) -> StopLoss | None:
    m = _STOP_RE.search(text)
    if not m:
        return None
    price = _price_from_raw(m.group("value"))
    return StopLoss(price=price) if price is not None else None


def _extract_take_profits(text: str) -> list[TakeProfit]:
    results = []
    for i, m in enumerate(_TP_RE.finditer(text), start=1):
        price = _price_from_raw(m.group("value"))
        if price is not None:
            results.append(TakeProfit(sequence=i, price=price, label=f"TP{i}"))
    return results


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


