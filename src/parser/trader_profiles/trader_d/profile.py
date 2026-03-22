"""Trader D profile parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.parser.intent_action_map import intent_policy_for_intent
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser

_NAKED_SYMBOL_RE = re.compile(r"^\s*\$?(?P<symbol>[A-Za-z]{2,12}(?:USDT)?)\b")
# Multiline: finds a symbol on a line that also has a side marker (more reliable for prefixed texts)
_SYMBOL_WITH_SIDE_RE = re.compile(
    r"^\s*\$?(?P<symbol>[A-Za-z]{2,12}(?:USDT)?)\b.*\b(?:long|short|лонг|шорт)\b",
    re.IGNORECASE | re.MULTILINE,
)
_SIDE_RE = re.compile(r"\b(?P<side>long|short|лонг|шорт)\b", re.IGNORECASE)
_LIMIT_ENTRY_RE = re.compile(r"вход\s+лимит\s+(?P<price>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_LIMIT_STANDALONE_RE = re.compile(r"\bлим(?:ит|т)\s+(?P<price>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_ENTRY_MARKET_PRICE_RE = re.compile(r"вход\s+по\s+рынку\s+(?P<price>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_FIKS_PCT_RE = re.compile(r"\bфикс\s+(?P<value>\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_REMAINING_PCT_RE = re.compile(r"\bост[а-яё]+\s+(?P<value>\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_SL_SHORT_FORM_RE = re.compile(r"^\s*(?:sl|сл)\s+(?P<value>-\d+(?:[.,]\d+)?)\s*$", re.IGNORECASE | re.MULTILINE)
_TP_HIT_IDX_RE = re.compile(r"\b(?:tp|тп)\s*(?P<idx>\d)\+?\b", re.IGNORECASE)
_PARTIAL_PERCENT_RE = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_R_RESULT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*[рr]\b", re.IGNORECASE)
_PERCENT_RESULT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_CLOSE_PRICE_RE = re.compile(r"закры\w*\s*(?:полностью|по\s+текущим)?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RISK_RE = re.compile(r"риск\s*(?P<value>\d+(?:[.,]\d+)?)", re.IGNORECASE)
_ENTRY_MARKET_MARKERS = ("вход с текущих", "вход по рынку", "рыночный")
_TP_COMPACT_MARKERS = ("tp1+", "tp1", "tp 1", "тп1", "тп 1", "tp2", "тп2", "tp3", "тп3")
_BE_EXIT_MARKERS = ("позиция ушла в бу", "остаток ушел в бу", "остаток ушел по бу", "остаток позиции ушел в бу", "ушел в бу+")
_BE_MOVE_MARKERS = ("перевод в безубыток", "стоп в бу", "стоп переводим в бу", "стоп в бу+", "стоп переставляем в бу")
_CLOSE_FULL_MARKERS = ("закрываем полностью", "закрываю по текущим", "позиция закрыта", "сделка закрыта")
_UPDATE_TP_MARKERS = ("первый тейк убираем",)


class TraderDProfileParser(TraderBProfileParser):
    """Trader D parser built on top of Trader B deterministic rules.

    The parser keeps backwards compatibility with the legacy profile output and
    additionally fills the v2 semantic envelope fields.
    """

    trader_code = "trader_d"

    def __init__(self, rules_path: Path | None = None) -> None:
        super().__init__(rules_path=rules_path or Path(__file__).resolve().parent / "parsing_rules.json")

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        global_target_scope = self._resolve_global_target_scope(prepared=prepared)
        base_result = super().parse_message(text=text, context=context)
        base_result = self._postprocess_result(base_result=base_result, text=text, context=context)
        primary_intent = self._derive_primary_intent(
            message_type=base_result.message_type,
            intents=base_result.intents,
        )
        actions_structured = self._build_actions_structured(
            message_type=base_result.message_type,
            intents=base_result.intents,
            entities=base_result.entities,
        )
        linking = self._build_linking(target_refs=base_result.target_refs, context=context, global_target_scope=global_target_scope)
        target_scope = self._build_target_scope(
            target_refs=base_result.target_refs,
            global_target_scope=global_target_scope,
            intents=base_result.intents,
        )

        return TraderParseResult(
            message_type=base_result.message_type,
            intents=base_result.intents,
            entities=base_result.entities,
            target_refs=base_result.target_refs,
            reported_results=base_result.reported_results,
            warnings=base_result.warnings,
            confidence=base_result.confidence,
            primary_intent=primary_intent,
            actions_structured=actions_structured,
            target_scope=target_scope,
            linking=linking,
            diagnostics={
                "parser_version": "trader_d_v2_compatible",
                "warning_count": len(base_result.warnings),
            },
        )

    def _postprocess_result(self, *, base_result: TraderParseResult, text: str, context: ParserContext) -> TraderParseResult:
        raw_text = text or context.raw_text
        normalized = raw_text.lower()
        entities = dict(base_result.entities)
        intents = list(base_result.intents)
        warnings = list(base_result.warnings)
        message_type = base_result.message_type
        target_refs = list(base_result.target_refs)

        inferred_symbol = _extract_symbol_flexible(raw_text)
        if inferred_symbol:
            entities.setdefault("symbol_raw", inferred_symbol["symbol_raw"])
            entities.setdefault("symbol", inferred_symbol["symbol"])

        compact_new_signal = self._is_compact_new_signal(raw_text=raw_text, entities=entities)
        operational_update = self._is_operational_update(raw_text=raw_text, normalized=normalized)

        # Passive BE: position reached breakeven naturally — informational only, no action
        if "остаток ушел в бу" in normalized and "бу+" not in normalized:
            message_type = "INFO_ONLY"
            intents = []
        elif compact_new_signal:
            message_type = "NEW_SIGNAL"
        elif operational_update and message_type in {"UNCLASSIFIED", "INFO_ONLY"}:
            message_type = "UPDATE"

        if message_type == "NEW_SIGNAL":
            intents = ["NS_CREATE_SIGNAL"]
            entities = self._enrich_new_signal_entities(raw_text=raw_text, entities=entities)
        elif message_type == "UPDATE":
            intents, entities = self._enrich_update_intents_entities(raw_text=raw_text, normalized=normalized, intents=intents, entities=entities)
            if not any(ref.get("kind") in {"reply", "telegram_link", "message_id"} for ref in target_refs) and entities.get("symbol"):
                target_refs.append({"kind": "symbol", "ref": entities.get("symbol")})
            if intents and not any(ref.get("kind") in {"reply", "telegram_link", "message_id", "symbol"} for ref in target_refs):
                if "trader_d_update_missing_target" not in warnings:
                    warnings.append("trader_d_update_missing_target")

        confidence = base_result.confidence
        if message_type == "NEW_SIGNAL" and confidence < 0.78:
            confidence = 0.82
        if message_type == "UPDATE" and confidence < 0.62:
            confidence = 0.68
        reported_results = list(base_result.reported_results)
        if not reported_results:
            if entities.get("reported_profit_r") is not None:
                reported_results.append({"value": entities.get("reported_profit_r"), "unit": "R"})
            elif entities.get("reported_profit_percent") is not None:
                reported_results.append({"value": entities.get("reported_profit_percent"), "unit": "PERCENT"})

        return TraderParseResult(
            message_type=message_type,
            intents=_unique(intents),
            entities=entities,
            target_refs=target_refs,
            warnings=_unique(warnings),
            confidence=confidence,
            reported_results=reported_results,
        )

    @staticmethod
    def _derive_primary_intent(*, message_type: str, intents: list[str]) -> str | None:
        if message_type == "NEW_SIGNAL":
            return "NS_CREATE_SIGNAL"
        if message_type == "SETUP_INCOMPLETE":
            return "NS_CREATE_SIGNAL"
        for intent in intents:
            if intent.startswith("U_"):
                return intent
        return None

    @staticmethod
    def _build_actions_structured(*, message_type: str, intents: list[str], entities: dict) -> list[dict]:
        if message_type == "NEW_SIGNAL":
            return [
                {
                    "action": "CREATE_SIGNAL",
                    "instrument": entities.get("symbol"),
                    "side": entities.get("side"),
                    "entries": entities.get("entry", []),
                    "stop_loss": entities.get("stop_loss"),
                    "take_profits": entities.get("take_profits", []),
                }
            ]

        actions: list[dict] = []
        passive_close_status = bool(entities.get("close_status_passive"))
        for intent in intents:
            if not intent_policy_for_intent(intent).get("state_change"):
                continue
            if intent == "U_MOVE_STOP_TO_BE":
                actions.append({"action": "MOVE_STOP", "new_stop_level": "ENTRY"})
            elif intent == "U_MOVE_STOP":
                actions.append({"action": "MOVE_STOP", "new_stop_level": entities.get("new_stop_level")})
            elif intent == "U_CLOSE_FULL":
                if passive_close_status:
                    actions.append({"action": "MARK_POSITION_CLOSED"})
                else:
                    actions.append({"action": "CLOSE_POSITION", "scope": "FULL"})
            elif intent == "U_CANCEL_PENDING_ORDERS":
                actions.append({"action": "CANCEL_PENDING", "scope": "ALL_PENDING_ENTRIES"})
            elif intent == "U_TP_HIT":
                actions.append({"action": "TAKE_PROFIT", "target": "TP"})
            elif intent == "U_STOP_HIT":
                actions.append({"action": "CLOSE_POSITION", "target": "STOP"})
            elif intent == "U_CLOSE_PARTIAL":
                actions.append({"action": "CLOSE_POSITION", "scope": "PARTIAL", "fraction": entities.get("close_fraction")})
            elif intent == "U_EXIT_BE":
                actions.append({"action": "CLOSE_POSITION", "target": "BREAKEVEN"})
            elif intent == "U_UPDATE_TAKE_PROFITS":
                actions.append({"action": "UPDATE_TAKE_PROFITS", "note": entities.get("take_profit_update_note")})
        return actions

    @staticmethod
    def _build_linking(*, target_refs: list[dict], context: ParserContext, global_target_scope: str | None = None) -> dict:
        has_global = global_target_scope is not None
        return {
            "targeted": bool(target_refs or context.reply_to_message_id or has_global),
            "reply_to_message_id": context.reply_to_message_id,
            "target_refs_count": len(target_refs),
            "strategy": "reply_or_link" if (target_refs or context.reply_to_message_id) else ("global_scope" if has_global else "unresolved"),
        }

    def _is_compact_new_signal(self, *, raw_text: str, entities: dict[str, Any]) -> bool:
        has_symbol = bool(entities.get("symbol") or _extract_symbol_flexible(raw_text))
        has_side = bool(_SIDE_RE.search(raw_text))
        lowered = raw_text.lower()
        has_stop = "sl" in lowered or "стоп" in lowered or "сл" in lowered
        has_tp = "tp" in lowered or "тп" in lowered or "тейк" in lowered
        return has_symbol and has_side and has_stop and has_tp

    @staticmethod
    def _is_operational_update(*, raw_text: str, normalized: str) -> bool:
        return bool(_SL_SHORT_FORM_RE.search(raw_text)) or any(
            marker in normalized
            for marker in [
                *_TP_COMPACT_MARKERS,
                *_BE_EXIT_MARKERS,
                *_BE_MOVE_MARKERS,
                *_CLOSE_FULL_MARKERS,
                *_UPDATE_TP_MARKERS,
                "срежем",
                "срезал",
                "фикс 50%",
                "переставляем в +",
            ]
        )

    def _enrich_new_signal_entities(self, *, raw_text: str, entities: dict[str, Any]) -> dict[str, Any]:
        out = dict(entities)
        symbol_info = _extract_symbol_flexible(raw_text)
        if symbol_info:
            out.update(symbol_info)

        side_match = _SIDE_RE.search(raw_text)
        if side_match:
            side_token = side_match.group("side").lower()
            out["side"] = "LONG" if side_token in {"long", "лонг"} else "SHORT"

        limit_entry = _LIMIT_ENTRY_RE.search(raw_text) or _LIMIT_STANDALONE_RE.search(raw_text)
        market_price_match = _ENTRY_MARKET_PRICE_RE.search(raw_text)
        if limit_entry:
            entry = _to_float(limit_entry.group("price"))
            out["entry"] = [entry] if entry is not None else []
            out["entry_order_type"] = "LIMIT"
            out["entry_text_raw"] = limit_entry.group(0)
            out["entry_plan_entries"] = [{"order_type": "LIMIT", "price": entry}]
        elif market_price_match:
            price = _to_float(market_price_match.group("price"))
            out["entry"] = [price] if price is not None else []
            out["entry_order_type"] = "MARKET"
            out["entry_text_raw"] = "MARKET_WITH_INDICATIVE_PRICE"
            out["entry_plan_entries"] = [{"order_type": "MARKET", "price": price}]
        elif any(marker in raw_text.lower() for marker in _ENTRY_MARKET_MARKERS):
            out["entry"] = out.get("entry", [])
            out["entry_order_type"] = "MARKET"
            out["entry_text_raw"] = "MARKET_IMPLICIT"
            out["entry_plan_entries"] = [{"order_type": "MARKET", "price": None}]
        elif not out.get("entry"):
            out["entry_order_type"] = "MARKET"
            out["entry_text_raw"] = "MARKET_IMPLICIT_COMPACT"
            out["entry_plan_entries"] = [{"order_type": "MARKET", "price": None}]

        risk = _RISK_RE.search(raw_text)
        if risk:
            out["risk_value_raw"] = risk.group("value")
            out["risk_value_normalized"] = _to_float(risk.group("value"))
            out["risk_percent"] = out["risk_value_normalized"]

        if out.get("stop_loss") is None:
            stop_loss = _extract_stop_price_flexible(raw_text)
            if stop_loss is not None:
                out["stop_loss"] = stop_loss
        if not out.get("take_profits"):
            take_profits = _extract_take_profits_flexible(raw_text)
            if take_profits:
                out["take_profits"] = take_profits

        if not out.get("entry_plan_type"):
            if out.get("entry_order_type") == "MARKET" and not out.get("entry"):
                out["entry_plan_type"] = "SINGLE_MARKET"
            else:
                out["entry_plan_type"] = "SINGLE"
        out.setdefault("entry_structure", "ONE_SHOT")
        out.setdefault("has_averaging_plan", False)

        return out

    def _enrich_update_intents_entities(
        self,
        *,
        raw_text: str,
        normalized: str,
        intents: list[str],
        entities: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        out_intents = list(intents)
        out_entities = dict(entities)

        hit_targets = sorted({int(m.group("idx")) for m in _TP_HIT_IDX_RE.finditer(raw_text)})
        if hit_targets:
            out_intents.append("U_TP_HIT")
            out_entities["hit_target"] = f"TP{max(hit_targets)}" if len(hit_targets) == 1 else "TP"
            out_entities["hit_targets"] = hit_targets
            out_entities["max_target_hit"] = max(hit_targets)

        # Partial close: "срежем/срезал/еще X%" or "фикс X%" with any percentage
        fiks_match = _FIKS_PCT_RE.search(raw_text)
        if any(marker in normalized for marker in ("срежем", "срезал", "еще 25%")) or fiks_match:
            out_intents.append("U_CLOSE_PARTIAL")
            if fiks_match:
                percent = _to_float(fiks_match.group("value"))
            else:
                percent = _extract_partial_percent(raw_text)
            if percent is not None:
                out_entities["close_fraction_percent"] = percent
                out_entities["close_fraction"] = round(percent / 100.0, 4)
                out_entities["partial_close_percent"] = percent
            out_entities["close_scope"] = "PARTIAL"

        # Remaining position percent: "Остаток/Осталок X% позиции"
        remaining_match = _REMAINING_PCT_RE.search(raw_text)
        if remaining_match:
            remaining_pct = _to_float(remaining_match.group("value"))
            if remaining_pct is not None:
                out_entities["remaining_position_percent"] = remaining_pct

        if any(marker in normalized for marker in (*_BE_MOVE_MARKERS, "перевод в бу")):
            # BE move: only U_MOVE_STOP_TO_BE, NOT U_MOVE_STOP (breakeven is not a numeric stop move)
            out_intents.append("U_MOVE_STOP_TO_BE")
            out_entities.setdefault("new_stop_level", "ENTRY")
            out_entities["new_stop_reference_text"] = "BREAKEVEN"

        if any(marker in normalized for marker in _BE_EXIT_MARKERS):
            out_intents.append("U_EXIT_BE")

        if "переставляем в +" in normalized or "переставляем на" in normalized:
            out_intents.append("U_MOVE_STOP")
            price = _extract_move_stop_price(raw_text)
            if price is not None:
                out_entities["new_stop_level"] = price
                out_entities["new_stop_price"] = price
            elif "в +" in normalized:
                out_entities["new_stop_reference_text"] = "IN_PROFIT"

        if any(marker in normalized for marker in _CLOSE_FULL_MARKERS):
            out_intents.append("U_CLOSE_FULL")
            close_price = _extract_close_price(raw_text)
            if close_price is not None:
                out_entities["close_price"] = close_price

        # SL short form: "Sl -0.5" or "Сл -1.2" — stop loss hit notification
        if not out_intents and _SL_SHORT_FORM_RE.search(raw_text):
            out_intents.append("U_CLOSE_FULL")
            out_entities["close_scope"] = "FULL"
            # Hitting SL = canonical 1R loss
            out_entities["reported_profit_r"] = 1.0

        if any(marker in normalized for marker in _UPDATE_TP_MARKERS):
            out_intents.append("U_UPDATE_TAKE_PROFITS")
            out_entities["take_profit_update_note"] = "FIRST_TP_REMOVED"

        profit_percent = _extract_percent_result(raw_text)
        if profit_percent is None and ("профит" in normalized or "закрыта" in normalized or "закрыта +" in normalized):
            profit_percent = _extract_signed_value(raw_text)
        if profit_percent is not None:
            out_entities["reported_profit_percent"] = profit_percent
        profit_r = _extract_r_result(raw_text)
        if profit_r is not None:
            out_entities["reported_profit_r"] = profit_r

        if "позиция закрыта" in normalized or "сделка закрыта" in normalized:
            out_intents.append("U_CLOSE_FULL")

        # If U_CLOSE_FULL came from TraderB base but close_price not yet extracted, try now
        if "U_CLOSE_FULL" in out_intents and out_entities.get("close_price") is None:
            close_price = _extract_close_price(raw_text)
            if close_price is not None:
                out_entities["close_price"] = close_price

        return _unique(out_intents), out_entities


_SYMBOL_NON_TOKENS = {"TP", "SL", "UPD", "TRADER", "TRADE", "LONG", "SHORT"}


def _extract_symbol_flexible(raw_text: str) -> dict[str, str] | None:
    # Prefer a symbol on a line that also has a side marker (skips prefix tags like "[trader#d]")
    match = _SYMBOL_WITH_SIDE_RE.search(raw_text) or _NAKED_SYMBOL_RE.search(raw_text)
    if not match:
        return None
    token = match.group("symbol").upper()
    if token in _SYMBOL_NON_TOKENS:
        return None
    if token.endswith("USDT"):
        return {"symbol_raw": token, "symbol": token}
    return {"symbol_raw": token, "symbol": f"{token}USDT"}


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_partial_percent(raw_text: str) -> float | None:
    for match in _PARTIAL_PERCENT_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is not None and 0 < value <= 100:
            return value
    return None


def _extract_percent_result(raw_text: str) -> float | None:
    match = _PERCENT_RESULT_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_r_result(raw_text: str) -> float | None:
    match = _R_RESULT_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_move_stop_price(raw_text: str) -> float | None:
    match = re.search(r"на\s+(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_close_price(raw_text: str) -> float | None:
    match = _CLOSE_PRICE_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_signed_value(raw_text: str) -> float | None:
    match = re.search(r"[+](?P<value>\d+(?:[.,]\d+)?)", raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_stop_price_flexible(raw_text: str) -> float | None:
    match = re.search(r"(?:\bsl\b|\bсл\b|стоп(?:\s*лосс)?)\s*[:=]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_take_profits_flexible(raw_text: str) -> list[float]:
    out: list[float] = []
    for match in re.finditer(r"(?:\btp\d*\b|тп\d*\b|тейк(?:\s*\d+)?)\s*[;:=]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE):
        value = _to_float(match.group("value"))
        if value is not None and value not in out:
            out.append(value)
    return out


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
