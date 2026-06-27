from __future__ import annotations

import re

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import SignalDraft
from src.parser_v2.core.symbol_normalizer import normalize_symbol

_HEADER_RE = re.compile(
    r"#(?P<symbol>[A-Z0-9]{2,20}USDT)\s+[^\n]*?\n[^\n]*(?P<side>Long|Short)\s+Entry Zone:\s*"
    r"(?P<entry_a>\d[\d,]*(?:\.\d+)?)\s*[-–]\s*(?P<entry_b>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_STOP_LOSS_RE = re.compile(r"Stop-Loss:\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_TARGET_RE = re.compile(
    r"Target\s*(?P<level>\d+)\s*:\s*(?P<price>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)


class SignalExtractor:
    def extract(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> SignalDraft | None:
        match = _HEADER_RE.search(text.raw_text)
        if match is None:
            return None

        symbol = normalize_symbol(match.group("symbol").upper())
        side = match.group("side").upper()
        entries = _build_entries(match.group("entry_a"), match.group("entry_b"))
        stop_loss = _extract_stop_loss(text.raw_text)
        take_profits = _extract_take_profits(text.raw_text)
        missing = _missing_fields(symbol, side, entries, stop_loss, take_profits)

        return SignalDraft(
            symbol=symbol,
            side=side,
            entry_structure="TWO_STEP",
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
            risk_hint=None,
            leverage_hint=None,
            missing_fields=missing,
            completeness="COMPLETE" if not missing else "INCOMPLETE",
        )


def _build_entries(raw_a: str, raw_b: str) -> list[EntryLeg]:
    prices = [price for price in (_price(raw_a), _price(raw_b)) if price is not None]
    prices.sort(key=lambda item: item.value)
    entries: list[EntryLeg] = []
    for index, price in enumerate(prices, start=1):
        entries.append(
            EntryLeg(
                sequence=index,
                entry_type="LIMIT",
                price=price,
                role="PRIMARY" if index == 1 else "AVERAGING",
                is_optional=False,
            )
        )
    return entries


def _extract_stop_loss(text: str) -> StopLoss | None:
    match = _STOP_LOSS_RE.search(text)
    if match is None:
        return None
    price = _price(match.group("price"))
    return StopLoss(price=price) if price is not None else None


def _extract_take_profits(text: str) -> list[TakeProfit]:
    take_profits: list[TakeProfit] = []
    for match in _TARGET_RE.finditer(text):
        price = _price(match.group("price"))
        if price is None:
            continue
        level = int(match.group("level"))
        take_profits.append(TakeProfit(sequence=level, price=price, label=f"TP{level}"))
    take_profits.sort(key=lambda tp: tp.sequence)
    return take_profits


def _missing_fields(
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

