"""Legacy TA compatibility shim.

Runtime parsing for TA/A/trader_a is now centralized in the canonical
`trader_a` profile via registry alias canonicalization.
This module keeps a minimal backward-compatible API for older tests/tools.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.common_utils import extract_hashtags, extract_telegram_links
from src.parser.trader_profiles.registry import get_profile_parser

_DEFAULT_RULES_PATH = Path(__file__).resolve().parents[3] / "traders" / "TA" / "parsing_rules.json"

_DEFAULT_MESSAGE_TYPE_MARKERS: dict[str, tuple[str, ...]] = {
    "NEW_SIGNAL": ("вход", "sl", "tp", "#"),
    "UPDATE": ("стоп в бу", "cancel pending", "снимаем лимитки", "закрываю", "зафиксировать"),
    "INFO_ONLY": ("обзор", "коммент", "наблюдаю"),
}

_DEFAULT_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "U_MOVE_STOP_TO_BE": ("стоп в бу", "breakeven", "stop to breakeven"),
    "U_CANCEL_PENDING_ORDERS": ("снимаем лимитки", "уберем лимитки", "cancel pending"),
    "U_CLOSE_FULL": ("закрываю все", "close all"),
    "U_REPORT_FINAL_RESULT": ("r", "rr"),
}

_DEFAULT_PHRASE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "market_entry": ("вход с текущих", "entry from market"),
    "breakeven": ("стоп в бу", "breakeven"),
    "cancel_pending": ("снимаем лимитки", "cancel pending"),
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
        ignore_if_contains=tuple(_as_str_list(payload.get("ignore_if_contains"))),
        regex_patterns=_merge_str_lists({}, payload.get("regex_patterns")),
    )


def extract_ta_fields(text: str, normalized: str) -> TAExtractedFields:
    parser = get_profile_parser("trader_a")
    parsed = parser.parse_message(
        text=text,
        context=ParserContext(
            trader_code="trader_a",
            message_id=None,
            reply_to_message_id=None,
            channel_id=None,
            raw_text=text,
            extracted_links=extract_telegram_links(text),
            hashtags=extract_hashtags(text),
        ),
    )

    entities = parsed.entities or {}
    intents = list(parsed.intents or [])
    if not intents:
        intents = _compat_intents_from_text(normalized)
    if "U_MOVE_STOP_TO_BE" in intents and "U_MOVE_STOP" not in intents:
        intents.append("U_MOVE_STOP")

    symbol = entities.get("symbol")
    if not isinstance(symbol, str):
        symbol = None

    side = entities.get("side")
    direction = None
    if isinstance(side, str):
        side_upper = side.upper()
        if side_upper in {"LONG", "BUY"}:
            direction = "BUY"
        elif side_upper in {"SHORT", "SELL"}:
            direction = "SELL"

    entry_values = entities.get("entry")
    primary_entry_raw = None
    if isinstance(entry_values, list) and entry_values:
        first = entry_values[0]
        if isinstance(first, (int, float)):
            primary_entry_raw = str(float(first))

    secondary_entry_raw = None
    averaging = entities.get("averaging")
    if isinstance(averaging, (int, float)):
        secondary_entry_raw = str(float(averaging))

    stop_raw = None
    stop_loss = entities.get("stop_loss")
    if isinstance(stop_loss, (int, float)):
        stop_raw = str(float(stop_loss))

    targets: list[str] = []
    take_profits = entities.get("take_profits")
    if isinstance(take_profits, list):
        targets = [str(float(value)) for value in take_profits if isinstance(value, (int, float))]

    update_hits = len([intent for intent in intents if intent.startswith("U_") and intent != "U_REPORT_FINAL_RESULT"])
    has_cancel = "U_CANCEL_PENDING_ORDERS" in intents

    return TAExtractedFields(
        symbol=symbol,
        direction=direction,
        primary_entry_raw=primary_entry_raw,
        secondary_entry_raw=secondary_entry_raw,
        stop_raw=stop_raw,
        targets=targets,
        risk_hint=None,
        entry_cancel_rule_raw="cancel_pending" if has_cancel else None,
        setup_intent=parsed.message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"},
        entry_intent=primary_entry_raw is not None,
        overview_hint=parsed.message_type == "INFO_ONLY",
        teaser_hint=False,
        admin_info_hint=False,
        session_hint=False,
        update_hits=update_hits,
        multi_symbol_update=False,
        multi_action_update=update_hits >= 2,
        intents=intents,
    )


def classify_ta_message(
    normalized: str,
    extracted: object,
    has_strong_link: bool,
    ta_fields: TAExtractedFields,
) -> str:
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
    if ta_fields.intents:
        only_result = set(ta_fields.intents) == {"U_REPORT_FINAL_RESULT"}
        if only_result and not has_strong_link:
            return "INFO_ONLY"
        return "UPDATE"
    if has_strong_link and ta_fields.update_hits > 0:
        return "UPDATE"
    if ta_fields.setup_intent:
        return "SETUP_INCOMPLETE"
    return "INFO_ONLY"


def _compat_intents_from_text(normalized: str) -> list[str]:
    intents: list[str] = []
    text = normalized.lower()
    if any(marker in text for marker in ("стоп в бу", "breakeven", "stop to breakeven")):
        intents.append("U_MOVE_STOP_TO_BE")
        intents.append("U_MOVE_STOP")
    if any(marker in text for marker in ("cancel pending", "снимаем лимитки", "уберем лимитки", "отменяем лимитки")):
        intents.append("U_CANCEL_PENDING_ORDERS")
    if any(marker in text for marker in ("close all", "закрываю все", "зафиксировать все")):
        intents.append("U_CLOSE_FULL")
    if "r" in text and any(token in text for token in (" rr", " r", "+", "-")):
        intents.append("U_REPORT_FINAL_RESULT")
    return intents


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
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
    return out


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
