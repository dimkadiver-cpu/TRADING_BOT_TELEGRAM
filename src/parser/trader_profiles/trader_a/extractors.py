from __future__ import annotations

import re
from typing import Any

from src.parser.event_envelope_v1 import (
    EntryLegRaw,
    InstrumentRaw,
    ReportEventRaw,
    ReportPayloadRaw,
    ReportedResultRaw,
    RiskHintRaw,
    SignalPayloadRaw,
    SignalRawFragments,
    StopLossRaw,
    StopUpdateRaw,
    TakeProfitRaw,
    UpdatePayloadRaw,
    UpdateRawFragments,
)
from src.parser.trader_profiles.base import ParserContext

_PRICE_CAPTURE = r"\d[\d\s]*(?:[.,]\d+)?"

_SYMBOL_RE = re.compile(r"(?:#|\$)?(?P<symbol>[A-Z0-9]{2,24}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b", re.IGNORECASE)
_ENTRY_CURRENT_RE = re.compile(r"вход\s+с\s+текущих\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_ENTRY_LIMIT_RE = re.compile(r"вход\s+(?:лимиткой|лимитным\s+ордером)\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_ENTRY_A_RE = re.compile(
    r"вход\s*(?:\((?:a|а)\)|(?:a|а))(?:\s*\([^)]+\))?\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")",
    re.IGNORECASE,
)
_ENTRY_SIMPLE_RE = re.compile(r"(?:^|\n)\s*(?:[-—•]\s*)?вход\s*[:=@-]\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_AVERAGING_RE = re.compile(
    r"(?:усреднение|вход\s*(?:\((?:b|б)\)|(?:b|б)))\s*(?:\([^)]+\))?\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")",
    re.IGNORECASE,
)
_ENTRY_AB_RE = re.compile(
    r"(?:^|\n)\s*(?:[-—•]\s*)?(?:вход\s*)?(?:\((?P<label_paren>[abаб])\)|(?P<label>[abаб]))"
    r"(?:\s*\((?P<qual>[^)]*)\))?\s*[:=@-]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?|-)",
    re.IGNORECASE,
)
_STOP_LOSS_RE = re.compile(r"\bsl\b\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_TP_RE = re.compile(r"\bTP(?P<index>\d+)?\b\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_RISK_PREFIX = r"(?:риск|вход|заходим|зайдем|зайти|не\s+более|не\s+больше|на\s+сделку|тут\s+риск)"
_RISK_RANGE_WITH_PREFIX_RE = re.compile(
    r"(?:" + _RISK_PREFIX + r")[^\d]{0,24}(?P<min>\d+(?:[.,]\d+)?)\s*-\s*(?P<max>\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)
_RISK_RANGE_WITH_SUFFIX_RE = re.compile(
    r"(?P<min>\d+(?:[.,]\d+)?)\s*-\s*(?P<max>\d+(?:[.,]\d+)?)\s*%\s*(?:от\s+депозита|риска|на\s+сделку)",
    re.IGNORECASE,
)
_RISK_SINGLE_WITH_PREFIX_RE = re.compile(
    r"(?:" + _RISK_PREFIX + r")[^\d]{0,24}(?P<value>\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)
_RISK_SINGLE_WITH_SUFFIX_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*%\s*(?:от\s+депозита|риска|на\s+сделку)",
    re.IGNORECASE,
)
_RESULT_PERCENT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*%")

_LONG_MARKERS = ("лонг", "long")
_SHORT_MARKERS = ("шорт", "short")
_STOP_TO_ENTRY_MARKERS = (
    "стоп на точку входа",
    "переставить стоп на точку входа",
    "стоп обязательно на точку входа",
    "стоп в бу",
    "стоп в безубыток",
)
_STOP_HIT_MARKERS = (
    "к сожалению стоп",
    "словили стоп",
    "выбило по стопу",
    "стоп сработал",
)
_EXIT_BE_MARKERS = (
    "закрылась в безубыток",
    "закрылась в бу",
    "закрылся в бу",
    "сетап полностью закрыт",
    "ушел в бу",
    "ушла в бу",
)
_ENTRY_FILLED_MARKERS = (
    "взяли лимитку",
    "лимитка",
    "средняя",
    "вход исполнен",
)

class TraderAExtractors:
    def extract(
        self,
        text: str,
        context: ParserContext,
        rules: Any,  # noqa: ARG002 - shared protocol requires it
    ) -> dict[str, Any]:
        lowered = _normalize_text(text)
        instrument = _extract_instrument(text, lowered)
        signal_payload_raw = _extract_signal_payload(text)
        update_payload_raw = _extract_update_payload(text, lowered)
        report_payload_raw = _extract_report_payload(text, lowered)
        intents_extra = _extract_intents_extra(lowered)

        diagnostics: dict[str, Any] = {}
        if signal_payload_raw.entries:
            diagnostics["legacy_entry_prices"] = [leg.price for leg in signal_payload_raw.entries]
        if signal_payload_raw.take_profits:
            diagnostics["legacy_take_profit_prices"] = [tp.price for tp in signal_payload_raw.take_profits]

        return {
            "instrument": instrument,
            "signal_payload_raw": signal_payload_raw,
            "update_payload_raw": update_payload_raw,
            "report_payload_raw": report_payload_raw,
            "intents_extra": intents_extra,
            "diagnostics": diagnostics,
        }


def _extract_instrument(text: str, lowered: str) -> InstrumentRaw:
    match = _SYMBOL_RE.search(text.upper())
    symbol = match.group("symbol").upper() if match else _extract_symbol_from_bare_hashtag(text)
    side = None
    if any(marker in lowered for marker in _LONG_MARKERS):
        side = "LONG"
    elif any(marker in lowered for marker in _SHORT_MARKERS):
        side = "SHORT"
    return InstrumentRaw(symbol=symbol, side=side, market_type="UNKNOWN")


def _extract_signal_payload(text: str) -> SignalPayloadRaw:
    entries = _extract_entry_legs(text)

    stop_loss_value = _search_float(_STOP_LOSS_RE, text)
    stop_loss = StopLossRaw(price=stop_loss_value, raw=_search_line(text, "sl")) if stop_loss_value is not None else None

    take_profits: list[TakeProfitRaw] = []
    for match in _TP_RE.finditer(text):
        price = _to_float(match.group("value"))
        if price is None:
            continue
        index_raw = match.group("index")
        index = int(index_raw) if index_raw else 1
        take_profits.append(
            TakeProfitRaw(
                sequence=index,
                price=price,
                label=f"TP{index}",
                raw=match.group(0),
            )
        )

    risk_hint = _extract_risk_hint(text)

    entry_structure = None
    if len(entries) >= 2:
        entry_structure = "TWO_STEP"
    elif len(entries) == 1:
        entry_structure = "ONE_SHOT"

    return SignalPayloadRaw(
        entry_structure=entry_structure,
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        risk_hint=risk_hint,
        raw_fragments=SignalRawFragments(
            entry_text_raw=_search_line(text, "вход"),
            stop_text_raw=_search_line(text, "sl"),
            take_profits_text_raw=_collect_tp_lines(text),
        ),
    )


def _extract_update_payload(text: str, lowered: str) -> UpdatePayloadRaw:
    stop_update = None
    if any(marker in lowered for marker in _STOP_TO_ENTRY_MARKERS):
        stop_update = StopUpdateRaw(mode="TO_ENTRY", raw=text.strip())

    return UpdatePayloadRaw(
        stop_update=stop_update,
        raw_fragments=UpdateRawFragments(
            stop_text_raw=text.strip() if stop_update is not None else None,
        ),
    )


def _extract_report_payload(text: str, lowered: str) -> ReportPayloadRaw:
    events: list[ReportEventRaw] = []
    results: list[ReportedResultRaw] = []

    if any(marker in lowered for marker in _STOP_HIT_MARKERS):
        result = _extract_percent_result(text)
        if result is not None:
            results.append(result)
        events.append(ReportEventRaw(event_type="SL_HIT", result=result, raw_fragment=text.strip()))

    if any(marker in lowered for marker in _EXIT_BE_MARKERS):
        events.append(ReportEventRaw(event_type="EXIT_BE", raw_fragment=text.strip()))

    if any(marker in lowered for marker in _ENTRY_FILLED_MARKERS):
        events.append(ReportEventRaw(event_type="ENTRY_FILLED", raw_fragment=text.strip()))

    return ReportPayloadRaw(
        events=events,
        reported_results=results,
        summary_text_raw=text.strip() if events or results else None,
    )


def _extract_intents_extra(lowered: str) -> list[str]:
    intents: list[str] = []
    if any(marker in lowered for marker in _STOP_TO_ENTRY_MARKERS):
        intents.append("MOVE_STOP_TO_BE")
    if any(marker in lowered for marker in _STOP_HIT_MARKERS):
        intents.append("SL_HIT")
    if any(marker in lowered for marker in _EXIT_BE_MARKERS):
        intents.append("EXIT_BE")
    if any(marker in lowered for marker in _ENTRY_FILLED_MARKERS):
        intents.append("ENTRY_FILLED")
    return intents


def _extract_entry_legs(text: str) -> list[EntryLegRaw]:
    entries: list[EntryLegRaw] = []
    ab_primary: tuple[float, str] | None = None
    ab_secondary: float | None = None

    for match in _ENTRY_AB_RE.finditer(text):
        label = str(match.group("label") or match.group("label_paren") or "").lower()
        qual = _normalize_text(str(match.group("qual") or ""))
        value = _to_float(match.group("value"))
        if label in {"a", "а"} and value is not None and ab_primary is None:
            entry_type = "MARKET" if "текущ" in qual else "LIMIT"
            ab_primary = (value, entry_type)
        elif label in {"b", "б"} and value is not None and ab_secondary is None:
            ab_secondary = value

    if ab_primary is not None:
        entries.append(
            EntryLegRaw(
                sequence=1,
                entry_type=ab_primary[1],
                price=ab_primary[0],
                role="PRIMARY",
                is_optional=False,
            )
        )
    if ab_secondary is not None:
        entries.append(
            EntryLegRaw(
                sequence=2,
                entry_type="LIMIT",
                price=ab_secondary,
                role="AVERAGING",
                is_optional=True,
            )
        )
    if entries:
        return entries

    entry_current = _search_float(_ENTRY_CURRENT_RE, text)
    entry_limit = _search_float(_ENTRY_LIMIT_RE, text)
    entry_a = _search_float(_ENTRY_A_RE, text)
    entry_simple = _search_float(_ENTRY_SIMPLE_RE, text)
    averaging = _search_float(_AVERAGING_RE, text)

    primary_entry = entry_current
    primary_type = "MARKET"
    if primary_entry is None:
        primary_entry = entry_limit if entry_limit is not None else entry_a if entry_a is not None else entry_simple
        primary_type = "LIMIT"

    if primary_entry is not None:
        entries.append(
            EntryLegRaw(
                sequence=1,
                entry_type=primary_type,
                price=primary_entry,
                role="PRIMARY",
                is_optional=False,
            )
        )
    if averaging is not None:
        entries.append(
            EntryLegRaw(
                sequence=2,
                entry_type="LIMIT",
                price=averaging,
                role="AVERAGING",
                is_optional=True,
            )
        )
    return entries


def _extract_percent_result(text: str) -> ReportedResultRaw | None:
    match = _RESULT_PERCENT_RE.search(text)
    if not match:
        return None
    value = _to_float(match.group("value"))
    if value is None:
        return None
    return ReportedResultRaw(value=value, unit="PERCENT", text=f"{value}%")


def _extract_risk_hint(text: str) -> RiskHintRaw | None:
    normalized = _normalize_text(text)
    for candidate in normalized.splitlines():
        range_match = _RISK_RANGE_WITH_PREFIX_RE.search(candidate) or _RISK_RANGE_WITH_SUFFIX_RE.search(candidate)
        if range_match:
            min_raw = range_match.group("min")
            max_raw = range_match.group("max")
            min_value = _to_float(min_raw)
            max_value = _to_float(max_raw)
            if min_value is not None and max_value is not None:
                return RiskHintRaw(
                    value=None,
                    min_value=min_value,
                    max_value=max_value,
                    unit="PERCENT",
                    raw=range_match.group(0),
                )

        single_match = _RISK_SINGLE_WITH_PREFIX_RE.search(candidate) or _RISK_SINGLE_WITH_SUFFIX_RE.search(candidate)
        if not single_match:
            continue
        value_raw = single_match.group("value")
        value = _to_float(value_raw)
        if value is None:
            continue
        return RiskHintRaw(
            value=value,
            min_value=None,
            max_value=None,
            unit="PERCENT",
            raw=single_match.group(0),
        )

    return None


def _collect_tp_lines(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if "tp" in line.lower()]
    if not lines:
        return None
    return "\n".join(lines)


def _extract_symbol_from_bare_hashtag(text: str) -> str | None:
    for match in re.finditer(r"#\s*([A-Z0-9]{2,24}(?:\.P)?)\b", text, re.IGNORECASE):
        token = str(match.group(1) or "").upper()
        if not token:
            continue
        if token.endswith((".P", "USDT", "USDC", "USD", "BTC", "ETH")):
            return token
        return f"{token}USDT"
    return None


def _search_line(text: str, needle: str) -> str | None:
    for line in text.splitlines():
        if needle.lower() in line.lower():
            stripped = line.strip()
            if stripped:
                return stripped
    return None


def _search_float(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    return _to_float(match.group("value"))


def _to_float(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_text(text: str) -> str:
    return (
        text.lower()
        .replace("ё", "е")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )
