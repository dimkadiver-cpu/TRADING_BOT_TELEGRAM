"""Trader 3 deterministic profile parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.parser.canonical_v1.models import (
    CanonicalMessage,
    CloseOperation,
    EntryLeg,
    ModifyEntriesOperation,
    Price,
    RawContext,
    ReportEvent,
    ReportPayload,
    ReportedResult,
    SignalPayload,
    StopLoss,
    TakeProfit,
    Targeting,
    TargetRef,
    TargetScope,
    UpdateOperation,
    UpdatePayload,
)
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"

_SIGNAL_ID_RE = re.compile(r"\bSIGNAL\s*ID\s*:\s*#?\s*(?P<id>\d+)\b", re.IGNORECASE)
_COIN_LINE_RE = re.compile(r"\bCOIN\s*:\s*(?P<coin>[^\n]+)", re.IGNORECASE)
_SYMBOL_IN_COIN_RE = re.compile(r"\$?(?P<base>[A-Z0-9]{2,20})\s*/\s*(?P<quote>[A-Z0-9]{2,10})", re.IGNORECASE)
_SYMBOL_FALLBACK_RE = re.compile(r"\$(?P<base>[A-Z0-9]{2,20})\b", re.IGNORECASE)
_DIRECTION_RE = re.compile(r"\bDIRECTION?\s*:\s*(?P<side>LONG|SHORT)\b", re.IGNORECASE)
_ENTRY_LINE_RE = re.compile(r"\bENTRY\s*:\s*(?P<value>[^\n]+)", re.IGNORECASE)
_TARGETS_BLOCK_RE = re.compile(r"\bTARGETS?\s*:\s*(?P<value>.+?)(?=\n\s*(?:STOP\s*LOSS|SL)\s*:|\Z)", re.IGNORECASE | re.DOTALL)
_STOP_RE = re.compile(r"\b(?:STOP\s*LOSS|SL)\s*:\s*(?P<value>\d[\d\s,]*(?:\.\d+)?)", re.IGNORECASE)
_TARGET_HIT_RE = re.compile(r"\bTarget\s*(?P<idx>\d+)\s*:\s*(?P<price>\d[\d\s,]*(?:\.\d+)?)\s*✅", re.IGNORECASE)
_PROFIT_BLOCK_RE = re.compile(r"(?P<percent>\d+(?:\.\d+)?)%\s*Profit\s*\((?P<lev>\d+(?:\.\d+)?)x\)", re.IGNORECASE)
_LOSS_BLOCK_RE = re.compile(r"(?P<percent>\d+(?:\.\d+)?)%\s*Loss\s*\((?P<lev>\d+(?:\.\d+)?)x\)", re.IGNORECASE)
_CLOSED_MANUALLY_RE = re.compile(r"\bClosed\s+Manually\b", re.IGNORECASE)
_REENTER_RE = re.compile(r"\bRe-?\s*Enter\.?\b", re.IGNORECASE)
_SAME_SETUP_NOTE_RE = re.compile(r"Same\s+Entry\s+level\s*,?\s*Targets\s*&\s*SL", re.IGNORECASE)
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)


class Trader3ProfileParser(TraderBProfileParser):
    """Deterministic parser for trader 3 regular message patterns."""

    trader_code = "trader_3"

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        message_type = self._classify_message(prepared=prepared)
        entities = self._extract_entities(prepared=prepared, message_type=message_type)
        intents = self._extract_intents(message_type=message_type, entities=entities)
        target_refs = self._extract_targets(prepared=prepared, context=context, entities=entities)
        warnings = self._build_warnings(
            message_type=message_type,
            entities=entities,
            target_refs=target_refs,
            context=context,
            intents=intents,
        )
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)

        primary_intent = self._derive_primary_intent(message_type=message_type, entities=entities)
        actions_structured = self._build_actions_structured(message_type=message_type, entities=entities)
        linking = self._build_linking(target_refs=target_refs, context=context, entities=entities)
        target_scope = {"kind": "signal", "scope": "single" if linking["targeted"] else "unknown"}

        return TraderParseResult(
            message_type=message_type,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            warnings=warnings,
            confidence=confidence,
            primary_intent=primary_intent,
            actions_structured=actions_structured,
            target_scope=target_scope,
            linking=linking,
            diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
        )

    # ------------------------------------------------------------------
    # Canonical v1 native output (Phase 8)
    # ------------------------------------------------------------------

    def parse_canonical(self, text: str, context: ParserContext) -> CanonicalMessage:
        """Produce a CanonicalMessage v1 directly without the normalizer."""
        prepared = self._preprocess(text=text, context=context)
        message_type = self._classify_message(prepared=prepared)
        entities = self._extract_entities(prepared=prepared, message_type=message_type)
        intents = self._extract_intents(message_type=message_type, entities=entities)
        target_refs = self._extract_targets(prepared=prepared, context=context, entities=entities)
        warnings: list[str] = list(
            self._build_warnings(
                message_type=message_type,
                entities=entities,
                target_refs=target_refs,
                context=context,
                intents=intents,
            )
        )
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)
        primary_intent = self._derive_primary_intent(message_type=message_type, entities=entities)

        raw_ctx = RawContext(
            raw_text=context.raw_text or "",
            reply_to_message_id=context.reply_to_message_id,
            extracted_links=list(context.extracted_links or []),
            hashtags=list(context.hashtags or []),
            source_chat_id=str(context.channel_id) if context.channel_id else None,
        )
        targeting = _build_t3_targeting(message_type, target_refs, context)

        if message_type == "NEW_SIGNAL":
            signal = _build_t3_signal_payload(entities, warnings)
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
                diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
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
                diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
                raw_context=raw_ctx,
            )

        if message_type == "UPDATE":
            intent_set = set(intents)
            has_update_intent = bool(intent_set & {"U_CLOSE_FULL", "U_REENTER"})
            has_report_intent = bool(intent_set & {"U_TP_HIT", "U_STOP_HIT"})

            if has_update_intent:
                ops = _build_t3_update_ops(intents, entities, warnings)
                report = _build_t3_report_payload(intents, entities) if has_report_intent else None
                parse_status = "PARSED" if ops else "PARTIAL"
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="UPDATE",
                    parse_status=parse_status,
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    update=UpdatePayload(operations=ops),
                    report=report,
                    warnings=warnings,
                    diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
                    raw_context=raw_ctx,
                )

            if has_report_intent:
                report = _build_t3_report_payload(intents, entities)
                parse_status = (
                    "PARSED"
                    if report is not None and (report.events or report.reported_result is not None)
                    else "PARTIAL"
                )
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="REPORT",
                    parse_status=parse_status,
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    report=report,
                    warnings=warnings,
                    diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
                    raw_context=raw_ctx,
                )

            # UPDATE with no recognised intents — check for bare reported results
            reported_loss = entities.get("reported_loss_percent")
            reported_profit = entities.get("reported_profit_percent")
            if reported_loss is not None or reported_profit is not None:
                value = reported_loss if reported_loss is not None else reported_profit
                report = ReportPayload(
                    events=[],
                    reported_result=ReportedResult(value=value, unit="PERCENT"),
                )
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="REPORT",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    report=report,
                    warnings=warnings,
                    diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
                    raw_context=raw_ctx,
                )

            # Truly unresolvable update
            warnings.append("trader_3_update_no_intents_no_data")
            return CanonicalMessage(
                parser_profile=context.trader_code,
                primary_class="INFO",
                parse_status="UNCLASSIFIED",
                confidence=confidence,
                intents=intents,
                primary_intent=primary_intent,
                targeting=targeting,
                warnings=warnings,
                diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
                raw_context=raw_ctx,
            )

        # UNCLASSIFIED
        return CanonicalMessage(
            parser_profile=context.trader_code,
            primary_class="INFO",
            parse_status="UNCLASSIFIED",
            confidence=confidence,
            intents=intents,
            primary_intent=primary_intent,
            targeting=targeting,
            warnings=warnings,
            diagnostics={"parser_version": "trader_3_v1", "warning_count": len(warnings)},
            raw_context=raw_ctx,
        )

    def _preprocess(self, *, text: str, context: ParserContext) -> dict[str, Any]:
        raw_text = text or context.raw_text
        return {"raw_text": raw_text, "normalized_text": normalize_text(raw_text)}

    def _classify_message(self, *, prepared: dict[str, Any]) -> str:
        raw_text = str(prepared.get("raw_text") or "")
        normalized = str(prepared.get("normalized_text") or "")

        if self._contains_any(normalized, self._as_markers("classification_markers", "info_only")):
            return "INFO_ONLY"

        has_signal_id = _extract_signal_id(raw_text) is not None
        has_coin = _extract_symbol_bundle(raw_text).get("symbol") is not None
        has_side = _extract_side(raw_text) is not None
        has_entry = _extract_entry_range(raw_text) is not None
        has_targets = bool(_extract_take_profits(raw_text)[0]) or bool(_TARGETS_BLOCK_RE.search(raw_text))
        has_stop = _extract_stop(raw_text)[1] is not None

        if has_signal_id and has_coin and has_side and has_entry and has_targets and has_stop:
            return "NEW_SIGNAL"

        has_tp_hit = bool(_TARGET_HIT_RE.search(raw_text))
        has_manual_close = bool(_CLOSED_MANUALLY_RE.search(raw_text))
        has_reenter = bool(_REENTER_RE.search(raw_text))
        has_loss = bool(_LOSS_BLOCK_RE.search(raw_text))
        if has_tp_hit or has_manual_close or has_reenter or has_loss:
            return "UPDATE"

        return "UNCLASSIFIED"

    def _extract_entities(self, *, prepared: dict[str, Any], message_type: str) -> dict[str, Any]:
        raw_text = str(prepared.get("raw_text") or "")
        entities: dict[str, Any] = {}

        signal_id = _extract_signal_id(raw_text)
        if signal_id is not None:
            entities["signal_id"] = signal_id

        entities.update(_extract_symbol_bundle(raw_text))

        if message_type == "NEW_SIGNAL":
            entry_range = _extract_entry_range(raw_text)
            stop_loss_raw, stop_loss = _extract_stop(raw_text)
            take_profits, targets_raw = _extract_take_profits(raw_text)
            if entry_range is not None:
                low, high = entry_range
                entities["entry_range_low"] = low
                entities["entry_range_high"] = high
                entities["entry"] = [low, high]
            entities.update(
                {
                    "side": _extract_side(raw_text),
                    "leverage_hint_raw": _extract_leverage_hint(raw_text),
                    "entry_text_raw": _extract_entry_line(raw_text),
                    "targets_text_raw": targets_raw,
                    "stop_loss_raw": stop_loss_raw,
                    "stop_loss": stop_loss,
                    "take_profits": take_profits,
                    "entry_plan_type": "SINGLE",
                    "entry_structure": "RANGE",
                    "has_averaging_plan": False,
                }
            )

        if message_type == "UPDATE":
            hit_targets = sorted({int(match.group("idx")) for match in _TARGET_HIT_RE.finditer(raw_text)})
            if hit_targets:
                entities["hit_targets"] = hit_targets
                entities["max_target_hit"] = max(hit_targets)

            profit_match = _PROFIT_BLOCK_RE.search(raw_text)
            if profit_match:
                entities["reported_profit_percent"] = _to_float(profit_match.group("percent"))
                entities["reported_leverage_hint"] = _to_float(profit_match.group("lev"))

            loss_match = _LOSS_BLOCK_RE.search(raw_text)
            if loss_match:
                entities["reported_loss_percent"] = _to_float(loss_match.group("percent"))
                entities["reported_leverage_hint"] = _to_float(loss_match.group("lev"))

            stop_loss_raw, stop_loss = _extract_stop(raw_text)
            if stop_loss_raw is not None:
                entities["stop_loss_raw"] = stop_loss_raw
            if stop_loss is not None:
                entities["stop_price"] = stop_loss

            if _CLOSED_MANUALLY_RE.search(raw_text):
                entities["manual_close"] = True
            if _REENTER_RE.search(raw_text):
                entities["reenter"] = True
                if _SAME_SETUP_NOTE_RE.search(raw_text):
                    entities["reenter_note"] = "Same Entry level, Targets & SL"

        return {k: v for k, v in entities.items() if v is not None}

    def _extract_intents(self, *, message_type: str, entities: dict[str, Any]) -> list[str]:
        if message_type == "NEW_SIGNAL":
            return ["NS_CREATE_SIGNAL"]
        if message_type != "UPDATE":
            return []

        intents: list[str] = []
        if entities.get("manual_close"):
            intents.append("U_CLOSE_FULL")
        if entities.get("reenter"):
            intents.append("U_REENTER")
        if entities.get("hit_targets"):
            intents.append("U_TP_HIT")
        if _is_explicit_stop_hit(entities):
            intents.append("U_STOP_HIT")
        return intents

    def _extract_targets(
        self,
        *,
        prepared: dict[str, Any],
        context: ParserContext,
        entities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _append(kind: str, ref: object) -> None:
            key = (kind, str(ref))
            if key in seen:
                return
            seen.add(key)
            out.append({"kind": kind, "ref": ref})

        signal_id = entities.get("signal_id")
        if signal_id is not None:
            _append("signal_id", int(signal_id))

        if context.reply_to_message_id is not None:
            _append("reply", int(context.reply_to_message_id))

        raw_text = str(prepared.get("raw_text") or "")
        for link in [*context.extracted_links, *extract_telegram_links(raw_text)]:
            _append("telegram_link", link)
            match = _LINK_ID_RE.search(link)
            if match:
                _append("message_id", int(match.group("id")))

        symbol = entities.get("symbol")
        if symbol and not out:
            _append("symbol", str(symbol))

        return out

    def _build_warnings(
        self,
        *,
        message_type: str,
        entities: dict[str, Any],
        target_refs: list[dict[str, Any]],
        context: ParserContext,
        intents: list[str],
    ) -> list[str]:
        warnings: list[str] = []
        if message_type == "NEW_SIGNAL":
            if entities.get("signal_id") is None:
                warnings.append("trader_3_new_signal_missing_signal_id")
            if entities.get("entry_range_low") is None or entities.get("entry_range_high") is None:
                warnings.append("trader_3_new_signal_missing_entry")
            if not entities.get("take_profits"):
                warnings.append("trader_3_new_signal_missing_take_profits")
            if entities.get("stop_loss") is None:
                warnings.append("trader_3_new_signal_missing_stop_loss")
            return warnings

        if message_type == "UPDATE" and intents:
            has_strong_target = any(ref.get("kind") in {"signal_id", "reply", "telegram_link", "message_id"} for ref in target_refs)
            if not has_strong_target and context.reply_to_message_id is None:
                warnings.append("trader_3_update_missing_target")
        return warnings

    @staticmethod
    def _estimate_confidence(*, message_type: str, warnings: list[str]) -> float:
        if message_type == "NEW_SIGNAL":
            return 0.9 if not warnings else 0.78
        if message_type == "UPDATE":
            return 0.82 if not warnings else 0.6
        if message_type == "INFO_ONLY":
            return 0.75
        return 0.25

    @staticmethod
    def _derive_primary_intent(*, message_type: str, entities: dict[str, Any]) -> str | None:
        if message_type == "NEW_SIGNAL":
            return "OPEN_POSITION"
        if message_type == "INFO_ONLY":
            return "MARKET_COMMENTARY"
        if message_type != "UPDATE":
            return None
        if entities.get("reenter"):
            return "REENTER_POSITION"
        if entities.get("manual_close"):
            return "CLOSE_POSITION"
        if entities.get("reported_loss_percent") is not None:
            return "REPORT_LOSS"
        if entities.get("hit_targets"):
            return "REPORT_PROFIT"
        return None

    @staticmethod
    def _build_actions_structured(*, message_type: str, entities: dict[str, Any]) -> list[dict[str, Any]]:
        if message_type == "NEW_SIGNAL":
            return [
                {
                    "action": "OPEN_POSITION",
                    "signal_id": entities.get("signal_id"),
                    "instrument": entities.get("symbol"),
                    "side": entities.get("side"),
                    "entry_range": [entities.get("entry_range_low"), entities.get("entry_range_high")],
                    "stop_loss": entities.get("stop_loss"),
                    "take_profits": entities.get("take_profits", []),
                }
            ]
        if message_type == "INFO_ONLY":
            return [{"action": "MARKET_COMMENTARY"}]

        actions: list[dict[str, Any]] = []
        if entities.get("hit_targets"):
            actions.append(
                {
                    "action": "REPORT_PROFIT",
                    "signal_id": entities.get("signal_id"),
                    "hit_targets": entities.get("hit_targets", []),
                    "max_target_hit": entities.get("max_target_hit"),
                    "reported_profit_percent": entities.get("reported_profit_percent"),
                }
            )
        if entities.get("reported_loss_percent") is not None:
            actions.append(
                {
                    "action": "REPORT_LOSS",
                    "signal_id": entities.get("signal_id"),
                    "reported_loss_percent": entities.get("reported_loss_percent"),
                    "stop_price": entities.get("stop_price"),
                    "close_in_loss": True,
                }
            )
        if entities.get("manual_close"):
            actions.append({"action": "CLOSE_POSITION", "signal_id": entities.get("signal_id"), "manual": True})
        if entities.get("reenter"):
            actions.append(
                {
                    "action": "REENTER_POSITION",
                    "signal_id": entities.get("signal_id"),
                    "note": entities.get("reenter_note"),
                }
            )
        return actions

    @staticmethod
    def _build_linking(*, target_refs: list[dict[str, Any]], context: ParserContext, entities: dict[str, Any]) -> dict[str, Any]:
        has_signal_id = any(ref.get("kind") == "signal_id" for ref in target_refs)
        has_reply = context.reply_to_message_id is not None or any(ref.get("kind") == "reply" for ref in target_refs)
        has_link = any(ref.get("kind") in {"telegram_link", "message_id"} for ref in target_refs)
        has_symbol_fallback = any(ref.get("kind") == "symbol" for ref in target_refs)

        if has_signal_id:
            strategy = "signal_id"
        elif has_reply:
            strategy = "reply"
        elif has_link:
            strategy = "telegram_link"
        elif has_symbol_fallback:
            strategy = "symbol_fallback"
        else:
            strategy = "unresolved"

        return {
            "targeted": bool(target_refs),
            "signal_id": entities.get("signal_id"),
            "reply_to_message_id": context.reply_to_message_id,
            "target_refs_count": len(target_refs),
            "strategy": strategy,
        }


def _extract_signal_id(raw_text: str) -> int | None:
    match = _SIGNAL_ID_RE.search(raw_text)
    if not match:
        return None
    try:
        return int(match.group("id"))
    except ValueError:
        return None


def _extract_symbol_bundle(raw_text: str) -> dict[str, Any]:
    coin_match = _COIN_LINE_RE.search(raw_text)
    if not coin_match:
        return {}

    symbol_raw = coin_match.group("coin").strip()
    pair_match = _SYMBOL_IN_COIN_RE.search(symbol_raw)
    if pair_match:
        base = pair_match.group("base").upper()
        quote = pair_match.group("quote").upper()
        return {
            "symbol_raw": symbol_raw,
            "symbol": f"{base}{quote}",
            "base_asset": base,
            "quote_asset": quote,
            "instrument": f"{base}{quote}",
        }

    fallback = _SYMBOL_FALLBACK_RE.search(symbol_raw)
    if not fallback:
        return {"symbol_raw": symbol_raw}

    base = fallback.group("base").upper()
    return {
        "symbol_raw": symbol_raw,
        "symbol": base,
        "base_asset": base,
        "quote_asset": None,
        "instrument": base,
    }


def _extract_side(raw_text: str) -> str | None:
    match = _DIRECTION_RE.search(raw_text)
    if not match:
        return None
    return str(match.group("side")).upper()


def _extract_entry_line(raw_text: str) -> str | None:
    match = _ENTRY_LINE_RE.search(raw_text)
    return match.group("value").strip() if match else None


def _extract_entry_range(raw_text: str) -> tuple[float, float] | None:
    line = _extract_entry_line(raw_text)
    if not line:
        return None

    normalized = _normalize_decimal_spaces(_normalize_dash(line))
    match = re.search(r"(?P<low>\d[\d,]*(?:\.\d+)?)\s*-\s*(?P<high>\d[\d,]*(?:\.\d+)?)", normalized)
    if not match:
        return None

    low = _to_float(match.group("low"))
    high = _to_float(match.group("high"))
    if low is None or high is None:
        return None

    return (low, high)


def _extract_take_profits(raw_text: str) -> tuple[list[float], str | None]:
    block_match = _TARGETS_BLOCK_RE.search(raw_text)
    block_raw = block_match.group("value").strip() if block_match else None
    search_area = block_raw or raw_text

    out: list[float] = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", search_area):
        value = _to_float(raw)
        if value is not None and value not in out:
            out.append(value)
    return out, block_raw


def _extract_stop(raw_text: str) -> tuple[str | None, float | None]:
    match = _STOP_RE.search(raw_text)
    if not match:
        return None, None
    raw_value = match.group("value").strip()
    return raw_value, _to_float(raw_value)


def _extract_leverage_hint(raw_text: str) -> str | None:
    profit = _PROFIT_BLOCK_RE.search(raw_text)
    if profit:
        return f"{profit.group('lev')}x"
    loss = _LOSS_BLOCK_RE.search(raw_text)
    if loss:
        return f"{loss.group('lev')}x"
    return None


def _is_explicit_stop_hit(entities: dict[str, Any]) -> bool:
    return entities.get("stop_price") is not None and not entities.get("manual_close")


def _normalize_decimal_spaces(value: str) -> str:
    previous = value
    while True:
        updated = re.sub(r"(?P<int>\d)\.\s+(?P<frac>\d)", r"\g<int>.\g<frac>", previous)
        if updated == previous:
            return updated
        previous = updated


def _normalize_dash(value: str) -> str:
    return value.replace("–", "-").replace("—", "-").replace("−", "-")


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace(" ", "")
    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        if re.match(r"^\d{1,3}(,\d{3})+$", cleaned):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Canonical v1 builder helpers (module-level, used by parse_canonical)
# ---------------------------------------------------------------------------

def _build_t3_targeting(
    message_type: str,
    target_refs: list[dict[str, Any]],
    context: ParserContext,
) -> Targeting | None:
    if message_type == "NEW_SIGNAL":
        return None

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
        if kind == "signal_id" and isinstance(ref, int):
            _add("EXPLICIT_ID", ref)
        elif kind == "reply" and isinstance(ref, int):
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

    has_strong = any(r.ref_type in {"EXPLICIT_ID", "REPLY", "TELEGRAM_LINK", "MESSAGE_ID"} for r in refs)
    strategy = "REPLY_OR_LINK" if has_strong else "SYMBOL_MATCH"

    return Targeting(
        refs=refs,
        scope=TargetScope(kind="SINGLE_SIGNAL"),
        strategy=strategy,
        targeted=True,
    )


def _build_t3_signal_payload(entities: dict[str, Any], warnings: list[str]) -> SignalPayload:
    symbol = entities.get("symbol") or None
    side = entities.get("side") or None
    low: float | None = entities.get("entry_range_low")
    high: float | None = entities.get("entry_range_high")
    stop_val: float | None = entities.get("stop_loss")
    tps_raw: list[float] = [v for v in (entities.get("take_profits") or []) if isinstance(v, (int, float))]

    entries: list[EntryLeg] = []
    if low is not None and high is not None:
        entries = [
            EntryLeg(sequence=1, entry_type="LIMIT", price=Price.from_float(low), role="PRIMARY"),
            EntryLeg(sequence=2, entry_type="LIMIT", price=Price.from_float(high), role="AVERAGING"),
        ]

    stop_loss = StopLoss(price=Price.from_float(stop_val)) if stop_val is not None else None
    take_profits = [
        TakeProfit(sequence=i + 1, price=Price.from_float(float(v)))
        for i, v in enumerate(tps_raw)
    ]

    missing: list[str] = []
    if not symbol:
        missing.append("symbol")
    if not side:
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
        entry_structure="RANGE" if entries else None,
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        completeness="COMPLETE" if not missing else "INCOMPLETE",
        missing_fields=missing,
    )


def _build_t3_update_ops(
    intents: list[str],
    entities: dict[str, Any],
    warnings: list[str],
) -> list[UpdateOperation]:
    ops: list[UpdateOperation] = []
    for intent in intents:
        if intent == "U_CLOSE_FULL":
            ops.append(UpdateOperation(op_type="CLOSE", close=CloseOperation(close_scope="FULL")))
        elif intent == "U_REENTER":
            # Trader 3 reenter notes "same entry level" — no explicit new prices
            warnings.append("trader_3_reenter_no_explicit_entry_prices")
    return ops


def _build_t3_report_payload(
    intents: list[str],
    entities: dict[str, Any],
) -> ReportPayload | None:
    events: list[ReportEvent] = []
    for intent in intents:
        if intent == "U_TP_HIT":
            max_hit: int | None = entities.get("max_target_hit")
            profit_pct: float | None = entities.get("reported_profit_percent")
            result = ReportedResult(value=profit_pct, unit="PERCENT") if profit_pct is not None else None
            events.append(ReportEvent(event_type="TP_HIT", level=max_hit, result=result))
        elif intent == "U_STOP_HIT":
            loss_pct: float | None = entities.get("reported_loss_percent")
            result = ReportedResult(value=loss_pct, unit="PERCENT") if loss_pct is not None else None
            events.append(ReportEvent(event_type="STOP_HIT", result=result))
    if not events:
        return None
    return ReportPayload(events=events)


__all__ = ["Trader3ProfileParser"]
