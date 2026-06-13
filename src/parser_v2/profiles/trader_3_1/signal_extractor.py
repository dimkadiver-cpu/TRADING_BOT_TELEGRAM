from __future__ import annotations

import re

from src.parser_v2.contracts.entities import EntryLeg, StopLoss, TakeProfit
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import SignalDraft
from src.parser_v2.core.parsing_utils import price_from_raw as _price_from_raw
from src.parser_v2.core.symbol_normalizer import normalize_symbol

_COIN_RE = re.compile(
    r"coin\s*:\s*#?(?P<base>[a-z0-9]+)\s*/\s*(?P<quote>usdt|usdc|usd|btc|eth)\b",
    re.IGNORECASE,
)
_SIDE_RE = re.compile(r"(?:^|\n)\s*(?P<emoji>🟢|🔴)\s*(?P<side>long|short)\b", re.IGNORECASE)
_ENTRY_LINE_RE = re.compile(r"^.*entry\s*:\s*(?P<first>\d[\d.,]*)\s*-\s*(?P<second>\d[\d.,]*)", re.IGNORECASE | re.MULTILINE)
_TARGET_LINE_RE = re.compile(
    r"^.*target\s+(?P<index>\d+)\s*:\s*(?P<value>\d[\d.,]*)",
    re.IGNORECASE | re.MULTILINE,
)
_STOP_LOSS_RE = re.compile(r"^.*stop\s*loss\s*:\s*(?P<value>\d[\d.,]*)", re.IGNORECASE | re.MULTILINE)
_LEVERAGE_RE = re.compile(r"leverage\s*:\s*(?P<value>\d+(?:[.,]\d+)?)\s*x", re.IGNORECASE)


class SignalExtractor:
    def extract(self, normalized: NormalizedText) -> SignalDraft | None:
        text = normalized.raw_text
        if not _looks_like_signal(text):
            return None

        symbol = _extract_symbol(text)
        side = _extract_side(text)
        entries = _extract_entries(text)
        stop_loss = _extract_stop_loss(text)
        take_profits = _extract_take_profits(text)
        leverage_hint = _extract_leverage(text)

        missing_fields = _missing_fields(
            symbol=symbol,
            side=side,
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
        )

        return SignalDraft(
            symbol=symbol,
            side=side,
            entry_structure="RANGE" if len(entries) == 2 else None,
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
            risk_hint=None,
            leverage_hint=leverage_hint,
            missing_fields=missing_fields,
            completeness="COMPLETE" if not missing_fields else "INCOMPLETE",
        )


def _looks_like_signal(text: str) -> bool:
    lowered = text.lower()
    return (
        "coin" in lowered
        and "entry:" in lowered
        and "stoploss:" in lowered
        and "target 1:" in lowered
    )


def _extract_symbol(text: str) -> str | None:
    match = _COIN_RE.search(text)
    if not match:
        return None
    return normalize_symbol(f"{match.group('base').upper()}{match.group('quote').upper()}")


def _extract_side(text: str) -> str | None:
    match = _SIDE_RE.search(text)
    if not match:
        return None
    return match.group("side").upper()


def _extract_entries(text: str) -> list[EntryLeg]:
    match = _ENTRY_LINE_RE.search(text)
    if not match:
        return []

    prices = [
        _price_from_raw(match.group("first")),
        _price_from_raw(match.group("second")),
    ]
    normalized_prices = [price for price in prices if price is not None]
    if len(normalized_prices) != 2:
        return []

    normalized_prices.sort(key=lambda price: price.value)
    return [
        EntryLeg(sequence=1, entry_type="LIMIT", price=normalized_prices[0], role="PRIMARY"),
        EntryLeg(sequence=2, entry_type="LIMIT", price=normalized_prices[1], role="AVERAGING"),
    ]


def _extract_stop_loss(text: str) -> StopLoss | None:
    match = _STOP_LOSS_RE.search(text)
    if not match:
        return None
    price = _price_from_raw(match.group("value"))
    return StopLoss(price=price) if price is not None else None


def _extract_take_profits(text: str) -> list[TakeProfit]:
    take_profits: list[TakeProfit] = []
    for match in _TARGET_LINE_RE.finditer(text):
        price = _price_from_raw(match.group("value"))
        if price is None:
            continue
        sequence = int(match.group("index"))
        take_profits.append(TakeProfit(sequence=sequence, price=price, label=f"TP{sequence}"))
    return take_profits


def _extract_leverage(text: str) -> float | None:
    match = _LEVERAGE_RE.search(text)
    if not match:
        return None
    raw = match.group("value").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _missing_fields(
    *,
    symbol: str | None,
    side: str | None,
    entries: list[EntryLeg],
    stop_loss: StopLoss | None,
    take_profits: list[TakeProfit],
) -> list[str]:
    missing_fields: list[str] = []
    if symbol is None:
        missing_fields.append("symbol")
    if side is None:
        missing_fields.append("side")
    if not entries:
        missing_fields.append("entries")
    if stop_loss is None:
        missing_fields.append("stop_loss")
    if not take_profits:
        missing_fields.append("take_profits")
    return missing_fields
