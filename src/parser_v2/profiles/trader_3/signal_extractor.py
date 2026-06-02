from __future__ import annotations

import re

from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import SignalDraft
from src.parser_v2.core.symbol_normalizer import normalize_symbol

_COIN_RE = re.compile(
    r"coin:\s*\$?(?P<base>[a-z0-9]+)\s*(?:/)?\s*(?P<quote>usdt|usdc|usd|btc|eth)\b",
    re.IGNORECASE,
)
_DIRECTION_RE = re.compile(r"direction:\s*(?P<side>long|short)\b", re.IGNORECASE)
_ENTRY_LINE_RE = re.compile(r"^entry:\s*(?P<body>.+)$", re.IGNORECASE | re.MULTILINE)
_TARGETS_LINE_RE = re.compile(r"^targets:\s*(?P<body>.+)$", re.IGNORECASE | re.MULTILINE)
_STOP_LOSS_RE = re.compile(r"^stop\s+loss:\s*(?P<value>.+)$", re.IGNORECASE | re.MULTILINE)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class SignalExtractor:
    def extract(self, normalized: NormalizedText) -> SignalDraft | None:
        text = normalized.raw_text
        lowered = normalized.normalized_text

        if not _looks_like_signal(lowered):
            return None

        symbol = _extract_symbol(text)
        side = _extract_side(text)
        entries = _extract_entries(text)
        stop_loss = _extract_stop_loss(text)
        take_profits = _extract_take_profits(text)

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
            entry_structure=_entry_structure(entries),
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
            risk_hint=None,
            missing_fields=missing_fields,
            completeness="COMPLETE" if not missing_fields else "INCOMPLETE",
        )


def _looks_like_signal(text: str) -> bool:
    required = ("coin:", "direction:", "entry:", "targets:", "stop loss:")
    return all(marker in text for marker in required)


def _extract_symbol(text: str) -> str | None:
    match = _COIN_RE.search(text)
    if not match:
        return None
    return normalize_symbol(f"{match.group('base')}{match.group('quote')}".upper())


def _extract_side(text: str) -> str | None:
    match = _DIRECTION_RE.search(text)
    if not match:
        return None
    return match.group("side").upper()


def _extract_entries(text: str) -> list[EntryLeg]:
    match = _ENTRY_LINE_RE.search(text)
    if not match:
        return []

    prices = _prices_from_text(match.group("body"))
    if not prices:
        return []
    if len(prices) == 1:
        return [
            EntryLeg(
                sequence=1,
                entry_type="LIMIT",
                price=prices[0],
                role="PRIMARY",
                is_optional=False,
            )
        ]

    first, second = prices[0], prices[1]
    if first.value > second.value:
        first, second = second, first
    return [
        EntryLeg(sequence=1, entry_type="LIMIT", price=first, role="PRIMARY", is_optional=False),
        EntryLeg(sequence=2, entry_type="LIMIT", price=second, role="AVERAGING", is_optional=False),
    ]


def _extract_stop_loss(text: str) -> StopLoss | None:
    match = _STOP_LOSS_RE.search(text)
    if not match:
        return None
    prices = _prices_from_text(match.group("value"))
    if not prices:
        return None
    return StopLoss(price=prices[0])


def _extract_take_profits(text: str) -> list[TakeProfit]:
    match = _TARGETS_LINE_RE.search(text)
    if not match:
        return []
    prices = _prices_from_text(match.group("body"))
    return [
        TakeProfit(sequence=index, price=price, label=f"TP{index}")
        for index, price in enumerate(prices, start=1)
    ]


def _entry_structure(entries: list[EntryLeg]) -> str | None:
    if len(entries) == 2:
        return "RANGE"
    if len(entries) == 1:
        return "ONE_SHOT"
    if len(entries) >= 3:
        return "LADDER"
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


def _prices_from_text(text: str) -> list[Price]:
    prices: list[Price] = []
    for match in _NUMBER_RE.finditer(text):
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
    compact = raw.strip().replace(" ", "").replace(",", "")
    if not compact:
        return None
    try:
        return float(compact)
    except ValueError:
        return None
