"""Trader C deterministic lifecycle-oriented profile parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_SYMBOL_RE = re.compile(r"\$(?P<symbol>[A-Z0-9]{2,20}(?:USDT|USDC|USD|BTC|ETH)?)\b", re.IGNORECASE)
_SIDE_RE = re.compile(r"\b(?P<side>LONG|SHORT|ЛОНГ|ШОРТ)\b", re.IGNORECASE)
_RISK_RE = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*%\s*деп", re.IGNORECASE)
_STOP_RE = re.compile(r"\b(?:stop|стоп(?:\s*лосс)?)\s*[:\- ]*\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RANGE_ENTRY_RE = re.compile(r"вход[^\n]*?(?P<a>\d[\d\s]*(?:[.,]\d+)?)\s*[-–]\s*(?P<b>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_LIMIT_ENTRY_RE = re.compile(r"вход\s+лимит(?:кой|ка)?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TRANCHE_RE = re.compile(r"(?P<idx>\d+)\)\s*(?P<price>\d[\d\s]*(?:[.,]\d+)?)\s*\((?P<size>\d/\d)\)", re.IGNORECASE)
_TP_PRICE_RE = re.compile(r"(?:тейк[- ]?профит|tейк[- ]?профит|тп|tp)\s*\d*\s*[:\- ]*\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TP_HIT_RE = re.compile(r"(?:tp|тп|тейк)\s*(?P<idx>\d)", re.IGNORECASE)
_PARTIAL_PERCENT_RE = re.compile(r"\((?P<value>\d+(?:[.,]\d+)?)%\)")
_PARTIAL_PRICE_RE = re.compile(r"(?:по\s+текущим|по)\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RR_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*RR", re.IGNORECASE)

_ACTIVATION_MARKERS = ("первая лимитка сработала", "активировалась")
_TP_HIT_MARKERS = ("tp1", "tp2", "tp3", "tp4", "тп1", "тп2", "тп3", "тп4", "тейк 1", "позиция закрыта по тейку")
_MOVE_BE_MARKERS = ("в бу перевел", "после первого тп в бу", "стоп в б/у", "стоп в бу")
_EXIT_BE_MARKERS = ("ушли в б/у", "позиция закрыта в бу", "закрыто в бу", "остаток ушел в бу", "закрыт остаток в бу", "остаток в бу уш")
_CLOSE_PARTIAL_MARKERS = ("скинул часть", "закрыл часть", "снял часть")
_CLOSE_FULL_MARKERS = (
    "закрываю по рынку",
    "закрыл по рынку",
    "закрываю на точке входа",
    "закрыл не нравится",
    "остаток закрыт по рынку",
    "закрыт остаток",
    "закрыл в бу",
    "закрыл -",
    "закрыл по текущим",
)
_CANCEL_PENDING_MARKERS = ("не актуально", "убрал лимитку", "ушел без нас", "улетели")
_REMOVE_PENDING_MARKERS = ("доливку убрал", "добор убрал", "лимитку с", "доливку убираем")
_UPDATE_TP_MARKERS = ("изменения", "тп дополнительный", "актуально если прид")
_UPDATE_STOP_MARKERS = ("стоп переносим",)
_REENTER_MARKERS = ("перезаход", "re-enter")


class TraderCProfileParser(TraderBProfileParser):
    trader_code = "trader_c"

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        message_type = self._classify_message(prepared=prepared)
        entities = self._extract_entities(prepared=prepared, message_type=message_type)
        intents = self._extract_intents(prepared=prepared, message_type=message_type, entities=entities)
        target_refs = self._extract_targets(prepared=prepared, context=context, entities=entities)
        warnings = self._build_warnings(message_type=message_type, intents=intents, target_refs=target_refs, entities=entities)
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)

        linking = self._build_linking(target_refs=target_refs, context=context)
        target_scope = {"kind": "signal", "scope": "single" if linking["targeted"] else "unknown"}

        return TraderParseResult(
            message_type=message_type,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            warnings=warnings,
            confidence=confidence,
            primary_intent=self._derive_primary_intent(message_type=message_type, intents=intents),
            actions_structured=self._build_actions_structured(message_type=message_type, intents=intents, entities=entities),
            linking=linking,
            target_scope=target_scope,
            diagnostics={"parser_version": "trader_c_v1", "warning_count": len(warnings)},
        )

    def _preprocess(self, *, text: str, context: ParserContext) -> dict[str, Any]:
        raw_text = text or context.raw_text
        return {"raw_text": raw_text, "normalized_text": normalize_text(raw_text)}

    def _classify_message(self, *, prepared: dict[str, Any]) -> str:
        raw_text = str(prepared.get("raw_text") or "")
        normalized = str(prepared.get("normalized_text") or "")
        if self._contains_any(normalized, self._as_markers("classification_markers", "info_only")):
            return "INFO_ONLY"

        has_symbol = _extract_symbol(raw_text) is not None
        has_side = _extract_side(raw_text) is not None
        has_stop = _extract_stop(raw_text) is not None
        has_tp = bool(_extract_take_profits(raw_text)) or any(token in normalized for token in ("тейк", "tейк", "тп", "tp"))
        has_entry_signal = bool(_RANGE_ENTRY_RE.search(raw_text) or _LIMIT_ENTRY_RE.search(raw_text) or _TRANCHE_RE.search(raw_text) or "вход" in normalized)
        if has_symbol and has_side and has_stop and has_tp and has_entry_signal:
            return "NEW_SIGNAL"

        if self._is_operational_update(normalized=normalized):
            return "UPDATE"
        return "UNCLASSIFIED"

    def _extract_entities(self, *, prepared: dict[str, Any], message_type: str) -> dict[str, Any]:
        raw_text = str(prepared.get("raw_text") or "")
        normalized = str(prepared.get("normalized_text") or "")
        entities: dict[str, Any] = {}

        symbol = _extract_symbol(raw_text)
        if symbol:
            entities["symbol"] = symbol

        if message_type == "NEW_SIGNAL":
            side = _extract_side(raw_text)
            stop = _extract_stop(raw_text)
            take_profits = _extract_take_profits(raw_text)
            entries, order_type, entry_text = _extract_entries(raw_text)
            risk_raw, risk_norm = _extract_risk(raw_text)
            entities.update(
                {
                    "side": side,
                    "entry_order_type": order_type,
                    "entries": entries,
                    "entry": [entries[0]["price"]] if entries else [],
                    "entry_text_raw": entry_text,
                    "stop_loss": stop,
                    "stop_text_raw": _extract_stop_text(raw_text),
                    "take_profits": take_profits,
                    "take_profits_text_raw": _extract_tp_text(raw_text),
                    "risk_value_raw": risk_raw,
                    "risk_value_normalized": risk_norm,
                    "entry_plan_type": "MULTI" if len(entries) > 1 else "SINGLE",
                    "entry_structure": "LADDER" if len(entries) > 1 else ("RANGE" if order_type == "RANGE" else "ONE_SHOT"),
                    "has_averaging_plan": len(entries) > 1,
                }
            )

        if message_type == "UPDATE":
            partial_percent = _extract_partial_percent(raw_text)
            partial_price = _extract_partial_price(raw_text)
            if partial_percent is not None:
                entities["partial_close_percent"] = partial_percent
            if partial_price is not None:
                entities["partial_close_price"] = partial_price
            rr = _extract_rr(raw_text)
            if rr is not None:
                entities["reported_rr"] = rr
            be_price = _extract_be_price(raw_text)
            if be_price is not None:
                entities["new_stop_price"] = be_price

            hit_targets = _extract_hit_targets(raw_text)
            if hit_targets:
                entities["hit_targets"] = hit_targets
                entities["max_target_hit"] = max(hit_targets)

            updated_tp = _extract_update_tp(raw_text, normalized)
            if updated_tp:
                entities.update(updated_tp)

            if "стоп переносим" in normalized:
                new_stop = _extract_stop_update(raw_text)
                if new_stop is not None:
                    entities["new_stop_price"] = new_stop

            close_price = _extract_close_price(raw_text)
            if close_price is not None:
                entities["close_price"] = close_price

        return {k: v for k, v in entities.items() if v is not None}

    def _extract_intents(self, *, prepared: dict[str, Any], message_type: str, entities: dict[str, Any]) -> list[str]:
        if message_type == "NEW_SIGNAL":
            return ["NS_CREATE_SIGNAL"]
        if message_type != "UPDATE":
            return []

        normalized = str(prepared.get("normalized_text") or "")
        intents: list[str] = []
        if self._contains_any(normalized, _ACTIVATION_MARKERS):
            intents.append("U_ACTIVATION")
        if self._contains_any(normalized, _TP_HIT_MARKERS):
            intents.append("U_TP_HIT")
        if self._contains_any(normalized, _MOVE_BE_MARKERS):
            intents.extend(["U_MOVE_STOP_TO_BE", "U_MOVE_STOP"])
        if self._contains_any(normalized, _EXIT_BE_MARKERS):
            intents.append("U_EXIT_BE")
        if self._contains_any(normalized, _CLOSE_PARTIAL_MARKERS):
            intents.append("U_CLOSE_PARTIAL")
        if self._contains_any(normalized, _CLOSE_FULL_MARKERS):
            intents.append("U_CLOSE_FULL")
        if self._contains_any(normalized, _CANCEL_PENDING_MARKERS):
            intents.append("U_CANCEL_PENDING_ORDERS")
        if self._contains_any(normalized, _REMOVE_PENDING_MARKERS):
            intents.append("U_REMOVE_PENDING_ENTRY")
        if self._contains_any(normalized, _UPDATE_TP_MARKERS) or _looks_like_tp_update(normalized):
            intents.append("U_UPDATE_TAKE_PROFITS")
        if self._contains_any(normalized, _UPDATE_STOP_MARKERS):
            intents.append("U_UPDATE_STOP")
        if "стоп -" in normalized:
            intents.append("U_STOP_HIT")
        if self._contains_any(normalized, _REENTER_MARKERS):
            intents.append("U_REENTER")

        if entities.get("partial_close_percent") is not None:
            intents.append("U_CLOSE_PARTIAL")
        return _unique(intents)

    def _extract_targets(self, *, prepared: dict[str, Any], context: ParserContext, entities: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _add(kind: str, ref: object) -> None:
            key = (kind, str(ref))
            if key in seen:
                return
            seen.add(key)
            out.append({"kind": kind, "ref": ref})

        if context.reply_to_message_id is not None:
            _add("reply", int(context.reply_to_message_id))

        raw_text = str(prepared.get("raw_text") or "")
        for link in [*context.extracted_links, *extract_telegram_links(raw_text)]:
            _add("telegram_link", link)
            match = _LINK_ID_RE.search(link)
            if match:
                _add("message_id", int(match.group("id")))

        if entities.get("symbol"):
            _add("symbol", entities["symbol"])
        return out

    def _build_warnings(self, *, message_type: str, intents: list[str], target_refs: list[dict[str, Any]], entities: dict[str, Any]) -> list[str]:
        if message_type != "UPDATE" or not intents:
            return []
        has_strong = any(ref.get("kind") in {"reply", "telegram_link", "message_id", "symbol"} for ref in target_refs)
        if has_strong:
            return []
        return ["trader_c_update_missing_target"]

    @staticmethod
    def _estimate_confidence(*, message_type: str, warnings: list[str]) -> float:
        if message_type == "NEW_SIGNAL":
            return 0.88
        if message_type == "UPDATE":
            return 0.8 if not warnings else 0.6
        if message_type == "INFO_ONLY":
            return 0.5
        return 0.2

    @staticmethod
    def _build_linking(*, target_refs: list[dict[str, Any]], context: ParserContext) -> dict[str, Any]:
        strategy = "reply_or_link" if (target_refs or context.reply_to_message_id) else "unresolved"
        return {
            "targeted": bool(target_refs or context.reply_to_message_id),
            "reply_to_message_id": context.reply_to_message_id,
            "target_refs_count": len(target_refs),
            "strategy": strategy,
        }

    @staticmethod
    def _derive_primary_intent(*, message_type: str, intents: list[str]) -> str | None:
        if message_type == "NEW_SIGNAL":
            return "NS_CREATE_SIGNAL"
        for intent in intents:
            if intent.startswith("U_"):
                return intent
        return None

    @staticmethod
    def _build_actions_structured(*, message_type: str, intents: list[str], entities: dict[str, Any]) -> list[dict[str, Any]]:
        if message_type == "NEW_SIGNAL":
            return [{"action": "CREATE_SIGNAL", "instrument": entities.get("symbol"), "side": entities.get("side"), "entries": entities.get("entries", []), "stop_loss": entities.get("stop_loss"), "take_profits": entities.get("take_profits", [])}]
        actions: list[dict[str, Any]] = []
        for intent in intents:
            actions.append({"action": intent})
        return actions

    @staticmethod
    def _is_operational_update(*, normalized: str) -> bool:
        return any(
            marker in normalized
            for marker in [
                *_ACTIVATION_MARKERS,
                *_TP_HIT_MARKERS,
                *_MOVE_BE_MARKERS,
                *_EXIT_BE_MARKERS,
                *_CLOSE_PARTIAL_MARKERS,
                *_CLOSE_FULL_MARKERS,
                *_CANCEL_PENDING_MARKERS,
                *_REMOVE_PENDING_MARKERS,
                *_UPDATE_TP_MARKERS,
                *_UPDATE_STOP_MARKERS,
                *_REENTER_MARKERS,
                "стоп -",
            ]
        ) or "tp" in normalized or "тп" in normalized


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_symbol(raw_text: str) -> str | None:
    match = _SYMBOL_RE.search(raw_text.upper())
    return match.group("symbol").upper() if match else None


def _extract_side(raw_text: str) -> str | None:
    match = _SIDE_RE.search(raw_text)
    if not match:
        return None
    token = match.group("side").lower()
    return "LONG" if token in {"long", "лонг"} else "SHORT"


def _extract_stop(raw_text: str) -> float | None:
    match = _STOP_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_entries(raw_text: str) -> tuple[list[dict[str, Any]], str, str | None]:
    entries: list[dict[str, Any]] = []
    for match in _TRANCHE_RE.finditer(raw_text):
        price = _to_float(match.group("price"))
        if price is None:
            continue
        entries.append({"sequence": int(match.group("idx")), "price": price, "size_hint": match.group("size")})
    if entries:
        entries.sort(key=lambda x: x["sequence"])
        return entries, "LIMIT", "TRANCHE_PLAN"

    range_match = _RANGE_ENTRY_RE.search(raw_text)
    if range_match:
        a = _to_float(range_match.group("a"))
        b = _to_float(range_match.group("b"))
        if a is not None and b is not None:
            return ([{"sequence": 1, "price": a}, {"sequence": 2, "price": b}], "RANGE", range_match.group(0))

    limit = _LIMIT_ENTRY_RE.search(raw_text)
    if limit:
        price = _to_float(limit.group("value"))
        return ([{"sequence": 1, "price": price}] if price is not None else [], "LIMIT", limit.group(0))

    normalized = normalize_text(raw_text)
    if "вход по рынку" in normalized or "вход с текущих" in normalized:
        return [], "MARKET", "MARKET_ENTRY"
    return [], "CURRENT", None


def _extract_risk(raw_text: str) -> tuple[str | None, float | None]:
    match = _RISK_RE.search(raw_text)
    if not match:
        return None, None
    raw = match.group("value")
    return raw, _to_float(raw)


def _extract_take_profits(raw_text: str) -> list[float]:
    out: list[float] = []
    for match in _TP_PRICE_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is not None and value not in out:
            out.append(value)
    if not out:
        line_match = re.search(r"(?:тейк[- ]?профит|tейк[- ]?профит)\s*(?P<tail>[^\n]+)", raw_text, re.IGNORECASE)
        if line_match:
            for raw in re.findall(r"\d[\d\s]*(?:[.,]\d+)?", line_match.group("tail")):
                value = _to_float(raw)
                if value is not None and value not in out:
                    out.append(value)
    return out


def _extract_stop_text(raw_text: str) -> str | None:
    match = _STOP_RE.search(raw_text)
    return match.group(0) if match else None


def _extract_tp_text(raw_text: str) -> str | None:
    return "\n".join(m.group(0) for m in _TP_PRICE_RE.finditer(raw_text)) or None


def _extract_hit_targets(raw_text: str) -> list[int]:
    out = sorted({int(m.group("idx")) for m in _TP_HIT_RE.finditer(raw_text)})
    return out


def _extract_partial_percent(raw_text: str) -> float | None:
    match = _PARTIAL_PERCENT_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_partial_price(raw_text: str) -> float | None:
    match = _PARTIAL_PRICE_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_rr(raw_text: str) -> float | None:
    match = _RR_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_be_price(raw_text: str) -> float | None:
    match = re.search(r"в\s*бу\s*перевел\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_update_tp(raw_text: str, normalized: str) -> dict[str, Any]:
    if not _looks_like_tp_update(normalized):
        return {}
    match = re.search(r"тп\s*(?P<idx>\d)\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)?", raw_text, re.IGNORECASE)
    if not match:
        return {"tp_update_note": raw_text}
    out: dict[str, Any] = {"tp_update_index": int(match.group("idx"))}
    if match.group("value"):
        out["tp_update_price"] = _to_float(match.group("value"))
    if "дополнительный" in normalized:
        out["additional_tp"] = True
    return out


def _extract_stop_update(raw_text: str) -> float | None:
    match = re.search(r"стоп\s*переносим\s*на\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_close_price(raw_text: str) -> float | None:
    match = re.search(r"закрыл\w*[^\d]*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _looks_like_tp_update(normalized: str) -> bool:
    if any(marker in normalized for marker in _UPDATE_TP_MARKERS):
        return True
    return bool(re.search(r"(?:тп|tp)\s*\d\s*\d", normalized))


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


__all__ = ["TraderCProfileParser"]
