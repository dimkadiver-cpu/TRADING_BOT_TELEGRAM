"""Trader 3 deterministic profile parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

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
_ENTRY_MARKET_MARKERS = (
    "вход с текущих",
    "(вход с текущих)",
    "a (с текущих)",
    "market entry",
    "entry from market",
)
_ENTRY_LIMIT_MARKERS = (
    "вход лимиткой",
    "вход лимитным ордером",
    "лимитным ордером",
    "limit entry",
    "a (лимит)",
    "b (лимит)",
    "buy zone",
    "sell zone",
)
_ENTRY_AVERAGING_MARKERS = (
    "усреднение",
    "entry b",
    "вход b",
    "b (усреднение)",
    "b (лимит)",
)
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
        entities = self._extract_entities(prepared=prepared, message_type=message_type, context=context)
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
        normalized = str(prepared.get("normalized_text") or "")
        has_entry = bool(_extract_entry_values(raw_text)) or any(
            marker in normalized for marker in (*_ENTRY_MARKET_MARKERS, *_ENTRY_LIMIT_MARKERS, *_ENTRY_AVERAGING_MARKERS)
        )
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

    def _extract_entities(self, *, prepared: dict[str, Any], message_type: str, context: ParserContext) -> dict[str, Any]:
        raw_text = str(prepared.get("raw_text") or "")
        entities: dict[str, Any] = {}

        signal_id = _extract_signal_id(raw_text)
        if signal_id is not None:
            entities["signal_id"] = signal_id

        entities.update(_extract_symbol_bundle(raw_text))

        if message_type == "NEW_SIGNAL":
            entry_values = _extract_entry_values(raw_text)
            stop_loss_raw, stop_loss = _extract_stop(raw_text)
            take_profits, targets_raw = _extract_take_profits(raw_text)
            entry_order_type = _infer_entry_order_type(raw_text)
            if entry_values:
                low, high = entry_values[0], entry_values[-1]
                entities["entry_range_low"] = low
                entities["entry_range_high"] = high
                entities["entry"] = list(entry_values)
                entities["entry_plan_entries"] = _build_entry_plan_entries(entry_values, entry_order_type=entry_order_type)
            elif any(marker in normalize_text(raw_text) for marker in (*_ENTRY_MARKET_MARKERS, *_ENTRY_LIMIT_MARKERS, *_ENTRY_AVERAGING_MARKERS)):
                entities["entry"] = []
                entities["entry_plan_entries"] = [
                    {
                        "sequence": 1,
                        "role": "PRIMARY",
                        "order_type": entry_order_type,
                        "price": None,
                        "raw_label": "ENTRY",
                        "source_style": "MARKET" if entry_order_type == "MARKET" else "SINGLE",
                        "is_optional": False,
                    }
                ]
            entities.update(
                {
                    "side": _extract_side(raw_text),
                    "leverage_hint_raw": _extract_leverage_hint(raw_text),
                    "entry_text_raw": _extract_entry_line(raw_text),
                    "targets_text_raw": targets_raw,
                    "stop_loss_raw": stop_loss_raw,
                    "stop_loss": stop_loss,
                    "take_profits": take_profits,
                    "entry_order_type": entry_order_type,
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
                entities["loss_close"] = True

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

            if entities.get("reenter") and context.reply_raw_text:
                parent_text = context.reply_raw_text
                parent_entry_values = _extract_entry_values(parent_text)
                parent_stop_raw, parent_stop = _extract_stop(parent_text)
                parent_take_profits, parent_targets_raw = _extract_take_profits(parent_text)
                parent_entry_order_type = _infer_entry_order_type(parent_text)
                if parent_entry_values:
                    low, high = parent_entry_values[0], parent_entry_values[-1]
                    entities["entry_range_low"] = low
                    entities["entry_range_high"] = high
                    entities["entry"] = list(parent_entry_values)
                    entities["entry_plan_entries"] = _build_entry_plan_entries(parent_entry_values, entry_order_type=parent_entry_order_type)
                    entities["entry_order_type"] = parent_entry_order_type
                    entities["entry_text_raw"] = _extract_entry_line(parent_text)
                if parent_stop is not None:
                    entities["stop_loss_raw"] = parent_stop_raw
                    entities["stop_loss"] = parent_stop
                if parent_take_profits:
                    entities["take_profits"] = parent_take_profits
                    entities["targets_text_raw"] = parent_targets_raw

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
        if entities.get("reported_loss_percent") is not None or entities.get("loss_close"):
            intents.append("U_REPORT_FINAL_RESULT")
        if (entities.get("reported_loss_percent") is not None or entities.get("loss_close")) and not entities.get("manual_close"):
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
        if entities.get("reported_loss_percent") is not None or entities.get("loss_close"):
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
        if entities.get("reported_loss_percent") is not None or entities.get("loss_close"):
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
    values = _extract_entry_values(raw_text)
    if len(values) < 2:
        return None
    return values[0], values[-1]


def _extract_entry_values(raw_text: str) -> list[float]:
    line = _extract_entry_line(raw_text)
    if not line:
        return []

    normalized = _normalize_dash(line)
    match = re.search(r"(?P<low>\d[\d,]*(?:\.\d+)?)\s*-\s*(?P<high>\d[\d,]*(?:\.\d+)?)", normalized)
    if match:
        low = _to_float(match.group("low"))
        high = _to_float(match.group("high"))
        if low is not None and high is not None:
            return [low, high]

    values: list[float] = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", normalized):
        value = _to_float(raw)
        if value is not None and value not in values:
            values.append(value)
    return values


def _infer_entry_order_type(raw_text: str) -> str:
    normalized = normalize_text(raw_text)
    has_market = any(marker in normalized for marker in _ENTRY_MARKET_MARKERS)
    has_limit = any(marker in normalized for marker in _ENTRY_LIMIT_MARKERS)
    if has_market and not has_limit:
        return "MARKET"
    if has_limit:
        return "LIMIT"
    if has_market:
        return "MARKET"
    return "LIMIT"


def _build_entry_plan_entries(entry_values: list[float], *, entry_order_type: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for index, value in enumerate(entry_values, start=1):
        out.append(
            {
                "sequence": index,
                "role": "PRIMARY" if index == 1 else "AVERAGING",
                "order_type": entry_order_type,
                "price": float(value),
                "raw_label": "ENTRY" if index == 1 else "AVERAGING",
                "source_style": "SINGLE" if len(entry_values) == 1 else "RANGE",
                "is_optional": index > 1,
            }
        )
    return out


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


__all__ = ["Trader3ProfileParser"]
