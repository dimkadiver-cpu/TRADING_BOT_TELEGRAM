"""Trader D profile parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.parser.canonical_v1.models import (
    CanonicalMessage,
    CloseOperation,
    EntryLeg,
    ModifyTargetsOperation,
    Price,
    RawContext,
    ReportEvent,
    ReportPayload,
    ReportedResult,
    SignalPayload,
    StopLoss,
    StopTarget,
    TakeProfit,
    Targeting,
    TargetRef,
    TargetScope,
    UpdateOperation,
    UpdatePayload,
)
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

    def parse_canonical(self, text: str, context: ParserContext) -> CanonicalMessage:
        """Produce a CanonicalMessage v1 directly without the normalizer."""
        result = self.parse_message(text, context)
        prepared = self._preprocess(text=text, context=context)
        global_target_scope = (
            self._resolve_global_target_scope(prepared=prepared)
            if result.message_type == "UPDATE"
            else None
        )

        message_type = result.message_type
        intents = result.intents
        entities = result.entities
        target_refs = result.target_refs
        warnings = list(result.warnings)
        confidence = result.confidence

        raw_ctx = RawContext(
            raw_text=context.raw_text or "",
            reply_to_message_id=context.reply_to_message_id,
            extracted_links=list(context.extracted_links or []),
            hashtags=list(context.hashtags or []),
            source_chat_id=str(context.channel_id) if context.channel_id else None,
        )
        targeting = _build_td_targeting(message_type, target_refs, global_target_scope, context)
        diag: dict[str, Any] = {"parser_version": "trader_d_v1", "warning_count": len(warnings)}
        primary_intent = self._derive_primary_intent(message_type=message_type, intents=intents)

        if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
            signal = _build_td_signal_payload(entities, message_type, warnings)
            parse_status = "PARSED" if signal.completeness == "COMPLETE" else "PARTIAL"
            return CanonicalMessage(
                parser_profile=context.trader_code,
                primary_class="SIGNAL",
                parse_status=parse_status,
                confidence=confidence,
                intents=intents,
                primary_intent=primary_intent,
                targeting=targeting,
                signal=signal,
                warnings=warnings,
                diagnostics=diag,
                raw_context=raw_ctx,
            )

        if message_type == "INFO_ONLY":
            return CanonicalMessage(
                parser_profile=context.trader_code,
                primary_class="INFO",
                parse_status="PARSED",
                confidence=confidence,
                intents=intents,
                primary_intent=primary_intent,
                targeting=targeting,
                warnings=warnings,
                diagnostics=diag,
                raw_context=raw_ctx,
            )

        if message_type == "UPDATE":
            update_ops = _build_td_update_ops(intents, entities, warnings)
            report_events = _build_td_report_events(intents, entities)
            reported_result = _build_td_reported_result(entities, result.reported_results)
            has_ops = bool(update_ops)
            has_report = bool(report_events) or reported_result is not None
            report_payload = (
                ReportPayload(events=report_events, reported_result=reported_result)
                if has_report
                else None
            )

            if has_ops and has_report:
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="UPDATE",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    update=UpdatePayload(operations=update_ops),
                    report=report_payload,
                    warnings=warnings,
                    diagnostics=diag,
                    raw_context=raw_ctx,
                )
            if has_ops:
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="UPDATE",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    update=UpdatePayload(operations=update_ops),
                    warnings=warnings,
                    diagnostics=diag,
                    raw_context=raw_ctx,
                )
            if has_report:
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="REPORT",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    report=report_payload,
                    warnings=warnings,
                    diagnostics=diag,
                    raw_context=raw_ctx,
                )
            if intents:
                warnings.append("trader_d_update_no_resolvable_ops")
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="UPDATE",
                    parse_status="PARTIAL",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    update=UpdatePayload(operations=[]),
                    warnings=warnings,
                    diagnostics=diag,
                    raw_context=raw_ctx,
                )
            return CanonicalMessage(
                parser_profile=context.trader_code,
                primary_class="INFO",
                parse_status="UNCLASSIFIED",
                confidence=confidence,
                intents=intents,
                primary_intent=primary_intent,
                targeting=targeting,
                warnings=warnings,
                diagnostics=diag,
                raw_context=raw_ctx,
            )

        return CanonicalMessage(
            parser_profile=context.trader_code,
            primary_class="INFO",
            parse_status="UNCLASSIFIED",
            confidence=confidence,
            intents=intents,
            primary_intent=primary_intent,
            targeting=targeting,
            warnings=warnings,
            diagnostics=diag,
            raw_context=raw_ctx,
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
        compact_setup_incomplete = self._is_compact_setup_incomplete(raw_text=raw_text, entities=entities)
        operational_update = self._is_operational_update(raw_text=raw_text, normalized=normalized)

        # Passive BE without target is informational; anchored cases are operational updates.
        if (
            "??????? ???? ? ??" in normalized
            and "??+" not in normalized
            and not any(ref.get("kind") in {"reply", "telegram_link", "message_id"} for ref in target_refs)
        ):
            message_type = "INFO_ONLY"
            intents = []
        elif compact_new_signal:
            message_type = "NEW_SIGNAL"
            intents = ["NS_CREATE_SIGNAL"]
            entities = {}
            warnings = []
        elif compact_setup_incomplete:
            message_type = "SETUP_INCOMPLETE"
            intents = ["NS_CREATE_SIGNAL"]
            entities = {}
            warnings = []
        elif operational_update and message_type in {"UNCLASSIFIED", "INFO_ONLY"}:
            message_type = "UPDATE"

        if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
            intents = ["NS_CREATE_SIGNAL"]
            entities = self._enrich_new_signal_entities(raw_text=raw_text, entities={})
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
        if message_type == "SETUP_INCOMPLETE" and confidence < 0.55:
            confidence = 0.55
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
        if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
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
        has_entry = bool(
            _LIMIT_ENTRY_RE.search(raw_text)
            or _LIMIT_STANDALONE_RE.search(raw_text)
            or _ENTRY_MARKET_PRICE_RE.search(raw_text)
            or any(marker in lowered for marker in _ENTRY_MARKET_MARKERS)
        )
        has_stop = "sl" in lowered or "стоп" in lowered or "сл" in lowered
        has_tp = "tp" in lowered or "тп" in lowered or "тейк" in lowered
        return has_symbol and has_side and has_entry and has_stop and has_tp

    def _is_compact_setup_incomplete(self, *, raw_text: str, entities: dict[str, Any]) -> bool:
        has_symbol = bool(entities.get("symbol") or _extract_symbol_flexible(raw_text))
        has_side = bool(_SIDE_RE.search(raw_text))
        lowered = raw_text.lower()
        has_entry = bool(
            _LIMIT_ENTRY_RE.search(raw_text)
            or _LIMIT_STANDALONE_RE.search(raw_text)
            or _ENTRY_MARKET_PRICE_RE.search(raw_text)
            or any(marker in lowered for marker in _ENTRY_MARKET_MARKERS)
        )
        has_stop = "sl" in lowered or "стоп" in lowered or "сл" in lowered
        has_tp = "tp" in lowered or "тп" in lowered or "тейк" in lowered
        return has_symbol and has_side and not has_entry and has_stop and has_tp

    @staticmethod
    def _is_operational_update(*, raw_text: str, normalized: str) -> bool:
        return (
            _has_short_sl_form(raw_text)
            or _is_brief_be_update(raw_text)
            or _is_tp_hit_shorthand(raw_text)
            or _is_fix_close_shorthand(raw_text)
            or any(
                marker in normalized
                for marker in [
                    *_TP_COMPACT_MARKERS,
                    *_BE_EXIT_MARKERS,
                    *_BE_MOVE_MARKERS,
                    *_CLOSE_FULL_MARKERS,
                    *_UPDATE_TP_MARKERS,
                    "??????",
                    "??????",
                    "???? 50%",
                    "???????????? ? +",
                    "?????? ????",
                    "????",
                ]
            )
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

        if _is_in_profit_stop_move(raw_text):
            out_intents = [intent for intent in out_intents if intent not in {"U_STOP_HIT", "U_CLOSE_FULL"}]
            for stale_key in ("close_scope", "hit_target", "hit_targets", "max_target_hit"):
                out_entities.pop(stale_key, None)

        hit_targets = sorted({int(m.group("idx")) for m in _TP_HIT_IDX_RE.finditer(raw_text)})
        if not hit_targets:
            hit_targets = _extract_brief_tp_hit_indices(raw_text)
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
        elif _is_brief_be_update(raw_text):
            out_intents.append("U_MOVE_STOP_TO_BE")
            out_entities.setdefault("new_stop_level", "ENTRY")
            out_entities["new_stop_reference_text"] = "BREAKEVEN"

        if any(marker in normalized for marker in _BE_EXIT_MARKERS):
            out_intents.append("U_EXIT_BE")

        if _is_in_profit_stop_move(raw_text):
            out_intents.append("U_MOVE_STOP")
            price = _extract_move_stop_price(raw_text)
            if price is not None:
                out_entities["new_stop_level"] = price
                out_entities["new_stop_price"] = price
            else:
                out_entities["new_stop_reference_text"] = "IN_PROFIT"

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
        elif _is_fix_close_shorthand(raw_text) and "U_CLOSE_PARTIAL" not in out_intents:
            out_intents.append("U_CLOSE_FULL")

        # SL short form: "Sl -0.5" or "Сл -1.2" — stop loss hit notification
        if not out_intents and _SL_SHORT_FORM_RE.search(raw_text):
            out_intents.append("U_CLOSE_FULL")
            out_entities["close_scope"] = "FULL"
            # Hitting SL = canonical 1R loss
            out_entities["reported_profit_r"] = 1.0
        elif _has_short_sl_form(raw_text):
            out_intents.append("U_CLOSE_FULL")
            out_entities.setdefault("close_scope", "FULL")
            out_entities.setdefault("reported_profit_r", 1.0)

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
    candidate_text = "\n".join(_content_lines(raw_text)) or raw_text
    # Prefer a symbol on a line that also has a side marker (skips prefix tags like "[trader#d]")
    match = _SYMBOL_WITH_SIDE_RE.search(candidate_text) or _NAKED_SYMBOL_RE.search(candidate_text)
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
    if match:
        return _to_float(match.group("value"))
    standalone = re.search(r"^\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)\s*$", raw_text, re.MULTILINE)
    return _to_float(standalone.group("value")) if standalone else None


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
    pattern = re.compile(
        r"^\s*(?:[^A-Za-z0-9\u0410-\u042f\u0430-\u044f\u0401\u0451]+\s*)?"
        r"(?:"
        r"(?:tp|\u0442\u043f)(?:\s*\d{1,2})?"
        r"|(?:tp|\u0442\u043f)\d{1,2}"
        r"|\u0442\u0435\u0439\u043a(?:\s*\d{1,2})?"
        r")"
        r"(?:\s*[:;=]\s*|\s+)"
        r"(?P<value>\d[\d\s]*(?:[.,]\d+)?)\s*$",
        re.IGNORECASE,
    )
    for line in _content_lines(raw_text):
        match = pattern.match(line.strip())
        if not match:
            continue
        value = _to_float(match.group("value"))
        if value is not None and value not in out:
            out.append(value)
    return out


def _content_lines(raw_text: str) -> list[str]:
    out: list[str] = []
    for line in (raw_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^\s*(?:\[?\s*trader\s*#?\s*d\s*\]?|trader\s*\[\s*#d\s*\])\s*", "", stripped, flags=re.IGNORECASE)
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("http://t.me/") or lowered.startswith("https://t.me/"):
            continue
        compact = re.sub(r"[^a-z0-9#\[\] ]+", "", lowered)
        compact = re.sub(r"\s+", " ", compact).strip()
        if compact in {"trader#d", "[trader#d]", "trader [ #d]", "[trader #d]"}:
            continue
        out.append(stripped)
    return out


def _brief_marker_text(raw_text: str) -> str:
    content = " ".join(_content_lines(raw_text)).lower()
    return re.sub(r"\s+", " ", content).strip()


def _brief_word_tokens(raw_text: str) -> list[str]:
    return re.findall(r"[a-zA-Z\u0430-\u044f\u0410-\u042f\u0451\u0401]+", _brief_marker_text(raw_text))


def _has_short_sl_form(raw_text: str) -> bool:
    return bool(
        re.search(
            r"^\s*(?:upd\s*[:;#.\-]?\s*)?(?:sl|\u0441\u043b)\s*[:.\-]*\s*-?\d+(?:[.,]\d+)?\s*(?:%|[\u0440r])?(?:\b|$)",
            raw_text,
            re.IGNORECASE | re.MULTILINE,
        )
    )


def _is_brief_be_update(raw_text: str) -> bool:
    tokens = _brief_word_tokens(raw_text)
    return tokens in (["\u0431\u0443"], ["upd", "\u0431\u0443"])


def _extract_brief_tp_hit_indices(raw_text: str) -> list[int]:
    content = "\n".join(_content_lines(raw_text))
    match = re.search(
        r"^\s*(?:[?*+-]\s*)?(?:tp|\u0442\u043f)\s*(?P<idx>\d)\b",
        content,
        re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        return []
    return [int(match.group("idx"))]


def _is_tp_hit_shorthand(raw_text: str) -> bool:
    return bool(_extract_brief_tp_hit_indices(raw_text))


def _is_in_profit_stop_move(raw_text: str) -> bool:
    lowered = _brief_marker_text(raw_text)
    return bool(re.search(r"\u0441\u0442\u043e\u043f\s+\w*\s*\u0432\s*\+", lowered))


def _is_fix_close_shorthand(raw_text: str) -> bool:
    lowered = _brief_marker_text(raw_text)
    return "\u0444\u0438\u043a\u0441" in lowered and not re.search(r"\b(?:25|50|70|80|100)\s*%", lowered)


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


# ---------------------------------------------------------------------------
# Canonical v1 builder helpers (module-level, used by parse_canonical)
# ---------------------------------------------------------------------------

def _build_td_targeting(
    message_type: str,
    target_refs: list[dict[str, Any]],
    global_target_scope: str | None,
    context: ParserContext,
) -> Targeting | None:
    if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
        return None

    if global_target_scope:
        if global_target_scope == "ALL_LONGS":
            scope = TargetScope(kind="PORTFOLIO_SIDE", value=global_target_scope, side_filter="LONG", applies_to_all=True)
        elif global_target_scope == "ALL_SHORTS":
            scope = TargetScope(kind="PORTFOLIO_SIDE", value=global_target_scope, side_filter="SHORT", applies_to_all=True)
        else:
            scope = TargetScope(kind="ALL_OPEN", value=global_target_scope, applies_to_all=True)
        return Targeting(refs=[], scope=scope, strategy="GLOBAL_SCOPE", targeted=True)

    refs: list[TargetRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(ref_type: str, value: int | str) -> None:
        key = (ref_type, str(value))
        if key in seen:
            return
        seen.add(key)
        refs.append(TargetRef(ref_type=ref_type, value=value))  # type: ignore[arg-type]

    for item in target_refs:
        kind = str(item.get("kind") or "")
        ref = item.get("ref")
        if kind == "reply" and isinstance(ref, int):
            _add("REPLY", ref)
        elif kind == "telegram_link" and isinstance(ref, str):
            _add("TELEGRAM_LINK", ref)
        elif kind == "message_id" and isinstance(ref, int):
            _add("MESSAGE_ID", ref)
        elif kind == "symbol" and isinstance(ref, str):
            _add("SYMBOL", ref)

    if context.reply_to_message_id is not None:
        _add("REPLY", context.reply_to_message_id)

    if not refs:
        return None

    has_strong = any(r.ref_type in {"REPLY", "TELEGRAM_LINK", "MESSAGE_ID"} for r in refs)
    strategy = "REPLY_OR_LINK" if has_strong else "SYMBOL_MATCH"
    return Targeting(
        refs=refs,
        scope=TargetScope(kind="SINGLE_SIGNAL"),
        strategy=strategy,
        targeted=True,
    )


def _build_td_signal_payload(
    entities: dict[str, Any],
    message_type: str,
    warnings: list[str],
) -> SignalPayload:
    symbol = entities.get("symbol")
    side = entities.get("side")
    order_type = str(entities.get("entry_order_type") or "MARKET").upper()

    entries: list[EntryLeg] = []
    plan_entries = entities.get("entry_plan_entries")
    if isinstance(plan_entries, list) and plan_entries:
        for idx, item in enumerate(plan_entries, start=1):
            if not isinstance(item, dict):
                continue
            price_raw = item.get("price")
            ot = str(item.get("order_type") or order_type).upper()
            price = Price.from_float(float(price_raw)) if isinstance(price_raw, (int, float)) else None
            if ot == "LIMIT" and price is None:
                continue
            entries.append(EntryLeg(
                sequence=idx,
                entry_type=ot,
                price=price,
                role="PRIMARY" if idx == 1 else "AVERAGING",
            ))
    else:
        flat = entities.get("entry") or []
        if order_type == "MARKET":
            price = Price.from_float(float(flat[0])) if flat and isinstance(flat[0], (int, float)) else None
            entries = [EntryLeg(sequence=1, entry_type="MARKET", price=price, role="PRIMARY")]
        else:
            for idx, v in enumerate(flat, start=1):
                if isinstance(v, (int, float)):
                    entries.append(EntryLeg(
                        sequence=idx,
                        entry_type="LIMIT",
                        price=Price.from_float(float(v)),
                        role="PRIMARY" if idx == 1 else "AVERAGING",
                    ))

    stop_val = entities.get("stop_loss")
    stop_loss = StopLoss(price=Price.from_float(float(stop_val))) if isinstance(stop_val, (int, float)) else None

    tps_raw = entities.get("take_profits") or []
    take_profits = [
        TakeProfit(sequence=i + 1, price=Price.from_float(float(v)))
        for i, v in enumerate(tps_raw)
        if isinstance(v, (int, float))
    ]

    entry_structure_raw = str(entities.get("entry_structure") or "").upper()
    if entry_structure_raw in {"ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"}:
        entry_structure: str | None = entry_structure_raw
    elif order_type == "MARKET" or (entries and entries[0].entry_type == "MARKET"):
        entry_structure = "ONE_SHOT"
    elif len(entries) == 1:
        entry_structure = "ONE_SHOT"
    elif len(entries) == 2:
        entry_structure = "TWO_STEP"
    elif len(entries) >= 3:
        entry_structure = "LADDER"
    else:
        entry_structure = None

    # SETUP_INCOMPLETE: signal without an explicit entry price — force INCOMPLETE
    if message_type == "SETUP_INCOMPLETE":
        has_explicit_price = any(e.price is not None for e in entries)
        if not has_explicit_price:
            return SignalPayload(
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                entry_structure=entry_structure,  # type: ignore[arg-type]
                entries=entries,
                stop_loss=stop_loss,
                take_profits=take_profits,
                completeness="INCOMPLETE",
                missing_fields=["entries"],
            )

    missing: list[str] = []
    if not symbol:
        missing.append("symbol")
    if side not in {"LONG", "SHORT"}:
        missing.append("side")
    if not entries:
        missing.append("entries")
    if stop_loss is None:
        missing.append("stop_loss")
    if not take_profits:
        missing.append("take_profits")

    return SignalPayload(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        entry_structure=entry_structure,  # type: ignore[arg-type]
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        completeness="COMPLETE" if not missing else "INCOMPLETE",
        missing_fields=missing,
    )


def _build_td_update_ops(
    intents: list[str],
    entities: dict[str, Any],
    warnings: list[str],
) -> list[UpdateOperation]:
    ops: list[UpdateOperation] = []
    intent_set = set(intents)

    if "U_MOVE_STOP_TO_BE" in intent_set or "U_MOVE_STOP" in intent_set:
        op = _resolve_td_set_stop_op(intent_set, entities, warnings)
        if op is not None:
            ops.append(op)

    # Skip CLOSE FULL when stop hit implies close
    if "U_CLOSE_FULL" in intent_set and "U_STOP_HIT" not in intent_set:
        close_scope = str(entities.get("close_scope") or "FULL")
        close_price_val = entities.get("close_price")
        close_price = Price.from_float(float(close_price_val)) if isinstance(close_price_val, (int, float)) else None
        ops.append(UpdateOperation(op_type="CLOSE", close=CloseOperation(close_scope=close_scope, close_price=close_price)))

    if "U_CLOSE_PARTIAL" in intent_set:
        close_fraction = entities.get("close_fraction")
        if not isinstance(close_fraction, float):
            pct = entities.get("close_fraction_percent")
            close_fraction = round(float(pct) / 100.0, 4) if isinstance(pct, (int, float)) else None
        close_price_val = entities.get("close_price") or entities.get("partial_close_price")
        close_price = Price.from_float(float(close_price_val)) if isinstance(close_price_val, (int, float)) else None
        if close_fraction is not None:
            ops.append(UpdateOperation(
                op_type="CLOSE",
                close=CloseOperation(close_scope="PARTIAL", close_fraction=close_fraction, close_price=close_price),
            ))
        else:
            ops.append(UpdateOperation(op_type="CLOSE", close=CloseOperation(close_scope="PARTIAL")))

    if "U_CANCEL_PENDING_ORDERS" in intent_set:
        cancel_scope = entities.get("cancel_scope")
        from src.parser.canonical_v1.models import CancelPendingOperation
        ops.append(UpdateOperation(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope=str(cancel_scope) if cancel_scope else None),
        ))

    if "U_UPDATE_TAKE_PROFITS" in intent_set:
        tps_raw = entities.get("take_profits")
        if isinstance(tps_raw, list) and tps_raw:
            tp_legs = [
                TakeProfit(sequence=i + 1, price=Price.from_float(float(v)))
                for i, v in enumerate(tps_raw)
                if isinstance(v, (int, float))
            ]
            if tp_legs:
                ops.append(UpdateOperation(
                    op_type="MODIFY_TARGETS",
                    modify_targets=ModifyTargetsOperation(mode="REPLACE_ALL", take_profits=tp_legs),
                ))
            else:
                warnings.append("U_UPDATE_TAKE_PROFITS: no resolvable TP prices")
        else:
            warnings.append("U_UPDATE_TAKE_PROFITS: no resolvable TP prices")

    return ops


def _resolve_td_set_stop_op(
    intent_set: set[str],
    entities: dict[str, Any],
    warnings: list[str],
) -> UpdateOperation | None:
    new_stop_level = entities.get("new_stop_level")

    if isinstance(new_stop_level, (int, float)):
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=StopTarget(target_type="PRICE", value=float(new_stop_level)),
        )
    if new_stop_level == "ENTRY" or "U_MOVE_STOP_TO_BE" in intent_set:
        return UpdateOperation(op_type="SET_STOP", set_stop=StopTarget(target_type="ENTRY"))

    ref_text = entities.get("new_stop_reference_text")
    if ref_text:
        warnings.append(f"trader_d_stop_structural_reference_no_price: {ref_text!r}")
        return None

    warnings.append("trader_d_move_stop_unresolvable: no price found")
    return None


def _build_td_report_events(
    intents: list[str],
    entities: dict[str, Any],
) -> list[ReportEvent]:
    events: list[ReportEvent] = []
    reported_result = _build_td_reported_result(entities, [])

    for intent in intents:
        if intent == "U_TP_HIT":
            hit_target = entities.get("hit_target")
            level: int | None = None
            if isinstance(hit_target, str) and hit_target.startswith("TP"):
                try:
                    level = int(hit_target[2:])
                except ValueError:
                    pass
            events.append(ReportEvent(event_type="TP_HIT", level=level, result=reported_result))
        elif intent == "U_STOP_HIT":
            events.append(ReportEvent(event_type="STOP_HIT", result=reported_result))
        elif intent == "U_EXIT_BE":
            events.append(ReportEvent(event_type="BREAKEVEN_EXIT", result=reported_result))

    return events


def _build_td_reported_result(
    entities: dict[str, Any],
    reported_results: list[Any],
) -> ReportedResult | None:
    profit_r = entities.get("reported_profit_r")
    if isinstance(profit_r, (int, float)):
        return ReportedResult(value=float(profit_r), unit="R")

    profit_pct = entities.get("reported_profit_percent")
    if isinstance(profit_pct, (int, float)):
        return ReportedResult(value=float(profit_pct), unit="PERCENT")

    if reported_results:
        first = reported_results[0]
        if isinstance(first, dict):
            val = first.get("value")
            unit = str(first.get("unit") or "UNKNOWN")
            if isinstance(val, (int, float)):
                return ReportedResult(value=float(val), unit=unit)  # type: ignore[arg-type]

    return None
