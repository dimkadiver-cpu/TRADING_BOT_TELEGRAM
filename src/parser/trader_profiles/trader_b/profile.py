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
_ENTRY_RE = re.compile(r"(?:вход(?:\s+с\s+текущих)?|entry)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_STOP_RE = re.compile(r"(?:стоп\s*лосс|sl|stop)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TP_RE = re.compile(r"(?:тейк\s*профит|tp\d*|тп\d*|target\s*\d*)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RISK_RE = re.compile(r"риск\s*на\s*сделку\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)%", re.IGNORECASE)
_POTENTIAL_RE = re.compile(r"(?:потенц\w*\s*прибыл\w*|potential\s*profit)\s*[:=]?\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)%", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)%")
_STOP_LEVEL_RE = re.compile(
    r"(?:переносим\s*(?:на|в)\s*(?:уровень\s*)?|на\s*отмет\w*\s*|уровень\s*)(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_DEFAULT_IGNORE_MARKERS = ("#админ", "#admin")
_DEFAULT_UPDATE_FALLBACK_MARKERS = (
    "закрыт",
    "закрываю",
    "закрылись",
    "переносим",
    "в бу",
    "без рисковой",
    "new sl",
    "update stop",
    "переносим стоп",
    "стоп на 1 тейк",
    "стоп на первый тейк",
    "stop on 1 tp",
    "stop on tp1",
    "не актуально",
)
_DEFAULT_INFO_ONLY_MARKERS = (
    "сделка закрыта",
    "закрыта в бу",
    "сделка закрыта в бу",
    "сделка закрыта по стоп лоссу",
    "закрыта по стоп лоссу",
    "закрыта в безубыток",
    "закрылись по стоп лоссу",
)
_DEFAULT_SETUP_INCOMPLETE_MARKERS = ("тейк", "tp", "target", "тп")
_DEFAULT_CLOSE_FULL_EXTRA_MARKERS = ("сделка полностью закрыта", "закрыта", "закрываю", "закрыть позицию")
_DEFAULT_TP_HIT_EXPLICIT_MARKERS = ("тейк достиг", "take profit hit", "tp hit", "target hit")
_DEFAULT_MARKET_CONTEXT_SPOT_MARKERS = ("сделка на споте",)
_DEFAULT_ENTRY_ORDER_MARKET_MARKERS = ("по текущим", "вход с текущих")
_DEFAULT_SIDE_LONG_MARKERS = ("лонг", "long", "buy")
_DEFAULT_SIDE_SHORT_MARKERS = ("шорт", "short", "sell")
_CANCEL_ONLY_MARKERS = (
    "не актуально",
    "пока не актуально",
    "тут не актуально",
    "цена ушла высоко",
    "будем искать твх повторно",
    "лонгов открытых нет",
)
_PASSIVE_OUTCOME_MARKERS = (
    "сделка ушла в бу",
    "закрыта в бу",
    "сделка закрыта в бу",
    "сделка закрыта по стоп лоссу",
    "закрыта по стоп лоссу",
    "закрыта в безубыток",
    "закрылись по стоп лоссу",
)


class TraderBProfileParser:
    trader_code = "trader_b"

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        target_refs = self._extract_targets(prepared=prepared, context=context)
        global_target_scope = self._resolve_global_target_scope(prepared=prepared)
        message_type = self._classify_message(prepared=prepared, global_target_scope=global_target_scope)
        intents = self._extract_intents(
            prepared=prepared,
            message_type=message_type,
            target_refs=target_refs,
            global_target_scope=global_target_scope,
        )
        entities = self._extract_entities(
            prepared=prepared,
            intents=intents,
            message_type=message_type,
            target_refs=target_refs,
            global_target_scope=global_target_scope,
        )
        warnings = self._build_warnings(
            prepared=prepared,
            message_type=message_type,
            target_refs=target_refs,
            intents=intents,
            global_target_scope=global_target_scope,
        )
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)
        target_scope = self._build_target_scope(
            target_refs=target_refs,
            global_target_scope=global_target_scope,
            intents=intents,
        )
        linking = self._build_linking(
            target_refs=target_refs,
            context=context,
            global_target_scope=global_target_scope,
        )
        diagnostics = self._build_diagnostics(
            prepared=prepared,
            message_type=message_type,
            intents=intents,
            warnings=warnings,
            global_target_scope=global_target_scope,
        )
        return TraderParseResult(
            message_type=message_type,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            warnings=warnings,
            confidence=confidence,
            target_scope=target_scope,
            linking=linking,
            diagnostics=diagnostics,
        )

    def _preprocess(self, *, text: str, context: ParserContext) -> dict[str, Any]:
        raw_text = text or context.raw_text
        return {"raw_text": raw_text, "normalized_text": normalize_text(raw_text)}

    def _classify_message(self, *, prepared: dict[str, Any], global_target_scope: str | None = None) -> str:
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
        has_market_entry_marker = self._contains_any(normalized, _merge_markers(self._as_markers("entry_order_markers", "market"), _DEFAULT_ENTRY_ORDER_MARKET_MARKERS))
        has_entry = _extract_entry(raw_text) is not None or has_market_entry_marker
        has_stop = _extract_stop(raw_text) is not None
        has_tp = bool(_extract_take_profits(raw_text))

        if has_symbol and has_side and has_entry and has_stop and has_tp:
            return "NEW_SIGNAL"
        setup_incomplete_markers = _merge_markers(
            self._as_markers("classification_markers", "setup_incomplete"),
            _DEFAULT_SETUP_INCOMPLETE_MARKERS,
        )
        if has_symbol and has_side and has_entry and has_stop and (has_tp or self._contains_any(normalized, setup_incomplete_markers)):
            if not has_tp:
                return "SETUP_INCOMPLETE"
        if has_symbol and has_side and has_entry and has_stop and not has_tp:
            return "SETUP_INCOMPLETE"

        if self._is_cancel_only_message(normalized=normalized, global_target_scope=global_target_scope):
            return "UPDATE"

        if self._contains_any(normalized, self._operational_markers()):
            return "UPDATE"

        info_only_markers = _merge_markers(self._as_markers("classification_markers", "info_only"), _DEFAULT_INFO_ONLY_MARKERS)
        if self._contains_any(normalized, info_only_markers) or self._contains_any(normalized, _PASSIVE_OUTCOME_MARKERS):
            return "INFO_ONLY"

        update_markers = _merge_markers(self._as_markers("classification_markers", "update"), _DEFAULT_UPDATE_FALLBACK_MARKERS)
        if self._contains_any(normalized, update_markers) or (
            self._contains_whole_word(normalized, "бу")
            and self._contains_any(normalized, ("стоп", "перенос", "перевод", "entry", "точка", "безубыт", "безриск"))
        ):
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

    def _extract_intents(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        target_refs: list[dict[str, Any]],
        global_target_scope: str | None = None,
    ) -> list[str]:
        normalized = str(prepared.get("normalized_text") or "")
        raw_text = str(prepared.get("raw_text") or "")
        intents: list[str] = []
        if message_type == "NEW_SIGNAL":
            return ["NS_CREATE_SIGNAL"]
        if message_type not in {"UPDATE", "UNCLASSIFIED"}:
            return intents

        if self._is_cancel_only_message(normalized=normalized, global_target_scope=global_target_scope):
            return ["U_CANCEL_PENDING_ORDERS"]

        close_full_markers = _merge_markers(
            self._as_markers("intent_markers", "U_CLOSE_FULL"),
            _DEFAULT_CLOSE_FULL_EXTRA_MARKERS,
        )
        has_close_full = self._contains_any(normalized, close_full_markers)
        be_markers = tuple(marker for marker in self._as_markers("intent_markers", "U_MOVE_STOP_TO_BE") if marker not in {"бу", "в бу"})
        has_strong_be = self._contains_any(normalized, be_markers)
        stop_level = _extract_stop_level(raw_text)
        move_stop_markers = _merge_markers(self._as_markers("intent_markers", "U_MOVE_STOP"), ("stop on 1 tp", "stop on tp1", "stop on first tp", "stop on first тейк", "new sl", "update stop"))
        has_structural_move_stop = self._contains_any(normalized, move_stop_markers)

        if has_strong_be:
            intents.append("U_MOVE_STOP_TO_BE")
            if stop_level is not None or has_structural_move_stop:
                intents.append("U_MOVE_STOP")
        elif stop_level is not None or has_structural_move_stop:
            intents.append("U_MOVE_STOP")

        if has_close_full:
            intents.append("U_CLOSE_FULL")
        if self._contains_any(normalized, self._as_markers("intent_markers", "U_STOP_HIT")):
            intents.append("U_STOP_HIT")
        if self._contains_any(
            normalized,
            _merge_markers(self._as_markers("intent_markers", "U_TP_HIT_EXPLICIT"), _DEFAULT_TP_HIT_EXPLICIT_MARKERS),
        ):
            intents.append("U_TP_HIT")
        if self._contains_any(normalized, self._as_markers("intent_markers", "U_CANCEL_PENDING_ORDERS")) or self._is_cancel_only_message(
            normalized=normalized,
            global_target_scope=global_target_scope,
        ):
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
        global_target_scope: str | None = None,
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
                            "price": entry if isinstance(entry, float) else None,
                            "raw_label": "ENTRY",
                            "source_style": "SINGLE" if isinstance(entry, float) else "ENTRY_AVERAGING",
                            "is_optional": False,
                        }
                    ]
                    if is_market_entry or isinstance(entry, float)
                    else [],
                }
            )

        if "U_MOVE_STOP_TO_BE" in intents:
            stop_level = _extract_stop_level(raw_text)
            entities["new_stop_level"] = stop_level if isinstance(stop_level, float) else "ENTRY"
            if "U_MOVE_STOP" in intents and stop_level is not None:
                entities["new_stop_level"] = stop_level
        elif "U_MOVE_STOP" in intents:
            stop_level = _extract_stop_level(raw_text)
            if stop_level is not None:
                entities["new_stop_level"] = stop_level
            else:
                reference_text = self._extract_stop_reference_text(normalized=normalized)
                if reference_text:
                    entities["stop_reference_text"] = reference_text

        if "U_CLOSE_FULL" in intents:
            entities["close_scope"] = self._resolve_close_scope(global_target_scope=global_target_scope) or "FULL"
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities["result_percent"] = result_percent

        if "U_STOP_HIT" in intents:
            entities["hit_target"] = "STOP"
        elif "U_TP_HIT" in intents:
            entities["hit_target"] = "TP"
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities.setdefault("result_percent", result_percent)
        if "U_REPORT_FINAL_RESULT" in intents:
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities["result_percent"] = result_percent

        if "U_CANCEL_PENDING_ORDERS" in intents:
            entities["cancel_scope"] = self._resolve_cancel_scope(
                prepared=prepared,
                target_refs=target_refs,
                global_target_scope=global_target_scope,
            )

        return entities

    def _build_warnings(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        target_refs: list[dict[str, Any]],
        intents: list[str],
        global_target_scope: str | None = None,
    ) -> list[str]:
        if message_type != "UPDATE":
            return []
        if not any(intent.startswith("U_") and intent != "U_REPORT_FINAL_RESULT" for intent in intents):
            return []
        has_symbol = _extract_symbol(str(prepared.get("raw_text") or "")) is not None
        if target_refs or has_symbol or global_target_scope:
            return []
        return [f"{self.trader_code}_update_missing_target"]

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

    def _marker_map(self, key: str) -> dict[str, tuple[str, ...]]:
        node: Any = self._rules.get(key)
        if not isinstance(node, dict):
            return {}
        out: dict[str, tuple[str, ...]] = {}
        for subkey, values in node.items():
            if isinstance(values, list):
                markers = tuple(str(value).strip().lower() for value in values if str(value).strip())
            elif isinstance(values, str):
                markers = (values.strip().lower(),)
            else:
                markers = ()
            if markers:
                out[str(subkey)] = markers
        return out

    def _has_global_marker(self, *, normalized: str, scope_name: str) -> bool:
        marker_map = self._marker_map("global_target_markers")
        return self._contains_any(normalized, marker_map.get(scope_name, ()))

    def _resolve_global_target_scope(self, *, prepared: dict[str, Any]) -> str | None:
        normalized = str(prepared.get("normalized_text") or "")
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

    def _is_cancel_only_message(self, *, normalized: str, global_target_scope: str | None) -> bool:
        if not self._contains_any(normalized, _CANCEL_ONLY_MARKERS):
            return False
        if self._contains_any(normalized, self._operational_markers()):
            return False
        return True

    def _operational_markers(self) -> tuple[str, ...]:
        markers = _merge_markers(
            (
                *self._as_markers("intent_markers", "U_MOVE_STOP_TO_BE"),
                *self._as_markers("intent_markers", "U_MOVE_STOP"),
                *self._as_markers("intent_markers", "U_CLOSE_FULL"),
                *self._as_markers("intent_markers", "U_STOP_HIT"),
                *self._as_markers("intent_markers", "U_TP_HIT_EXPLICIT"),
                *self._as_markers("intent_markers", "U_CANCEL_PENDING_ORDERS"),
                *self._as_markers("intent_markers", "U_REPORT_FINAL_RESULT"),
            ),
            (),
        )
        return tuple(marker for marker in markers if marker not in {"бу", "в бу"})

    def _resolve_cancel_scope(
        self,
        *,
        prepared: dict[str, Any],
        target_refs: list[dict[str, Any]],
        global_target_scope: str | None,
    ) -> str:
        if target_refs:
            return "TARGETED"
        if global_target_scope == "ALL_LONGS":
            return "ALL_LONG"
        if global_target_scope == "ALL_SHORTS":
            return "ALL_SHORT"
        if global_target_scope in {"ALL_ALL", "ALL_OPEN", "ALL_REMAINING"}:
            return "ALL_PENDING_ENTRIES"
        return "ALL_PENDING_ENTRIES"

    def _resolve_close_scope(self, *, global_target_scope: str | None) -> str | None:
        if global_target_scope in {"ALL_LONGS", "ALL_SHORTS", "ALL_ALL", "ALL_OPEN", "ALL_REMAINING"}:
            return global_target_scope
        return None

    def _extract_stop_reference_text(self, *, normalized: str) -> str | None:
        move_markers = self._as_markers("intent_markers", "U_MOVE_STOP")
        for marker in move_markers:
            if marker and marker in normalized:
                return marker
        return None

    def _build_target_scope(
        self,
        *,
        target_refs: list[dict[str, Any]],
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

    def _build_linking(
        self,
        *,
        target_refs: list[dict[str, Any]],
        context: ParserContext,
        global_target_scope: str | None,
    ) -> dict[str, Any]:
        return {
            "targeted": bool(target_refs),
            "reply_targeted": context.reply_to_message_id is not None,
            "target_ref_count": len(target_refs),
            "has_global_target_scope": global_target_scope is not None,
            "telegram_link_count": sum(1 for ref in target_refs if ref.get("kind") == "telegram_link"),
        }

    def _build_diagnostics(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        intents: list[str],
        warnings: list[str],
        global_target_scope: str | None,
    ) -> dict[str, Any]:
        return {
            "parser_version": "trader_b_v2",
            "message_type": message_type,
            "intent_count": len(intents),
            "warning_count": len(warnings),
            "has_global_target_scope": global_target_scope is not None,
            "raw_length": len(str(prepared.get("raw_text") or "")),
        }

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

    @staticmethod
    def _contains_whole_word(text: str, token: str) -> bool:
        if not token:
            return False
        return bool(re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE))


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
