"""Trader A profile parser."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text, split_lines
from src.parser.intent_action_map import intent_policy_for_intent

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"
_SYMBOL_RE = re.compile(r"\b[A-Z0-9]{1,24}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?\b")
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_RESULT_R_RE = re.compile(r"\b[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?\s*[-:=]\s*[+-]?\d+(?:[.,]\d+)?\s*R{1,2}\b", re.IGNORECASE)
_RESULT_R_CAPTURE_RE = re.compile(
    r"\b(?P<symbol>[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?)\s*[-:=]\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*R{1,2}\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"\b(?P<value>\d{1,3}(?:[.,]\d+)?)%")
_TP_INDEX_RE = re.compile(r"\btp(?P<index>\d+)\b", re.IGNORECASE)
_STOP_LEVEL_RE = re.compile(r"(?:move\s*(?:sl|stop)\s*(?:to)?|sl|stop)\s*[:=@-]?\s*(?P<value>\d+(?:[.,]\d+)?)", re.IGNORECASE)
_ENTRY_VALUE_RE = re.compile(
    r"(?:entry|entries|\u0432\u0445\u043e\u0434(?:\s+\u0441\s+\u0442\u0435\u043a\u0443\u0449\u0438\u0445|\s+\u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439|\s+\u043b\u0438\u043c\u0438\u0442\u043d\u044b\u043c\s+\u043e\u0440\u0434\u0435\u0440\u043e\u043c)?)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_STOP_LOSS_VALUE_RE = re.compile(r"(?:\bsl\b|stop|\u0441\u0442\u043e\u043f)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TAKE_PROFIT_VALUE_RE = re.compile(r"\btp(?:\d+)?\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_AVERAGING_VALUE_RE = re.compile(r"(?:averaging|\u0443\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_STOP_TO_TP1_RE = re.compile(
    r"(?:\u0441\u0442\u043e\u043f\s+\u043d\u0430\s+(?:1|\u043f\u0435\u0440\u0432\u044b\u0439)\s+\u0442\u0435\u0439\u043a|\u0441\u0442\u043e\u043f\s+\u043d\u0430\s+tp1)",
    re.IGNORECASE,
)
_TP2_HIT_RE = re.compile(
    r"(?:\u0434\u043e\u0448\u043b\u0438\s+\u0434\u043e\s+2[-\s]?(?:\u0445|x)?\s+\u0442\u0435\u0439\u043a\u043e\u0432|2[-\s]?(?:\u0445|x)?\s+\u0442\u0435\u0439\u043a)",
    re.IGNORECASE,
)
_TP1_HIT_RE = re.compile(r"(?:1\s+\u0442\u0435\u0439\u043a|\u043f\u0435\u0440\u0432\u044b\u0439\s+\u0442\u0435\u0439\u043a)", re.IGNORECASE)
_RESULT_PERCENT_RE = re.compile(r"(?P<value>[+-]?\d{1,3}(?:[.,]\d+)?)%")
_ENTRY_AB_VALUE_RE = re.compile(
    r"(?:^|\n)\s*(?:[-\u2014\u2022]\s*)?(?:\u0432\u0445\u043e\u0434\s*)?(?:\((?P<label_paren>[ab\u0430\u0431])\)|(?P<label>[ab\u0430\u0431]))(?:\s*\((?P<qual>[^)]*)\))?\s*[:=@-]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_DEFAULT_CLASSIFICATION_MARKERS: dict[str, tuple[str, ...]] = {
    "new_signal_strong": (
        "long",
        "short",
        "\u043b\u043e\u043d\u0433",
        "\u0448\u043e\u0440\u0442",
        "entry",
        "\u0432\u0445\u043e\u0434",
        "\u0432\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445",
        "sl:",
        "tp1:",
        "tp2:",
        "tp3:",
        "tp:",
    ),
    "update_strong": (
        "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443",
        "\u0441\u0442\u043e\u043f \u043d\u0430 1 \u0442\u0435\u0439\u043a",
        "\u0441\u0442\u043e\u043f \u043d\u0430 \u043f\u0435\u0440\u0432\u044b\u0439 \u0442\u0435\u0439\u043a",
        "\u043e\u0441\u0442\u0430\u0442\u043e\u043a \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0446\u0435\u043d\u0435",
        "\u0434\u043e\u0448\u043b\u0438 \u0434\u043e 2-\u0445 \u0442\u0435\u0439\u043a\u043e\u0432",
        "move stop",
        "зафиксировать",
        "хочу зафиксировать",
        "фиксация 100%",
        "фиксация 100% по текущим отметкам",
        "close all",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
        "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u0443\u044e \u0432\u0441\u0435 \u0441\u0432\u043e\u0438 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445",
        "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445",
    ),
    "setup_incomplete": (
        "\u0442\u0435\u0439\u043a\u0438 \u043f\u043e\u0437\u0436\u0435",
        "\u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u043e \u0442\u0435\u0439\u043a\u0430\u0445 \u043f\u043e\u0437\u0436\u0435",
        "entry only",
        "sl later",
        "tp later",
    ),
}


_DEFAULT_INTENT_MARKERS: dict[str, tuple[str, ...]] = {
    "U_MOVE_STOP_TO_BE": (
        "move stop to be",
        "stop to breakeven",
        "stop to entry",
        "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443",
        "\u0441\u0442\u043e\u043f\u044b \u0432 \u0431\u0443",
        "\u0441\u0442\u043e\u043f \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
        "\u0441\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430",
        "\u0441\u0442\u043e\u043f \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u0442\u043e\u044f\u0442\u044c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043a\u0435",
        "\u043f\u0435\u0440\u0435\u0432\u0435\u0441\u0442\u0438 \u0441\u0442\u043e\u043f \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
        "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c \u0432 \u0431\u0443",
        "\u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c \u0441\u0442\u043e\u043f \u0432 \u0431\u0443",
    ),
    "U_MOVE_STOP": (
        "move stop",
        "move sl",
        "\u0441\u0442\u043e\u043f \u043d\u0430 1 \u0442\u0435\u0439\u043a",
        "\u0441\u0442\u043e\u043f \u043d\u0430 \u043f\u0435\u0440\u0432\u044b\u0439 \u0442\u0435\u0439\u043a",
        "\u0441\u0442\u043e\u043f \u043d\u0430 tp1",
    ),
    "U_CANCEL_PENDING_ORDERS": (
        "cancel pending",
        "cancel limit",
        "\u0443\u0431\u0438\u0440\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
        "\u0443\u0431\u0435\u0440\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
        "\u0441\u043d\u0438\u043c\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
        "\u0441\u043d\u044f\u0442\u044c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
        "\u043b\u0438\u043c\u0438\u0442\u043a\u0438 \u0443\u0431\u0438\u0440\u0430\u0435\u043c",
        "\u043e\u0442\u043c\u0435\u043d\u044f\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
        "\u0441\u043d\u044f\u0442\u044c \u0432\u0441\u0435 \u043b\u0438\u043c\u0438\u0442\u043d\u044b\u0435 \u043e\u0440\u0434\u0435\u0440\u0430",
        "\u0441\u043d\u044f\u0442\u044c \u043b\u0438\u043c\u0438\u0442\u043d\u044b\u0435 \u043e\u0440\u0434\u0435\u0440\u0430",
        "\u0441\u043d\u044f\u0442\u044c \u043e\u0440\u0434\u0435\u0440\u0430",
        "\u0443\u0431\u0438\u0440\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
        "\u043b\u0438\u043c\u0438\u0442\u043a\u0443 \u0443\u0431\u0438\u0440\u0430\u0435\u043c",
        "\u0443\u0431\u0440\u0430\u0442\u044c \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
    ),
    "U_INVALIDATE_SETUP": (
        "\u043e\u0442\u043c\u0435\u043d\u0430 \u0432\u0445\u043e\u0434\u0430",
        "\u0431\u0435\u0437 \u0440\u0435\u0442\u0435\u0441\u0442\u0430",
        "without retest",
        "\u0435\u0441\u043b\u0438 15m \u0437\u0430\u043a\u0440\u0435\u043f\u0438\u0442\u0441\u044f \u0432\u044b\u0448\u0435",
        "\u0435\u0441\u043b\u0438 \u0446\u0435\u043d\u0430 \u0443\u0439\u0434\u0435\u0442 \u043a",
        "\u0435\u0441\u043b\u0438 \u0446\u0435\u043d\u0430 \u0443\u0439\u0434\u0451\u0442 \u043a",
        "\u0437\u0430\u043a\u0440\u0435\u043f\u0438\u0442\u0441\u044f \u0432\u044b\u0448\u0435",
        "\u0437\u0430\u043a\u0440\u0435\u043f\u0438\u0442\u0441\u044f \u043d\u0438\u0436\u0435",
        "\u0443\u0439\u0434\u0435\u0442 \u043a",
        "\u0443\u0439\u0434\u0451\u0442 \u043a",
        "price goes to",
    ),
    "U_CLOSE_FULL": (
        "close all",
        "close full",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
        "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u0443\u044e \u0432\u0441\u0435 \u0441\u0432\u043e\u0438 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "\u043e\u0441\u0442\u0430\u0442\u043e\u043a \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0446\u0435\u043d\u0435",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "\u0434\u0430\u0432\u0430\u0439\u0442\u0435 \u0438\u0445 \u043f\u0440\u0438\u043a\u0440\u043e\u0435\u043c",
        "\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445",
        "\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445",
        "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445",
        "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445",
        "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b",
        "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438",
        "\u0445\u043e\u0447\u0443 \u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
        "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
        "\u0444\u0438\u043a\u0441\u0430\u0446\u0438\u044f 100%",
        "\u0444\u0438\u043a\u0441\u0430\u0446\u0438\u044f 100% \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u043c",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432 \u0443\u0431\u044b\u0442\u043e\u043a",
    ),
    "U_CLOSE_PARTIAL": ("partial close", "close half", "\u0447\u0430\u0441\u0442\u0438\u0447\u043d\u043e", "\u043f\u043e\u043b\u043e\u0432\u0438\u043d\u0443"),
    "U_TP_HIT": (
        "tp hit",
        "tp1 hit",
        "\u0442\u0435\u0439\u043a \u0432\u0437\u044f\u0442",
        "\u0434\u043e\u0448\u043b\u0438 \u0434\u043e 2-\u0445 \u0442\u0435\u0439\u043a\u043e\u0432",
        "1 \u0442\u0435\u0439\u043a",
        "\u0442\u0443\u0442 \u0442\u0435\u0439\u043a",
        "\u043e\u0447\u0435\u0440\u0435\u0434\u043d\u043e\u0439 \u0442\u0435\u0439\u043a",
    ),
    "U_STOP_HIT": (
        "stop hit",
        "stopped out",
        "\u0432\u044b\u0431\u0438\u043b\u043e \u043f\u043e \u0441\u0442\u043e\u043f\u0443",
        "\u0443\u0432\u044b \u0441\u0442\u043e\u043f",
        "\u043a \u0441\u043e\u0436\u0430\u043b\u0435\u043d\u0438\u044e \u0441\u0442\u043e\u043f",
    ),
    "U_MARK_FILLED": (
        "entry filled",
        "filled",
        "\u0432\u0445\u043e\u0434 \u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d",
        "\u0432\u0437\u044f\u043b\u0438 \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
        "\u043b\u0438\u043c\u0438\u0442\u043a\u0430 \u0432\u0437\u044f\u043b\u0430\u0441\u044c",
        "\u0432\u0437\u044f\u043b\u043e \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
    ),
    "U_REPORT_FINAL_RESULT": ("final result", "results", "\u0438\u0442\u043e\u0433", "\u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b"),
}

_INTERMEDIATE_RESULT_MARKERS: tuple[str, ...] = (
    "\u0447\u0438\u0441\u0442\u043e\u0433\u043e \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u044f",
    "\u0447\u0438\u0441\u0442\u044b\u043c\u0438",
    "\u043f\u043e\u0437\u0434\u0440\u0430\u0432\u043b\u044f\u044e",
    "\u043f\u0440\u043e\u0444\u0438\u0442",
    "profit",
)

class TraderAProfileParser:
    trader_code = "trader_a"
    supports_targeted_actions_structured = True

    def __init__(self, rules_path: Path | None = None) -> None:
        self._rules_path = rules_path or _RULES_PATH
        self._rules = self._load_rules(self._rules_path)

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        prepared = self._preprocess(text=text, context=context)
        target_refs = self._extract_targets(prepared=prepared, context=context)
        global_target_scope = self._resolve_global_target_scope(prepared=prepared)
        has_global_target = global_target_scope is not None
        message_type = self._classify_message(prepared=prepared, context=context, target_refs=target_refs)
        intents = self._extract_intents(
            prepared=prepared,
            context=context,
            message_type=message_type,
            target_refs=target_refs,
        )
        if message_type == "UNCLASSIFIED" and "U_REPORT_FINAL_RESULT" in intents:
            if (target_refs or has_global_target) and any(intent in intents for intent in ("U_CLOSE_FULL", "U_CLOSE_PARTIAL", "U_CANCEL_PENDING_ORDERS", "U_MOVE_STOP_TO_BE", "U_MOVE_STOP")):
                message_type = "UPDATE"
            else:
                message_type = "INFO_ONLY"
        if message_type == "UNCLASSIFIED" and "U_CANCEL_PENDING_ORDERS" in intents and (target_refs or has_global_target):
            message_type = "UPDATE"
        if message_type == "UNCLASSIFIED" and (target_refs or has_global_target) and any(intent in intents for intent in ("U_CLOSE_FULL", "U_CLOSE_PARTIAL")):
            message_type = "UPDATE"
        if message_type == "UNCLASSIFIED" and (target_refs or has_global_target) and any(
            intent in intents
            for intent in (
                "U_MOVE_STOP_TO_BE",
                "U_MOVE_STOP",
                "U_TP_HIT",
                "U_STOP_HIT",
                "U_MARK_FILLED",
            )
        ):
            message_type = "UPDATE"
        reported_results = self._extract_reported_results(prepared=prepared, context=context, intents=intents)
        entities = self._extract_entities(
            prepared=prepared,
            context=context,
            intents=intents,
            target_refs=target_refs,
            reported_results=reported_results,
            global_target_scope=global_target_scope,
        )
        warnings = self._build_warnings(
            prepared=prepared,
            context=context,
            message_type=message_type,
            intents=intents,
            target_refs=target_refs,
        )
        confidence = self._estimate_confidence(
            prepared=prepared,
            context=context,
            message_type=message_type,
            intents=intents,
            warnings=warnings,
        )
        # Legacy message_type/intents/entities/target_refs stay unchanged.
        # The v2 semantic envelope below is additive for backward compatibility.
        primary_intent = self._derive_primary_intent(message_type=message_type, intents=intents)
        actions_structured = self._build_actions_structured(message_type=message_type, intents=intents, entities=entities)
        actions_structured = self._build_grouped_targeted_actions(
            prepared=prepared,
            message_type=message_type,
            intents=intents,
            target_refs=target_refs,
            actions_structured=actions_structured,
            global_target_scope=global_target_scope,
        )
        linking = self._build_linking(target_refs=target_refs, context=context, has_global_target=has_global_target)
        target_scope = self._build_target_scope(
            entities=entities,
            has_global_target=has_global_target,
            global_target_scope=global_target_scope,
        )
        diagnostics = self._build_diagnostics(
            prepared=prepared,
            message_type=message_type,
            intents=intents,
            warnings=warnings,
            has_global_target=has_global_target,
        )
        return TraderParseResult(
            message_type=message_type,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            reported_results=reported_results,
            warnings=warnings,
            confidence=confidence,
            primary_intent=primary_intent,
            actions_structured=actions_structured,
            target_scope=target_scope,
            linking=linking,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _derive_primary_intent(*, message_type: str, intents: list[str]) -> str | None:
        if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
            return "NS_CREATE_SIGNAL"
        for intent in intents:
            if intent.startswith("U_"):
                return intent
        return intents[0] if intents else None

    @staticmethod
    def _build_actions_structured(*, message_type: str, intents: list[str], entities: dict[str, Any]) -> list[dict[str, Any]]:
        if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
            return [
                {
                    "action": "CREATE_SIGNAL",
                    "instrument": entities.get("symbol"),
                    "side": entities.get("side"),
                    "entries": entities.get("entry", []),
                    "stop_loss": entities.get("stop_loss"),
                    "take_profits": entities.get("take_profits", []),
                    "setup_invalidation": entities.get("setup_invalidation"),
                }
            ]

        actions: list[dict[str, Any]] = []
        for intent in intents:
            if not intent_policy_for_intent(intent).get("state_change"):
                continue
            if intent == "U_MOVE_STOP_TO_BE":
                actions.append({"action": "MOVE_STOP", "new_stop_level": "ENTRY"})
            elif intent == "U_MOVE_STOP":
                actions.append({"action": "MOVE_STOP", "new_stop_level": entities.get("new_stop_level")})
            elif intent == "U_CLOSE_PARTIAL":
                actions.append(
                    {
                        "action": "CLOSE_POSITION",
                        "scope": "PARTIAL",
                        "close_fraction": entities.get("close_fraction"),
                    }
                )
            elif intent == "U_CLOSE_FULL":
                actions.append({"action": "CLOSE_POSITION", "scope": entities.get("close_scope", "FULL")})
            elif intent == "U_CANCEL_PENDING_ORDERS":
                actions.append({"action": "CANCEL_PENDING", "scope": entities.get("cancel_scope", "ALL_PENDING_ENTRIES")})
            elif intent == "U_TP_HIT":
                actions.append({"action": "TAKE_PROFIT", "target": entities.get("hit_target", "TP")})
            elif intent == "U_STOP_HIT":
                actions.append({"action": "CLOSE_POSITION", "target": "STOP"})
            elif intent == "U_MARK_FILLED":
                actions.append({"action": "MARK_FILLED", "fill_state": entities.get("fill_state", "FILLED")})
            elif intent == "U_REPORT_FINAL_RESULT":
                actions.append({"action": "REPORT_RESULT", "mode": entities.get("result_mode", "TEXT_SUMMARY")})
        return actions

    def _build_actions_structured_granular(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        intents: list[str],
        entities: dict[str, Any],
        target_refs: list[dict[str, Any]],
        global_target_scope: str | None,
        legacy_actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        if not self.supports_targeted_actions_structured or message_type != "UPDATE":
            return None

        explicit_target_ids = self._explicit_target_message_ids(target_refs=target_refs)
        raw_text = str(prepared.get("raw_text") or "")

        granular_stop_actions = self._build_line_level_move_stop_actions(raw_text=raw_text)
        if granular_stop_actions:
            remaining = [item for item in legacy_actions if item.get("action") != "MOVE_STOP"]
            return [*granular_stop_actions, *remaining]

        if "U_CLOSE_FULL" in intents and len(explicit_target_ids) >= 2:
            targeted_close = {
                "action": "CLOSE_POSITION",
                "scope": entities.get("close_scope", "FULL"),
                "targeting": {
                    "mode": "TARGET_GROUP",
                    "targets": explicit_target_ids,
                },
            }
            remaining = [item for item in legacy_actions if item.get("action") != "CLOSE_POSITION"]
            return [targeted_close, *remaining]

        selector = self._selector_from_global_scope(global_target_scope=global_target_scope)
        if "U_CLOSE_FULL" in intents and selector:
            targeted_close = {
                "action": "CLOSE_POSITION",
                "scope": entities.get("close_scope", "FULL"),
                "targeting": {
                    "mode": "SELECTOR",
                    "selector": selector,
                },
            }
            remaining = [item for item in legacy_actions if item.get("action") != "CLOSE_POSITION"]
            return [targeted_close, *remaining]

        return None

    @staticmethod
    def _explicit_target_message_ids(*, target_refs: list[dict[str, Any]]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for ref in target_refs:
            if ref.get("kind") != "message_id":
                continue
            value = ref.get("ref")
            if not isinstance(value, int) or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    def _build_line_level_move_stop_actions(self, *, raw_text: str) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        seen_targets: set[int] = set()
        for line in split_lines(raw_text):
            level = self._line_stop_level(line=line)
            if level is None:
                continue
            message_ids = self._extract_message_ids_from_line(line=line)
            if not message_ids:
                continue
            for message_id in message_ids:
                if message_id in seen_targets:
                    continue
                seen_targets.add(message_id)
                actions.append(
                    {
                        "action": "MOVE_STOP",
                        "new_stop_level": level,
                        "targeting": {
                            "mode": "EXPLICIT_TARGETS",
                            "targets": [message_id],
                        },
                    }
                )
        return actions

    @staticmethod
    def _extract_message_ids_from_line(*, line: str) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for link in extract_telegram_links(line):
            match = _LINK_ID_RE.search(link)
            if not match:
                continue
            message_id = int(match.group("id"))
            if message_id in seen:
                continue
            seen.add(message_id)
            out.append(message_id)
        return out

    @staticmethod
    def _line_stop_level(*, line: str) -> str | None:
        normalized = normalize_text(line)
        if _STOP_TO_TP1_RE.search(line):
            return "TP1"
        if _contains_any(
            normalized,
            (
                "стоп в бу",
                "стопы в бу",
                "стоп в безубыток",
                "stop to be",
                "stop to breakeven",
                "stop to entry",
            ),
        ):
            return "ENTRY"
        return None

    @staticmethod
    def _selector_from_global_scope(*, global_target_scope: str | None) -> dict[str, str] | None:
        if global_target_scope in {"ALL_REMAINING_SHORTS", "ALL_SHORTS"}:
            return {"side": "SHORT", "status": "OPEN"}
        if global_target_scope in {"ALL_REMAINING_LONGS", "ALL_LONGS"}:
            return {"side": "LONG", "status": "OPEN"}
        return None

    @staticmethod
    def _build_linking(*, target_refs: list[dict[str, Any]], context: ParserContext, has_global_target: bool) -> dict[str, Any]:
        return {
            "targeted": bool(target_refs or has_global_target),
            "reply_to_message_id": context.reply_to_message_id,
            "target_refs_count": len(target_refs),
            "has_global_target_scope": has_global_target,
            "strategy": "reply_or_link" if target_refs else ("global_scope" if has_global_target else "unresolved"),
        }

    @staticmethod
    def _build_target_scope(
        *,
        entities: dict[str, Any],
        has_global_target: bool,
        global_target_scope: str | None,
    ) -> dict[str, Any]:
        close_scope = entities.get("close_scope")
        side = str(entities.get("side") or "").upper()
        if global_target_scope in {"ALL_REMAINING_SHORTS", "ALL_REMAINING"}:
            if side in {"SHORT", ""}:
                return {
                    "kind": "portfolio_side",
                    "scope": "ALL_OPEN_SHORTS",
                    "applies_to_all": True,
                    "position_side_filter": "SHORT",
                    "position_status_filter": "OPEN",
                }
        if global_target_scope == "ALL_REMAINING_LONGS":
            if side in {"LONG", ""}:
                return {
                    "kind": "portfolio_side",
                    "scope": "ALL_OPEN_LONGS",
                    "applies_to_all": True,
                    "position_side_filter": "LONG",
                    "position_status_filter": "OPEN",
                }
        if global_target_scope == "ALL_REMAINING":
            return {
                "kind": "portfolio_side",
                "scope": "ALL_OPEN",
                "applies_to_all": True,
                "position_status_filter": "OPEN",
            }
        if close_scope in {"ALL_LONGS", "ALL_SHORTS", "ALL_ALL", "ALL_OPEN", "ALL_REMAINING", "ALL_REMAINING_SHORTS", "ALL_REMAINING_LONGS"}:
            return {"kind": "portfolio_side", "scope": close_scope}
        if global_target_scope in {"ALL_LONGS", "ALL_SHORTS", "ALL_ALL", "ALL_OPEN", "ALL_REMAINING", "ALL_REMAINING_SHORTS", "ALL_REMAINING_LONGS"}:
            return {"kind": "portfolio_side", "scope": global_target_scope}
        if has_global_target:
            return {"kind": "portfolio_side", "scope": "GLOBAL"}
        return {"kind": "signal", "scope": "single"}

    @staticmethod
    def _build_diagnostics(
        *,
        prepared: dict[str, Any],
        message_type: str,
        intents: list[str],
        warnings: list[str],
        has_global_target: bool,
    ) -> dict[str, Any]:
        return {
            "parser_version": "trader_a_v2_compatible",
            "message_type": message_type,
            "intent_count": len(intents),
            "warning_count": len(warnings),
            "has_global_target_scope": has_global_target,
            "raw_text_length": len(str(prepared.get("raw_text") or "")),
        }

    def _build_grouped_targeted_actions(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        intents: list[str],
        target_refs: list[dict[str, Any]],
        actions_structured: list[dict[str, Any]],
        global_target_scope: str | None,
    ) -> list[dict[str, Any]]:
        if message_type != "UPDATE":
            return actions_structured

        raw_text = str(prepared.get("raw_text") or "")
        per_target = self._extract_per_target_action_items(raw_text=raw_text)
        if per_target is None:
            return actions_structured
        if per_target:
            return self._group_action_items(per_target)

        explicit_targets = [int(item.get("ref")) for item in target_refs if item.get("kind") == "message_id" and isinstance(item.get("ref"), int)]
        if explicit_targets and "U_CLOSE_FULL" in intents:
            return [
                {
                    "action": "CLOSE_POSITION",
                    "scope": "FULL",
                    "targeting": {"mode": "TARGET_GROUP", "targets": sorted(set(explicit_targets))},
                }
            ]

        if not explicit_targets and "U_CLOSE_FULL" in intents and global_target_scope in {"ALL_SHORTS", "ALL_LONGS"}:
            return [
                {
                    "action": "CLOSE_POSITION",
                    "scope": "FULL",
                    "targeting": {
                        "mode": "SELECTOR",
                        "selector": {
                            "side": "SHORT" if global_target_scope == "ALL_SHORTS" else "LONG",
                            "status": "OPEN",
                        },
                    },
                }
            ]
        return actions_structured

    def _extract_per_target_action_items(self, *, raw_text: str) -> list[dict[str, Any]] | None:
        items: list[dict[str, Any]] = []
        lines = split_lines(raw_text)
        for line in lines:
            link_match = _LINK_ID_RE.search(line)
            if not link_match:
                continue
            try:
                target = int(link_match.group("id"))
            except ValueError:
                continue
            normalized_line = normalize_text(line)
            if "стоп в бу" in normalized_line or "stop in be" in normalized_line:
                items.append({"action": "MOVE_STOP", "new_stop_level": "ENTRY", "target": target})
            elif any(token in normalized_line for token in ("стоп на 1 тейк", "стоп на первый тейк", "стоп на tp1")):
                items.append({"action": "MOVE_STOP", "new_stop_level": "TP1", "target": target})
            elif "стоп" in normalized_line:
                # ambiguous per-target stop command: force full legacy fallback
                return None
        return items

    @staticmethod
    def _group_action_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[int]] = {}
        for item in items:
            action = str(item.get("action") or "")
            level = str(item.get("new_stop_level") or "")
            target = item.get("target")
            if not action or not level or not isinstance(target, int):
                continue
            grouped.setdefault((action, level), []).append(target)

        out: list[dict[str, Any]] = []
        for (action, level), targets in grouped.items():
            unique_targets = sorted(set(targets))
            out.append(
                {
                    "action": action,
                    "new_stop_level": level,
                    "targeting": {
                        "mode": "TARGET_GROUP" if len(unique_targets) > 1 else "EXPLICIT_TARGETS",
                        "targets": unique_targets,
                    },
                }
            )
        return out

    def _preprocess(self, *, text: str, context: ParserContext) -> dict[str, Any]:
        raw_text = text or context.raw_text
        return {
            "raw_text": raw_text,
            "normalized_text": normalize_text(raw_text),
            "lines": split_lines(raw_text),
        }

    def _classify_message(
        self,
        *,
        prepared: dict[str, Any],
        context: ParserContext,
        target_refs: list[dict[str, Any]],
    ) -> str:
        normalized = str(prepared.get("normalized_text") or "")
        raw_text = str(prepared.get("raw_text") or "")
        has_target = bool(target_refs) or self._has_global_target_scope(prepared=prepared)
        classification = self._rules.get("classification_markers") if isinstance(self._rules, dict) else {}
        new_markers = _merge_markers(_class_markers(classification, "new_signal"), _DEFAULT_CLASSIFICATION_MARKERS["new_signal_strong"])
        update_markers = _merge_markers(_class_markers(classification, "update"), _DEFAULT_CLASSIFICATION_MARKERS["update_strong"])
        report_markers = _merge_markers(None, _DEFAULT_INTENT_MARKERS["U_REPORT_FINAL_RESULT"])
        info_only_markers = (
            "\u0437\u0430\u043a\u0440\u044b\u043b\u0430\u0441\u044c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
            "\u0441\u0434\u0435\u043b\u043a\u0430 \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
            "\u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
            "\u0441\u0435\u0442\u0430\u043f \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e \u0437\u0430\u043a\u0440\u044b\u0442",
        )
        incomplete_markers = _merge_markers(
            classification.get("setup_incomplete") if isinstance(classification, dict) else None,
            _DEFAULT_CLASSIFICATION_MARKERS["setup_incomplete"],
        )

        if _contains_any(normalized, tuple(_ignore_markers(self._rules))):
            return "INFO_ONLY"

        has_direction = _contains_any(normalized, ("long", "short", "buy", "sell", "\u043b\u043e\u043d\u0433", "\u0448\u043e\u0440\u0442"))
        has_entry = _contains_any(normalized, ("entry", "entries", "\u0432\u0445\u043e\u0434", "\u0432\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445")) or bool(_extract_signal_entry_levels(raw_text))
        has_stop = _contains_any(normalized, ("stop", "sl", "sl:", "\u0441\u0442\u043e\u043f"))
        has_tp = bool(re.search(r"\btp\d+\b", raw_text, re.IGNORECASE)) or _contains_any(
            normalized,
            (
                "tp1:",
                "tp2:",
                "tp3:",
                "tp:",
                "tps",
                "take profit",
                "target",
                "\u0442\u0435\u0439\u043a\u0438",
                "1 \u0442\u0435\u0439\u043a",
                "\u043f\u0435\u0440\u0432\u044b\u0439 \u0442\u0435\u0439\u043a",
            ),
        )
        if _contains_any(normalized, ("\u0442\u0435\u0439\u043a\u0438 \u043f\u043e\u0437\u0436\u0435", "\u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u043e \u0442\u0435\u0439\u043a\u0430\u0445 \u043f\u043e\u0437\u0436\u0435", "tp later")):
            has_tp = False
        has_symbol = _extract_signal_symbol(raw_text) is not None
        if not has_symbol and has_direction and has_entry and has_stop and has_tp:
            has_symbol = _extract_signal_symbol_from_bare_hashtag(raw_text) is not None
        setup_parts = sum(1 for value in (has_symbol, has_direction, has_entry, has_stop, has_tp) if value)

        # Full setup has highest priority, even if message is a reply.
        if has_symbol and has_direction and has_entry and has_stop and has_tp and (_contains_any(normalized, tuple(new_markers)) or has_entry):
            return "NEW_SIGNAL"
        if setup_parts >= 2 and _contains_any(normalized, tuple(incomplete_markers)):
            return "SETUP_INCOMPLETE"
        if _contains_any(normalized, info_only_markers):
            return "INFO_ONLY"
        if has_target and (
            _contains_any(normalized, tuple(report_markers))
            or _contains_any(
                normalized,
                (
                    "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
                    "\u0444\u0438\u043a\u0441\u0430\u0446\u0438\u044f",
                    "\u0444\u0438\u043a\u0441\u0438\u0440\u0443\u044e",
                    "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043b",
                    "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043b\u0430",
                    "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043b\u0438",
                ),
            )
        ):
            return "UPDATE"
        if not has_target and (
            _contains_any(normalized, tuple(update_markers))
            or _contains_any(
                normalized,
                (
                    "\u0441\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430",
                    "\u043f\u0435\u0440\u0435\u0432\u0435\u0441\u0442\u0438 \u0441\u0442\u043e\u043f \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
                    "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
                    "\u0432\u0437\u044f\u043b\u0438 \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
                    "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u0442\u044c 80%",
                    "\u043d\u0430 1 \u0442\u0435\u0439\u043a\u0435 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u0442\u044c",
                    "\u043f\u0440\u0438 \u0432\u0437\u044f\u0442\u0438\u0438 1 \u0442\u0435\u0439\u043a\u0430",
                    "\u043f\u043e \u0432\u0441\u0435\u043c \u043c\u043e\u0438\u043c \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u043c\u0441\u044f \u0448\u043e\u0440\u0442\u0430\u043c",
                    "\u043f\u043e \u0448\u043e\u0440\u0442\u0430\u043c \u0441\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430",
                    "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
                    "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c",
                ),
            )
            or _contains_any(
                normalized,
                (
                    "\u0432\u0437\u044f\u043b\u0438 \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
                    "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u0442\u044c 80%",
                    "\u043d\u0430 1 \u0442\u0435\u0439\u043a\u0435 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u0442\u044c",
                    "\u043f\u0440\u0438 \u0432\u0437\u044f\u0442\u0438\u0438 1 \u0442\u0435\u0439\u043a\u0430",
                    "\u043f\u043e \u0432\u0441\u0435\u043c \u043c\u043e\u0438\u043c \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u043c\u0441\u044f \u0448\u043e\u0440\u0442\u0430\u043c",
                    "\u043f\u043e \u0448\u043e\u0440\u0442\u0430\u043c \u0441\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430",
                    "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
                    "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c",
                ),
            )
        ):
            return "UPDATE"
        if _contains_any(
            normalized,
            (
                "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
                "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
                "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u0443\u044e \u0432\u0441\u0435 \u0441\u0432\u043e\u0438 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
                "\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
                "\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445",
                "\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445",
                "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
                "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445",
                "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445",
                "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445",
                "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b",
                "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438",
            ),
        ):
            return "UPDATE"
        if has_target and _contains_any(normalized, tuple(update_markers)):
            return "UPDATE"
        if has_target and _contains_any(
            normalized,
            (
                "move",
                "close",
                "cancel",
                "filled",
                "tp hit",
                "tp1 hit",
                "target hit",
                "stop hit",
                "stopped out",
                "stop to be",
                "\u0442\u0435\u0439\u043a \u0432\u0437\u044f\u0442",
                "\u0432\u044b\u0431\u0438\u043b\u043e \u043f\u043e \u0441\u0442\u043e\u043f\u0443",
                "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443",
                "\u0441\u0442\u043e\u043f\u044b \u0432 \u0431\u0443",
                "\u0441\u0442\u043e\u043f \u043d\u0430 1 \u0442\u0435\u0439\u043a",
                "\u043e\u0441\u0442\u0430\u0442\u043e\u043a \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0446\u0435\u043d\u0435",
                "\u0434\u043e\u0448\u043b\u0438 \u0434\u043e 2-\u0445 \u0442\u0435\u0439\u043a\u043e\u0432",
            ),
        ):
            return "UPDATE"
        if _contains_any(
            normalized,
            (
                "\u0432\u0437\u044f\u043b\u0438 \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
                "\u043b\u0438\u043c\u0438\u0442\u043a\u0430 \u0432\u0437\u044f\u043b\u0430\u0441\u044c",
                "\u0432\u0437\u044f\u043b\u043e \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
                "entry filled",
                "filled",
                "\u0432\u0445\u043e\u0434 \u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d",
                "\u043e\u0440\u0434\u0435\u0440 \u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d",
            ),
        ):
            return "UPDATE"
        if _has_intermediate_result_language(normalized):
            return "INFO_ONLY"
        _ = context
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
        context: ParserContext,
        message_type: str,
        target_refs: list[dict[str, Any]],
    ) -> list[str]:
        normalized = str(prepared.get("normalized_text") or "")
        raw_text = str(prepared.get("raw_text") or "")
        marker_map = self._rules.get("intent_markers") if isinstance(self._rules, dict) else {}
        if not isinstance(marker_map, dict):
            marker_map = {}

        intents: list[str] = []
        has_target = bool(target_refs)
        report_only_markers = (
            "\u0437\u0430\u043a\u0440\u044b\u043b\u0430\u0441\u044c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
            "\u0441\u0434\u0435\u043b\u043a\u0430 \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
            "\u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
            "\u0441\u0435\u0442\u0430\u043f \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e \u0437\u0430\u043a\u0440\u044b\u0442",
        )
        report_only_context = _contains_any(normalized, report_only_markers)
        if _contains_any(normalized, tuple(_ignore_markers(self._rules))):
            _ = context
            return []
        if message_type == "NEW_SIGNAL":
            intents.append("NS_CREATE_SIGNAL")

        move_to_be_markers = _merge_markers(_strong_only(marker_map.get("U_MOVE_STOP_TO_BE")), _DEFAULT_INTENT_MARKERS["U_MOVE_STOP_TO_BE"])
        move_markers = _merge_markers(_strong_only(marker_map.get("U_MOVE_STOP")), _DEFAULT_INTENT_MARKERS["U_MOVE_STOP"])
        cancel_markers = _merge_markers(marker_map.get("U_CANCEL_PENDING_ORDERS"), _DEFAULT_INTENT_MARKERS["U_CANCEL_PENDING_ORDERS"])
        invalidate_markers = _merge_markers(marker_map.get("U_INVALIDATE_SETUP"), _DEFAULT_INTENT_MARKERS["U_INVALIDATE_SETUP"])
        filled_markers = _merge_markers(marker_map.get("U_MARK_FILLED"), _DEFAULT_INTENT_MARKERS["U_MARK_FILLED"])
        future_management_context = False
        strong_move_without_target = _contains_any(normalized, tuple(move_to_be_markers)) or _contains_any(normalized, tuple(move_markers))
        strong_cancel_without_target = _contains_any(normalized, tuple(cancel_markers))

        allow_update_intents = (
            not report_only_context
            and (message_type == "UPDATE" or has_target or strong_move_without_target or strong_cancel_without_target)
        )
        if allow_update_intents:
            move_markers = _merge_markers(marker_map.get("U_MOVE_STOP"), _DEFAULT_INTENT_MARKERS["U_MOVE_STOP"])
            close_partial_markers = _merge_markers(marker_map.get("U_CLOSE_PARTIAL"), _DEFAULT_INTENT_MARKERS["U_CLOSE_PARTIAL"])
            close_full_markers = _merge_markers(marker_map.get("U_CLOSE_FULL"), _DEFAULT_INTENT_MARKERS["U_CLOSE_FULL"])
            tp_hit_markers = _merge_markers(_strong_only(marker_map.get("U_TP_HIT")), _DEFAULT_INTENT_MARKERS["U_TP_HIT"])
            stop_hit_markers = _merge_markers(_strong_only(marker_map.get("U_STOP_HIT")), _DEFAULT_INTENT_MARKERS["U_STOP_HIT"])
            future_management_context = _has_future_management_language(normalized) and _contains_any(normalized, tuple(filled_markers))

            if message_type != "NEW_SIGNAL":
                if _contains_any(normalized, tuple(move_to_be_markers)):
                    intents.append("U_MOVE_STOP_TO_BE")
                elif _contains_any(normalized, tuple(move_markers)):
                    intents.append("U_MOVE_STOP")

            if _contains_any(normalized, tuple(cancel_markers)):
                intents.append("U_CANCEL_PENDING_ORDERS")
            if _contains_any(normalized, tuple(invalidate_markers)):
                intents.append("U_INVALIDATE_SETUP")
            if not future_management_context and _contains_any(normalized, tuple(close_partial_markers)):
                intents.append("U_CLOSE_PARTIAL")
            elif not future_management_context and _contains_any(normalized, tuple(close_full_markers)):
                intents.append("U_CLOSE_FULL")

            stop_to_tp_context = bool(_STOP_TO_TP1_RE.search(raw_text))
            if message_type != "NEW_SIGNAL" and not future_management_context and not stop_to_tp_context and _contains_any(normalized, tuple(tp_hit_markers)):
                intents.append("U_TP_HIT")
            if message_type != "NEW_SIGNAL" and not future_management_context and _contains_any(normalized, tuple(stop_hit_markers)):
                intents.append("U_STOP_HIT")
            if _contains_any(normalized, tuple(filled_markers)):
                intents.append("U_MARK_FILLED")

        if message_type == "UNCLASSIFIED" and not report_only_context and _contains_any(
            normalized,
            tuple(move_to_be_markers) + tuple(move_markers) + tuple(cancel_markers) + tuple(filled_markers),
        ):
            message_type = "UPDATE"
        if message_type == "UNCLASSIFIED" and not report_only_context and _has_intermediate_result_language(normalized):
            message_type = "INFO_ONLY"

        report_markers = _merge_markers(marker_map.get("U_REPORT_FINAL_RESULT"), _DEFAULT_INTENT_MARKERS["U_REPORT_FINAL_RESULT"])
        if _should_emit_report_final_result(raw_text=raw_text, normalized=normalized, report_markers=report_markers):
            intents.append("U_REPORT_FINAL_RESULT")

        if future_management_context:
            intents = [value for value in intents if value in {"NS_CREATE_SIGNAL", "U_MARK_FILLED", "U_CANCEL_PENDING_ORDERS", "U_INVALIDATE_SETUP"}]
        elif "U_CLOSE_FULL" in intents and "U_REPORT_FINAL_RESULT" in intents:
            intents = [value for value in intents if value not in ("U_MOVE_STOP_TO_BE", "U_MOVE_STOP")]

        _ = context
        return _unique(intents)

    def _extract_entities(
        self,
        *,
        prepared: dict[str, Any],
        context: ParserContext,
        intents: list[str],
        target_refs: list[dict[str, Any]],
        reported_results: list[dict[str, Any]],
        global_target_scope: str | None,
    ) -> dict[str, Any]:
        normalized = str(prepared.get("normalized_text") or "")
        raw_text = str(prepared.get("raw_text") or "")
        entities: dict[str, Any] = {}

        if "NS_CREATE_SIGNAL" in intents:
            entities["symbol"] = _extract_signal_symbol(raw_text) or _extract_signal_symbol_from_bare_hashtag(raw_text)
            entities["side"] = _extract_signal_side(normalized)
            entities["entry"] = _extract_signal_entry_levels(raw_text)
            entities["stop_loss"] = _extract_signal_stop_loss(raw_text)
            entities["take_profits"] = _extract_signal_take_profits(raw_text)
            averaging = _extract_signal_averaging(raw_text)
            if averaging is not None:
                entities["averaging"] = averaging
            entry_plan = _extract_signal_entry_plan(raw_text)
            entities["entry_plan_entries"] = entry_plan["entries"]
            entities["entry_plan_type"] = entry_plan["entry_plan_type"]
            entities["entry_structure"] = entry_plan["entry_structure"]
            entities["has_averaging_plan"] = entry_plan["has_averaging_plan"]

        if "U_MOVE_STOP_TO_BE" in intents:
            entities["new_stop_level"] = "ENTRY"
        elif "U_MOVE_STOP" in intents:
            stop_level = _extract_stop_level(raw_text)
            if stop_level is not None:
                entities["new_stop_level"] = stop_level
        if "U_CLOSE_FULL" in intents:
            if global_target_scope in {"ALL_LONGS", "ALL_SHORTS", "ALL_ALL", "ALL_OPEN", "ALL_REMAINING"}:
                entities["close_scope"] = global_target_scope
            elif _contains_any(normalized, ("\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438", "all longs", "\u043b\u043e\u043d\u0433\u0438")):
                entities["close_scope"] = "ALL_LONGS"
            elif _contains_any(normalized, ("\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b", "all shorts", "\u0448\u043e\u0440\u0442\u044b")):
                entities["close_scope"] = "ALL_SHORTS"
            else:
                entities["close_scope"] = "FULL"
        elif "U_CLOSE_PARTIAL" in intents:
            entities["close_scope"] = "PARTIAL"
            close_fraction = _extract_close_fraction(raw_text=raw_text, normalized_text=normalized)
            if close_fraction is not None:
                entities["close_fraction"] = close_fraction
        if "U_STOP_HIT" in intents:
            entities["hit_target"] = "STOP"
        elif "U_TP_HIT" in intents:
            entities["hit_target"] = _extract_hit_target(raw_text) or "TP"
        if "U_MARK_FILLED" in intents:
            entities["fill_state"] = "FILLED"
        if "U_CANCEL_PENDING_ORDERS" in intents:
            entities["cancel_scope"] = self._resolve_cancel_scope(prepared=prepared, target_refs=target_refs)
        if "U_INVALIDATE_SETUP" in intents:
            invalidation = _extract_setup_invalidation(raw_text)
            if invalidation is not None:
                entities["setup_invalidation"] = invalidation
        result_percent = _extract_result_percent(raw_text, normalized)
        if result_percent is not None:
            entities["result_percent"] = result_percent
        if "U_REPORT_FINAL_RESULT" in intents:
            entities["result_mode"] = "R_MULTIPLE" if reported_results else "TEXT_SUMMARY"
        _ = context
        return entities

    def _extract_reported_results(
        self,
        *,
        prepared: dict[str, Any],
        context: ParserContext,
        intents: list[str],
    ) -> list[dict[str, Any]]:
        raw_text = str(prepared.get("raw_text") or "")
        if "U_REPORT_FINAL_RESULT" not in intents and not _RESULT_R_RE.search(raw_text):
            _ = context
            return []
        patterns = self._rules.get("result_patterns") if isinstance(self._rules, dict) else {}
        capture_patterns = _as_str_list(patterns.get("r_multiple")) if isinstance(patterns, dict) else []
        regexes: list[re.Pattern[str]] = []
        for pattern in capture_patterns:
            try:
                regexes.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
        if not regexes:
            regexes = [_RESULT_R_CAPTURE_RE]

        out: list[dict[str, Any]] = []
        seen: set[tuple[str, float]] = set()
        for regex in regexes:
            for match in regex.finditer(raw_text):
                symbol = match.groupdict().get("symbol")
                value_raw = match.groupdict().get("value")
                if not symbol or not value_raw:
                    continue
                value = _to_float(value_raw)
                if value is None:
                    continue
                key = (symbol.upper(), value)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"symbol": symbol.upper(), "value": value, "unit": "R"})
        _ = context
        return out

    def _build_warnings(
        self,
        *,
        prepared: dict[str, Any],
        context: ParserContext,
        message_type: str,
        intents: list[str],
        target_refs: list[dict[str, Any]],
    ) -> list[str]:
        warnings: list[str] = []
        normalized = str(prepared.get("normalized_text") or "")
        classification = self._rules.get("classification_markers") if isinstance(self._rules, dict) else {}
        update_markers = _merge_markers(_class_markers(classification if isinstance(classification, dict) else {}, "update"), _DEFAULT_CLASSIFICATION_MARKERS["update_strong"])
        has_target = bool(target_refs) or self._has_global_target_scope(prepared=prepared)
        has_update_language = _contains_any(normalized, tuple(update_markers)) or _contains_any(
            normalized,
            ("move", "close", "cancel", "filled", "tp hit", "stop hit", "stopped out", "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443", "\u0441\u0442\u043e\u043f\u044b \u0432 \u0431\u0443"),
        )
        has_update_intent = any(intent.startswith("U_") and intent not in {"U_REPORT_FINAL_RESULT", "U_INVALIDATE_SETUP"} for intent in intents)
        if message_type == "NEW_SIGNAL":
            has_update_intent = any(
                intent in {"U_MOVE_STOP_TO_BE", "U_MOVE_STOP", "U_CLOSE_FULL", "U_CLOSE_PARTIAL", "U_TP_HIT", "U_STOP_HIT", "U_MARK_FILLED"}
                for intent in intents
            )

        if not has_target and (has_update_language or has_update_intent):
            if message_type == "UPDATE":
                warnings.append("trader_a_update_missing_target")
            else:
                warnings.append("trader_a_ambiguous_update_without_target")
        _ = context
        return warnings

    def _estimate_confidence(
        self,
        *,
        prepared: dict[str, Any],
        context: ParserContext,
        message_type: str,
        intents: list[str],
        warnings: list[str],
    ) -> float:
        _ = (prepared, context, intents)
        if message_type == "NEW_SIGNAL":
            return 0.75
        if message_type == "UPDATE":
            return 0.7 if not warnings else 0.55
        if message_type == "SETUP_INCOMPLETE":
            return 0.45
        return 0.2

    def _load_rules(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                # Normalize JSON key names to internal names used by the parser.
                if "intent_keywords" in data and "intent_markers" not in data:
                    data["intent_markers"] = data.pop("intent_keywords")
                if "message_type_markers" in data and "classification_markers" not in data:
                    data["classification_markers"] = data.pop("message_type_markers")
                return data
        except (OSError, ValueError):
            return {}
        return {}

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
        if global_scope in {"ALL_ALL", "ALL_OPEN", "ALL_REMAINING"}:
            return "ALL_PENDING_ENTRIES"
        return "ALL_PENDING_ENTRIES"

    def _resolve_global_target_scope(self, *, prepared: dict[str, Any]) -> str | None:
        normalized = str(prepared.get("normalized_text") or "")
        markers = self._rules.get("global_target_markers") if isinstance(self._rules, dict) else {}
        all_longs = _merge_markers(
            markers.get("ALL_LONGS") if isinstance(markers, dict) else None,
            ("\u0432\u0441\u0435 \u043b\u043e\u043d\u0433\u0438", "all longs"),
        )
        all_shorts = _merge_markers(
            markers.get("ALL_SHORTS") if isinstance(markers, dict) else None,
            (
                "\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b",
                "\u0432\u0441\u0435\u043c \u043c\u043e\u0438\u043c \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u043c\u0441\u044f \u0448\u043e\u0440\u0442\u0430\u043c",
                "\u043c\u043e\u0438\u043c \u0448\u043e\u0440\u0442\u0430\u043c",
                "all shorts",
            ),
        )
        if _contains_any(normalized, tuple(all_longs)):
            return "ALL_LONGS"
        all_all = _merge_markers(
            markers.get("ALL_ALL") if isinstance(markers, dict) else None,
            (
                "\u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
                "\u0432\u0441\u0435 \u0441\u0432\u043e\u0438 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
                "all positions",
                "all trades",
            ),
        )
        all_open = _merge_markers(
            markers.get("ALL_OPEN") if isinstance(markers, dict) else None,
            (
                "\u0432\u0441\u0435 \u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
                "all open positions",
                "all open trades",
            ),
        )
        all_remaining = _merge_markers(
            markers.get("ALL_REMAINING") if isinstance(markers, dict) else None,
            (
                "\u0432\u0441\u0435 \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u0435\u0441\u044f \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
                "\u0432\u0441\u0435 \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u0435\u0441\u044f \u0441\u0434\u0435\u043b\u043a\u0438",
                "\u043f\u043e \u0432\u0441\u0435\u043c \u043c\u043e\u0438\u043c \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u043c\u0441\u044f \u0448\u043e\u0440\u0442\u0430\u043c",
                "\u043f\u043e \u0432\u0441\u0435\u043c \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u043c\u0441\u044f \u0448\u043e\u0440\u0442\u0430\u043c",
                "\u043e\u0441\u0442\u0430\u0432\u0448\u0438\u043c\u0441\u044f \u0448\u043e\u0440\u0442\u0430\u043c",
                "remaining positions",
                "remaining shorts",
            ),
        )
        if _contains_any(normalized, tuple(all_remaining)):
            if _contains_any(normalized, tuple(all_shorts)):
                return "ALL_REMAINING_SHORTS"
            if _contains_any(normalized, tuple(all_longs)):
                return "ALL_REMAINING_LONGS"
            return "ALL_REMAINING"
        if _contains_any(normalized, tuple(all_shorts)):
            return "ALL_SHORTS"
        if _contains_any(normalized, tuple(all_all)):
            return "ALL_ALL"
        if _contains_any(normalized, tuple(all_open)):
            return "ALL_OPEN"
        return None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip().lower() for item in value if isinstance(item, str) and item.strip()]


def _rule_markers(value: Any) -> list[str]:
    if isinstance(value, list):
        return _as_str_list(value)
    if isinstance(value, dict):
        out: list[str] = []
        for nested in value.values():
            out.extend(_rule_markers(nested))
        return out
    return []


def _merge_markers(value: Any, defaults: tuple[str, ...] | list[str]) -> list[str]:
    merged = list(_rule_markers(value))
    for marker in defaults:
        normalized = str(marker).strip().lower()
        if normalized and normalized not in merged:
            merged.append(normalized)
    return merged


def _class_markers(classification: Any, base: str) -> list[str]:
    if not isinstance(classification, dict):
        return []
    merged: list[str] = []
    for key in (base, f"{base}_strong", f"{base}_weak"):
        merged = _merge_markers(classification.get(key), merged)
    return merged


def _ignore_markers(rules: dict[str, Any]) -> list[str]:
    markers = _rule_markers(rules.get("ignore_markers")) if isinstance(rules, dict) else []
    for marker in (
        "# \u0430\u0434\u043c\u0438\u043d",
        "#\u0430\u0434\u043c\u0438\u043d",
        "\u044d\u0442\u043e \u0430\u0434\u043c\u0438\u043d",
        "\u0441\u0442\u0430\u0440\u0442:",
        "\u0444\u0438\u043d\u0438\u0448:",
        "#admin",
    ):
        value = marker.strip().lower()
        if value and value not in markers:
            markers.append(value)
    return markers


def _strong_only(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("strong", [])
    return value


def _contains_any(text: str, markers: tuple[str, ...] | list[str]) -> bool:
    lowered = text.lower()
    padded = f" {lowered} "
    for marker in markers:
        probe = (str(marker) if marker is not None else "").strip().lower()
        if not probe:
            continue
        if " " not in probe and len(probe) <= 3 and re.fullmatch(r"[a-z0-9]+", probe):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(probe)}(?![A-Za-z0-9_])", lowered, re.IGNORECASE):
                return True
            continue
        if probe in padded:
            return True
    return False


def _has_future_management_language(normalized: str) -> bool:
    return _contains_any(
        normalized,
        (
            "при взятии 1 тейка",
            "при взятии первого тейка",
            "на 1 тейке закрывать",
            "закрывать 80%",
            "закрывать",
            "стоп переводим в безубыток",
            "стоп переводим",
            "перезайдем",
            "если возьмет стоп",
            "если возьмет стоп, значит",
            "если стоп возьмет",
            "если возьмет тейк",
            "должны сегодня забрать",
            "взяли лимитку",
            "лимитка взялась",
            "взяло лимитку",
        ),
    )


def _has_intermediate_result_language(normalized: str) -> bool:
    return _contains_any(
        normalized,
        (
            "чистого движения",
            "чистыми",
            "поздравляю",
            "профит",
            "profit",
        ),
    )


def _extract_result_percent(raw_text: str, normalized: str) -> float | None:
    if not _has_intermediate_result_language(normalized):
        return None
    match = _RESULT_PERCENT_RE.search(raw_text)
    if not match:
        return None
    return _to_float(match.group("value"))


def _should_emit_report_final_result(*, raw_text: str, normalized: str, report_markers: tuple[str, ...] | list[str]) -> bool:
    if _RESULT_R_RE.search(raw_text):
        return True
    if _contains_any(
        normalized,
        (
            "закрылась в безубыток",
            "сделка закрыта",
            "позиция закрыта",
            "сетап полностью закрыт",
        ),
    ):
        return True
    if _has_intermediate_result_language(normalized):
        return False
    if _contains_any(
        normalized,
        (
            "итог",
            "результаты",
            "final result",
            "results",
            "общий профит",
            "профит по сделке",
            "заработали",
            "trade result",
            "net result",
            "pnl",
            "profit on trade",
            "loss on trade",
            "closed for profit",
            "closed for loss",
            "итого",
            "результат",
            "result summary",
        ),
    ):
        return True
    if _contains_any(normalized, tuple(report_markers)):
        return False if _contains_any(normalized, ("убыток", "profit", "loss", "pnl")) else True
    return False
def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _extract_close_fraction(*, raw_text: str, normalized_text: str) -> float | None:
    match = _PERCENT_RE.search(raw_text)
    if match:
        value = _to_float(match.group("value"))
        if value is not None:
            return round(max(0.0, min(1.0, value / 100.0)), 6)
    if _contains_any(normalized_text, ("half", "1/2", "\u043f\u043e\u043b\u043e\u0432\u0438\u043d\u0443", "\u0447\u0430\u0441\u0442\u044c")):
        return 0.5
    return None


def _extract_hit_target(raw_text: str) -> str | None:
    match = _TP_INDEX_RE.search(raw_text)
    if match:
        return f"TP{match.group('index')}"
    if _TP2_HIT_RE.search(raw_text):
        return "TP2"
    if _TP1_HIT_RE.search(raw_text):
        return "TP1"
    return None


def _extract_setup_invalidation(raw_text: str) -> str | None:
    for line in split_lines(raw_text):
        normalized = normalize_text(line)
        if "отмена входа" not in normalized and "без ретеста" not in normalized and "закреп" not in normalized and "уйдет к" not in normalized and "уйдёт к" not in normalized:
            continue
        if ":" in line:
            _, _, tail = line.partition(":")
            tail = tail.strip()
            if tail:
                return tail
        return line.strip()
    return None


def _extract_stop_level(raw_text: str) -> float | str | None:
    if _STOP_TO_TP1_RE.search(raw_text):
        return "TP1"
    match = _STOP_LEVEL_RE.search(raw_text)
    if not match:
        return None
    value_raw = match.group("value")
    value = _to_float(value_raw)
    if value is not None:
        return value
    return value_raw


def _extract_signal_symbol(raw_text: str) -> str | None:
    match = _SYMBOL_RE.search(raw_text.upper())
    if not match:
        return None
    return match.group(0).upper()


def _extract_signal_symbol_from_bare_hashtag(raw_text: str) -> str | None:
    for match in re.finditer(r"#\s*([A-Z0-9]{2,24}(?:\.P)?)\b", raw_text, re.IGNORECASE):
        token = str(match.group(1) or "").upper()
        if not token:
            continue
        if token.endswith((".P", "USDT", "USDC", "USD", "BTC", "ETH")):
            return token
        return f"{token}USDT"
    return None


def _extract_signal_side(normalized_text: str) -> str | None:
    if _contains_any(normalized_text, ("\u043b\u043e\u043d\u0433", "long", "buy")):
        return "LONG"
    if _contains_any(normalized_text, ("\u0448\u043e\u0440\u0442", "short", "sell")):
        return "SHORT"
    return None


def _extract_signal_entry_levels(raw_text: str) -> list[float]:
    out: list[float] = []
    for match in _ENTRY_VALUE_RE.finditer(raw_text):
        line_start = raw_text.rfind("\n", 0, match.start()) + 1
        line_end = raw_text.find("\n", match.end())
        if line_end == -1:
            line_end = len(raw_text)
        line_text = raw_text[line_start:line_end]
        suffix = line_text[match.end() - line_start :]
        if re.match(r"^\s*%", suffix):
            continue
        value = _to_float(match.group("value"))
        if value is None or value in out:
            continue
        out.append(value)
    for match in _ENTRY_AB_VALUE_RE.finditer(raw_text):
        line_start = raw_text.rfind("\n", 0, match.start()) + 1
        line_end = raw_text.find("\n", match.end())
        if line_end == -1:
            line_end = len(raw_text)
        line_text = raw_text[line_start:line_end]
        suffix = line_text[match.end() - line_start :]
        if re.match(r"^\s*%", suffix):
            continue
        qual = str(match.groupdict().get("qual") or "").lower()
        label = str(match.groupdict().get("label") or match.groupdict().get("label_paren") or "").lower()
        if label in ("b", "\u0431") and _contains_any(qual, ("\u0443\u0441\u0440\u0435\u0434", "\u0434\u043e\u0431\u043e\u0440", "averag", "top up")):
            continue
        value = _to_float(str(match.group("value")))
        if value is None or value in out:
            continue
        out.append(value)
    # Fallback for noisy unicode bullets/labels: capture explicit A/B entry lines.
    for match in re.finditer(
        r"(?:^|\n)[^\n]*?\b(?P<label>[ab])\b(?P<tail>[^\n:]*)[:=@-]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
        raw_text,
        re.IGNORECASE,
    ):
        line_start = raw_text.rfind("\n", 0, match.start()) + 1
        line_end = raw_text.find("\n", match.end())
        if line_end == -1:
            line_end = len(raw_text)
        line_text = raw_text[line_start:line_end]
        suffix = line_text[match.end() - line_start :]
        if re.match(r"^\s*%", suffix):
            continue
        tail = str(match.groupdict().get("tail") or "").lower()
        label = str(match.groupdict().get("label") or "").lower()
        if label == "b" and _contains_any(tail, ("\u0443\u0441\u0440\u0435\u0434", "\u0434\u043e\u0431\u043e\u0440", "averag", "top up")):
            continue
        value = _to_float(str(match.group("value")))
        if value is None or value in out:
            continue
        out.append(value)
    return out


def _extract_signal_entry_order_type(raw_text: str) -> str:
    normalized = normalize_text(raw_text)
    if _contains_any(
        normalized,
        (
            "вход с текущих",
            "с текущих",
            "entry from market",
            "market entry",
            "at market",
        ),
    ):
        return "MARKET"
    if _contains_any(
        normalized,
        (
            "вход лимиткой",
            "вход лимитным ордером",
            "лимиткой",
            "лимитным ордером",
            "limit entry",
            "limit order",
        ),
    ):
        return "LIMIT"
    # Prudential policy for this trader: when not explicit, primary entry is treated as LIMIT.
    return "LIMIT"


def _extract_signal_entry_plan(raw_text: str) -> dict[str, Any]:
    entry_levels = _extract_signal_entry_levels(raw_text)
    primary = entry_levels[0] if entry_levels else None
    averaging = _extract_signal_averaging(raw_text)
    secondary = averaging if averaging is not None else (entry_levels[1] if len(entry_levels) > 1 else None)

    ab_matches = list(_ENTRY_AB_VALUE_RE.finditer(raw_text))
    has_ab_style = bool(ab_matches)
    has_averaging_marker = averaging is not None
    if has_ab_style:
        source_style = "AB"
    elif has_averaging_marker:
        source_style = "ENTRY_AVERAGING"
    elif primary is not None:
        source_style = "SINGLE"
    else:
        source_style = "UNKNOWN"

    primary_order_type = _extract_signal_entry_order_type(raw_text)
    plan_entries: list[dict[str, Any]] = []
    if primary is None and primary_order_type == "MARKET":
        plan_entries.append(
            {
                "sequence": 1,
                "role": "PRIMARY",
                "order_type": "MARKET",
                "price": None,
                "raw_label": "ENTRY",
                "source_style": source_style if source_style != "UNKNOWN" else "SINGLE",
                "is_optional": False,
            }
        )
    if isinstance(primary, float):
        plan_entries.append(
            {
                "sequence": 1,
                "role": "PRIMARY",
                "order_type": primary_order_type,
                "price": primary,
                "raw_label": "A" if has_ab_style else "ENTRY",
                "source_style": source_style,
                "is_optional": False,
            }
        )
    if isinstance(secondary, float):
        plan_entries.append(
            {
                "sequence": 2,
                "role": "AVERAGING",
                "order_type": "LIMIT",
                "price": secondary,
                "raw_label": "B" if has_ab_style else "AVERAGING",
                "source_style": source_style if source_style != "SINGLE" else "ENTRY_AVERAGING",
                "is_optional": True,
            }
        )

    has_averaging_plan = len(plan_entries) > 1
    if not plan_entries:
        entry_plan_type = "UNKNOWN"
        entry_structure = "UNKNOWN"
    elif has_averaging_plan:
        if primary_order_type == "MARKET":
            entry_plan_type = "MARKET_WITH_LIMIT_AVERAGING"
        elif primary_order_type == "LIMIT":
            entry_plan_type = "LIMIT_WITH_LIMIT_AVERAGING"
        else:
            entry_plan_type = "UNKNOWN"
        entry_structure = "TWO_STEP"
    else:
        if primary_order_type == "MARKET":
            entry_plan_type = "SINGLE_MARKET"
        elif primary_order_type == "LIMIT":
            entry_plan_type = "SINGLE_LIMIT"
        else:
            entry_plan_type = "UNKNOWN"
        entry_structure = "SINGLE"

    return {
        "entries": plan_entries,
        "entry_plan_type": entry_plan_type,
        "entry_structure": entry_structure,
        "has_averaging_plan": has_averaging_plan,
    }


def _extract_signal_stop_loss(raw_text: str) -> float | None:
    match = _STOP_LOSS_VALUE_RE.search(raw_text)
    if not match:
        return None
    return _to_float(match.group("value"))


def _extract_signal_take_profits(raw_text: str) -> list[float]:
    out: list[float] = []
    for match in _TAKE_PROFIT_VALUE_RE.finditer(raw_text):
        value = _to_float(match.group("value"))
        if value is None or value in out:
            continue
        out.append(value)
    if out:
        return out
    normalized = normalize_text(raw_text)
    if not _contains_any(normalized, ("\u0442\u0435\u0439\u043a\u0438", "tps", "targets")):
        return out
    for match in re.finditer(r"(?:^|\n)\s*(?:[-\u2014\u2022]\s*)?(?P<value>\d[\d\s]*(?:[.,]\d+)?)", raw_text):
        value = _to_float(str(match.group("value")))
        if value is None or value in out:
            continue
        out.append(value)
    return out


def _extract_signal_averaging(raw_text: str) -> float | None:
    match = _AVERAGING_VALUE_RE.search(raw_text)
    if not match:
        for ab_match in _ENTRY_AB_VALUE_RE.finditer(raw_text):
            qual = str(ab_match.groupdict().get("qual") or "").lower()
            label = str(ab_match.groupdict().get("label") or ab_match.groupdict().get("label_paren") or "").lower()
            if label not in ("b", "\u0431"):
                continue
            if not _contains_any(qual, ("\u0443\u0441\u0440\u0435\u0434", "\u0434\u043e\u0431\u043e\u0440", "averag", "top up")):
                continue
            value = _to_float(str(ab_match.group("value")))
            if value is not None:
                return value
        for fallback_match in re.finditer(
            r"(?:^|\n)[^\n]*?\b(?P<label>b)\b(?P<tail>[^\n:]*)[:=@-]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
            raw_text,
            re.IGNORECASE,
        ):
            tail = str(fallback_match.groupdict().get("tail") or "").lower()
            if not _contains_any(tail, ("\u0443\u0441\u0440\u0435\u0434", "\u0434\u043e\u0431\u043e\u0440", "averag", "top up")):
                continue
            value = _to_float(str(fallback_match.group("value")))
            if value is not None:
                return value
        return None
    return _to_float(match.group("value"))


def _to_float(raw: str) -> float | None:
    cleaned = raw.replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None
