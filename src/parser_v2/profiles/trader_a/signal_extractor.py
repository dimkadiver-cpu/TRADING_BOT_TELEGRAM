from __future__ import annotations

import re

from src.parser_v2.contracts.entities import (
    EntryLeg,
    Price,
    RiskHint,
    StopLoss,
    TakeProfit,
)
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import SignalDraft
from src.parser_v2.core.symbol_normalizer import normalize_symbol


_NUMBER_PATTERN = r"\d(?:[\d\s.,]*\d)?"

_CYR_LONG = "\u043b\u043e\u043d\u0433"
_CYR_SHORT = "\u0448\u043e\u0440\u0442"
_CYR_ENTRY = "\u0432\u0445\u043e\u0434"
_CYR_AVERAGING = "\u0443\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435"
_CYR_STOP = "\u0441\u0442\u043e\u043f"
_CYR_LIMIT_ROOT = "\u043b\u0438\u043c\u0438\u0442"
_CYR_CURRENT_ROOT = "\u0442\u0435\u043a\u0443\u0449"
_CYR_MARKET_ROOT = "\u0440\u044b\u043d"

_SYMBOL_RE = re.compile(
    r"(?:#|\$)?(?P<symbol>[A-Z0-9]{1,24}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b",
    re.IGNORECASE,
)
_BARE_HASHTAG_SYMBOL_RE = re.compile(r"#(?P<symbol>[A-Z0-9]{2,20})\b", re.IGNORECASE)

_ENTRY_MARKET_RE = re.compile(
    rf"(?:entry|enter|vhod|{_CYR_ENTRY})\s+"
    rf"(?:market|at\s+market|now|[^\n]*{_CYR_CURRENT_ROOT}\w*|[^\n]*{_CYR_MARKET_ROOT}\w*)"
    rf"\s*:?\s*(?P<value>{_NUMBER_PATTERN})",
    re.IGNORECASE,
)
_ENTRY_RE = re.compile(
    rf"\b(?:entry|vhod|{_CYR_ENTRY})"
    rf"(?:\s+(?:limit|limitka|{_CYR_LIMIT_ROOT}\w*))?"
    rf"\s*[:=@-]?\s*(?P<value>{_NUMBER_PATTERN})(?!\s*%)",
    re.IGNORECASE,
)
_AVERAGING_RE = re.compile(
    rf"(?:averaging|avg|average|{_CYR_AVERAGING})\s*[:=@-]?\s*(?P<value>{_NUMBER_PATTERN})",
    re.IGNORECASE,
)
_ENTRY_AB_RE = re.compile(
    rf"(?:^|\n)\s*(?:[-*]\s*)?(?:entry\s*)?(?:\((?P<label_paren>[ab])\)|(?P<label>[ab]))"
    rf"(?:\s*\((?P<qual>[^)]*)\))?\s*[:=@-]\s*(?P<value>{_NUMBER_PATTERN})",
    re.IGNORECASE,
)
_STOP_LOSS_RE = re.compile(
    rf"(?:\bsl\b|stop|{_CYR_STOP})\s*:?\s*(?P<value>{_NUMBER_PATTERN})(?![.,\d]*\s*%)",
    re.IGNORECASE,
)
_TAKE_PROFIT_RE = re.compile(rf"\btp(?P<index>\d+)?\b\s*:?\s*(?P<value>{_NUMBER_PATTERN})", re.IGNORECASE)

_DEFAULT_RISK_PREFIXES = ["risk", "риск", "вход", "на сделку"]
_DEFAULT_RISK_SUFFIXES = ["от депозита", "риска", "на сделку"]


class SignalExtractor:
    def __init__(
        self,
        risk_prefixes: list[str] | None = None,
        risk_suffixes: list[str] | None = None,
    ) -> None:
        self._risk_prefixes = risk_prefixes or _DEFAULT_RISK_PREFIXES
        self._risk_suffixes = risk_suffixes or _DEFAULT_RISK_SUFFIXES

    def extract(self, normalized: NormalizedText) -> SignalDraft | None:
        text = normalized.raw_text
        normalized_text = normalized.normalized_text

        symbol = normalize_symbol(_extract_symbol(text))
        side = _extract_side(normalized_text)
        entries = _extract_entries(text)
        stop_loss = _extract_stop_loss(text)
        take_profits = _extract_take_profits(text)
        risk_hint = _extract_risk_hint(text, self._risk_prefixes, self._risk_suffixes)

        if not any((entries, stop_loss, take_profits)):
            return None

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
            risk_hint=risk_hint,
            missing_fields=missing_fields,
            completeness="COMPLETE" if not missing_fields else "INCOMPLETE",
        )


def _extract_symbol(text: str) -> str | None:
    match = _SYMBOL_RE.search(text.upper())
    if match:
        return match.group("symbol").upper()

    bare_match = _BARE_HASHTAG_SYMBOL_RE.search(text.upper())
    if bare_match:
        return f"{bare_match.group('symbol').upper()}USDT"

    return None


def _extract_side(normalized_text: str) -> str | None:
    if any(marker in normalized_text for marker in ("long", "buy", _CYR_LONG)):
        return "LONG"
    if any(marker in normalized_text for marker in ("short", "sell", _CYR_SHORT)):
        return "SHORT"
    return None


def _extract_entries(text: str) -> list[EntryLeg]:
    ab_entries = _extract_ab_entries(text)
    if ab_entries:
        return ab_entries

    entries: list[EntryLeg] = []
    primary_type = "MARKET"
    primary = _search_price(_ENTRY_MARKET_RE, text)
    if primary is None:
        primary = _search_price(_ENTRY_RE, text)
        primary_type = "LIMIT"

    if primary is not None:
        entries.append(
            EntryLeg(
                sequence=1,
                entry_type=primary_type,
                price=primary,
                role="PRIMARY",
                is_optional=False,
            )
        )

    averaging = _search_price(_AVERAGING_RE, text)
    if averaging is not None:
        entries.append(
            EntryLeg(
                sequence=len(entries) + 1,
                entry_type="LIMIT",
                price=averaging,
                role="AVERAGING",
                is_optional=True,
            )
        )

    return entries


def _extract_ab_entries(text: str) -> list[EntryLeg]:
    entries: list[EntryLeg] = []
    for match in _ENTRY_AB_RE.finditer(text):
        price = _price_from_raw(match.group("value"))
        if price is None:
            continue

        label = (match.group("label") or match.group("label_paren") or "").lower()
        qual = (match.group("qual") or "").lower()
        role = "PRIMARY" if label == "a" or not entries else "AVERAGING"
        entry_type = "MARKET" if role == "PRIMARY" and "market" in qual else "LIMIT"
        entries.append(
            EntryLeg(
                sequence=len(entries) + 1,
                entry_type=entry_type,
                price=price,
                role=role,
                is_optional=role == "AVERAGING",
            )
        )
    return entries


def _extract_stop_loss(text: str) -> StopLoss | None:
    price = _search_price(_STOP_LOSS_RE, text)
    return StopLoss(price=price) if price is not None else None


def _extract_take_profits(text: str) -> list[TakeProfit]:
    take_profits: list[TakeProfit] = []
    for fallback_sequence, match in enumerate(_TAKE_PROFIT_RE.finditer(text), start=1):
        price = _price_from_raw(match.group("value"))
        if price is None:
            continue

        index_raw = match.group("index")
        sequence = int(index_raw) if index_raw else fallback_sequence
        take_profits.append(TakeProfit(sequence=sequence, price=price, label=f"TP{sequence}"))

    return take_profits


def _extract_risk_hint(text: str, prefixes: list[str], suffixes: list[str]) -> RiskHint | None:
    prefix_pattern = "|".join(re.escape(p) for p in prefixes)
    suffix_pattern = "|".join(re.escape(s) for s in suffixes)

    range_re = re.compile(
        rf"(?:{prefix_pattern})[^\d]{{0,24}}"
        rf"(?P<min>\d+(?:[.,]\d+)?)\s*[-–—]\s*(?P<max>\d+(?:[.,]\d+)?)\s*%",
        re.IGNORECASE,
    )
    single_re = re.compile(
        rf"(?:(?:{prefix_pattern})[^\d]{{0,24}}(?P<value>\d+(?:[.,]\d+)?)\s*%|"
        rf"(?P<value_suffix>\d+(?:[.,]\d+)?)\s*%\s*(?:{suffix_pattern}))",
        re.IGNORECASE,
    )

    _sl_line_re = re.compile(r"^[^\n]*(?:sl:|стоп:)[^\n]*$", re.IGNORECASE | re.MULTILINE)
    sl_spans = {(m.start(), m.end()) for m in _sl_line_re.finditer(text)}

    def _on_sl_line(pos: int) -> bool:
        return any(start <= pos < end for start, end in sl_spans)

    range_match = range_re.search(text)
    if range_match and not _on_sl_line(range_match.start()):
        min_value = _float_from_raw(range_match.group("min"))
        max_value = _float_from_raw(range_match.group("max"))
        if min_value is not None and max_value is not None:
            return RiskHint(raw=range_match.group(0), min_value=min_value, max_value=max_value)

    single_match = single_re.search(text)
    if single_match and not _on_sl_line(single_match.start()):
        value = _float_from_raw(single_match.group("value") or single_match.group("value_suffix"))
        if value is not None:
            return RiskHint(raw=single_match.group(0), value=value)

    return None


def _entry_structure(entries: list[EntryLeg]) -> str | None:
    if len(entries) >= 3:
        return "LADDER"
    if len(entries) == 2:
        return "TWO_STEP"
    if len(entries) == 1:
        return "ONE_SHOT"
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


def _search_price(pattern: re.Pattern[str], text: str) -> Price | None:
    match = pattern.search(text)
    if not match:
        return None
    return _price_from_raw(match.group("value"))


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
