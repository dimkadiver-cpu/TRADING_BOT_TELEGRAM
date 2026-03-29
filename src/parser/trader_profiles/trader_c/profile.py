"""Trader C deterministic lifecycle-oriented profile parser."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser
from src.parser.rules_engine import RulesEngine

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
# $ is optional — some messages use "Btcusdt SHORT" without prefix.
# Two capture groups to handle optional space between base and quote:
# e.g. "$KAS USDT" → base="KAS", quote="USDT" → "KASUSDT"
_SYMBOL_RE = re.compile(
    r"\$?(?P<base>[A-Z0-9]{2,20})\s*(?P<quote>USDT|USDC|USD|BTC|ETH)\b",
    re.IGNORECASE,
)
_SIDE_RE = re.compile(r"\b(?P<side>LONG|SHORT|ЛОНГ|ШОРТ)\b", re.IGNORECASE)
_RISK_RE = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*%\s*деп", re.IGNORECASE)
# FIX B: value must not cross spaces — use \d+(?:[.,]\d+)? (no \s inside number)
# Lookahead stops at "% деп" or end-of-word to avoid "75500 1,2%" → "755001.2"
# Added "\." to separator set to handle "Stop . 76,6" (dot-separated format)
_STOP_RE = re.compile(
    r"\b(?:stop|стоп(?:\s*лосс)?)\s*[:\-\.]*\s*(?P<value>\d+(?:[.,]\d+)?)(?!\d)",
    re.IGNORECASE,
)
_RANGE_ENTRY_RE = re.compile(
    r"вход[^\n]*?(?P<a>\d+(?:[.,]\d+)?)\s*[-–]\s*(?P<b>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# Handle all limit-entry phrasings:
# "лимитка" (nom.), "лимитки" (gen.), "лимитке" (dat./prep. after "по"),
# "лимитку" (acc.), "лимиткой" (instr. after "с"), bare "лимит"
_LIMIT_ENTRY_RE = re.compile(
    r"вход\s+(?:(?:с|по)\s+)?лимит(?:кой|ку|ка|ке|ки)?[ \t]*(?P<value>\d+(?:[.,]\d+)?)?",
    re.IGNORECASE,
)
# FIX C: require that the index digit is followed by ) and then a price digit,
# NOT a slash (to avoid matching "1/3)" as idx=1, price from next line)
_TRANCHE_RE = re.compile(
    r"(?P<idx>\d+)\)\s*(?P<price>\d+(?:[.,]\d+)?)\s*\((?P<size>\d/\d)\)",
    re.IGNORECASE,
)
# Tranche without leading index: "2910 (1/3)" / "2900 (2/3)"
_TRANCHE_NO_IDX_RE = re.compile(
    r"(?<!\d/)(?P<price>\d+(?:[.,]\d+)?)\s*\((?P<size>[12]/[23])\)",
    re.IGNORECASE,
)
# FIX A: dedicated compact TP pattern — "Тп1. 87222", "Тп2 88150", "тп 1: 87100"
# Separators include "." and space
_TP_COMPACT_RE = re.compile(
    r"(?:тейк[- ]?профит|tейк[- ]?профит|тп|tp)\s*(?P<n>\d)\s*[:.,-]?\s*(?P<value>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# TP numbered-list line: "1) 87100 (RR…)", "2)86800", " 3) 67500"
# Extracts the FIRST price from a numbered list item, ignoring RR ratios in parens
_TP_LIST_LINE_RE = re.compile(
    r"^\s*\d+[.)]\s*(?P<value>\d+(?:[.,]\d+)?\+?)(?!\s*/)",   # not "1/3" size hints
    re.MULTILINE,
)
_ENTRY_LIST_LINE_RE = re.compile(
    r"^\s*(?P<idx>\d+)[.)]\s*(?P<price>\d+(?:[.,]\d+)?)(?!\s*\()",
    re.MULTILINE,
)
# Section header for TP block
_TP_SECTION_HEADER_RE = re.compile(
    r"[тTТ]ейк[- ]?профит[:\s]*",
    re.IGNORECASE,
)
_TP_HIT_RE = re.compile(r"(?:tp|тп|тейк)\s*(?P<idx>\d)", re.IGNORECASE)
_PARTIAL_PERCENT_RE = re.compile(r"\((?P<value>\d+(?:[.,]\d+)?)%\)")
_PARTIAL_PRICE_RE = re.compile(r"(?:по\s+текущим|по)\s*(?P<value>\d+(?:[.,]\d+)?)", re.IGNORECASE)
_REDUCE_PERCENT_RE = re.compile(
    r"(?:сократил|скинул|закрыл\w*\s+часть)[^\d+-]*(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)
_RR_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*RR", re.IGNORECASE)
_LEADING_TP_CLOSE_PRICE_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s+позиция\s+закрыта\s+по\s+тейку",
    re.IGNORECASE,
)
_SHORT_STOP_VALUE_RE = re.compile(
    r"(?:на\s+\w+\s+)?стоп\s+(?P<value>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_LIMITKA_UPDATE_RE = re.compile(
    r"(?:новая\s+)?лимитк\w*\s+на\s+\d+(?:[.,]\d+)?",
    re.IGNORECASE,
)

_ACTIVATION_MARKERS = ("первая лимитка сработала", "активировалась", "лимитка сработала", "лимитный ордер исполнен")
# NOTE: TP hit is now detected via _TP_HIT_RE regex (handles spaces: "тп 1", "тп 2")
# These string markers remain as a fast-path for compact no-space variants
_TP_HIT_MARKERS = ("tp1", "tp2", "tp3", "tp4", "тп1", "тп2", "тп3", "тп4", "тейк 1", "позиция закрыта по тейку")
# When a TP number appears in "still valid" context — NOT a TP hit
_TP_STILL_VALID_EXCLUSIONS = ("актуально если", "актуально если прид", "тп актуально", "тп1 актуально", "тп2 актуально")
_MOVE_BE_MARKERS = ("в бу перевел", "перевел в бу", "после первого тп в бу", "стоп в б/у", "стоп в бу", "в бу ушли", "в бу ушел")
_EXIT_BE_MARKERS = ("ушли в б/у", "позиция закрыта в бу", "закрыто в бу", "остаток ушел в бу", "закрыт остаток в бу", "остаток в бу уш", "остаток в бу ушёл")
_EXIT_BE_EXTRA_MARKERS = ("остаток закрыт в бу", "увы ушли в бу")
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
    "закрыл в минус",
    "закрыл в плюс",
    "закрыл в +",
    "закрыл в -",
    "сэтап закрыт",        # "Сэтап закрыт в 0"
    "теперь закрыто",      # "Теперь закрыто до тп не дошли"
    "закрываю по рынку остаток",
)
_INFO_ONLY_STATUS_MARKERS = ("актуально",)
_CANCEL_PENDING_MARKERS = ("не актуально", "убрал лимитку", "лимитку убрал", "ушел без нас", "улетели")
# NOTE: "лимитку с" removed — falsely matched "лимитку слабая" (с = first char of слабая)
_REMOVE_PENDING_MARKERS = ("доливку убрал", "добор убрал", "доливку убираем", "убрал лимитку с", "убираем лимитку")
# Regex for inverted word order: "Лимитку с 63750 убираем"
_REMOVE_PENDING_RE = re.compile(r"(?:доливк|добор|лимитк)\w*\b.*?\b(?:убрал|убрали|убираем)\b", re.IGNORECASE | re.DOTALL)
_UPDATE_TP_MARKERS = (
    "изменения",
    "изменённо",           # typo variant: "изменённо !!! тп тоже"
    "тп дополнительный",
    "актуально если прид",
    "тп переставил",
    "переставил тп",
    "тп перенес",
    "перенес тп",
    "везём дальше",        # "Остаток объема везём дальше 87000 86444"
    "везем дальше",
)
# Subset used to SUPPRESS U_TP_HIT (modification/replacement, not continuation after a TP hit)
_UPDATE_TP_STRONG_EXCLUSION_MARKERS = (
    "изменения",
    "изменённо",
    "тп переставил",
    "переставил тп",
    "тп перенес",
    "перенес тп",
)
_UPDATE_STOP_MARKERS = ("стоп переносим", "рискуем профитом с тп")
_REENTER_MARKERS = ("перезаход", "re-enter", "перезашел", "перезашли")
# Detects "Стоп в PRICE" stop-move pattern
_STOP_AT_PRICE_RE = re.compile(r"\bстоп\s+в\s+\d", re.IGNORECASE)
# INFO-level clarification openers
_INFO_CLARIFICATION_MARKERS = ("возникли вопросы", "хочу объяснить", "хочу уточнить", "ребята, возникли")
_ADMIN_MARKERS = ("#админ", "админ на связи", "друзья, это снова #админ")


class TraderCProfileParser(TraderBProfileParser):
    trader_code = "trader_c"

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)
        self._engine = RulesEngine.load(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        message_type = self._classify_message(prepared=prepared)
        entities = self._extract_entities(prepared=prepared, message_type=message_type)
        intents = self._extract_intents(prepared=prepared, message_type=message_type, entities=entities)
        target_refs = self._extract_targets(prepared=prepared, context=context, entities=entities)
        warnings = self._build_warnings(message_type=message_type, intents=intents, target_refs=target_refs, entities=entities)
        confidence = self._estimate_confidence(message_type=message_type, warnings=warnings)

        linking = self._build_linking(target_refs=target_refs, context=context)
        has_strong_target = any(ref.get("kind") in {"reply", "telegram_link", "message_id"} for ref in target_refs)
        target_scope = {"kind": "signal", "scope": "single" if has_strong_target else "unknown"}

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

        if self._contains_any(normalized, _ADMIN_MARKERS):
            return "INFO_ONLY"

        # INFO_ONLY: use RulesEngine (reads classification_markers.info_only strong/weak)
        engine_result = self._engine.classify(raw_text)
        if engine_result.message_type == "INFO_ONLY":
            return "INFO_ONLY"
        # Fallback: long clarification messages not caught by engine markers
        if self._contains_any(normalized, _INFO_CLARIFICATION_MARKERS):
            return "INFO_ONLY"
        if self._looks_like_contextual_info_only(normalized=normalized, raw_text=raw_text):
            return "INFO_ONLY"

        # Structural NEW_SIGNAL detection (symbol + side + stop + tp + entry required)
        has_symbol = _extract_symbol(raw_text) is not None
        has_side = _extract_side(raw_text) is not None
        has_stop = _extract_stop(raw_text) is not None
        has_tp = bool(_extract_take_profits(raw_text)) or any(token in normalized for token in ("тейк", "tейк", "тп", "tp"))
        has_entry_signal = bool(_RANGE_ENTRY_RE.search(raw_text) or _LIMIT_ENTRY_RE.search(raw_text) or _TRANCHE_RE.search(raw_text) or "вход" in normalized)
        if has_symbol and has_side and has_stop and has_tp and has_entry_signal:
            return "NEW_SIGNAL"

        # UPDATE: operational marker scan (Python constants) + RulesEngine hint
        if self._is_operational_update(normalized=normalized, raw_text=raw_text):
            return "UPDATE"
        if engine_result.message_type == "UPDATE":
            return "UPDATE"
        return "UNCLASSIFIED"

    @staticmethod
    def _looks_like_contextual_info_only(*, normalized: str, raw_text: str) -> bool:
        if normalized.strip() in _INFO_ONLY_STATUS_MARKERS:
            return True
        if _contains_any_static(normalized, _ADMIN_MARKERS):
            return True
        return False

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
            is_range_entry = order_type == "RANGE"
            entities.update(
                {
                    "side": side,
                    "entry_order_type": order_type,
                    "entries": entries,
                    "entry": [item["price"] for item in entries] if is_range_entry else ([entries[0]["price"]] if entries else []),
                    "entry_text_raw": entry_text,
                    "stop_loss": stop,
                    "stop_text_raw": _extract_stop_text(raw_text),
                    "take_profits": take_profits,
                    "take_profits_text_raw": _extract_tp_text(raw_text),
                    "risk_value_raw": risk_raw,
                    "risk_value_normalized": risk_norm,
                    "entry_plan_type": "SINGLE" if is_range_entry or len(entries) <= 1 else "MULTI",
                    "entry_structure": "RANGE" if is_range_entry else ("LADDER" if len(entries) > 1 else "ONE_SHOT"),
                    "has_averaging_plan": len(entries) > 1 and not is_range_entry,
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
            if self._contains_any(normalized, _MOVE_BE_MARKERS):
                entities["new_stop_level"] = "ENTRY"
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
                    entities["new_stop_level"] = new_stop
                    entities["new_stop_price"] = new_stop
            elif _looks_like_short_stop_update(normalized):
                short_stop = _extract_short_stop_value(raw_text)
                if short_stop is not None:
                    entities["new_stop_level"] = short_stop
                    entities["new_stop_price"] = short_stop

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
        raw_text = str(prepared.get("raw_text") or "")
        intents: list[str] = []

        if self._contains_any(normalized, _ACTIVATION_MARKERS):
            intents.append("U_ACTIVATION")

        # U_TP_HIT: use REGEX (_TP_HIT_RE) to handle both "тп1" and "тп 1" (with space).
        # Exclude: "still valid" context, "рискуем профитом с тп" (stop at TP level),
        # and "изменения / тп переставил" context (UPDATE_TP, not TP hit).
        tp_hit_context_excluded = (
            self._contains_any(normalized, _TP_STILL_VALID_EXCLUSIONS)
            or "рискуем профитом с тп" in normalized
            # Fix G: only STRONG modification markers suppress U_TP_HIT
            # "везём дальше" etc. can co-exist with a TP hit
            or self._contains_any(normalized, _UPDATE_TP_STRONG_EXCLUSION_MARKERS)
        )
        if (
            (self._contains_any(normalized, _TP_HIT_MARKERS) or bool(_TP_HIT_RE.search(raw_text)))
            and not tp_hit_context_excluded
        ):
            intents.append("U_TP_HIT")

        if self._contains_any(normalized, _MOVE_BE_MARKERS):
            intents.extend(["U_MOVE_STOP_TO_BE", "U_MOVE_STOP"])

        if self._contains_any(normalized, (*_EXIT_BE_MARKERS, *_EXIT_BE_EXTRA_MARKERS)):
            intents.append("U_EXIT_BE")

        if self._contains_any(normalized, _CLOSE_PARTIAL_MARKERS):
            intents.append("U_CLOSE_PARTIAL")

        # "Закрыл 2980 -" / "Закрыл в минус" → U_CLOSE_FULL
        if (
            self._contains_any(normalized, _CLOSE_FULL_MARKERS)
            or re.search(r"\bзакрыл\w*\s+\d", normalized)
            or re.search(r"\bзакрыл\w*\s+по\s+\d", normalized)
        ):
            intents.append("U_CLOSE_FULL")

        if self._contains_any(normalized, _CANCEL_PENDING_MARKERS):
            intents.append("U_CANCEL_PENDING_ORDERS")

        if self._contains_any(normalized, _REMOVE_PENDING_MARKERS) or _REMOVE_PENDING_RE.search(raw_text):
            intents.append("U_REMOVE_PENDING_ENTRY")

        if self._contains_any(normalized, _UPDATE_TP_MARKERS) or _looks_like_tp_update(normalized):
            intents.append("U_UPDATE_TAKE_PROFITS")

        # "Стоп в 91600, рискуем профитом с тп1" → stop-move, not TP hit
        if self._contains_any(normalized, _UPDATE_STOP_MARKERS) or _STOP_AT_PRICE_RE.search(raw_text) or _looks_like_short_stop_update(normalized):
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
        has_strong = any(ref.get("kind") in {"reply", "telegram_link", "message_id"} for ref in target_refs)
        if has_strong:
            return []
        if any(ref.get("kind") == "symbol" for ref in target_refs):
            return ["trader_c_update_weak_target_only"]
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
    def _is_operational_update(*, normalized: str, raw_text: str = "") -> bool:
        if TraderCProfileParser._looks_like_contextual_info_only(normalized=normalized, raw_text=raw_text):
            return False
        if any(
            marker in normalized
            for marker in [
                *_ACTIVATION_MARKERS,
                *_TP_HIT_MARKERS,
                *_MOVE_BE_MARKERS,
                *_EXIT_BE_MARKERS,
                *_EXIT_BE_EXTRA_MARKERS,
                *_CLOSE_PARTIAL_MARKERS,
                *_CLOSE_FULL_MARKERS,
                *_CANCEL_PENDING_MARKERS,
                *_REMOVE_PENDING_MARKERS,
                *_UPDATE_TP_MARKERS,
                *_UPDATE_STOP_MARKERS,
                *_REENTER_MARKERS,
                "стоп -",
            ]
        ):
            return True
        if bool(_TP_HIT_RE.search(raw_text)) or _looks_like_tp_update(normalized):
            return True
        # "Закрыл 2980 -" style: close report with a price
        if re.search(r"\bзакрыл\w*\s+\d", normalized):
            return True
        if re.search(r"\bзакрыл\w*\s+по\s+\d", normalized):
            return True
        # "Стоп в 91600" style: stop-at-price update
        if _STOP_AT_PRICE_RE.search(raw_text):
            return True
        if _looks_like_short_stop_update(normalized):
            return True
        if _LIMITKA_UPDATE_RE.search(raw_text):
            return True
        if _REDUCE_PERCENT_RE.search(raw_text):
            return True
        # "Лимитку с 63750 убираем" — remove pending with inverted word order
        if _REMOVE_PENDING_RE.search(raw_text):
            return True
        return False


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".").strip().rstrip("+")
    try:
        return float(cleaned)
    except ValueError:
        return None


# Cyrillic characters that are visually identical to Latin letters and often
# typed by mistake in crypto symbols (e.g. $BTСUSDT where С is U+0421).
_CYRILLIC_LOOKALIKE_MAP: dict[int, int] = str.maketrans(
    "АВСЕНІКМОРТХУРУ"
    "авсенікмортхуру",
    "ABCEHIKMOPТXUPY"
    "abcehikmopтxupy",
)
# fmt: off
_CYRILLIC_LOOKALIKE_MAP = str.maketrans({
    0x0410: ord("A"),  # А → A
    0x0412: ord("B"),  # В → B
    0x0421: ord("C"),  # С → C   ← main culprit in $BTСUSDT
    0x0415: ord("E"),  # Е → E
    0x0397: ord("H"),  # Η (Greek) → H  (just in case)
    0x041D: ord("H"),  # Н → H
    0x0406: ord("I"),  # І → I
    0x041A: ord("K"),  # К → K
    0x041C: ord("M"),  # М → M
    0x041E: ord("O"),  # О → O
    0x0420: ord("P"),  # Р → P
    0x0422: ord("T"),  # Т → T
    0x0425: ord("X"),  # Х → X
    # lowercase Cyrillic lookalikes
    0x0430: ord("a"),  # а → a
    0x0432: ord("b"),  # в → b  (rare but possible)
    0x0441: ord("c"),  # с → c
    0x0435: ord("e"),  # е → e
    0x043A: ord("k"),  # к → k
    0x043C: ord("m"),  # м → m
    0x043E: ord("o"),  # о → o
    0x0440: ord("p"),  # р → p
    0x0442: ord("t"),  # т → t
    0x0445: ord("x"),  # х → x
})
# fmt: on


def _normalize_symbol_text(text: str) -> str:
    """Replace Cyrillic look-alike characters with their Latin equivalents.

    Trader C frequently types crypto symbols with Cyrillic characters that are
    visually identical to Latin ones (e.g. $BTСUSDT where С = U+0421).
    Applying this map before the symbol regex makes extraction reliable.
    """
    return text.translate(_CYRILLIC_LOOKALIKE_MAP)


def _extract_symbol(raw_text: str) -> str | None:
    normalized = _normalize_symbol_text(raw_text.upper())
    match = _SYMBOL_RE.search(normalized)
    if not match:
        return None
    # Concatenate base + quote to handle spaced formats like "KAS USDT" → "KASUSDT"
    return (match.group("base") + match.group("quote")).upper()


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
    """Extract entry levels.  Priority: tranche-with-idx > tranche-no-idx > range > limit > market."""
    # --- Tranche with explicit idx: "1)88650(1/3)" ---
    entries: list[dict[str, Any]] = []
    for match in _TRANCHE_RE.finditer(raw_text):
        price = _to_float(match.group("price"))
        idx = int(match.group("idx"))
        # Guard: idx must be <= 5 to avoid false-match on price digits
        if price is None or idx > 5:
            continue
        entries.append({"sequence": idx, "price": price, "size_hint": match.group("size")})
    if entries:
        entries.sort(key=lambda x: x["sequence"])
        return entries, "LIMIT", "TRANCHE_PLAN"

    # --- Tranche without idx: "2910 (1/3)" / "2900 (2/3)" ---
    no_idx: list[dict[str, Any]] = []
    for seq, match in enumerate(_TRANCHE_NO_IDX_RE.finditer(raw_text), start=1):
        price = _to_float(match.group("price"))
        if price is not None:
            no_idx.append({"sequence": seq, "price": price, "size_hint": match.group("size")})
    if no_idx:
        return no_idx, "LIMIT", "TRANCHE_PLAN"

    entry_block = _extract_entry_block(raw_text)
    indexed_entries: list[dict[str, Any]] = []
    if entry_block:
        for match in _ENTRY_LIST_LINE_RE.finditer(entry_block):
            price = _to_float(match.group("price"))
            idx = int(match.group("idx"))
            if price is None or idx > 5:
                continue
            indexed_entries.append({"sequence": idx, "price": price})
    if indexed_entries:
        indexed_entries.sort(key=lambda x: x["sequence"])
        return indexed_entries, "LIMIT", "INDEXED_ENTRY_PLAN"

    # --- Range entry: "Вход лимитка 67300-400" / "Вход с текущих (88000-87900)" ---
    range_entry = _extract_range_entry(raw_text)
    if range_entry is not None:
        a, b, raw_value = range_entry
        return [{"sequence": 1, "price": a}, {"sequence": 2, "price": b}], "RANGE", raw_value

    # --- Single limit entry: "Вход лимитка 92550" / "Вход с лимиткой" ---
    limit = _LIMIT_ENTRY_RE.search(raw_text)
    if limit:
        price = _to_float(limit.group("value")) if limit.group("value") else None
        return ([{"sequence": 1, "price": price}] if price is not None else []), "LIMIT", limit.group(0)

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
    """Extract TP prices.  Strategy:
    1. Compact inline: "Тп1. 87222", "тп 2: 88150" via _TP_COMPACT_RE
    2. Numbered-list block after "Тейк-профит" header via _TP_LIST_LINE_RE
    3. Skip RR ratio values (integers 1-9 inside "(RR …)" context)
    """
    out: list[float] = []

    # Strategy 1 — compact "тп N value" format
    for match in _TP_COMPACT_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is not None and value not in out:
            out.append(value)
    if out:
        return out

    # Strategy 2 — numbered list block after Тейк-профит header
    header = _TP_SECTION_HEADER_RE.search(raw_text)
    if header:
        block = raw_text[header.end():]
        for match in _TP_LIST_LINE_RE.finditer(block):
            value = _to_float(match.group("value"))
            if value is not None and value not in out:
                out.append(value)

    return out


def _extract_stop_text(raw_text: str) -> str | None:
    match = _STOP_RE.search(raw_text)
    return match.group(0) if match else None


def _extract_tp_text(raw_text: str) -> str | None:
    return "\n".join(m.group(0) for m in _TP_COMPACT_RE.finditer(raw_text)) or None


def _extract_hit_targets(raw_text: str) -> list[int]:
    out = sorted({int(m.group("idx")) for m in _TP_HIT_RE.finditer(raw_text)})
    return out


def _extract_partial_percent(raw_text: str) -> float | None:
    match = _PARTIAL_PERCENT_RE.search(raw_text)
    if match:
        return _to_float(match.group("value"))
    reduce_match = _REDUCE_PERCENT_RE.search(raw_text)
    if not reduce_match:
        return None
    value = _to_float(reduce_match.group("value"))
    return abs(value) if value is not None else None


def _extract_partial_price(raw_text: str) -> float | None:
    match = _PARTIAL_PRICE_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_rr(raw_text: str) -> float | None:
    match = _RR_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_be_price(raw_text: str) -> float | None:
    """Extract the stop price when moved to BE.
    Handles: "В бу перевел 89650" / "Стоп в бу на точку входа 92200" / "стоп в б/у 92200"
    """
    patterns = [
        r"в\s*бу\s*перевел\s*(?P<value>\d+(?:[.,]\d+)?)",
        r"стоп\s+в\s+[бb][уu/][уu]?\s*(?:на\s+точку\s+входа\s*)?(?P<value>\d+(?:[.,]\d+)?)",
    ]
    for pat in patterns:
        match = re.search(pat, raw_text, re.IGNORECASE)
        if match:
            return _to_float(match.group("value"))
    return None


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
    leading_tp_hit = _LEADING_TP_CLOSE_PRICE_RE.search(raw_text)
    if leading_tp_hit:
        return _to_float(leading_tp_hit.group("value"))
    match = re.search(r"закрыл\w*[^\d]*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text, re.IGNORECASE)
    return _to_float(match.group("value")) if match else None


def _extract_short_stop_value(raw_text: str) -> float | None:
    match = _SHORT_STOP_VALUE_RE.search(raw_text)
    return _to_float(match.group("value")) if match else None


def _extract_entry_block(raw_text: str) -> str:
    normalized = raw_text.lower()
    start = normalized.find("вход")
    if start == -1:
        return ""
    stop_candidates: list[int] = []
    for marker_re in (_STOP_RE, _TP_SECTION_HEADER_RE):
        match = marker_re.search(raw_text, start)
        if match:
            stop_candidates.append(match.start())
    end = min(stop_candidates) if stop_candidates else len(raw_text)
    return raw_text[start:end]


def _extract_range_entry(raw_text: str) -> tuple[float, float, str] | None:
    search_areas: list[str] = []
    entry_block = _extract_entry_block(raw_text)
    if entry_block:
        search_areas.append(entry_block)
    search_areas.append(raw_text)

    for search_area in search_areas:
        normalized = _normalize_dash(search_area)
        match = re.search(r"(?P<a>\d+(?:[.,]\d+)?)\s*-\s*(?P<b>\d+(?:[.,]\d+)?)", normalized)
        if not match:
            continue
        a_raw = match.group("a")
        b_raw = match.group("b")
        a = _to_float(a_raw)
        b = _expand_shorthand_price(a_raw, b_raw)
        if a is None or b is None:
            continue
        return a, b, match.group(0)
    return None


def _expand_shorthand_price(first_raw: str, second_raw: str) -> float | None:
    first_value = _to_float(first_raw)
    second_value = _to_float(second_raw)
    if first_value is None or second_value is None:
        return None

    if "." in first_raw or "." in second_raw or "," in first_raw or "," in second_raw:
        return second_value

    first_digits = re.sub(r"\D", "", first_raw)
    second_digits = re.sub(r"\D", "", second_raw)
    if not first_digits or not second_digits or len(second_digits) >= len(first_digits):
        return second_value

    expanded_digits = first_digits[: len(first_digits) - len(second_digits)] + second_digits
    try:
        return float(expanded_digits)
    except ValueError:
        return second_value


def _normalize_dash(value: str) -> str:
    return value.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")


def _looks_like_short_stop_update(normalized: str) -> bool:
    return "стоп" in normalized and _SHORT_STOP_VALUE_RE.search(normalized) is not None


def _contains_any_static(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


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
