"""Trader B profile parser."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text
from src.parser.rules_engine import RulesEngine

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_SYMBOL_RE = re.compile(r"\$?(?P<symbol>[A-Z0-9]{2,20}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b", re.IGNORECASE)
_ENTRY_RE = re.compile(
    r"(?:вход(?:\s+с\s+текущих)?|entry)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)(?:\s*\(\s*[+\-±][^)]*\))?",
    re.IGNORECASE,
)
_STOP_RE = re.compile(r"(?:стоп\s*лосс|sl|stop)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TP_RE = re.compile(r"(?:тейк\s*профит|tp\d*|тп\d*|target\s*\d*)\s*[:=]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RISK_RE = re.compile(r"риск\s*на\s*сделку\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)%", re.IGNORECASE)
_POTENTIAL_RE = re.compile(r"(?:потенц\w*\s*прибыл\w*|potential\s*profit)\s*[:=]?\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)%", re.IGNORECASE)
# Handles: +5%, -1%, ≈0.2%, (~0.2%), (-0.5%), (≈0.2%)
_PERCENT_RE = re.compile(r"[(\s≈~]*(?P<sign>[+-]?)(?P<value>\d+(?:[.,]\d+)?)%", re.IGNORECASE)
_STOP_LEVEL_RE = re.compile(
    r"(?:переносим\s*(?:на|в)\s*(?:уровень\s*)?|на\s*отмет\w*\s*|уровень\s*|на\s*уровень\s*)(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# Detects setup-invalidation: price moved away, signal never hit
_INVALIDATE_RE = re.compile(
    r"(?:цена\s+ушла\s+высоко"
    r"|без\s+теста\s+точки\s+входа"
    r"|цели\s+достигнуты\s+без\s+теста"
    r"|без\s+теста\s+нашей\s+зоны"        # "без теста нашей зоны интереса"
    r"|цена\s+достигла\s+цели.*?без\s+теста"  # "цена достигла цели, однако без теста"
    r"|без\s+теста\s+зоны\s+интереса"
    r")",
    re.IGNORECASE | re.DOTALL,
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
    "пока не актуально",
)
_DEFAULT_INFO_ONLY_MARKERS = (
    "сделка закрыта",
    "закрыта в бу",
    "сделка закрыта в бу",
    "сделка закрыта по стоп лоссу",
    "закрыта в безубыток",
    "закрылись по стоп лоссу",
    # Passive commentary markers — no actionable instruction
    "к сожалению стоп",
    "небольшие изменения",
    "как и ожидалось",
    "идея в целом отработала",
)
_DEFAULT_SETUP_INCOMPLETE_MARKERS = ("тейк", "tp", "target", "тп")
_DEFAULT_CLOSE_FULL_EXTRA_MARKERS = (
    "сделка полностью закрыта",
    "закрыта",
    "закрываю",
    "закрываем",
    "закрыть",
    "закрыть позицию",
    "принял решение закрыть",
    "принимаю решение закрыть",
    "закрываю по текущим",
    "закрыть позицию по текущим",
    "закрыта в районе бу",
)
_DEFAULT_TP_HIT_EXPLICIT_MARKERS = (
    "тейк достиг",
    "тейк достигнут",
    "тейк профит достигнут",
    "цели достигнуты",
    "с профитом",
    "поздравляю с профитом",
    "сделка полностью реализована",
    "полностью реализована",
    "сделка полностью закрыта по тейк профиту",
    "полностью закрыта по тейк профиту",
    "закрыта в +",
    "закрываю в +",
    "take profit hit",
    "tp hit",
    "target hit",
)
_DEFAULT_STOP_HIT_EXPLICIT_MARKERS = (
    "по стопу",
    "по стоп лоссу",
    "по стоп лосс",
    "закрылись по стопу",
    "закрылись по стоп лоссу",
    "закрыта по стоп лоссу",
    "тут стоп",
    "обидный стоп",
    "стоп (-",
    "стоп лосс (-",
    "сделка закрыта по стоп лоссу",
    "тут к сожалению стоп",
)
_DEFAULT_MARKET_CONTEXT_SPOT_MARKERS = ("сделка на споте", "на споте", "spot")
_DEFAULT_ENTRY_ORDER_MARKET_MARKERS = (
    "по текущим",
    "вход с текущих",
    "+- по текущим",
    "+- с текущих",
    "± по текущим",
    "± с текущих",
)
_DEFAULT_SIDE_LONG_MARKERS = ("лонг", "long", "buy")
_DEFAULT_SIDE_SHORT_MARKERS = ("шорт", "short", "sell")
_DEFAULT_MOVE_STOP_TO_BE_FALLBACK_MARKERS = ("под минимум", "под локальный минимум")
_DEFAULT_GLOBAL_CLOSE_SCOPE_MARKERS: dict[str, tuple[str, ...]] = {
    "ALL_LONGS": ("все лонги", "все long", "all longs", "all long positions"),
    "ALL_SHORTS": ("все шорты", "все short", "all shorts", "all short positions"),
    # "все позиции" / "все сделки" = entire portfolio scope → ALL_ALL
    "ALL_ALL": (
        "все позиции",
        "все сделки",
        "все открытые",
        "all positions",
        "all trades",
        "all open positions",
    ),
}
_DEFAULT_ACTION_REQUEST_MARKERS = (
    "закрываю",
    "закрыть",
    "переносим",
    "перенести",
    "закрываем",
)
_DEFAULT_EVENT_REPORTED_MARKERS = (
    "закрыта",
    "закрылись",
    "закрылся",
    "ушла в бу",
    "ушел в бу",
    "сделка закрыта",
    "закрылась",
)
_DEFAULT_CANCEL_PENDING_MARKERS = (
    "cancel pending",
    "cancel limit",
    "remove pending",
    "не актуально",
    "пока не актуально",
)
_CANCEL_ONLY_MARKERS = ("не актуально", "пока не актуально")


class TraderBProfileParser:
    trader_code = "trader_b"

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)
        self._engine = RulesEngine.load(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        target_refs = self._extract_targets(prepared=prepared, context=context)
        message_type = self._classify_message(prepared=prepared)
        intents = self._extract_intents(prepared=prepared, message_type=message_type, target_refs=target_refs)
        global_target_scope = self._resolve_global_target_scope(prepared=prepared) if message_type == "UPDATE" else None
        entities = self._extract_entities(prepared=prepared, intents=intents, message_type=message_type, target_refs=target_refs, global_target_scope=global_target_scope)
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
        warnings = self._build_warnings(prepared=prepared, message_type=message_type, target_refs=target_refs, intents=intents, global_target_scope=global_target_scope)
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)
        return TraderParseResult(
            message_type=message_type,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            warnings=warnings,
            confidence=confidence,
            target_scope=target_scope,
            linking=linking,
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
        has_market_entry_marker = self._contains_any(normalized, _merge_markers(self._as_markers("entry_order_markers", "market"), _DEFAULT_ENTRY_ORDER_MARKET_MARKERS))
        has_entry = _extract_entry(raw_text) is not None or has_market_entry_marker
        has_stop = _extract_stop(raw_text) is not None
        has_tp = bool(_extract_take_profits(raw_text))

        if has_symbol and has_side and has_entry and has_stop and has_tp:
            return "NEW_SIGNAL"

        if has_symbol and has_side and has_entry and has_stop and not has_tp:
            return "SETUP_INCOMPLETE"

        # Passive/commentary messages are INFO_ONLY — check before UPDATE so that
        # phrases like "сделка закрыта" aren't promoted to actionable updates.
        if self._contains_any(normalized, _DEFAULT_INFO_ONLY_MARKERS):
            return "INFO_ONLY"

        update_intent_markers = _merge_markers(
            _merge_markers(
                _merge_markers(
                    _merge_markers(self._as_markers("intent_markers", "U_CLOSE_FULL"), _DEFAULT_CLOSE_FULL_EXTRA_MARKERS),
                    _merge_markers(self._as_markers("intent_markers", "U_TP_HIT_EXPLICIT"), _DEFAULT_TP_HIT_EXPLICIT_MARKERS),
                ),
                _merge_markers(self._as_markers("intent_markers", "U_STOP_HIT"), _DEFAULT_STOP_HIT_EXPLICIT_MARKERS),
            ),
            _merge_markers(self._as_markers("intent_markers", "U_CANCEL_PENDING_ORDERS"), _DEFAULT_CANCEL_PENDING_MARKERS),
        )
        # Use RulesEngine for marker-based UPDATE classification (reads classification_markers.update
        # strong/weak from parsing_rules.json), supplemented by default fallback markers.
        engine_result = self._engine.classify(raw_text)
        if engine_result.message_type == "UPDATE" or self._contains_any(
            normalized, _merge_markers(_DEFAULT_UPDATE_FALLBACK_MARKERS, update_intent_markers)
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

        # BE / stop movement detection
        be_markers = _merge_markers(self._as_markers("intent_markers", "U_MOVE_STOP_TO_BE"), ())
        has_be = self._contains_any(normalized, be_markers) or self._contains_any(normalized, _DEFAULT_MOVE_STOP_TO_BE_FALLBACK_MARKERS)
        stop_level = _extract_stop_level(raw_text)
        move_stop_markers = _merge_markers(
            self._as_markers("intent_markers", "U_MOVE_STOP"),
            ("stop on 1 tp", "stop on tp1", "stop on first tp", "new sl", "update stop"),
        )
        has_structural_move_stop = self._contains_any(normalized, move_stop_markers)

        # Detect explicit BE-movement commands (not mere commentary like "уходит в бу")
        _explicit_be_words = ("переносим", "перенести", "без риска", "безрисковой", "без рисковой", "в безубыток")
        has_explicit_be_command = has_be and any(w in normalized for w in _explicit_be_words)
        has_be_fallback = self._contains_any(normalized, _DEFAULT_MOVE_STOP_TO_BE_FALLBACK_MARKERS)

        if has_explicit_be_command or has_be_fallback:
            intents.append("U_MOVE_STOP_TO_BE")
            if stop_level is not None or has_structural_move_stop:
                intents.append("U_MOVE_STOP")
        elif stop_level is not None or has_structural_move_stop:
            intents.append("U_MOVE_STOP")

        close_full_markers = _merge_markers(
            self._as_markers("intent_markers", "U_CLOSE_FULL"),
            _DEFAULT_CLOSE_FULL_EXTRA_MARKERS,
        )
        if self._contains_any(normalized, close_full_markers):
            intents.append("U_CLOSE_FULL")

        if self._contains_any(
            normalized,
            _merge_markers(self._as_markers("intent_markers", "U_STOP_HIT"), _DEFAULT_STOP_HIT_EXPLICIT_MARKERS),
        ):
            intents.append("U_STOP_HIT")
            # A stop hit always implies a forced close — ensure U_CLOSE_FULL is present
            if "U_CLOSE_FULL" not in intents:
                intents.append("U_CLOSE_FULL")

        if self._contains_any(
            normalized,
            _merge_markers(self._as_markers("intent_markers", "U_TP_HIT_EXPLICIT"), _DEFAULT_TP_HIT_EXPLICIT_MARKERS),
        ):
            intents.append("U_TP_HIT")

        # U_CANCEL_PENDING_ORDERS checked first; U_INVALIDATE_SETUP only added when cancel
        # is NOT already present (invalidation is a subtype — cancel subsumes it).
        if self._contains_any(normalized, _merge_markers(self._as_markers("intent_markers", "U_CANCEL_PENDING_ORDERS"), _DEFAULT_CANCEL_PENDING_MARKERS)):
            intents.append("U_CANCEL_PENDING_ORDERS")
        elif _INVALIDATE_RE.search(raw_text):
            intents.append("U_INVALIDATE_SETUP")

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
            entities["new_stop_price"] = stop_level if isinstance(stop_level, float) else None
            entities["new_stop_reference_text"] = "BREAKEVEN"
        elif "U_MOVE_STOP" in intents:
            stop_level = _extract_stop_level(raw_text)
            if stop_level is not None:
                entities["new_stop_level"] = stop_level
                entities["new_stop_price"] = stop_level
            else:
                clean_ref = _extract_stop_reference_text(raw_text)
                full_ref = _extract_stop_reference_text_with_prep(raw_text)
                entities["new_stop_reference_text"] = clean_ref
                entities["stop_reference_text"] = full_ref

        if "U_CLOSE_FULL" in intents:
            close_scope = self._resolve_global_target_scope(prepared=prepared)
            entities["close_scope"] = close_scope or "FULL"
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities["result_percent"] = result_percent

        if "U_STOP_HIT" in intents:
            entities["hit_target"] = "STOP"
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities.setdefault("result_percent", result_percent)
        elif "U_TP_HIT" in intents:
            entities["hit_target"] = "TP"
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities.setdefault("result_percent", result_percent)

        if "U_REPORT_FINAL_RESULT" in intents:
            result_percent = _extract_result_percent(raw_text)
            if result_percent is not None:
                entities["result_percent"] = result_percent

        if "U_CANCEL_PENDING_ORDERS" in intents or "U_INVALIDATE_SETUP" in intents:
            entities["cancel_scope"] = _derive_cancel_scope(raw_text, target_refs=target_refs)

        if message_type in {"UPDATE", "INFO_ONLY"}:
            entities["update_tense"] = self._detect_update_tense(normalized=normalized)

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

    def _resolve_global_target_scope(self, *, prepared: dict[str, Any]) -> str | None:
        normalized = str(prepared.get("normalized_text") or "")
        # Check rules-defined global markers first
        marker_map = self._marker_map("global_target_markers")
        for scope in ("ALL_LONGS", "ALL_SHORTS", "ALL_ALL", "ALL_OPEN", "ALL_REMAINING"):
            markers = marker_map.get(scope, ())
            if markers and self._contains_any(normalized, markers):
                return scope
        # Fallback to defaults
        for scope, markers in _DEFAULT_GLOBAL_CLOSE_SCOPE_MARKERS.items():
            if self._contains_any(normalized, markers):
                return scope
        return None

    def _is_cancel_only_message(self, *, normalized: str) -> bool:
        if not self._contains_any(normalized, _CANCEL_ONLY_MARKERS):
            return False
        if self._contains_any(normalized, self._operational_markers()):
            return False
        return True

    def _operational_markers(self) -> tuple[str, ...]:
        markers = (
            *self._as_markers("intent_markers", "U_MOVE_STOP_TO_BE"),
            *self._as_markers("intent_markers", "U_MOVE_STOP"),
            *self._as_markers("intent_markers", "U_CLOSE_FULL"),
            *self._as_markers("intent_markers", "U_STOP_HIT"),
            *self._as_markers("intent_markers", "U_TP_HIT_EXPLICIT"),
            *self._as_markers("intent_markers", "U_CANCEL_PENDING_ORDERS"),
            *self._as_markers("intent_markers", "U_REPORT_FINAL_RESULT"),
        )
        return tuple(marker for marker in markers if marker not in {"бу", "в бу"})

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
        # A new signal creation always applies to itself — no external target needed
        if "NS_CREATE_SIGNAL" in intents:
            return {"kind": "signal", "scope": "self", "target_count": 0}
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
        has_global = global_target_scope is not None
        strategy = (
            "reply_or_link"
            if (target_refs or context.reply_to_message_id)
            else ("global_scope" if has_global else "unresolved")
        )
        return {
            "targeted": bool(target_refs or context.reply_to_message_id or has_global),
            "reply_to_message_id": context.reply_to_message_id,
            "target_refs_count": len(target_refs),
            "has_global_target_scope": has_global,
            "telegram_link_count": sum(1 for ref in target_refs if ref.get("kind") == "telegram_link"),
            "strategy": strategy,
        }

    def _detect_update_tense(self, *, normalized: str) -> str:
        if self._contains_any(normalized, _DEFAULT_ACTION_REQUEST_MARKERS):
            return "ACTION_REQUEST"
        if self._contains_any(normalized, _DEFAULT_EVENT_REPORTED_MARKERS):
            return "EVENT_REPORTED"
        return "UNSPECIFIED"

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
    """Extract the most prominent signed percent value from update messages.

    Prefers explicit sign (+/-) and handles patterns like:
    '+5%', '-1%', '(≈0.2%)', '(-0.5%)', 'в +2.3%', 'стоп (-1%)'
    """
    best: float | None = None
    for match in _PERCENT_RE.finditer(raw_text):
        sign = match.group("sign") or ""
        value = _to_float(match.group("value"))
        if value is None:
            continue
        # Determine sign from context: look one char before the match for '('
        start = match.start()
        context_char = raw_text[start - 1] if start > 0 else ""
        if sign == "-" or (not sign and context_char == "(" and raw_text[start:].startswith(f"({match.group('value')}%") is False):
            # Check if there's a '-' inside parentheses: '(-0.5%)'
            prefix = raw_text[max(0, start - 2): start]
            if "-" in prefix or sign == "-":
                signed_value = -abs(value)
            else:
                signed_value = value
        else:
            signed_value = value if sign != "-" else -abs(value)

        # Prefer the first match that has an explicit sign
        if best is None or sign in ("+", "-"):
            best = signed_value
            if sign in ("+", "-"):
                break
    return best


def _extract_stop_reference_text(raw_text: str) -> str | None:
    """Extract the stop reference label WITHOUT the leading preposition."""
    match = re.search(
        r"(?:переносим|перенести|переставляем|стоп\s*лосс\s*переносим)\s*(?:на|за|под|в)\s*(?P<value>[^\n.,;:]+)",
        raw_text,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = str(match.group("value")).strip()
    return value or None


def _extract_stop_reference_text_with_prep(raw_text: str) -> str | None:
    """Extract the stop reference phrase INCLUDING the leading preposition."""
    match = re.search(
        r"(?:переносим|перенести|переставляем|стоп\s*лосс\s*переносим)\s*(?P<value>(?:на|за|под|в)\s*[^\n.,;:]+)",
        raw_text,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = str(match.group("value")).strip()
    return value or None


def _derive_cancel_scope(raw_text: str, *, target_refs: list | None = None) -> str:
    normalized = normalize_text(raw_text)
    # Telegram links or reply-to always target a specific signal
    if extract_telegram_links(raw_text):
        return "TARGETED"
    if target_refs and any(ref.get("kind") in {"reply", "message_id"} for ref in target_refs):
        return "TARGETED"
    if "all shorts" in normalized or "все шорты" in normalized:
        return "ALL_SHORT"
    if "all longs" in normalized or "все лонги" in normalized:
        return "ALL_LONG"
    if "все позиции" in normalized or "все сделки" in normalized or "all positions" in normalized or "all trades" in normalized:
        return "ALL_OPEN"
    return "ALL_PENDING_ENTRIES"


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
