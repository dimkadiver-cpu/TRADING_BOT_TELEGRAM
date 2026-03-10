"""Trader A (TA) parser profile.

Keeps trader-specific parsing logic in Python while loading configurable lexical
markers/synonyms/patterns from traders/TA/parsing_rules.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Mapping

_DEFAULT_RULES_PATH = Path(__file__).resolve().parents[3] / "traders" / "TA" / "parsing_rules.json"

_DEFAULT_MESSAGE_TYPE_MARKERS: dict[str, tuple[str, ...]] = {
    "NEW_SIGNAL": (
        "вход",
        "усреднение",
        "стоп",
        "sl",
        "tp1",
        "tp2",
        "tp3",
        "#",
    ),
    "UPDATE": (
        "тейк",
        "тейкнулось",
        "увы стоп",
        "к сожалению стоп",
        "очень неприятный стоп",
        "стопы в бу",
        "перевести стоп в безубыток",
        "стоп в бу",
        "перевел стоп в бу",
        "убрать лимитку",
        "убираем лимитку",
        "убираем лимитки",
        "снимаем лимитки",
        "уберем лимитки",
        "взяли лимитку",
        "ждем лимитку",
        "удалить лимитку",
        "закрываю",
        "закрыть",
        "прикроем",
        "фиксировать",
        "зафиксировать",
        "в работе",
        "держим",
        "убыток",
        "профит",
        "фиксирую 100%",
        "лимитка. моя средняя",
        "breakeven",
    ),
    "INFO_ONLY": ("жду", "наблюдаю", "коммент"),
    "RESULT_REPORT": ("r",),
    "OVERVIEW": ("обзор", "направление", "поддержки", "инвалидация"),
    "TEASER": (
        "остальные данные чуть позже",
        "полная информация чуть позже",
        "полная информация через несколько минут",
    ),
    "ADMIN_INFO": (
        "#админ",
        "админ",
        "начинаем марафон",
        "через 10 минут начинаем",
        "через 2 часа начинаем",
        "пока ждем",
        "перерыв",
        "не будет новых сигналов",
        "возвращается с новыми сигналами",
    ),
    "SESSION": ("марафон", "сессия"),
}

_DEFAULT_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "U_MOVE_STOP_TO_BE": (
        "стоп в бу",
        "стопы в бу",
        "перевести стоп в безубыток",
        "перевел стоп в бу",
        "точку входа",
        "breakeven",
    ),
    "U_CANCEL_PENDING_ORDERS": (
        "убрать лимитку",
        "убираем лимитки",
        "снимаем лимитки",
        "удалить лимитку",
        "убрать все лимитные ордера",
    ),
    "U_CLOSE_FULL": (
        "зафиксирую все",
        "закрываю все",
        "закрыть все",
        "фиксирую 100%",
    ),
    "U_MANUAL_CLOSE": ("зафиксирую", "закрываю", "прикроем", "фиксировать", "зафиксировать"),
    "U_CLOSE_PARTIAL": ("частично", "часть", "частями", "фиксирую часть"),
    "U_STOP_HIT": ("к сожалению стоп", "увы стоп", "стоп выбило", "стоп -"),
    "U_TP_HIT": ("тейк", "тейкнулось", "tp hit", "цель взята"),
    "U_REPORT_FINAL_RESULT": ("r",),
}

_DEFAULT_PHRASE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "market_entry": ("вход с текущих", "(вход с текущих)"),
    "breakeven": ("стоп в бу", "точку входа", "безубыток", "breakeven"),
    "cancel_pending": ("убрать лимитку", "лимитные ордера"),
}

_DEFAULT_IGNORE_IF_CONTAINS = ("#admin",)

_DEFAULT_REGEX_PATTERNS: dict[str, tuple[str, ...]] = {
    "symbol": (r"#\s*([A-Z0-9]{2,20}USDT(?:\.P)?)\b", r"\b([A-Z0-9]{2,20}USDT(?:\.P)?)\b"),
    "entry": (
        r"\bвход(?:\s*\([abаб]\))?(?:\s+с\s+текущих|\s+лимиткой)?\s*[:=@-]?\s*([0-9][0-9.,]*(?:\s*-\s*[0-9][0-9.,]*)?)",
    ),
    "market_entry": (r"(?:\(\s*вход\s+с\s+текущих\s*\)|\bвход\s+с\s+текущих\b)",),
    "averaging": (
        r"(?:усреднение|втор(?:ой|ая)\s+вход)\s*[:=@-]?\s*([0-9][0-9.,]*(?:\s*-\s*[0-9][0-9.,]*)?)",
    ),
    "stop": (r"(?:\bsl\b|\bстоп\b)\s*[:=@-]?\s*([0-9][0-9.,]*)",),
    "tp": (r"\btp\d*\s*[:=@-]?\s*([0-9][0-9.,]*)",),
    "risk": (r"(?:риск\s+на\s+сделку|risk)\s*[:=@-]?\s*([0-9]+(?:[.,][0-9]+)?\s*%)",),
    "entry_cancel": (r"(отмена\s+входа[^\n\r]*)",),
    "stop_out": (r"(?:к\s+сожалению\s+стоп|словили\s*-\s*\d+(?:[.,]\d+)?%|стоп\s*[-:]\s*-\d+(?:[.,]\d+)?%)",),
    "limit_avg_update": (r"лимитка\W+моя\s+средняя",),
    "linked_update_hint": (
        r"(?:лимитка|средняя|тейк|стоп\s+в\s+бу|breakeven|убра(?:ть|ем)\s+лимитку)",
    ),
    "result_r": (r"\b[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?\s*[-:=]\s*[+-]?\d+(?:[.,]\d+)?\s*R\b",),
    "tp_index": (r"\btp(\d+)\b",),
    "percent": (r"\b\d+(?:[.,]\d+)?%\b",),
}


@dataclass(slots=True)
class TAProfileConfig:
    profile_options: dict[str, Any]
    message_type_markers: dict[str, tuple[str, ...]]
    intent_keywords: dict[str, tuple[str, ...]]
    phrase_templates: dict[str, tuple[str, ...]]
    ignore_if_contains: tuple[str, ...]
    regex_patterns: dict[str, tuple[str, ...]]


@dataclass(slots=True)
class TAExtractedFields:
    symbol: str | None
    direction: str | None
    primary_entry_raw: str | None
    secondary_entry_raw: str | None
    stop_raw: str | None
    targets: list[str]
    risk_hint: str | None
    entry_cancel_rule_raw: str | None
    setup_intent: bool
    entry_intent: bool
    overview_hint: bool
    teaser_hint: bool
    admin_info_hint: bool
    session_hint: bool
    update_hits: int
    multi_symbol_update: bool
    multi_action_update: bool
    intents: list[str]


def load_ta_profile_config(*, rules: Mapping[str, Any] | None = None, path: Path | None = None) -> TAProfileConfig:
    payload: Mapping[str, Any]
    if rules is not None:
        payload = rules
    else:
        payload = _load_rules_from_path(path or _DEFAULT_RULES_PATH)

    return TAProfileConfig(
        profile_options=_as_dict(payload.get("profile_options")),
        message_type_markers=_merge_str_lists(_DEFAULT_MESSAGE_TYPE_MARKERS, payload.get("message_type_markers")),
        intent_keywords=_merge_str_lists(_DEFAULT_INTENT_KEYWORDS, payload.get("intent_keywords")),
        phrase_templates=_merge_str_lists(_DEFAULT_PHRASE_TEMPLATES, payload.get("phrase_templates")),
        ignore_if_contains=tuple(_as_str_list(payload.get("ignore_if_contains")) or list(_DEFAULT_IGNORE_IF_CONTAINS)),
        regex_patterns=_merge_str_lists(_DEFAULT_REGEX_PATTERNS, payload.get("regex_patterns")),
    )


@lru_cache(maxsize=1)
def _get_cached_runtime_config() -> TAProfileConfig:
    return load_ta_profile_config()


def extract_ta_fields(text: str, normalized: str) -> TAExtractedFields:
    cfg = _get_cached_runtime_config()
    symbols = _extract_symbols(text, cfg=cfg)
    symbol = symbols[0] if symbols else None
    direction = None
    padded = f" {normalized} "
    if any(marker in padded or marker in normalized for marker in ("лонг", " long ", " buy ")):
        direction = "BUY"
    elif any(marker in padded or marker in normalized for marker in ("шорт", " short ", " sell ")):
        direction = "SELL"

    entry_match = _first_search(text, cfg.regex_patterns.get("entry", ()))
    entry_market = _first_search(text, cfg.regex_patterns.get("market_entry", ()))
    averaging_match = _first_search(text, cfg.regex_patterns.get("averaging", ()))
    stop_match = _first_search(text, cfg.regex_patterns.get("stop", ()))
    targets = _all_group1(text, cfg.regex_patterns.get("tp", ()))
    risk_match = _first_search(text, cfg.regex_patterns.get("risk", ()))
    cancel_match = _first_search(text, cfg.regex_patterns.get("entry_cancel", ()))

    intents = _extract_intents(normalized=normalized, text=text, cfg=cfg)
    update_hits = len(intents)
    if _first_search(text, cfg.regex_patterns.get("stop_out", ())):
        update_hits += 1
        intents = _unique_append(intents, "U_STOP_HIT")
    if _first_search(text, cfg.regex_patterns.get("limit_avg_update", ())):
        update_hits += 1
    multi_action_update = update_hits >= 2
    multi_symbol_update = len(set(symbols)) >= 2

    setup_markers = cfg.message_type_markers.get("NEW_SIGNAL", ())
    setup_intent = any(marker in normalized for marker in setup_markers)
    entry_intent = entry_match is not None or entry_market is not None

    overview_hint = _has_any(normalized, cfg.message_type_markers.get("OVERVIEW", ()))
    teaser_hint = _has_any(normalized, cfg.message_type_markers.get("TEASER", ()))
    admin_info_hint = _has_any(normalized, cfg.message_type_markers.get("ADMIN_INFO", ()))
    session_hint = _has_any(normalized, cfg.message_type_markers.get("SESSION", ()))

    return TAExtractedFields(
        symbol=symbol,
        direction=direction,
        primary_entry_raw=entry_match.group(1).strip() if entry_match else ("MARKET_CURRENT" if entry_market else None),
        secondary_entry_raw=averaging_match.group(1).strip() if averaging_match else None,
        stop_raw=stop_match.group(1).strip() if stop_match else None,
        targets=targets,
        risk_hint=risk_match.group(1).replace(" ", "") if risk_match else None,
        entry_cancel_rule_raw=cancel_match.group(1).strip() if cancel_match else None,
        setup_intent=setup_intent,
        entry_intent=entry_intent,
        overview_hint=overview_hint,
        teaser_hint=teaser_hint,
        admin_info_hint=admin_info_hint,
        session_hint=session_hint,
        update_hits=update_hits,
        multi_symbol_update=multi_symbol_update,
        multi_action_update=multi_action_update,
        intents=intents,
    )


def classify_ta_message(
    normalized: str,
    extracted: object,
    has_strong_link: bool,
    ta_fields: TAExtractedFields,
) -> str:
    cfg = _get_cached_runtime_config()
    symbol = getattr(extracted, "symbol", None)
    direction = getattr(extracted, "direction", None)
    entry_raw = getattr(extracted, "entry_raw", None)
    stop_raw = getattr(extracted, "stop_raw", None)
    targets = getattr(extracted, "targets", [])
    has_complete_setup = (
        symbol is not None
        and direction is not None
        and entry_raw is not None
        and stop_raw is not None
        and len(targets) > 0
    )
    if has_complete_setup:
        return "NEW_SIGNAL"

    if any(marker in normalized for marker in cfg.ignore_if_contains):
        return "INFO_ONLY"

    if ta_fields.teaser_hint:
        return "SETUP_INCOMPLETE"

    only_result_report = bool(ta_fields.intents) and set(ta_fields.intents) == {"U_REPORT_FINAL_RESULT"}
    if only_result_report:
        return "INFO_ONLY"

    if ta_fields.update_hits > 0:
        return "UPDATE"
    linked_patterns = cfg.regex_patterns.get("linked_update_hint", ())
    if has_strong_link and _first_search(normalized, linked_patterns):
        return "UPDATE"

    if ta_fields.session_hint and (ta_fields.update_hits > 0 or _first_search(normalized, linked_patterns)):
        return "UPDATE"

    if ta_fields.overview_hint and ta_fields.update_hits == 0:
        return "INFO_ONLY"
    if ta_fields.admin_info_hint and ta_fields.update_hits == 0:
        return "INFO_ONLY"
    if ta_fields.session_hint and ta_fields.update_hits == 0:
        return "INFO_ONLY"

    has_setup_keywords = ta_fields.setup_intent or ta_fields.entry_intent or ta_fields.entry_cancel_rule_raw is not None
    if has_setup_keywords:
        return "SETUP_INCOMPLETE"

    info_markers = cfg.message_type_markers.get("INFO_ONLY", ())
    if any(hint in normalized for hint in info_markers):
        return "INFO_ONLY"
    return "INFO_ONLY"


def _extract_symbols(text: str, *, cfg: TAProfileConfig) -> list[str]:
    patterns = cfg.regex_patterns.get("symbol", ())
    if not patterns:
        return []
    hashtag_hits = [m.group(1).upper() for m in re.finditer(patterns[0], text, flags=re.IGNORECASE)]
    if hashtag_hits:
        return hashtag_hits
    fallback_patterns = patterns[1:] if len(patterns) > 1 else patterns
    values: list[str] = []
    for pattern in fallback_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            values.append(match.group(1).upper())
    return values


def _extract_intents(*, normalized: str, text: str, cfg: TAProfileConfig) -> list[str]:
    intents: list[str] = []
    for intent, markers in cfg.intent_keywords.items():
        if not markers:
            continue
        if intent == "U_REPORT_FINAL_RESULT":
            if _has_any(normalized, markers) and _first_search(text, cfg.regex_patterns.get("result_r", ())):
                intents.append(intent)
            continue
        if _has_any(normalized, markers):
            intents.append(intent)
    return intents


def _first_search(text: str, patterns: tuple[str, ...]) -> re.Match[str] | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match
    return None


def _all_group1(text: str, patterns: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            values.append(match.group(1).strip())
    return values


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker and marker in text for marker in markers)


def _load_rules_from_path(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, Mapping):
                return data
    except (OSError, ValueError):
        return {}
    return {}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip().lower()
            if text:
                result.append(text)
    return result


def _merge_str_lists(defaults: Mapping[str, tuple[str, ...]], override_value: Any) -> dict[str, tuple[str, ...]]:
    merged: dict[str, tuple[str, ...]] = {key: tuple(values) for key, values in defaults.items()}
    if not isinstance(override_value, Mapping):
        return merged
    for key, raw_values in override_value.items():
        if not isinstance(key, str):
            continue
        values = _as_str_list(raw_values)
        if values:
            merged[key] = tuple(values)
    return merged


def _unique_append(values: list[str], item: str) -> list[str]:
    if item not in values:
        values.append(item)
    return values
