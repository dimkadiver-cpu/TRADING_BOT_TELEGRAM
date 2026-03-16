"""Trader D profile parser."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_SYMBOL_RE = re.compile(r"\$?#?(?P<symbol>[A-Z0-9]{2,20}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b", re.IGNORECASE)
_BARE_SYMBOL_RE = re.compile(r"^\s*\$?#?(?P<symbol>[A-Z]{2,12})(?![A-Z])\b")
_ENTRY_RE = re.compile(
    r"(?:entry|entries|вход(?:\s+с\s+текущих)?)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"(?:\bsl\b|stop(?:\s*loss)?|стоп)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_TP_RE = re.compile(
    r"(?:\btp(?:\d+)?\b|тп(?:\d+)?|тейк(?:и)?|take\s*profit)\s*[:=@+\-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_RISK_RE = re.compile(r"риск\s*[:=]?\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*%?", re.IGNORECASE)
_PARTIAL_PERCENT_RE = re.compile(r"(?P<value>\d{1,3}(?:[.,]\d+)?)\s*%\s*(?:срежем|режем|фикс)", re.IGNORECASE)
_TP_HIT_RE = re.compile(r"\b(?:tp|тп)\s*(?P<index>\d+)\s*\+", re.IGNORECASE)
_STOP_LEVEL_RE = re.compile(
    r"(?:стоп\s*(?:переставляем|переносим)?\s*(?:в\s*\+)?\s*на|move\s*(?:stop|sl)\s*(?:to)?|на\s*\+?\s*)(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_RESULT_R_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*[rр]\b", re.IGNORECASE)

_DEFAULT_IGNORE_MARKERS = ("#админ", "#admin")
_DEFAULT_MARKET_ENTRY_MARKERS = ("вход с текущих", "рыночный", "market")
_DEFAULT_SIDE_LONG_MARKERS = ("лонг", "long", "buy")
_DEFAULT_SIDE_SHORT_MARKERS = ("шорт", "short", "sell")
_DEFAULT_UPDATE_MARKERS = (
    "перевод в безубыток",
    "перевод в бу",
    "стоп в бу",
    "tp1+",
    "tp2+",
    "tp3+",
    "срежем",
    "закрываем полностью",
    "закрываем",
    "стоп переставляем",
)
_DEFAULT_MOVE_STOP_TO_BE_MARKERS = ("перевод в безубыток", "перевод в бу", "стоп в бу", "безубыток", "бу")
_DEFAULT_MOVE_STOP_MARKERS = ("стоп переставляем", "move stop", "move sl", "стоп переносим")
_DEFAULT_CLOSE_PARTIAL_MARKERS = ("срежем", "partial", "частично")
_DEFAULT_CLOSE_FULL_MARKERS = ("закрываем полностью", "close full", "закрываем")
_DEFAULT_TP_HIT_MARKERS = ("tp1+", "tp2+", "tp3+", "тп1+", "тп2+", "тп3+")


class TraderDProfileParser:
    trader_code = "trader_d"

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        target_refs = self._extract_targets(prepared=prepared, context=context)
        message_type = self._classify_message(prepared=prepared)
        intents = self._extract_intents(prepared=prepared, message_type=message_type)
        if message_type == "UNCLASSIFIED" and intents:
            if any(intent.startswith("U_") and intent != "U_REPORT_FINAL_RESULT" for intent in intents):
                message_type = "UPDATE"
        entities = self._extract_entities(prepared=prepared, intents=intents, message_type=message_type)
        reported_results = self._extract_reported_results(prepared=prepared, intents=intents, entities=entities)
        warnings = self._build_warnings(
            prepared=prepared,
            message_type=message_type,
            target_refs=target_refs,
            intents=intents,
            entities=entities,
        )
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)
        return TraderParseResult(
            message_type=message_type,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            reported_results=reported_results,
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
        has_entry_price = _extract_entry(raw_text) is not None
        has_market_entry = self._contains_any(
            normalized,
            _merge_markers(self._as_markers("entry_order_markers", "market"), _DEFAULT_MARKET_ENTRY_MARKERS),
        )
        has_risk = _extract_risk_percent(raw_text) is not None
        has_stop = _extract_stop(raw_text) is not None
        has_tp = bool(_extract_take_profits(raw_text))

        if has_symbol and has_side and has_stop and has_tp and (has_entry_price or has_market_entry or has_risk):
            return "NEW_SIGNAL"

        if self._contains_any(
            normalized,
            _merge_markers(self._as_markers("classification_markers", "update"), _DEFAULT_UPDATE_MARKERS),
        ):
            return "UPDATE"

        if has_symbol and (has_side or has_stop or has_tp):
            return "SETUP_INCOMPLETE"
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

        if self._contains_any(
            normalized,
            _merge_markers(self._as_markers("intent_markers", "U_MOVE_STOP_TO_BE"), _DEFAULT_MOVE_STOP_TO_BE_MARKERS),
        ):
            intents.extend(["U_MOVE_STOP_TO_BE", "U_MOVE_STOP"])
        elif self._contains_any(
            normalized,
            _merge_markers(self._as_markers("intent_markers", "U_MOVE_STOP"), _DEFAULT_MOVE_STOP_MARKERS),
        ):
            intents.append("U_MOVE_STOP")

        tp_hit_markers = _merge_markers(self._as_markers("intent_markers", "U_TP_HIT"), _DEFAULT_TP_HIT_MARKERS)
        if _TP_HIT_RE.search(normalized) or self._contains_any(normalized, tp_hit_markers):
            intents.append("U_TP_HIT")

        partial_markers = _merge_markers(self._as_markers("intent_markers", "U_CLOSE_PARTIAL"), _DEFAULT_CLOSE_PARTIAL_MARKERS)
        if _PARTIAL_PERCENT_RE.search(normalized) or self._contains_any(normalized, partial_markers):
            intents.append("U_CLOSE_PARTIAL")

        full_markers = _merge_markers(self._as_markers("intent_markers", "U_CLOSE_FULL"), _DEFAULT_CLOSE_FULL_MARKERS)
        if self._contains_any(normalized, full_markers):
            intents.append("U_CLOSE_FULL")

        if "U_CLOSE_FULL" in intents and "U_CLOSE_PARTIAL" in intents:
            intents = [intent for intent in intents if intent != "U_CLOSE_PARTIAL"]

        if _RESULT_R_RE.search(normalized):
            intents.append("U_REPORT_FINAL_RESULT")
        return _unique(intents)

    def _extract_entities(self, *, prepared: dict[str, Any], intents: list[str], message_type: str) -> dict[str, Any]:
        raw_text = str(prepared.get("raw_text") or "")
        normalized = str(prepared.get("normalized_text") or "")
        entities: dict[str, Any] = {}

        if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
            entry = _extract_entry(raw_text)
            is_market_entry = self._contains_any(
                normalized,
                _merge_markers(self._as_markers("entry_order_markers", "market"), _DEFAULT_MARKET_ENTRY_MARKERS),
            )
            order_type = "MARKET" if is_market_entry else "LIMIT"
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
                    "risk_percent": _extract_risk_percent(raw_text),
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
            if isinstance(stop_level, float):
                entities["new_stop_level"] = stop_level

        if "U_TP_HIT" in intents:
            entities["hit_target"] = _extract_tp_hit_target(normalized) or "TP"

        if "U_CLOSE_PARTIAL" in intents:
            entities["close_scope"] = "PARTIAL"
            close_fraction = _extract_partial_fraction(normalized)
            if close_fraction is not None:
                entities["close_fraction"] = close_fraction
        elif "U_CLOSE_FULL" in intents:
            entities["close_scope"] = "FULL"

        return entities

    def _extract_reported_results(
        self,
        *,
        prepared: dict[str, Any],
        intents: list[str],
        entities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if "U_REPORT_FINAL_RESULT" not in intents:
            return []
        raw_text = str(prepared.get("raw_text") or "")
        symbol = entities.get("symbol") if isinstance(entities.get("symbol"), str) else _extract_symbol(raw_text)
        out: list[dict[str, Any]] = []
        for match in _RESULT_R_RE.finditer(raw_text):
            value = _to_float(match.group("value"))
            if value is None:
                continue
            out.append({"symbol": symbol, "value": value, "unit": "R"})
        return out

    def _build_warnings(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        target_refs: list[dict[str, Any]],
        intents: list[str],
        entities: dict[str, Any],
    ) -> list[str]:
        if message_type != "UPDATE":
            return []
        if not any(intent.startswith("U_") and intent != "U_REPORT_FINAL_RESULT" for intent in intents):
            return []
        has_symbol_hint = isinstance(entities.get("symbol"), str) and bool(str(entities.get("symbol")).strip())
        if target_refs or has_symbol_hint:
            return []
        _ = prepared
        return ["trader_d_update_missing_target"]

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
        if isinstance(node, dict):
            out: list[str] = []
            for value in node.values():
                if isinstance(value, list):
                    out.extend(str(item).strip().lower() for item in value if str(item).strip())
            return tuple(_unique(out))
        return ()

    @staticmethod
    def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in markers if marker)


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".").replace(";", ".").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_symbol(raw_text: str) -> str | None:
    match = _SYMBOL_RE.search(raw_text.upper())
    if match:
        return str(match.group("symbol")).upper()
    bare = _BARE_SYMBOL_RE.search(raw_text.upper())
    if not bare:
        return None
    token = str(bare.group("symbol")).upper()
    if token.endswith(("USDT", "USDC", "USD", "BTC", "ETH", ".P")):
        return token
    return f"{token}USDT"


def _extract_side(normalized: str, *, long_markers: tuple[str, ...], short_markers: tuple[str, ...]) -> str | None:
    if any(marker in normalized for marker in short_markers):
        return "SHORT"
    if any(marker in normalized for marker in long_markers):
        return "LONG"
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


def _extract_risk_percent(raw_text: str) -> float | None:
    match = _RISK_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_tp_hit_target(normalized_text: str) -> str | None:
    match = _TP_HIT_RE.search(normalized_text)
    if not match:
        return None
    return f"TP{match.group('index')}"


def _extract_stop_level(raw_text: str) -> float | None:
    match = _STOP_LEVEL_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_partial_fraction(normalized_text: str) -> float | None:
    match = _PARTIAL_PERCENT_RE.search(normalized_text)
    if not match:
        return None
    value = _to_float(match.group("value"))
    if value is None:
        return None
    return round(max(0.0, min(1.0, value / 100.0)), 6)


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
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
