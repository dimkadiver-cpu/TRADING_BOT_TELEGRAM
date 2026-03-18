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
_STOP_RE = re.compile(r"\b(?:stop|стоп(?:\s*лосс)?)\s*[\.\:\-\s]*\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RANGE_ENTRY_RE = re.compile(r"вход[^\n]*?(?P<a>\d[\d\s]*(?:[.,]\d+)?)\s*[-–]\s*(?P<b>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_LIMIT_ENTRY_RE = re.compile(r"вход\s+лимит(?:кой|ка)?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TRANCHE_RE = re.compile(r"(?P<idx>\d+)\)\s*(?P<price>\d[\d\s]*(?:[.,]\d+)?)\s*\((?P<size>\d/\d)\)", re.IGNORECASE)
_TP_PRICE_RE = re.compile(r"(?:[TТtт]ейк[- ]?профит|тп|tp)\s*\d*\s*[:\- ]*\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TP_HIT_RE = re.compile(r"(?:tp|тп|тейк|тр)\s*(?P<idx>\d)", re.IGNORECASE)
_PARTIAL_PERCENT_RE = re.compile(r"\((?P<value>\d+(?:[.,]\d+)?)%\)")
_PARTIAL_PRICE_RE = re.compile(r"(?:по\s+текущим|по)\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_RR_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*RR", re.IGNORECASE)

_ACTIVATION_MARKERS = ("первая лимитка сработала", "активировалась")
_TP_HIT_MARKERS = ("tp1", "tp2", "tp3", "tp4", "тп1", "тп2", "тп3", "тп4", "тейк 1", "тр 1", "тр 2", "тр 3", "тр 4", "позиция закрыта по тейку", "с профитом")
_MOVE_BE_MARKERS = ("в бу перевел", "после первого тп в бу", "стоп в б/у", "стоп в бу", "в бу либо в микро")
_EXIT_BE_MARKERS = ("ушли в бу", "ушли в б/у", "позиция закрыта в бу", "закрыто в бу", "остаток ушел в бу", "закрыт остаток в бу", "остаток в бу уш", "остаток в бу закрыт", "сэтап закрыт в 0", "сэтэп закрыт в 0")
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
    "закрыл по",
    "закрыл в минус",
    "закрываю в минус",
)
_CANCEL_PENDING_MARKERS = ("не актуально", "убрал лимитку", "ушел без нас", "улетели")
_REMOVE_PENDING_MARKERS = ("доливку убрал", "добор убрал", "лимитку с", "доливку убираем", "лимитку убрал", "лимитки убрал")
_UPDATE_TP_MARKERS = ("изменения", "тп дополнительный", "актуально если прид")
_UPDATE_STOP_MARKERS = ("стоп переносим",)
_UPDATE_PENDING_ENTRY_MARKERS = ("лимитка", "новая лимитка", "лимитку", "на этот объем", "этот объем")
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
        has_entry_signal = bool(
            _RANGE_ENTRY_RE.search(raw_text)
            or _LIMIT_ENTRY_RE.search(raw_text)
            or _TRANCHE_RE.search(raw_text)
            or "вход" in normalized
        )
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
                    "entry_plan_entries": entries,
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

            pending_entry = _extract_pending_entry(raw_text, normalized)
            if pending_entry is not None:
                entities["pending_entry_price"] = pending_entry

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
        if self._contains_any(normalized, _TP_HIT_MARKERS) or _looks_like_tp_hit(normalized):
            intents.append("U_TP_HIT")
        if self._contains_any(normalized, _MOVE_BE_MARKERS):
            intents.append("U_MOVE_STOP_TO_BE")
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
        if self._contains_any(normalized, _UPDATE_PENDING_ENTRY_MARKERS):
            intents.append("U_UPDATE_PENDING_ENTRY")
        if "остаток в бу закрыт" in normalized:
            intents.append("U_EXIT_BE")
        if "стоп -" in normalized:
            intents.append("U_STOP_HIT")
        if self._contains_any(normalized, _REENTER_MARKERS):
            intents.append("U_REENTER")
        if "с профитом" in normalized and "rr" in normalized:
            intents.append("U_TP_HIT")

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
                *_UPDATE_PENDING_ENTRY_MARKERS,
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
    match = _SYMBOL_RE.search(_normalize_symbol_text(raw_text).upper())
    return match.group("symbol").upper() if match else None


def _extract_side(raw_text: str) -> str | None:
    match = _SIDE_RE.search(raw_text)
    if not match:
        return None
    token = match.group("side").lower()
    return "LONG" if token in {"long", "лонг"} else "SHORT"


def _extract_stop(raw_text: str) -> float | None:
    match = _STOP_RE.search(raw_text)
    if not match:
        return None
    return _to_float(match.group("value").split()[0])


def _extract_entries(raw_text: str) -> tuple[list[dict[str, Any]], str, str | None]:
    entries: list[dict[str, Any]] = []
    entry_lines = _extract_entry_block_lines(raw_text)
    if entry_lines is not None:
        block_entries = _extract_entries_from_lines(entry_lines)
        if block_entries:
            order_type = "LIMIT" if any(entry.get("price") is not None for entry in block_entries) else "MARKET"
            return block_entries, order_type, "\n".join(entry_lines) if entry_lines else "ENTRY_BLOCK"
        header_line = _find_entry_header_line(raw_text)
        header_normalized = normalize_text(header_line or "")
        if _is_implicit_market_entry_header(header_normalized):
            return (
                [
                    {
                        "sequence": 1,
                        "price": None,
                        "role": "PRIMARY",
                        "order_type": "MARKET",
                        "raw_label": "ENTRY",
                        "source_style": "MARKET",
                        "is_optional": False,
                    }
                ],
                "MARKET",
                "MARKET_ENTRY",
            )
        header_entry = _parse_entry_header(header_line or "")
        if header_entry is not None:
            return header_entry
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
    tp_lines = _extract_tp_block_lines(raw_text)
    if tp_lines is not None:
        out.extend(_extract_prices_from_lines(tp_lines))
        if out:
            return _unique_floats(out)
    for match in _TP_PRICE_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is not None and value not in out:
            out.append(value)
    if not out:
        tp_tail = _extract_tp_tail(raw_text)
        if tp_tail:
            out.extend(_extract_prices_from_lines(tp_tail.splitlines()))
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
    if not match:
        return None
    return _to_float(match.group("value").split()[0])


def _extract_close_price(raw_text: str) -> float | None:
    match = re.search(r"закрыл\w*[^\d]*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_pending_entry(raw_text: str, normalized: str) -> float | None:
    if not any(marker in normalized for marker in _UPDATE_PENDING_ENTRY_MARKERS):
        return None
    match = re.search(r"(?:лимитк\w*|новая лимитк\w*|лимитка\s+на)\s*(?:на\s*этот\s*объем\s*)?(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    if match:
        return _to_float(match.group("value"))
    match = re.search(r"на\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_tp_tail(raw_text: str) -> str | None:
    match = re.search(r"(?:[TТtт]ейк[- ]?профит|тп|tp)\s*(?:\d+)?\s*[:\- ]*(?P<tail>.*)", raw_text, re.IGNORECASE | re.S)
    return match.group("tail") if match else None


def _normalize_symbol_text(raw_text: str) -> str:
    return raw_text.translate(str.maketrans({"С": "C", "с": "c", "Т": "T", "т": "t"}))


def _looks_like_tp_update(normalized: str) -> bool:
    if any(marker in normalized for marker in _UPDATE_TP_MARKERS):
        return True
    return bool(re.search(r"(?:тп|tp)\s*\d\s*\d", normalized))


def _looks_like_tp_hit(normalized: str) -> bool:
    if any(marker in normalized for marker in _TP_HIT_MARKERS):
        return True
    return bool(re.search(r"\b(?:tp|тп|тейк|тр)\s*[1-4]\b", normalized))


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _unique_floats(values: list[float]) -> list[float]:
    out: list[float] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _extract_entry_block_lines(raw_text: str) -> list[str] | None:
    lines = raw_text.splitlines()
    start_idx = None
    stop_idx = len(lines)
    for index, line in enumerate(lines):
        normalized = normalize_text(line)
        if start_idx is None and re.search(r"\bвход\b", normalized, re.IGNORECASE):
            start_idx = index
            continue
        if start_idx is not None and re.search(r"\b(?:stop|стоп)\b", normalized, re.IGNORECASE):
            stop_idx = index
            break
    if start_idx is None:
        return None
    return [line for line in lines[start_idx + 1 : stop_idx] if line.strip()]


def _find_entry_header_line(raw_text: str) -> str | None:
    for line in raw_text.splitlines():
        if re.search(r"\bвход\b", normalize_text(line), re.IGNORECASE):
            return line
    return None


def _is_implicit_market_entry_header(normalized_header: str) -> bool:
    if not normalized_header:
        return False
    if re.search(r"\d", normalized_header):
        return False
    if any(marker in normalized_header for marker in ("лимит", "range", "рейндж")):
        return False
    return "вход" in normalized_header


def _extract_entries_from_lines(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for sequence, line in enumerate(lines, start=1):
        parsed = _parse_entry_line(line, sequence=sequence)
        if parsed is not None:
            entries.append(parsed)
    if not entries:
        return []
    for index, entry in enumerate(entries, start=1):
        if not entry.get("role"):
            entry["role"] = "PRIMARY" if index == 1 else "AVERAGING"
        if not entry.get("order_type"):
            entry["order_type"] = "LIMIT" if entry.get("price") is not None else "MARKET"
        if not entry.get("raw_label"):
            entry["raw_label"] = "ENTRY" if index == 1 else "AVERAGING"
        if not entry.get("source_style"):
            entry["source_style"] = "SINGLE" if len(entries) == 1 else "UNKNOWN"
        if "is_optional" not in entry:
            entry["is_optional"] = index > 1
    return entries


def _parse_entry_line(line: str, *, sequence: int) -> dict[str, Any] | None:
    cleaned = _normalize_list_item(line)
    if not cleaned:
        return None
    cleaned = re.sub(r"\(.*?rr.*?\)", "", cleaned, flags=re.IGNORECASE)
    match = re.search(r"(?<!\d)(?P<price>\d+(?:[.,]\d+)?)(?!\d)", cleaned)
    if not match:
        return None
    price = _to_float(match.group("price"))
    if price is None:
        return None
    size_hint_match = re.search(r"(?P<size>\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?)|(?P<pct>\d+(?:[.,]\d+)?%)", cleaned)
    size_hint = None
    if size_hint_match:
        size_hint = size_hint_match.group("size") or size_hint_match.group("pct")
    out: dict[str, Any] = {"sequence": sequence, "price": price}
    if size_hint:
        out["size_hint"] = size_hint
    return out


def _parse_entry_header(header_line: str) -> tuple[list[dict[str, Any]], str, str | None] | None:
    normalized = normalize_text(header_line)
    if not normalized or "вход" not in normalized:
        return None

    numeric_tokens = [_to_float(token) for token in re.findall(r"\d[\d\s]*(?:[.,]\d+)?", header_line)]
    numeric_tokens = [value for value in numeric_tokens if value is not None]

    if "по лимитке" in normalized or "лимиткой" in normalized or "лимитка" in normalized:
        if numeric_tokens:
            return ([{"sequence": 1, "price": numeric_tokens[0], "order_type": "LIMIT", "role": "PRIMARY", "raw_label": "ENTRY", "source_style": "SINGLE", "is_optional": False}], "LIMIT", header_line.strip())

    if "с текущих" in normalized and len(numeric_tokens) >= 2:
        center = numeric_tokens[0]
        delta = numeric_tokens[1]
        low = round(center - delta, 8)
        high = round(center + delta, 8)
        return (
            [
                {"sequence": 1, "price": low, "role": "PRIMARY", "order_type": "MARKET", "raw_label": "ENTRY", "source_style": "MARKET_RANGE", "is_optional": False},
                {"sequence": 2, "price": high, "role": "AVERAGING", "order_type": "LIMIT", "raw_label": "AVERAGING", "source_style": "MARKET_RANGE", "is_optional": True},
            ],
            "RANGE",
            header_line.strip(),
        )

    if re.search(r"\bвход\s+с\s+текущих\b", normalized):
        return (
            [
                {
                    "sequence": 1,
                    "price": None,
                    "role": "PRIMARY",
                    "order_type": "MARKET",
                    "raw_label": "ENTRY",
                    "source_style": "MARKET",
                    "is_optional": False,
                }
            ],
            "MARKET",
            "MARKET_ENTRY",
        )

    return None


def _extract_tp_block_lines(raw_text: str) -> list[str] | None:
    lines = raw_text.splitlines()
    start_idx = None
    for index, line in enumerate(lines):
        if re.search(r"(?:[TТtт]ейк[- ]?профит|\btp\b|\bтп\b)", line, re.IGNORECASE):
            start_idx = index
            break
    if start_idx is None:
        return None
    return [line for line in lines[start_idx + 1 :] if line.strip()]


def _extract_prices_from_lines(lines: list[str]) -> list[float]:
    out: list[float] = []
    for line in lines:
        cleaned = _normalize_list_item(line)
        if not cleaned:
            continue
        cleaned = re.sub(r"\(.*?rr.*?\)", "", cleaned, flags=re.IGNORECASE)
        for raw in re.findall(r"(?<!\d)\d[\d\s]*(?:[.,]\d+)?(?!\d)", cleaned):
            value = _to_float(raw)
            if value is not None and value not in out:
                out.append(value)
                break
    return out


def _normalize_list_item(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*[-•*—–]+\s*", "", cleaned)
    cleaned = re.sub(r"^\s*\d+\s*[\).:-]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*\d+\s*\)\s*", "", cleaned)
    return cleaned.strip()


__all__ = ["TraderCProfileParser"]
