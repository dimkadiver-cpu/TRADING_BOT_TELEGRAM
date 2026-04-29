from __future__ import annotations

import re
from typing import Any

from src.parser.canonical_v1.models import (
    EntryLeg,
    Price,
    ReportedResult,
    RiskHint,
    SignalPayload,
    StopLoss,
    TakeProfit,
)
from src.parser.parsed_message import (
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    ExitBeEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    ReportFinalResultEntities,
    SlHitEntities,
    TpHitEntities,
)
from src.parser.rules_engine import RulesEngine
from src.parser.trader_profiles.base import ParserContext

_PRICE_CAPTURE = r"\d[\d\s]*(?:[.,]\d+)?"

_SYMBOL_RE = re.compile(r"(?:#|\$)?(?P<symbol>[A-Z0-9]{2,24}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b", re.IGNORECASE)
_ENTRY_CURRENT_RE = re.compile(r"вход\s+с\s+текущих\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_ENTRY_LIMIT_RE = re.compile(r"(?:вход|entry)\s+(?:лимиткой|лимитным\s+ордером)?\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_ENTRY_SIMPLE_RE = re.compile(r"(?:^|\n)\s*(?:[-—•]\s*)?(?:entry|вход)\s*[:=@-]\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_ENTRY_AB_RE = re.compile(
    r"(?:^|\n)\s*(?:[-—•]\s*)?(?:вход\s*)?(?:\((?P<label_paren>[abаб])\)|(?P<label>[abаб]))"
    r"(?:\s*\((?P<qual>[^)]*)\))?\s*[:=@-]\s*(?P<value>" + _PRICE_CAPTURE + r")",
    re.IGNORECASE,
)
_AVERAGING_RE = re.compile(r"(?:усреднение|вход\s*(?:\((?:b|б)\)|(?:b|б)))\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_STOP_LOSS_RE = re.compile(r"(?:\bsl\b|стоп)\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_TP_RE = re.compile(r"\bTP(?P<index>\d+)?\b\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_TP_LEVEL_RE = re.compile(r"\btp(?P<level>\d+)\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*%")
_RESULT_R_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*R{1,2}\b", re.IGNORECASE)
_STOP_PRICE_RE = re.compile(
    r"(?:move\s*(?:sl|stop)\s*(?:to)?|sl|stop|стоп\s*(?:переношу|переставляю|переносим|переставим)?\s*на)\s*[:=@-]?\s*(?P<value>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_STOP_TO_TP1_RE = re.compile(r"(?:стоп\s+на\s+(?:1|первый)\s+тейк|стоп\s+на\s+tp1)", re.IGNORECASE)
_STOP_TO_BE_MARKERS = (
    "стоп в бу",
    "стоп в безубыток",
    "стоп на точку входа",
    "переводим в бу",
    "перевод в безубыток",
)
_STOP_HIT_MARKERS = ("выбило по стопу", "стоп сработал", "stop hit", "stopped out")
_EXIT_BE_MARKERS = ("закрылась в безубыток", "закрылась в бу", "закрылся в бу", "ушел в бу", "ушла в бу")
_ENTRY_FILLED_MARKERS = ("взяли лимитку", "лимитка", "entry filled", "вход исполнен")
_CLOSE_FULL_MARKERS = ("закрываю по текущим", "закрываю все позиции", "close all", "зафиксировать")
_CLOSE_PARTIAL_MARKERS = ("partial close", "close half", "частично", "половину", "50%")
_CANCEL_PENDING_MARKERS = ("убираем лимитки", "отменяем лимитки", "cancel pending", "cancel limit")
_LONG_MARKERS = ("лонг", "long")
_SHORT_MARKERS = ("шорт", "short")


class TraderAExtractors:
    def extract(
        self,
        text: str,
        context: ParserContext,
        rules: RulesEngine,
    ) -> dict[str, Any]:
        normalized = _normalize_text(text)
        signal = _extract_signal(text, normalized, rules)
        intents = _extract_intents(text, normalized, rules)
        diagnostics: dict[str, Any] = {}
        parse_status: str | None = None

        if signal is not None:
            diagnostics["signal_entry_count"] = len(signal.entries)
            diagnostics["signal_tp_count"] = len(signal.take_profits)
            if signal.completeness == "INCOMPLETE":
                parse_status = "PARTIAL"

        return {
            "signal": signal,
            "intents": intents,
            "parse_status": parse_status,
            "diagnostics": diagnostics,
        }


def _extract_signal(text: str, normalized: str, rules: RulesEngine) -> SignalPayload | None:
    symbol = _extract_instrument_symbol(text)
    side = _extract_side(normalized)
    entries = _extract_entries(text)
    stop_loss = _extract_stop_loss(text)
    take_profits = _extract_take_profits(text)
    risk_hint = _extract_risk_hint(text, rules)

    if not any((symbol, side, entries, stop_loss, take_profits)):
        return None

    missing_fields: list[str] = []
    if not entries:
        missing_fields.append("entries")
    if stop_loss is None:
        missing_fields.append("stop_loss")
    if not take_profits:
        missing_fields.append("take_profits")

    if len(entries) >= 2:
        entry_structure = "TWO_STEP"
    elif len(entries) == 1:
        entry_structure = "ONE_SHOT"
    else:
        entry_structure = None

    return SignalPayload(
        symbol=symbol,
        side=side,
        entry_structure=entry_structure,
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        risk_hint=risk_hint,
        completeness="COMPLETE" if not missing_fields else "INCOMPLETE",
        missing_fields=missing_fields,
    )


def _extract_intents(text: str, normalized: str, rules: RulesEngine) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    for match in rules.detect_intents_with_evidence(text):
        entity_payload: Any
        if match.intent == "MOVE_STOP_TO_BE":
            entity_payload = MoveStopToBEEntities()
        elif match.intent == "MOVE_STOP":
            entity_payload = _move_stop_entities(text)
        elif match.intent == "CLOSE_FULL":
            entity_payload = CloseFullEntities()
        elif match.intent == "CLOSE_PARTIAL":
            entity_payload = ClosePartialEntities(fraction=_extract_fraction(text, normalized))
        elif match.intent == "CANCEL_PENDING":
            entity_payload = CancelPendingEntities()
        elif match.intent == "ENTRY_FILLED":
            entity_payload = EntryFilledEntities()
        elif match.intent == "TP_HIT":
            entity_payload = TpHitEntities(
                level=_extract_tp_level(text),
                result=_extract_reported_result(text),
            )
        elif match.intent == "SL_HIT":
            entity_payload = SlHitEntities(result=_extract_reported_result(text))
        elif match.intent == "EXIT_BE":
            entity_payload = ExitBeEntities()
        elif match.intent == "REPORT_FINAL_RESULT":
            entity_payload = ReportFinalResultEntities(result=_extract_reported_result(text))
        else:
            continue

        intents.append(
            {
                "type": match.intent,
                "entities": entity_payload,
                "confidence": 0.9 if match.strength == "strong" else 0.55,
                "raw_fragment": text.strip() or None,
            }
        )

    # Conservative fallback for high-signal report/update phrases when the marker file
    # intentionally keeps a smaller vocabulary during the migration.
    if not intents:
        if any(marker in normalized for marker in _STOP_TO_BE_MARKERS):
            intents.append(
                {
                    "type": "MOVE_STOP_TO_BE",
                    "entities": MoveStopToBEEntities(),
                    "confidence": 0.55,
                    "raw_fragment": text.strip() or None,
                }
            )
        elif any(marker in normalized for marker in _STOP_HIT_MARKERS):
            intents.append(
                {
                    "type": "SL_HIT",
                    "entities": SlHitEntities(result=_extract_reported_result(text)),
                    "confidence": 0.55,
                    "raw_fragment": text.strip() or None,
                }
            )
        elif any(marker in normalized for marker in _EXIT_BE_MARKERS):
            intents.append(
                {
                    "type": "EXIT_BE",
                    "entities": ExitBeEntities(),
                    "confidence": 0.55,
                    "raw_fragment": text.strip() or None,
                }
            )
        elif any(marker in normalized for marker in _ENTRY_FILLED_MARKERS):
            intents.append(
                {
                    "type": "ENTRY_FILLED",
                    "entities": EntryFilledEntities(),
                    "confidence": 0.55,
                    "raw_fragment": text.strip() or None,
                }
            )
        elif any(marker in normalized for marker in _CLOSE_FULL_MARKERS):
            intents.append(
                {
                    "type": "CLOSE_FULL",
                    "entities": CloseFullEntities(),
                    "confidence": 0.55,
                    "raw_fragment": text.strip() or None,
                }
            )
        elif any(marker in normalized for marker in _CLOSE_PARTIAL_MARKERS):
            intents.append(
                {
                    "type": "CLOSE_PARTIAL",
                    "entities": ClosePartialEntities(fraction=_extract_fraction(text, normalized)),
                    "confidence": 0.55,
                    "raw_fragment": text.strip() or None,
                }
            )
        elif any(marker in normalized for marker in _CANCEL_PENDING_MARKERS):
            intents.append(
                {
                    "type": "CANCEL_PENDING",
                    "entities": CancelPendingEntities(),
                    "confidence": 0.55,
                    "raw_fragment": text.strip() or None,
                }
            )
    return intents


def _extract_entries(text: str) -> list[EntryLeg]:
    entries: list[EntryLeg] = []
    ab_entries = list(_ENTRY_AB_RE.finditer(text))
    if ab_entries:
        for sequence, match in enumerate(ab_entries, start=1):
            label = str(match.group("label") or match.group("label_paren") or "").lower()
            qual = _normalize_text(str(match.group("qual") or ""))
            entry_type = "MARKET" if sequence == 1 and "текущ" in qual else "LIMIT"
            role = "PRIMARY" if label in {"a", "а"} or sequence == 1 else "AVERAGING"
            price = _price_from_match(match.group("value"))
            if price is None:
                continue
            entries.append(
                EntryLeg(
                    sequence=sequence,
                    entry_type=entry_type,
                    price=price,
                    role=role,
                    is_optional=role == "AVERAGING",
                )
            )
        if entries:
            return entries

    primary = _search_price(_ENTRY_CURRENT_RE, text)
    primary_type = "MARKET"
    if primary is None:
        primary = _search_price(_ENTRY_LIMIT_RE, text) or _search_price(_ENTRY_SIMPLE_RE, text)
        primary_type = "LIMIT"
    averaging = _search_price(_AVERAGING_RE, text)

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
    if averaging is not None:
        entries.append(
            EntryLeg(
                sequence=2,
                entry_type="LIMIT",
                price=averaging,
                role="AVERAGING",
                is_optional=True,
            )
        )
    return entries


def _extract_stop_loss(text: str) -> StopLoss | None:
    price = _search_price(_STOP_LOSS_RE, text)
    if price is None:
        return None
    return StopLoss(price=price)


def _extract_take_profits(text: str) -> list[TakeProfit]:
    take_profits: list[TakeProfit] = []
    for sequence, match in enumerate(_TP_RE.finditer(text), start=1):
        price = _price_from_match(match.group("value"))
        if price is None:
            continue
        index_raw = match.group("index")
        index = int(index_raw) if index_raw else sequence
        take_profits.append(
            TakeProfit(
                sequence=index,
                price=price,
                label=f"TP{index}",
            )
        )
    return take_profits


def _extract_risk_hint(text: str, rules: RulesEngine) -> RiskHint | None:
    extraction_markers = rules.raw_rules.get("extraction_markers", {})
    prefixes = _marker_strings(extraction_markers.get("risk_prefix")) or ["риск", "вход", "на сделку"]
    suffixes = _marker_strings(extraction_markers.get("risk_suffix")) or ["от депозита", "риска", "на сделку"]

    prefix_pattern = "|".join(re.escape(item) for item in prefixes)
    suffix_pattern = "|".join(re.escape(item) for item in suffixes)
    range_re = re.compile(
        rf"(?:(?:{prefix_pattern}))[^\d]{{0,24}}(?P<min>\d+(?:[.,]\d+)?)\s*-\s*(?P<max>\d+(?:[.,]\d+)?)\s*%",
        re.IGNORECASE,
    )
    single_re = re.compile(
        rf"(?:(?:{prefix_pattern}))[^\d]{{0,24}}(?P<value>\d+(?:[.,]\d+)?)\s*%|(?P<value_suffix>\d+(?:[.,]\d+)?)\s*%\s*(?:{suffix_pattern})",
        re.IGNORECASE,
    )

    range_match = range_re.search(text)
    if range_match:
        min_value = _to_float(range_match.group("min"))
        max_value = _to_float(range_match.group("max"))
        if min_value is not None and max_value is not None:
            return RiskHint(
                raw=range_match.group(0),
                min_value=min_value,
                max_value=max_value,
                unit="PERCENT",
            )

    single_match = single_re.search(text)
    if single_match:
        value = _to_float(single_match.group("value") or single_match.group("value_suffix"))
        if value is not None:
            return RiskHint(
                raw=single_match.group(0),
                value=value,
                unit="PERCENT",
            )
    return None


def _extract_instrument_symbol(text: str) -> str | None:
    match = _SYMBOL_RE.search(text.upper())
    return match.group("symbol").upper() if match else None


def _extract_side(normalized: str) -> str | None:
    if any(marker in normalized for marker in _LONG_MARKERS):
        return "LONG"
    if any(marker in normalized for marker in _SHORT_MARKERS):
        return "SHORT"
    return None


def _move_stop_entities(text: str) -> MoveStopEntities:
    if _STOP_TO_TP1_RE.search(text):
        return MoveStopEntities(stop_to_tp_level=1)
    price = _search_price(_STOP_PRICE_RE, text)
    return MoveStopEntities(new_stop_price=price)


def _extract_tp_level(text: str) -> int | None:
    match = _TP_LEVEL_RE.search(text)
    if not match:
        return None
    return int(match.group("level"))


def _extract_fraction(text: str, normalized: str) -> float | None:
    match = _PERCENT_RE.search(text)
    if match:
        value = _to_float(match.group("value"))
        if value is not None:
            return round(max(0.0, min(1.0, value / 100.0)), 6)
    if "half" in normalized or "половину" in normalized:
        return 0.5
    return None


def _extract_reported_result(text: str) -> ReportedResult | None:
    r_match = _RESULT_R_RE.search(text)
    if r_match:
        value = _to_float(r_match.group("value"))
        if value is not None:
            return ReportedResult(value=value, unit="R", text=r_match.group(0))
    percent_match = _PERCENT_RE.search(text)
    if percent_match:
        value = _to_float(percent_match.group("value"))
        if value is not None:
            return ReportedResult(value=value, unit="PERCENT", text=percent_match.group(0))
    return None


def _search_price(pattern: re.Pattern[str], text: str) -> Price | None:
    match = pattern.search(text)
    if not match:
        return None
    return _price_from_match(match.group("value"))


def _price_from_match(raw: str | None) -> Price | None:
    value = _to_float(raw)
    if raw is None or value is None:
        return None
    return Price(raw=raw, value=value)


def _marker_strings(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    items: list[str] = []
    for bucket in ("strong", "weak"):
        nested = value.get(bucket)
        if not isinstance(nested, list):
            continue
        items.extend(str(item) for item in nested if isinstance(item, str) and str(item).strip())
    return items


def _to_float(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
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
