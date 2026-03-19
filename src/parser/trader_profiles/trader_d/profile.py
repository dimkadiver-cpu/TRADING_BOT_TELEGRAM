"""Trader D profile parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.parser.intent_action_map import intent_policy_for_intent
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser

_NAKED_SYMBOL_RE = re.compile(r"\b\$?(?P<symbol>[A-Za-z]{2,12}(?:USDT)?)\b")
_SIDE_RE = re.compile(r"\b(?P<side>long|short|лонг|шорт)\b", re.IGNORECASE)
_LIMIT_ENTRY_RE = re.compile(r"(?:вход\s+)?(?:лимит|лимт)\s+(?P<price>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_MARKET_ENTRY_RE = re.compile(
    r"(?:вход\s+(?:по\s+рынку|с\s+текущих)|вход\s+рыночн\w*|рыночн\w*)\s*(?P<price>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_REMAINING_PERCENT_RE = re.compile(r"(?:остаток|осталось|remaining)\s+(?P<value>\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_TP_HIT_IDX_RE = re.compile(r"\b(?:tp|тп)\s*(?P<idx>\d)\+?\b", re.IGNORECASE)
_PARTIAL_PERCENT_RE = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_R_RESULT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*[рr]\b", re.IGNORECASE)
_PERCENT_RESULT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_CLOSE_PRICE_RE = re.compile(r"закры\w*\s*(?:полностью)?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RISK_RE = re.compile(r"риск\s*(?P<value>\d+(?:[.,]\d+)?)", re.IGNORECASE)
_ENTRY_MARKET_MARKERS = ("вход с текущих", "вход по рынку", "рыночный")
_TP_COMPACT_MARKERS = ("tp1+", "tp1", "tp 1", "тп1", "тп 1", "tp2", "tp 2", "тп2", "тп 2", "tp3", "tp 3", "тп3", "тп 3")
_BE_EXIT_MARKERS = (
    "позиция ушла в бу",
    "остаток ушел в бу",
    "остаток ушел по бу",
    "остаток позиции ушел в бу",
    "остаток ушел в бу+",
    "ушел в бу+",
    "ушли в бу",
)
_BE_MOVE_MARKERS = (
    "перевод в безубыток",
    "перевод в бу",
    "стоп в бу",
    "переносим в бу",
    "переводим в бу",
    "стоп переводим в бу",
    "стоп в бу+",
    "стоп переставляем в бу",
    "стоп на точку входа",
    "stop to breakeven",
    "stop to entry",
    "в безубыток",
)
_STOP_MOVE_MARKERS = (
    "стоп сдвигаю в +",
    "переносим стоп",
    "переносим на",
    "на уровень",
    "на отмет",
    "update stop",
    "new sl",
    "stop on 1 tp",
    "stop on tp1",
    "стоп на 1 тейк",
    "стоп на первый тейк",
)
_CLOSE_FULL_MARKERS = (
    "закрываем полностью",
    "закрываю по текущим",
    "закрываем по текущим",
    "закрыть по текущим",
    "остаток позиции закрываем по текущим",
    "позиция закрыта",
    "сделка закрыта",
    "полный фикс",
    "не нравится",
)
_UPDATE_TP_MARKERS = ("первый тейк убираем",)
_PASSIVE_CLOSE_STATUS_MARKERS = (
    "позиция закрыта",
    "сделка закрыта",
    "остаток ушел в бу",
    "остаток ушел по бу",
    "остаток позиции ушел в бу",
)


class TraderDProfileParser(TraderBProfileParser):
    """Trader D parser built on top of Trader B deterministic rules.

    The parser keeps backwards compatibility with the legacy profile output and
    additionally fills the v2 semantic envelope fields.
    """

    trader_code = "trader_d"

    def __init__(self, rules_path: Path | None = None) -> None:
        super().__init__(rules_path=rules_path or Path(__file__).resolve().parent / "parsing_rules.json")

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        raw_text = text or context.raw_text
        global_target_scope = self._resolve_global_target_scope(raw_text=raw_text)
        base_result = super().parse_message(text=text, context=context)
        base_result = self._postprocess_result(
            base_result=base_result,
            text=text,
            context=context,
            global_target_scope=global_target_scope,
        )
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

    def _postprocess_result(
        self,
        *,
        base_result: TraderParseResult,
        text: str,
        context: ParserContext,
        global_target_scope: str | None = None,
    ) -> TraderParseResult:
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
        implicit_market_signal = self._looks_like_market_signal(raw_text=raw_text)
        operational_update = self._is_operational_update(raw_text=raw_text, normalized=normalized) or self._looks_like_extra_operational_update(normalized=normalized)
        passive_close_status = self._looks_like_passive_close_status(normalized=normalized)
        passive_be_exit = self._looks_like_passive_be_exit(normalized=normalized)
        if implicit_market_signal:
            message_type = "NEW_SIGNAL"
        if compact_new_signal and (message_type in {"UNCLASSIFIED", "SETUP_INCOMPLETE"} or implicit_market_signal):
            message_type = "NEW_SIGNAL"
        elif operational_update and message_type in {"UNCLASSIFIED", "INFO_ONLY"}:
            message_type = "UPDATE"

        if passive_close_status:
            entities["close_status_passive"] = True
            if message_type == "UNCLASSIFIED":
                message_type = "INFO_ONLY"
        if passive_be_exit and "U_MOVE_STOP_TO_BE" in intents and not self._looks_like_operational_be_move(normalized=normalized):
            intents = [intent for intent in intents if intent not in {"U_MOVE_STOP_TO_BE", "U_MOVE_STOP"}]
        if passive_be_exit and not operational_update and message_type in {"UPDATE", "UNCLASSIFIED"}:
            message_type = "INFO_ONLY"

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
        elif message_type == "INFO_ONLY" and passive_close_status:
            intents = [intent for intent in intents if intent != "U_MOVE_STOP_TO_BE"]

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
                actions.append({"action": "MARK_POSITION_CLOSED"})
            elif intent == "U_UPDATE_TAKE_PROFITS":
                actions.append({"action": "UPDATE_TAKE_PROFITS", "note": entities.get("take_profit_update_note")})
        return actions

    def _build_linking(
        self,
        *,
        target_refs: list[dict],
        context: ParserContext,
        global_target_scope: str | None = None,
    ) -> dict:
        return {
            "targeted": bool(target_refs or context.reply_to_message_id or global_target_scope),
            "reply_to_message_id": context.reply_to_message_id,
            "target_refs_count": len(target_refs),
            "strategy": "reply_or_link"
            if (target_refs or context.reply_to_message_id)
            else ("global_scope" if global_target_scope else "unresolved"),
            "global_target_scope": global_target_scope,
        }

    def _resolve_global_target_scope(
        self,
        *,
        prepared: dict[str, Any] | None = None,
        raw_text: str | None = None,
    ) -> str | None:
        source_text = raw_text
        if source_text is None and isinstance(prepared, dict):
            source_text = str(prepared.get("raw_text") or "")
        normalized = (source_text or "").lower()
        if self._has_global_marker(normalized=normalized, scope_name="ALL_LONGS"):
            return "ALL_LONGS"
        if self._has_global_marker(normalized=normalized, scope_name="ALL_SHORTS"):
            return "ALL_SHORTS"
        if self._has_global_marker(normalized=normalized, scope_name="ALL_ALL"):
            return "ALL_ALL"
        if self._has_global_marker(normalized=normalized, scope_name="ALL_OPEN"):
            return "ALL_OPEN"
        if self._has_global_marker(normalized=normalized, scope_name="ALL_REMAINING"):
            return "ALL_REMAINING"
        return None

    def _has_global_marker(self, *, normalized: str, scope_name: str) -> bool:
        marker_map = self._marker_map("global_target_markers")
        return self._contains_any(normalized, marker_map.get(scope_name, ()))

    def _build_target_scope(
        self,
        *,
        target_refs: list[dict],
        global_target_scope: str | None,
        intents: list[str],
    ) -> dict[str, Any]:
        if global_target_scope in {"ALL_LONGS", "ALL_SHORTS", "ALL_ALL", "ALL_OPEN", "ALL_REMAINING"}:
            return {
                "kind": "portfolio_side",
                "scope": global_target_scope,
                "applies_to_all": True,
                "target_count": len(target_refs),
            }
        if target_refs:
            return {"kind": "signal", "scope": "single", "target_count": len(target_refs)}
        if intents:
            return {"kind": "signal", "scope": "unknown", "target_count": 0}
        return {}

    def _is_compact_new_signal(self, *, raw_text: str, entities: dict[str, Any]) -> bool:
        has_symbol = bool(entities.get("symbol") or _extract_symbol_flexible(raw_text))
        has_side = bool(_SIDE_RE.search(raw_text))
        has_stop = "sl" in raw_text.lower() or "стоп" in raw_text.lower()
        has_tp = "tp" in raw_text.lower() or "тп" in raw_text.lower() or "тейк" in raw_text.lower()
        return has_symbol and has_side and has_stop and has_tp

    @staticmethod
    def _is_operational_update(*, raw_text: str, normalized: str) -> bool:
        return any(
            marker in normalized
            for marker in [
                *_TP_COMPACT_MARKERS,
                *_BE_MOVE_MARKERS,
                *_CLOSE_FULL_MARKERS,
                *_UPDATE_TP_MARKERS,
                "срежем",
                "срезал",
                "фикс 50%",
                "переставляем в +",
                "переносим стоп",
                "update stop",
                "new sl",
                "стоп на 1 тейк",
                "стоп на первый тейк",
                "stop on 1 tp",
                "stop on tp1",
                "стоп сдвигаю в +",
            ]
        )

    @staticmethod
    def _looks_like_extra_operational_update(*, normalized: str) -> bool:
        return bool(
            re.search(r"\b(?:tp|тп)\s*[23]\b", normalized)
            or re.search(r"\bsl\b\s*-\s*\d", normalized)
            or re.search(r"первый фикс", normalized)
            or re.search(r"ушли по стопу в\s*\+\s*\d", normalized)
            or re.search(r"\bстоп\b[\s\.\n-]*-\s*\d", normalized)
            or re.search(r"стоп\s+на\s+1\s+тейк", normalized)
            or re.search(r"stop\s+on\s+1\s+tp", normalized)
            or re.search(r"остаток позиции закрываем по текущим", normalized)
            or re.search(r"закрываем по текущим", normalized)
            or re.search(r"полный фикс", normalized)
            or re.search(r"не нравится", normalized)
        )

    @staticmethod
    def _looks_like_market_signal(*, raw_text: str) -> bool:
        normalized = raw_text.lower()
        return bool(
            _extract_symbol_flexible(raw_text)
            and _SIDE_RE.search(raw_text)
            and ("риск" in normalized)
            and ("стоп" in normalized or "сл" in normalized)
            and ("tp" in normalized or "тп" in normalized or "тейк" in normalized)
            and not re.search(r"\b(?:вход|entry)\b.*\d", normalized)
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

        entry_price, entry_order_type, entry_text_raw = _extract_new_signal_entry(raw_text)
        if entry_price is not None:
            out["entry"] = [entry_price]
        elif not out.get("entry"):
            out["entry"] = []
        if entry_order_type is not None:
            out["entry_order_type"] = entry_order_type
        elif out.get("entry"):
            out["entry_order_type"] = "MARKET"
        else:
            out["entry_order_type"] = "MARKET"
        out["entry_text_raw"] = entry_text_raw or ("MARKET_IMPLICIT" if out.get("entry_order_type") == "MARKET" else "ENTRY_IMPLICIT")

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

        order_type = str(out.get("entry_order_type") or "MARKET").upper()
        primary_entry_price = out["entry"][0] if out.get("entry") and isinstance(out["entry"][0], float) else None
        out["entry_plan_entries"] = [
            {
                "sequence": 1,
                "role": "PRIMARY",
                "order_type": order_type,
                "price": primary_entry_price,
                "raw_label": "ENTRY",
                "source_style": "SINGLE" if primary_entry_price is not None else "MARKET",
                "is_optional": False,
            }
        ]
        out.setdefault("entry_plan_type", "SINGLE_MARKET" if order_type == "MARKET" else "SINGLE_LIMIT")
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

        if any(marker in normalized for marker in ("срежем", "срезал", "фикс 50%", "еще 25%", "ещё 25%")):
            out_intents.append("U_CLOSE_PARTIAL")
            percent = _extract_partial_percent(raw_text)
            if percent is not None:
                out_entities["close_fraction_percent"] = percent
                out_entities["close_fraction"] = round(percent / 100.0, 4)
                out_entities["partial_close_percent"] = percent
            remaining_percent = _extract_remaining_position_percent(raw_text)
            if remaining_percent is not None:
                out_entities["remaining_position_percent"] = remaining_percent
                out_entities["remaining_position_fraction"] = round(remaining_percent / 100.0, 4)
            out_entities["close_scope"] = "PARTIAL"

        has_be_move = self._looks_like_operational_be_move(normalized=normalized)
        has_stop_move = self._looks_like_operational_stop_move(normalized=normalized)
        if has_be_move:
            out_intents.append("U_MOVE_STOP_TO_BE")
            out_entities.setdefault("new_stop_level", "ENTRY")
            out_entities["new_stop_reference_text"] = "BREAKEVEN"
            numeric_stop = _extract_move_stop_price(raw_text)
            if numeric_stop is not None:
                out_entities["new_stop_price"] = numeric_stop
        elif has_stop_move:
            out_intents.append("U_MOVE_STOP")
            price = _extract_move_stop_price(raw_text)
            if price is not None:
                out_entities["new_stop_level"] = price
                out_entities["new_stop_price"] = price
            elif "в +" in normalized:
                out_entities["new_stop_reference_text"] = "IN_PROFIT"
            elif "за указанный минимум" in normalized or "под указанный минимум" in normalized:
                out_entities["stop_reference_text"] = "STRUCTURAL_LEVEL"

        if re.search(r"\bsl\b\s*-\s*\d", normalized):
            out_intents.append("U_STOP_HIT")
        if re.search(r"ушли по стопу в\s*\+\s*\d", normalized) or re.search(r"\bстоп\b[\s\.\n-]*-\s*\d", normalized):
            out_intents.append("U_STOP_HIT")

        if any(marker in normalized for marker in _BE_EXIT_MARKERS):
            out_intents.append("U_EXIT_BE")

        if any(marker in normalized for marker in _CLOSE_FULL_MARKERS):
            out_intents.append("U_CLOSE_FULL")
            out_entities["close_scope"] = "FULL"
            close_price = _extract_close_price(raw_text)
            if close_price is not None:
                out_entities["close_price"] = close_price

        if "фикс" in normalized and "%" in normalized and "полный фикс" not in normalized:
            out_intents.append("U_CLOSE_PARTIAL")
            percent = _extract_partial_percent(raw_text)
            if percent is not None:
                out_entities["close_scope"] = "PARTIAL"
                out_entities["close_fraction_percent"] = percent
                out_entities["close_fraction"] = round(percent / 100.0, 4)
                out_entities["partial_close_percent"] = percent

        if any(marker in normalized for marker in _UPDATE_TP_MARKERS):
            out_intents.append("U_UPDATE_TAKE_PROFITS")
            out_entities["take_profit_update_note"] = "FIRST_TP_REMOVED"
        if "первый фикс" in normalized:
            out_intents.append("U_UPDATE_TAKE_PROFITS")
            out_entities["take_profit_update_note"] = "FIRST_FIX"

        profit_percent = _extract_percent_result(raw_text, normalized)
        if profit_percent is None and ("профит" in normalized or "закрыта" in normalized or "закрыт" in normalized):
            profit_percent = _extract_signed_value(raw_text)
        if profit_percent is not None:
            out_entities["reported_profit_percent"] = profit_percent
        profit_r = _extract_r_result(raw_text)
        if profit_r is not None:
            out_entities["reported_profit_r"] = profit_r

        if re.search(r"\b(?:tp|тп)\s*\d\b", normalized) or ("профит" in normalized and (profit_r is not None or profit_percent is not None)):
            out_intents.append("U_TP_HIT")

        if "позиция закрыта" in normalized or "сделка закрыта" in normalized or "полный фикс" in normalized:
            out_intents.append("U_CLOSE_FULL")
            out_entities.setdefault("close_scope", "FULL")
            if "позиция закрыта" in normalized or "сделка закрыта" in normalized:
                out_entities["close_status_passive"] = True

        return _unique(out_intents), out_entities

    @staticmethod
    def _looks_like_operational_be_move(*, normalized: str) -> bool:
        return bool(any(marker in normalized for marker in _BE_MOVE_MARKERS))

    @staticmethod
    def _looks_like_operational_stop_move(*, normalized: str) -> bool:
        return bool(
            any(marker in normalized for marker in _STOP_MOVE_MARKERS)
            or "переставляем в +" in normalized
            or "переставляем на" in normalized
        )

    @staticmethod
    def _looks_like_passive_be_exit(*, normalized: str) -> bool:
        return bool(
            any(marker in normalized for marker in _BE_EXIT_MARKERS)
            or "закрыта в бу" in normalized
            or "закрыта в безубыток" in normalized
        )

    @staticmethod
    def _looks_like_passive_close_status(*, normalized: str) -> bool:
        return bool(
            any(marker in normalized for marker in _PASSIVE_CLOSE_STATUS_MARKERS)
            or "закрыта в бу" in normalized
            or "закрыта в безубыток" in normalized
        )


def _extract_symbol_flexible(raw_text: str) -> dict[str, str] | None:
    for match in _NAKED_SYMBOL_RE.finditer(raw_text):
        token = match.group("symbol").upper()
        if token in {"TP", "SL", "UPD", "TRADER", "SHORT", "LONG", "FULL", "FIX", "PROFIT", "RISK", "STOP", "ENTRY", "MARKET", "CURRENT", "GUN", "HTTP", "HTTPS", "WWW", "COM", "TELEGRAM", "ME", "RR"}:
            continue
        if token.endswith("USDT"):
            return {"symbol_raw": token, "symbol": token}
        return {"symbol_raw": token, "symbol": f"{token}USDT"}
    return None


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_new_signal_entry(raw_text: str) -> tuple[float | None, str | None, str | None]:
    limit_match = _LIMIT_ENTRY_RE.search(raw_text)
    if limit_match:
        return _to_float(limit_match.group("price")), "LIMIT", limit_match.group(0)
    market_match = _MARKET_ENTRY_RE.search(raw_text)
    if market_match:
        return _to_float(market_match.group("price")), "MARKET", market_match.group(0)
    if any(marker in raw_text.lower() for marker in _ENTRY_MARKET_MARKERS):
        return None, "MARKET", "MARKET_IMPLICIT"
    return None, None, None


def _extract_partial_percent(raw_text: str) -> float | None:
    for match in _PARTIAL_PERCENT_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is not None and 0 < value <= 100:
            return value
    return None


def _extract_remaining_position_percent(raw_text: str) -> float | None:
    match = _REMAINING_PERCENT_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_percent_result(raw_text: str, normalized: str) -> float | None:
    contextual_markers = (
        "профит",
        "результат",
        "итог",
        "итоги",
        "общий профит",
        "pnl",
        "trade result",
        "closed for profit",
        "closed for loss",
        "по сделке",
        "сделка закрыта",
        "позиция закрыта",
    )
    for marker in contextual_markers:
        idx = normalized.find(marker)
        if idx == -1:
            continue
        window = normalized[idx : idx + 80]
        match = re.search(r"[+-]?\d+(?:[.,]\d+)?", window)
        if match:
            return _to_float(match.group(0))
    if re.search(r"[+-]\d+(?:[.,]\d+)?%", normalized) and any(marker in normalized for marker in ("закрыта", "закрыт", "профит", "результат", "итог")):
        match = re.search(r"[+-]?\d+(?:[.,]\d+)?", normalized)
        if match:
            return _to_float(match.group(0))
    return None


def _extract_r_result(raw_text: str) -> float | None:
    match = _R_RESULT_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_move_stop_price(raw_text: str) -> float | None:
    match = re.search(r"на\s+(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    if not match:
        match = re.search(r"в\s*\+\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_close_price(raw_text: str) -> float | None:
    match = _CLOSE_PRICE_RE.search(raw_text)
    if match:
        return _to_float(match.group("value"))
    fallback = re.search(
        r"(?:закрываем(?:\s+позиции)?\s+по\s+текущим|закрываю\s+по\s+текущим|закрыть\s+по\s+текущим)\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
        raw_text,
        re.IGNORECASE,
    )
    return _to_float(fallback.group("value")) if fallback else None


def _extract_signed_value(raw_text: str) -> float | None:
    match = re.search(r"[+]\s*(?P<value>\d+(?:[.,]\d+)?)", raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_stop_price_flexible(raw_text: str) -> float | None:
    match = re.search(r"(?:\bsl\b|\bсл\b|стоп(?:\s*лосс)?)\s*[:=]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_take_profits_flexible(raw_text: str) -> list[float]:
    out: list[float] = []
    pattern = re.compile(
        r"(?:\btp\d+\b|\btp(?=\s)|\bтп\d+\b|\bтп(?=\s)|тейк(?:\s*\d+)?)\s*[:=;]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
        re.IGNORECASE,
    )
    for line in raw_text.splitlines():
        cleaned = line.strip()
        if not cleaned or re.fullmatch(r"(?:tp|тп)\s*\d+\s*", cleaned, re.IGNORECASE):
            continue
        for match in pattern.finditer(cleaned):
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
