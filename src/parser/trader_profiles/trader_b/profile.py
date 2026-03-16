"""Trader B profile parser."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_SYMBOL_RE = re.compile(r"\$?(?P<symbol>[A-Z0-9]{2,20}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b", re.IGNORECASE)
_ENTRY_RE = re.compile(r"(?:\u0432\u0445\u043e\u0434|entry)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_STOP_RE = re.compile(r"(?:\u0441\u0442\u043e\u043f\s*\u043b\u043e\u0441\u0441|sl|stop)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TP_RE = re.compile(r"(?:\u0442\u0435\u0439\u043a\s*\u043f\u0440\u043e\u0444\u0438\u0442|tp\d*|\u0442\u043f\d*|target\s*\d*)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RISK_RE = re.compile(r"\u0440\u0438\u0441\u043a\s*\u043d\u0430\s*\u0441\u0434\u0435\u043b\u043a\u0443\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)%", re.IGNORECASE)
_POTENTIAL_RE = re.compile(r"(?:\u043f\u043e\u0442\u0435\u043d\u0446\w*\s*\u043f\u0440\u0438\u0431\u044b\u043b\w*|potential\s*profit)\s*[:=]?\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)%", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)%")
_STOP_LEVEL_RE = re.compile(
    r"(?:\u043f\u0435\u0440\u0435\u043d\u043e\u0441\u0438\u043c\s*(?:\u043d\u0430|\u0432)\s*(?:\u0443\u0440\u043e\u0432\u0435\u043d\u044c\s*)?|\u043d\u0430\s*\u043e\u0442\u043c\u0435\u0442\w*\s*|\u0443\u0440\u043e\u0432\u0435\u043d\u044c\s*|(?:to|at)\s*level\s*|move\s*(?:stop|sl)\s*(?:to\s*)?)(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_DEFAULT_IGNORE_MARKERS = ("#\u0430\u0434\u043c\u0438\u043d", "#admin")
_DEFAULT_UPDATE_FALLBACK_MARKERS = (
    "\u0437\u0430\u043a\u0440\u044b\u0442",
    "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e",
    "\u0437\u0430\u043a\u0440\u044b\u043b\u0438\u0441\u044c",
    "\u043f\u0435\u0440\u0435\u043d\u043e\u0441\u0438\u043c",
    "\u0432 \u0431\u0443",
    "\u0431\u0435\u0437 \u0440\u0438\u0441\u043a\u043e\u0432\u043e\u0439",
    "\u043d\u0435 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u043e",
    "move stop",
    "move sl",
    "breakeven",
)
_DEFAULT_SETUP_INCOMPLETE_MARKERS = ("\u0442\u0435\u0439\u043a", "tp", "target", "\u0442\u043f")
_DEFAULT_CLOSE_FULL_EXTRA_MARKERS = ("\u0441\u0434\u0435\u043b\u043a\u0430 \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e \u0437\u0430\u043a\u0440\u044b\u0442\u0430", "\u0437\u0430\u043a\u0440\u044b\u0442\u0430", "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e", "\u0437\u0430\u043a\u0440\u044b\u0442\u044c \u043f\u043e\u0437\u0438\u0446\u0438\u044e")
_DEFAULT_TP_HIT_EXPLICIT_MARKERS = ("\u0442\u0435\u0439\u043a \u0434\u043e\u0441\u0442\u0438\u0433", "take profit hit", "tp hit", "target hit")
_DEFAULT_MARKET_CONTEXT_SPOT_MARKERS = ("\u0441\u0434\u0435\u043b\u043a\u0430 \u043d\u0430 \u0441\u043f\u043e\u0442\u0435",)
_DEFAULT_ENTRY_ORDER_MARKET_MARKERS = ("\u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",)
_DEFAULT_SIDE_LONG_MARKERS = ("\u043b\u043e\u043d\u0433", "long", "buy")
_DEFAULT_SIDE_SHORT_MARKERS = ("\u0448\u043e\u0440\u0442", "short", "sell")
_DEFAULT_GLOBAL_TARGET_LONG_MARKERS = ("\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438", "all longs", "all long")
_DEFAULT_GLOBAL_TARGET_SHORT_MARKERS = ("\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b", "all shorts", "all short")
_DEFAULT_CANCEL_PENDING_MARKERS = (
    "\u043d\u0435 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u043e",
    "\u043e\u0442\u043c\u0435\u043d",
    "\u0441\u043d\u0438\u043c\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
    "cancel pending",
    "cancel limit",
    "cancel orders",
)
_DEFAULT_MOVE_STOP_TO_BE_MARKERS = (
    "\u0432 \u0431\u0443",
    "\u0431\u0435\u0437 \u0440\u0438\u0441\u043a\u043e\u0432\u043e\u0439",
    "move stop to be",
    "breakeven",
)
_DEFAULT_MOVE_STOP_MARKERS = (
    "\u043f\u0435\u0440\u0435\u043d\u043e\u0441\u0438\u043c \u043d\u0430",
    "\u043d\u0430 \u0443\u0440\u043e\u0432\u0435\u043d\u044c",
    "move stop",
    "move sl",
)
_DEFAULT_STOP_HIT_MARKERS = ("\u043f\u043e \u0441\u0442\u043e\u043f\u0443", "stop hit", "stopped out")


class TraderBProfileParser:
    trader_code = "trader_b"

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        target_refs = self._extract_targets(prepared=prepared, context=context)
        message_type = self._classify_message(prepared=prepared)
        intents = self._extract_intents(prepared=prepared, message_type=message_type)
        if message_type == "UNCLASSIFIED" and "U_CANCEL_PENDING_ORDERS" in intents:
            message_type = "UPDATE"
        entities = self._extract_entities(prepared=prepared, intents=intents, message_type=message_type, target_refs=target_refs)
        warnings = self._build_warnings(prepared=prepared, message_type=message_type, target_refs=target_refs, intents=intents)
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)
        return TraderParseResult(
            message_type=message_type,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            warnings=warnings,
            confidence=confidence,
        )

    def _preprocess(self, *, text: str, context: ParserContext) -> dict[str, Any]:
        raw_text = text or context.raw_text
        return {"raw_text": raw_text, "normalized_text": normalize_text(raw_text)}

    def _classify_message(self, *, prepared: dict[str, Any]) -> str:
        normalized = str(prepared.get("normalized_text") or "")
        raw_text = str(prepared.get("raw_text") or "")
        if self._contains_any(normalized, _merge_markers(self._as_markers("ignore_markers"), _DEFAULT_IGNORE_MARKERS)):
            return "INFO_ONLY"

        has_symbol = _extract_symbol(raw_text) is not None
        has_side = _extract_side(
            normalized,
            long_markers=_merge_markers(self._as_markers("side_markers", "long"), _DEFAULT_SIDE_LONG_MARKERS),
            short_markers=_merge_markers(self._as_markers("side_markers", "short"), _DEFAULT_SIDE_SHORT_MARKERS),
        ) is not None
        is_market_entry = self._contains_any(
            normalized,
            _merge_markers(self._as_markers("entry_order_markers", "market"), _DEFAULT_ENTRY_ORDER_MARKET_MARKERS),
        )
        has_entry = _extract_entry(raw_text) is not None or is_market_entry
        has_stop = _extract_stop(raw_text) is not None
        has_tp = bool(_extract_take_profits(raw_text))

        if has_symbol and has_side and has_entry and has_stop and has_tp:
            return "NEW_SIGNAL"
        setup_incomplete_markers = _merge_markers(
            self._as_markers("classification_markers", "setup_incomplete"),
            _DEFAULT_SETUP_INCOMPLETE_MARKERS,
        )
        if has_symbol and has_side and has_entry and has_stop and self._contains_any(normalized, setup_incomplete_markers):
            return "SETUP_INCOMPLETE"

        update_markers = self._as_markers("classification_markers", "update")
        if self._contains_any(normalized, _merge_markers(update_markers, _DEFAULT_UPDATE_FALLBACK_MARKERS)):
            return "UPDATE"
        return "UNCLASSIFIED"

    def _extract_targets(self, *, prepared: dict[str, Any], context: ParserContext) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _append(kind: str, ref: object) -> None:
            key = (kind, str(ref))
            if key in seen:
                return
            seen.add(key)
            out.append({"kind": kind, "ref": ref})

        if context.reply_to_message_id is not None:
            _append("reply", int(context.reply_to_message_id))

        raw_text = str(prepared.get("raw_text") or "")
        for link in list(context.extracted_links) + extract_telegram_links(raw_text):
            _append("telegram_link", link)
            match = _LINK_ID_RE.search(link)
            if match:
                _append("message_id", int(match.group("id")))
        return out

    def _extract_intents(self, *, prepared: dict[str, Any], message_type: str) -> list[str]:
        normalized = str(prepared.get("normalized_text") or "")
        intents: list[str] = []
        if message_type == "NEW_SIGNAL":
            return ["NS_CREATE_SIGNAL"]
        if message_type not in {"UPDATE", "UNCLASSIFIED"}:
            return intents

        move_to_be_markers = _merge_markers(
            self._as_markers("intent_markers", "U_MOVE_STOP_TO_BE"),
            _DEFAULT_MOVE_STOP_TO_BE_MARKERS,
        )
        move_stop_markers = _merge_markers(self._as_markers("intent_markers", "U_MOVE_STOP"), _DEFAULT_MOVE_STOP_MARKERS)
        cancel_markers = _merge_markers(
            self._as_markers("intent_markers", "U_CANCEL_PENDING_ORDERS"),
            _DEFAULT_CANCEL_PENDING_MARKERS,
        )
        stop_hit_markers = _merge_markers(self._as_markers("intent_markers", "U_STOP_HIT"), _DEFAULT_STOP_HIT_MARKERS)

        if self._contains_any(normalized, move_to_be_markers):
            intents.extend(["U_MOVE_STOP_TO_BE", "U_MOVE_STOP"])
        elif self._contains_any(normalized, move_stop_markers):
            intents.append("U_MOVE_STOP")

        close_full_markers = _merge_markers(
            self._as_markers("intent_markers", "U_CLOSE_FULL"),
            _DEFAULT_CLOSE_FULL_EXTRA_MARKERS,
        )
        if self._contains_any(normalized, close_full_markers):
            intents.append("U_CLOSE_FULL")
        if self._contains_any(normalized, stop_hit_markers):
            intents.append("U_STOP_HIT")
        if self._contains_any(
            normalized,
            _merge_markers(self._as_markers("intent_markers", "U_TP_HIT_EXPLICIT"), _DEFAULT_TP_HIT_EXPLICIT_MARKERS),
        ):
            intents.append("U_TP_HIT")
        if self._contains_any(normalized, cancel_markers):
            intents.append("U_CANCEL_PENDING_ORDERS")
        if self._contains_any(normalized, self._as_markers("intent_markers", "U_REPORT_FINAL_RESULT")):
            intents.append("U_REPORT_FINAL_RESULT")
        return _unique(intents)

    def _extract_entities(
        self,
        *,
        prepared: dict[str, Any],
        intents: list[str],
        message_type: str,
        target_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raw_text = str(prepared.get("raw_text") or "")
        normalized = str(prepared.get("normalized_text") or "")
        entities: dict[str, Any] = {}

        if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
            entry = _extract_entry(raw_text)
            is_market_entry = self._contains_any(
                normalized,
                _merge_markers(self._as_markers("entry_order_markers", "market"), _DEFAULT_ENTRY_ORDER_MARKET_MARKERS),
            )
            order_type = "MARKET" if is_market_entry else "LIMIT"
            market_context = (
                "SPOT"
                if self._contains_any(
                    normalized,
                    _merge_markers(self._as_markers("market_context_markers", "spot"), _DEFAULT_MARKET_CONTEXT_SPOT_MARKERS),
                )
                else None
            )
            entities.update(
                {
                    "symbol": _extract_symbol(raw_text),
                    "side": _extract_side(
                        normalized,
                        long_markers=_merge_markers(self._as_markers("side_markers", "long"), _DEFAULT_SIDE_LONG_MARKERS),
                        short_markers=_merge_markers(self._as_markers("side_markers", "short"), _DEFAULT_SIDE_SHORT_MARKERS),
                    ),
                    "entry": [entry] if isinstance(entry, float) else [],
                    "stop_loss": _extract_stop(raw_text),
                    "take_profits": _extract_take_profits(raw_text),
                    "risk_percent": _extract_percent_from_regex(_RISK_RE, raw_text),
                    "potential_profit_percent": _extract_percent_from_regex(_POTENTIAL_RE, raw_text),
                    "market_context": market_context,
                    "entry_order_type": order_type,
                    "entry_plan_type": "SINGLE",
                    "entry_structure": "ONE_SHOT",
                    "has_averaging_plan": False,
                    "entry_plan_entries": [
                        {
                            "sequence": 1,
                            "role": "PRIMARY",
                            "order_type": order_type,
                            "price": entry,
                            "raw_label": "ENTRY",
                            "source_style": "SINGLE",
                            "is_optional": False,
                        }
                    ]
                    if isinstance(entry, float)
                    else [],
                }
            )

        if "U_MOVE_STOP_TO_BE" in intents:
            stop_level = _extract_stop_level(raw_text)
            entities["new_stop_level"] = stop_level if isinstance(stop_level, float) else "ENTRY"
        elif "U_MOVE_STOP" in intents:
            stop_level = _extract_stop_level(raw_text)
            if stop_level is not None:
                entities["new_stop_level"] = stop_level

        if "U_CLOSE_FULL" in intents:
            entities["close_scope"] = "FULL"
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities["result_percent"] = result_percent

        if "U_STOP_HIT" in intents:
            entities["hit_target"] = "STOP"
        elif "U_TP_HIT" in intents:
            entities["hit_target"] = "TP"

        if "U_CANCEL_PENDING_ORDERS" in intents:
            entities["cancel_scope"] = self._resolve_cancel_scope(prepared=prepared, target_refs=target_refs)

        return entities

    def _build_warnings(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        target_refs: list[dict[str, Any]],
        intents: list[str],
    ) -> list[str]:
        if message_type != "UPDATE":
            return []
        if not any(intent.startswith("U_") and intent != "U_REPORT_FINAL_RESULT" for intent in intents):
            return []
        if "U_CANCEL_PENDING_ORDERS" in intents:
            cancel_scope = self._resolve_cancel_scope(prepared=prepared, target_refs=target_refs)
            if cancel_scope in {"ALL_ALL", "ALL_LONG", "ALL_SHORT"}:
                return []
        has_symbol = _extract_symbol(str(prepared.get("raw_text") or "")) is not None
        if target_refs or has_symbol or self._has_global_target_scope(prepared=prepared):
            return []
        return ["trader_b_update_missing_target"]

    def _has_global_target_scope(self, *, prepared: dict[str, Any]) -> bool:
        return self._resolve_global_target_scope(prepared=prepared) is not None

    def _resolve_cancel_scope(self, *, prepared: dict[str, Any], target_refs: list[dict[str, Any]]) -> str:
        if target_refs:
            return "TARGETED"
        global_scope = self._resolve_global_target_scope(prepared=prepared)
        if global_scope == "ALL_LONGS":
            return "ALL_LONG"
        if global_scope == "ALL_SHORTS":
            return "ALL_SHORT"
        return "ALL_ALL"

    def _resolve_global_target_scope(self, *, prepared: dict[str, Any]) -> str | None:
        normalized = str(prepared.get("normalized_text") or "")
        long_markers = _merge_markers(
            self._as_markers("global_target_markers", "ALL_LONGS"),
            _DEFAULT_GLOBAL_TARGET_LONG_MARKERS,
        )
        short_markers = _merge_markers(
            self._as_markers("global_target_markers", "ALL_SHORTS"),
            _DEFAULT_GLOBAL_TARGET_SHORT_MARKERS,
        )
        if self._contains_any(normalized, long_markers):
            return "ALL_LONGS"
        if self._contains_any(normalized, short_markers):
            return "ALL_SHORTS"
        return None

    @staticmethod
    def _estimate_confidence(*, message_type: str, warnings: list[str]) -> float:
        if message_type == "NEW_SIGNAL":
            return 0.8
        if message_type == "UPDATE":
            return 0.68 if not warnings else 0.55
        if message_type == "SETUP_INCOMPLETE":
            return 0.45
        if message_type == "INFO_ONLY":
            return 0.4
        return 0.2

    def _load_rules(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            return {}
        return {}

    def _as_markers(self, *path: str) -> tuple[str, ...]:
        node: Any = self._rules
        for key in path:
            if not isinstance(node, dict):
                return ()
            node = node.get(key)
        if isinstance(node, list):
            return tuple(str(value).strip().lower() for value in node if str(value).strip())
        return ()

    @staticmethod
    def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in markers if marker)


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_symbol(raw_text: str) -> str | None:
    match = _SYMBOL_RE.search(raw_text.upper())
    return str(match.group("symbol")).upper() if match else None


def _extract_side(normalized: str, *, long_markers: tuple[str, ...], short_markers: tuple[str, ...]) -> str | None:
    if any(marker in normalized for marker in long_markers):
        return "LONG"
    if any(marker in normalized for marker in short_markers):
        return "SHORT"
    return None


def _extract_entry(raw_text: str) -> float | None:
    match = _ENTRY_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_stop(raw_text: str) -> float | None:
    match = _STOP_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_take_profits(raw_text: str) -> list[float]:
    out: list[float] = []
    for match in _TP_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is not None and value not in out:
            out.append(value)
    return out


def _extract_percent_from_regex(pattern: re.Pattern[str], raw_text: str) -> float | None:
    match = pattern.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_stop_level(raw_text: str) -> float | None:
    match = _STOP_LEVEL_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_result_percent(raw_text: str) -> float | None:
    for match in _PERCENT_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is not None:
            return value
    return None


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _merge_markers(markers: tuple[str, ...], defaults: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in [*markers, *defaults]:
        token = str(value).strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)
