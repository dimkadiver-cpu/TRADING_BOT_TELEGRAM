from __future__ import annotations

import re
from typing import Any, Literal

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
    AddEntryEntities,
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    ExitBeEntities,
    InvalidateSetupEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    ReenterEntities,
    ReportFinalResultEntities,
    ReportPartialResultEntities,
    SlHitEntities,
    TpHitEntities,
    UpdateTakeProfitsEntities,
)
from src.parser.rules_engine import RulesEngine
from src.parser.trader_profiles.base import ParserContext

_PRICE_CAPTURE = r"\d[\d\s]*(?:[.,]\d+)?"

_SYMBOL_RE = re.compile(r"(?:#|\$)?(?P<symbol>[A-Z0-9]{1,24}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b", re.IGNORECASE)
_ENTRY_CURRENT_RE = re.compile(r"вход\s+с\s+текущих\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
# Detects MARKET marker without requiring a price — _ENTRY_CURRENT_RE needs a numeric value.
_MARKET_MARKER_ONLY_RE = re.compile(r"вход\s+с\s+текущих", re.IGNORECASE)
_ENTRY_LIMIT_RE = re.compile(r"(?:вход|entry)\s+(?:лимиткой|лимитным\s+ордером)?\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_ENTRY_SIMPLE_RE = re.compile(r"(?:^|\n)\s*(?:[-—•]\s*)?(?:entry|вход)\s*[:=@-]\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_ENTRY_AB_RE = re.compile(
    r"(?:^|\n)\s*(?:[-—•]\s*)?(?:вход\s*)?(?:\((?P<label_paren>[abаб])\)|(?P<label>[abаб]))"
    r"(?:\s*\((?P<qual>[^)]*)\))?\s*[:=@-]\s*(?P<value>" + _PRICE_CAPTURE + r")",
    re.IGNORECASE,
)
_AVERAGING_RE = re.compile(r"(?:усреднение|вход\s*(?:\((?:b|б)\)|(?:b|б)))\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")", re.IGNORECASE)
_STOP_LOSS_RE = re.compile(r"(?:\bsl\b|стоп)\s*:?\s*(?P<value>" + _PRICE_CAPTURE + r")(?![.,\d]*\s*%)", re.IGNORECASE)
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
_LEGACY_INTENT_ALIASES = {
    "CANCEL_PENDING_ORDERS": "CANCEL_PENDING",
}


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

    if not any((entries, stop_loss, take_profits)):
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
        intent_name = _LEGACY_INTENT_ALIASES.get(match.intent, match.intent)
        entity_payload: Any
        if intent_name == "MOVE_STOP_TO_BE":
            entity_payload = MoveStopToBEEntities()
        elif intent_name == "MOVE_STOP":
            entity_payload = _move_stop_entities(text)
        elif intent_name == "CLOSE_FULL":
            entity_payload = CloseFullEntities()
        elif intent_name == "CLOSE_PARTIAL":
            entity_payload = ClosePartialEntities(fraction=_extract_fraction(text, normalized))
        elif intent_name == "CANCEL_PENDING":
            entity_payload = CancelPendingEntities()
        elif intent_name == "INVALIDATE_SETUP":
            entity_payload = InvalidateSetupEntities()
        elif intent_name == "REENTER":
            entity_payload = ReenterEntities(
                entries=_extract_entry_prices(text),
                entry_type=_extract_entry_type(normalized),
                entry_structure=_extract_entry_structure(text),
            )
        elif intent_name == "ADD_ENTRY":
            entry_price = _first_entry_price(text)
            if entry_price is None:
                continue
            entity_payload = AddEntryEntities(
                entry_price=entry_price,
                entry_type=_extract_entry_type(normalized),
            )
        elif intent_name == "UPDATE_TAKE_PROFITS":
            entity_payload = UpdateTakeProfitsEntities(
                new_take_profits=[tp.price for tp in _extract_take_profits(text)],
                target_tp_level=_extract_tp_level(text),
                mode=_extract_modify_targets_mode(normalized),
            )
        elif intent_name == "ENTRY_FILLED":
            entity_payload = EntryFilledEntities()
        elif intent_name == "TP_HIT":
            entity_payload = TpHitEntities(
                level=_extract_tp_level(text),
                result=_extract_reported_result(text),
            )
        elif intent_name == "SL_HIT":
            entity_payload = SlHitEntities(result=_extract_reported_result(text))
        elif intent_name == "EXIT_BE":
            entity_payload = ExitBeEntities()
        elif intent_name == "REPORT_PARTIAL_RESULT":
            entity_payload = ReportPartialResultEntities(result=_extract_reported_result(text))
        elif intent_name == "REPORT_FINAL_RESULT":
            entity_payload = ReportFinalResultEntities(result=_extract_reported_result(text))
        else:
            continue

        detection_strength = match.strength
        if intent_name == "REPORT_FINAL_RESULT":
            detection_strength = "weak"

        intents.append(
            {
                "type": intent_name,
                "entities": entity_payload,
                "confidence": 0.9 if detection_strength == "strong" else 0.55,
                "raw_fragment": text.strip() or None,
                "detection_strength": detection_strength,
            }
        )

    if any(item["type"] in {"MOVE_STOP_TO_BE", "MOVE_STOP"} for item in intents):
        intents = [item for item in intents if item["type"] not in {"SL_HIT", "EXIT_BE"}]

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
            if price is None and entry_type == "LIMIT":
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
    has_market_marker = bool(_MARKET_MARKER_ONLY_RE.search(text))

    if primary is not None:
        primary_type: Literal["MARKET", "LIMIT"] = "MARKET"
    elif has_market_marker:
        primary_type = "MARKET"
        # primary rimane None — entry at market, nessun livello numerico specificato.
        # Il marker MARKET ha priorità: eventuali pattern LIMIT nel testo vengono ignorati.
    else:
        primary = _search_price(_ENTRY_LIMIT_RE, text) or _search_price(_ENTRY_SIMPLE_RE, text)
        primary_type = "LIMIT"

    averaging = _search_price(_AVERAGING_RE, text)

    if primary is not None or has_market_marker:
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


def _extract_entry_prices(text: str) -> list[Price]:
    return [entry.price for entry in _extract_entries(text) if entry.price is not None]


def _first_entry_price(text: str) -> Price | None:
    prices = _extract_entry_prices(text)
    return prices[0] if prices else None


def _extract_entry_type(normalized: str) -> str | None:
    if "текущ" in normalized or "market" in normalized:
        return "MARKET"
    if "лимит" in normalized or "limit" in normalized:
        return "LIMIT"
    return None


def _extract_entry_structure(text: str) -> str | None:
    prices = _extract_entry_prices(text)
    if len(prices) >= 3:
        return "LADDER"
    if len(prices) == 2:
        return "TWO_STEP"
    if len(prices) == 1:
        return "ONE_SHOT"
    return None


def _extract_modify_targets_mode(normalized: str) -> str | None:
    if "убир" in normalized or "remove" in normalized:
        return "REMOVE_ONE"
    if "добав" in normalized or "add" in normalized:
        return "ADD"
    return "REPLACE_ALL"


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

    _sl_line_re = re.compile(r"^[^\n]*(?:sl:|стоп:)[^\n]*$", re.IGNORECASE | re.MULTILINE)
    sl_spans = {(m.start(), m.end()) for m in _sl_line_re.finditer(text)}

    def _on_sl_line(pos: int) -> bool:
        return any(start <= pos < end for start, end in sl_spans)

    range_match = range_re.search(text)
    if range_match and not _on_sl_line(range_match.start()):
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
    if single_match and not _on_sl_line(single_match.start()):
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
