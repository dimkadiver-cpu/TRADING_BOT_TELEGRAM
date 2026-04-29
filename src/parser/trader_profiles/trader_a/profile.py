"""Trader A profile parser."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from src.parser.canonical_v1.intent_candidate import IntentCandidate
from src.parser.canonical_v1.models import (
    CancelPendingOperation,
    CanonicalMessage,
    CloseOperation,
    EntryLeg,
    ModifyTargetsOperation,
    Price,
    RawContext,
    ReportEvent,
    ReportPayload,
    ReportedResult,
    RiskHint,
    SignalPayload,
    StopLoss,
    StopTarget,
    TakeProfit,
    Targeting,
    TargetRef,
    TargetScope,
    TargetedAction,
    TargetedReport,
    UpdateOperation,
    UpdatePayload,
)
from src.parser.shared.context_resolution_engine import ContextInput
from src.parser.shared.context_resolution_schema import ContextResolutionRulesBlock
from src.parser.shared.disambiguation_rules_schema import DisambiguationRulesBlock
from src.parser.shared.intent_compatibility_schema import IntentCompatibilityBlock
from src.parser.shared.semantic_resolver import SemanticResolver, SemanticResolverInput
from src.parser.canonical_v1.targeted_builder import (
    build_targeted_actions,
    build_targeted_reports_from_lines,
)
from src.parser.parsed_message import ParsedMessage
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_telegram_links, normalize_text, split_lines
from src.parser.trader_profiles.shared.rules_schema import validate_profile_rules, validate_semantic_markers
from src.parser.trader_profiles.trader_a.extractors import TraderAExtractors
from src.parser.intent_action_map import intent_policy_for_intent
from src.parser.rules_engine import RulesEngine

_RULES_PATH = Path(__file__).resolve().parent / "parsing_rules.json"
_SEMANTIC_MARKERS_PATH = Path(__file__).resolve().parent / "semantic_markers.json"
_PHASE4_RULES_PATH = Path(__file__).resolve().parent / "rules.json"
_SYMBOL_RE = re.compile(r"\b[A-Z0-9]{1,24}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?\b")
_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_RESULT_R_RE = re.compile(r"\b[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?\s*[-:=]\s*[+-]?\d+(?:[.,]\d+)?\s*R{1,2}\b", re.IGNORECASE)
_RESULT_R_CAPTURE_RE = re.compile(
    r"\b(?P<symbol>[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?)\s*[-:=]\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*R{1,2}\b",
    re.IGNORECASE,
)
_BARE_RESULT_R_RE = re.compile(r"\b(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*R{1,2}\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b(?P<value>\d{1,3}(?:[.,]\d+)?)%")
_RISK_RANGE_RE = re.compile(
    r"(?:риск|вход)[^0-9\n]*?(?P<min>\d+(?:[.,]\d+)?)[–—\-](?P<max>\d+(?:[.,]\d+)?)%",
    re.IGNORECASE,
)
_RISK_SINGLE_RE = re.compile(
    r"(?:риск|вход)[^0-9\n]*?(?P<value>\d+(?:[.,]\d+)?)%",
    re.IGNORECASE,
)
_TP_INDEX_RE = re.compile(r"\btp(?P<index>\d+)\b", re.IGNORECASE)
_STOP_LEVEL_RE = re.compile(
    r"(?:move\s*(?:sl|stop)\s*(?:to)?|sl|stop|стоп\s*(?:переношу|переставляю|переносим|переставим)\s*на)\s*[:=@-]?\s*(?P<value>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_ENTRY_VALUE_RE = re.compile(
    r"(?:entry|entries|\u043b\u0438\u043c\u0438\u0442\u043d\u044b\u0439\s+\u043e\u0440\u0434\u0435\u0440|\u0432\u0445\u043e\u0434(?:\s+\u0441\s+\u0442\u0435\u043a\u0443\u0449\u0438\u0445|\s+\u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439|\s+\u043b\u0438\u043c\u0438\u0442\u043d\u044b\u043c\s+\u043e\u0440\u0434\u0435\u0440\u043e\u043c)?)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
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
    "info_only": (
        "\u0437\u0430\u043a\u0440\u044b\u043b\u0430\u0441\u044c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
        "\u0441\u0434\u0435\u043b\u043a\u0430 \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
        "\u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0437\u0430\u043a\u0440\u044b\u0442\u0430",
        "\u0441\u0435\u0442\u0430\u043f \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e \u0437\u0430\u043a\u0440\u044b\u0442",
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
        "хочу зафиксировать",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438",
        "зафиксировать все позиции",
        "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u0443\u044e \u0432\u0441\u0435 \u0441\u0432\u043e\u0438 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
        "зафиксировать все шорты",
        "зафиксировать все лонги",
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
        "фиксация 100%",
        "фиксация 100% по текущим отметкам",
    ),
    "U_CLOSE_PARTIAL": ("partial close", "close half", "\u0447\u0430\u0441\u0442\u0438\u0447\u043d\u043e", "\u043f\u043e\u043b\u043e\u0432\u0438\u043d\u0443"),
    "U_TP_HIT": (
        "tp hit",
        "tp1 hit",
        "\u0442\u0435\u0439\u043a \u0432\u0437\u044f\u0442",
        "\u0434\u043e\u0448\u043b\u0438 \u0434\u043e 2-\u0445 \u0442\u0435\u0439\u043a\u043e\u0432",
        "1 \u0442\u0435\u0439\u043a",
        "\u0442\u0443\u0442 \u0442\u0435\u0439\u043a",
    ),
    "U_STOP_HIT": ("stop hit", "stopped out", "\u0432\u044b\u0431\u0438\u043b\u043e \u043f\u043e \u0441\u0442\u043e\u043f\u0443"),
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

_TA_UPDATE_INTENTS: frozenset[str] = frozenset(
    {
        "U_MOVE_STOP",
        "U_MOVE_STOP_TO_BE",
        "U_CLOSE_FULL",
        "U_CLOSE_PARTIAL",
        "U_CANCEL_PENDING_ORDERS",
        "U_INVALIDATE_SETUP",
        "U_UPDATE_TAKE_PROFITS",
        "U_REVERSE_SIGNAL",
    }
)

_TA_REPORT_INTENTS: frozenset[str] = frozenset(
    {
        "U_TP_HIT",
        "U_STOP_HIT",
        "U_REPORT_FINAL_RESULT",
        "U_MARK_FILLED",
        "U_EXIT_BE",
    }
)

_LEGACY_TO_CANONICAL_INTENT: dict[str, str] = {
    "NS_CREATE_SIGNAL": "NEW_SETUP",
    "U_MOVE_STOP_TO_BE": "MOVE_STOP_TO_BE",
    "U_MOVE_STOP": "MOVE_STOP",
    "U_CLOSE_FULL": "CLOSE_FULL",
    "U_CLOSE_PARTIAL": "CLOSE_PARTIAL",
    "U_CANCEL_PENDING_ORDERS": "CANCEL_PENDING_ORDERS",
    "U_INVALIDATE_SETUP": "INVALIDATE_SETUP",
    "U_UPDATE_TAKE_PROFITS": "UPDATE_TAKE_PROFITS",
    "U_MARK_FILLED": "ENTRY_FILLED",
    "U_TP_HIT": "TP_HIT",
    "U_STOP_HIT": "SL_HIT",
    "U_EXIT_BE": "EXIT_BE",
    "U_REPORT_FINAL_RESULT": "REPORT_FINAL_RESULT",
}

_CANONICAL_TO_LEGACY_INTENT: dict[str, str] = {
    value: key for key, value in _LEGACY_TO_CANONICAL_INTENT.items()
}


def _load_phase4_rules_engine() -> RulesEngine:
    semantic_markers = json.loads(_SEMANTIC_MARKERS_PATH.read_text(encoding="utf-8"))
    phase4_rules = json.loads(_PHASE4_RULES_PATH.read_text(encoding="utf-8"))
    validate_semantic_markers(semantic_markers, strict=True)
    validate_profile_rules(phase4_rules, strict=True)
    return RulesEngine.from_dict({**semantic_markers, **phase4_rules})


def _canonicalize_intents(intents: list[str]) -> list[str]:
    canonical: list[str] = []
    for intent in intents:
        mapped = _LEGACY_TO_CANONICAL_INTENT.get(intent, intent)
        if mapped not in canonical:
            canonical.append(mapped)
    return canonical

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
    ),
    "U_CLOSE_PARTIAL": ("partial close", "close half", "\u0447\u0430\u0441\u0442\u0438\u0447\u043d\u043e", "\u043f\u043e\u043b\u043e\u0432\u0438\u043d\u0443"),
    "U_TP_HIT": (
        "tp hit",
        "tp1 hit",
        "\u0442\u0435\u0439\u043a \u0432\u0437\u044f\u0442",
        "\u0434\u043e\u0448\u043b\u0438 \u0434\u043e 2-\u0445 \u0442\u0435\u0439\u043a\u043e\u0432",
        "1 \u0442\u0435\u0439\u043a",
        "\u0442\u0443\u0442 \u0442\u0435\u0439\u043a",
    ),
    "U_STOP_HIT": ("stop hit", "stopped out", "\u0432\u044b\u0431\u0438\u043b\u043e \u043f\u043e \u0441\u0442\u043e\u043f\u0443"),
    "U_MARK_FILLED": ("entry filled", "filled", "\u0432\u0445\u043e\u0434 \u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d"),
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
        self._rules_engine = RulesEngine.load(self._rules_path)
        self._semantic_resolver = self._build_semantic_resolver()
        self._phase4_rules_engine = _load_phase4_rules_engine()
        self._phase4_extractors = TraderAExtractors()

    def parse(self, text: str, context: ParserContext) -> ParsedMessage:
        from src.parser.shared.runtime import parse as parse_parsed_message

        return parse_parsed_message(
            trader_code=self.trader_code,
            text=text,
            context=context,
            rules=self._phase4_rules_engine,
            extractors=self._phase4_extractors,
        )

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
            canonical_mode=False,
        )
        if message_type == "UNCLASSIFIED" and "U_REPORT_FINAL_RESULT" in intents:
            if (target_refs or has_global_target) and any(
                intent in intents
                for intent in (
                    "U_CLOSE_FULL",
                    "U_CLOSE_PARTIAL",
                    "U_CANCEL_PENDING_ORDERS",
                    "U_MOVE_STOP_TO_BE",
                    "U_MOVE_STOP",
                    "U_TP_HIT",
                    "U_STOP_HIT",
                )
            ):
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
        # Downgrade UPDATE→UNCLASSIFIED when only stop-management intents with no target,
        # unless the text contains authoritative specific-phrase update indicators.
        if message_type == "UPDATE" and not target_refs and not has_global_target:
            _stop_mgmt_only = set(intents) <= {"U_MOVE_STOP_TO_BE", "U_MOVE_STOP"} and bool(intents)
            if _stop_mgmt_only:
                _norm = str(prepared.get("normalized_text") or "")
                _authoritative = _contains_any(
                    _norm,
                    (
                        "\u0441\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430",
                        "\u043f\u0435\u0440\u0435\u0432\u0435\u0441\u0442\u0438 \u0441\u0442\u043e\u043f \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
                        "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
                        "\u0441\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u043c",
                        "\u043f\u043e \u0448\u043e\u0440\u0442\u0430\u043c \u0441\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430",
                    ),
                )
                if not _authoritative:
                    message_type = "UNCLASSIFIED"
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
            entities=entities,
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

    def parse_canonical(self, text: str, context: ParserContext) -> CanonicalMessage:
        """Produce CanonicalMessage v1 directly from Trader A parser output."""
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
            canonical_mode=True,
        )
        extracted_intents = list(intents)

        if message_type == "UNCLASSIFIED" and "U_REPORT_FINAL_RESULT" in intents:
            if (target_refs or has_global_target) and any(
                intent in intents
                for intent in (
                    "U_CLOSE_FULL",
                    "U_CLOSE_PARTIAL",
                    "U_CANCEL_PENDING_ORDERS",
                    "U_MOVE_STOP_TO_BE",
                    "U_MOVE_STOP",
                    "U_TP_HIT",
                    "U_STOP_HIT",
                )
            ):
                message_type = "UPDATE"
            else:
                message_type = "INFO_ONLY"
        if message_type == "UNCLASSIFIED" and "U_CANCEL_PENDING_ORDERS" in intents and (target_refs or has_global_target):
            message_type = "UPDATE"
        if message_type == "UNCLASSIFIED" and (target_refs or has_global_target) and any(
            intent in intents for intent in ("U_CLOSE_FULL", "U_CLOSE_PARTIAL")
        ):
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

        if message_type == "UPDATE" and not target_refs and not has_global_target:
            stop_mgmt_only = set(intents) <= {"U_MOVE_STOP_TO_BE", "U_MOVE_STOP"} and bool(intents)
            if stop_mgmt_only:
                normalized = str(prepared.get("normalized_text") or "")
                authoritative = _contains_any(
                    normalized,
                    (
                        "стоп на точку входа",
                        "перевести стоп в безубыток",
                        "стоп переводим в безубыток",
                        "стоп переводим",
                        "по шортам стоп на точку входа",
                    ),
                )
                if not authoritative:
                    message_type = "UNCLASSIFIED"

        warning_message_type = message_type
        message_type, intents, primary_intent, semantic_diagnostics = self._resolve_semantics(
            prepared=prepared,
            context=context,
            message_type=message_type,
            intents=intents,
            target_refs=target_refs,
            global_target_scope=global_target_scope,
        )
        raw_text = str(prepared.get("raw_text") or "")
        granular_stop_actions = self._build_line_level_move_stop_actions(raw_text=raw_text)
        if warning_message_type == "UPDATE" and (target_refs or has_global_target) and granular_stop_actions:
            intents = _canonicalize_intents(extracted_intents)
            primary_intent = intents[0] if intents else primary_intent
            message_type = warning_message_type
        legacy_intents = [
            _CANONICAL_TO_LEGACY_INTENT.get(intent, intent) for intent in intents
        ]

        reported_results = self._extract_reported_results(
            prepared=prepared,
            context=context,
            intents=legacy_intents,
        )
        entities = self._extract_entities(
            prepared=prepared,
            context=context,
            intents=legacy_intents,
            target_refs=target_refs,
            reported_results=reported_results,
            global_target_scope=global_target_scope,
        )
        warnings: list[str] = list(
            self._build_warnings(
                prepared=prepared,
                context=context,
                message_type=warning_message_type,
                intents=extracted_intents,
                target_refs=target_refs,
            )
        )
        for warning in semantic_diagnostics.get("unresolved_warnings", []):
            if warning not in warnings:
                warnings.append(warning)
        confidence = self._estimate_confidence(
            prepared=prepared,
            context=context,
            message_type=message_type,
            intents=intents,
            warnings=warnings,
        )

        # --- targeted_actions / targeted_reports (multi-ref contract) --------
        targeted_actions: list[TargetedAction] = []
        targeted_reports: list[TargetedReport] = []
        if message_type in {"UPDATE", "REPORT"}:
            legacy_actions = self._build_actions_structured(
                message_type=message_type, intents=legacy_intents, entities=entities
            )
            grouped = self._build_grouped_targeted_actions(
                prepared=prepared,
                message_type=message_type,
                intents=legacy_intents,
                entities=entities,
                target_refs=target_refs,
                actions_structured=legacy_actions,
                global_target_scope=global_target_scope,
            )
            has_any_targeting = any("targeting" in a for a in grouped)
            has_refs = bool(target_refs) or has_global_target
            if has_any_targeting:
                targeted_actions = build_targeted_actions(grouped)
                raw_text = str(prepared.get("raw_text") or "")
                targeted_reports = build_targeted_reports_from_lines(raw_text)
            elif has_refs and not has_any_targeting:
                if any("targeting" not in a for a in grouped) and grouped:
                    warnings.append("targeted_binding_ambiguous")

        diagnostics = self._build_diagnostics(
            prepared=prepared,
            message_type=message_type,
            intents=intents,
            warnings=warnings,
            has_global_target=has_global_target,
        )
        if semantic_diagnostics:
            diagnostics = {**diagnostics, "semantic_resolver": semantic_diagnostics}
        if targeted_actions and isinstance(diagnostics, dict):
            diagnostics = {**diagnostics, "multi_ref_mode": True}

        raw_context = RawContext(
            raw_text=context.raw_text or "",
            reply_to_message_id=context.reply_to_message_id,
            extracted_links=list(context.extracted_links or []),
            hashtags=list(context.hashtags or []),
            source_chat_id=str(context.channel_id) if context.channel_id else None,
        )
        targeting = _build_ta_targeting(
            message_type=message_type,
            target_refs=target_refs,
            global_target_scope=global_target_scope,
            context=context,
        )

        canonical_intent_set = set(intents)
        has_signal = ("NEW_SETUP" in canonical_intent_set) or message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}
        has_update = bool(
            canonical_intent_set
            & {"MOVE_STOP_TO_BE", "MOVE_STOP", "CLOSE_FULL", "CLOSE_PARTIAL", "CANCEL_PENDING_ORDERS", "INVALIDATE_SETUP", "UPDATE_TAKE_PROFITS"}
        )
        if not has_update and message_type == "UPDATE" and granular_stop_actions and (target_refs or has_global_target):
            has_update = True
        has_report = bool(canonical_intent_set & {"TP_HIT", "SL_HIT", "REPORT_FINAL_RESULT", "ENTRY_FILLED", "EXIT_BE"})
        suppress_report_payload = message_type == "UPDATE" and granular_stop_actions and canonical_intent_set <= {"EXIT_BE"}
        if suppress_report_payload:
            has_report = False

        if has_signal:
            signal = _build_ta_signal_payload(entities=entities)
            parse_status = "PARSED" if signal.completeness == "COMPLETE" else "PARTIAL"
            return CanonicalMessage(
                parser_profile=context.trader_code,
                primary_class="SIGNAL",
                parse_status=parse_status,
                confidence=confidence,
                intents=intents,
                primary_intent=primary_intent,
                targeting=targeting,
                signal=signal,
                warnings=warnings,
                diagnostics=diagnostics,
                raw_context=raw_context,
            )

        if has_update:
            update_ops = _build_ta_update_ops(intents=legacy_intents, entities=entities, warnings=warnings)
            report_payload = None if suppress_report_payload else _build_ta_report_payload(
                intents=legacy_intents,
                entities=entities,
                reported_results=reported_results,
            )
            has_ops = bool(update_ops)
            has_report_payload = report_payload is not None and (
                bool(report_payload.events) or report_payload.reported_result is not None
            )

            if has_ops and has_report_payload:
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="UPDATE",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    update=UpdatePayload(operations=update_ops),
                    report=report_payload,
                    warnings=warnings,
                    diagnostics=diagnostics,
                    raw_context=raw_context,
                    targeted_actions=targeted_actions,
                    targeted_reports=targeted_reports,
                )
            if has_ops:
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="UPDATE",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    update=UpdatePayload(operations=update_ops),
                    warnings=warnings,
                    diagnostics=diagnostics,
                    raw_context=raw_context,
                    targeted_actions=targeted_actions,
                    targeted_reports=targeted_reports,
                )
            if has_report_payload:
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="REPORT",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    report=report_payload,
                    warnings=warnings,
                    diagnostics=diagnostics,
                    raw_context=raw_context,
                    targeted_actions=targeted_actions,
                    targeted_reports=targeted_reports,
                )
            if intents:
                warnings.append("trader_a_update_no_resolvable_ops")
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="UPDATE",
                    parse_status="PARTIAL",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    update=UpdatePayload(operations=[]),
                    warnings=warnings,
                    diagnostics=diagnostics,
                    raw_context=raw_context,
                    targeted_actions=targeted_actions,
                    targeted_reports=targeted_reports,
                )

        if has_report:
            report_payload = _build_ta_report_payload(
                intents=legacy_intents,
                entities=entities,
                reported_results=reported_results,
            )
            if report_payload is not None and (report_payload.events or report_payload.reported_result is not None):
                return CanonicalMessage(
                    parser_profile=context.trader_code,
                    primary_class="REPORT",
                    parse_status="PARSED",
                    confidence=confidence,
                    intents=intents,
                    primary_intent=primary_intent,
                    targeting=targeting,
                    report=report_payload,
                    warnings=warnings,
                    diagnostics=diagnostics,
                    raw_context=raw_context,
                    targeted_actions=targeted_actions,
                    targeted_reports=targeted_reports,
                )

        if message_type == "INFO_ONLY":
            return CanonicalMessage(
                parser_profile=context.trader_code,
                primary_class="INFO",
                parse_status="PARSED",
                confidence=confidence,
                intents=intents,
                primary_intent=primary_intent,
                targeting=targeting,
                warnings=warnings,
                diagnostics=diagnostics,
                raw_context=raw_context,
            )

        return CanonicalMessage(
            parser_profile=context.trader_code,
            primary_class="INFO",
            parse_status="UNCLASSIFIED",
            confidence=confidence,
            intents=intents,
            primary_intent=primary_intent,
            targeting=targeting,
            warnings=warnings,
            diagnostics=diagnostics,
            raw_context=raw_context,
        )

    def _build_semantic_resolver(self) -> SemanticResolver:
        compatibility = IntentCompatibilityBlock.model_validate(
            self._rules.get("intent_compatibility", {"pairs": []})
        )
        disambiguation = DisambiguationRulesBlock.model_validate(
            self._rules.get("disambiguation_rules", {"rules": []})
        )
        context_rules = ContextResolutionRulesBlock.model_validate(
            self._rules.get("context_resolution_rules", {"rules": []})
        )
        return SemanticResolver(
            compatibility_pairs=compatibility.pairs,
            disambiguation_rules=disambiguation.rules,
            context_resolution_rules=context_rules.rules,
        )

    def _resolve_semantics(
        self,
        *,
        prepared: dict[str, Any],
        context: ParserContext,
        message_type: str,
        intents: list[str],
        target_refs: list[dict[str, Any]],
        global_target_scope: str | None,
    ) -> tuple[str, list[str], str | None, dict[str, Any]]:
        intent_candidates = self._build_intent_candidates(
            prepared=prepared,
            message_type=message_type,
            intents=intents,
        )
        if not intent_candidates:
            primary_intent = self._derive_primary_intent(message_type=message_type, intents=intents)
            return message_type, intents, primary_intent, {}

        context_input = self._build_semantic_context(
            context=context,
            target_refs=target_refs,
            global_target_scope=global_target_scope,
            message_type=message_type,
        )
        resolved = self._semantic_resolver.resolve(
            SemanticResolverInput(
                text_normalized=str(prepared.get("normalized_text") or ""),
                intent_candidates=intent_candidates,
                context=context_input,
                resolution_unit="MESSAGE_WIDE",
            )
        )
        final_intents = self._normalize_resolved_intents(resolved.final_intents)
        if resolved.primary_intent in final_intents:
            final_intents = [resolved.primary_intent] + [
                intent for intent in final_intents if intent != resolved.primary_intent
            ]
        resolved_intents = self._map_resolved_intents(
            original_intents=intents,
            final_intents=final_intents,
        )
        resolved_message_type = self._derive_message_type_from_resolved_intents(
            message_type=message_type,
            resolved_intents=final_intents,
        )
        resolved_primary_intent = self._map_primary_intent(
            resolved.primary_intent,
            message_type=resolved_message_type,
            fallback_intents=resolved_intents,
        )
        canonical_intents = _canonicalize_intents(resolved_intents)
        canonical_primary_intent = (
            _LEGACY_TO_CANONICAL_INTENT.get(resolved_primary_intent, resolved_primary_intent)
            if resolved_primary_intent is not None
            else None
        )
        if canonical_primary_intent in canonical_intents:
            canonical_intents = [canonical_primary_intent] + [
                intent for intent in canonical_intents if intent != canonical_primary_intent
            ]
        diagnostics = {
            "intent_candidates": [candidate.model_dump() for candidate in intent_candidates],
            "context": context_input.model_dump(),
            "final_intents": list(final_intents),
            "primary_intent": resolved.primary_intent,
            "applied_disambiguation_rules": list(resolved.diagnostics.applied_disambiguation_rules),
            "applied_context_rules": list(resolved.diagnostics.applied_context_rules),
            "unresolved_warnings": list(resolved.diagnostics.unresolved_warnings),
        }
        return resolved_message_type, canonical_intents, canonical_primary_intent, diagnostics

    @staticmethod
    def _normalize_resolved_intents(final_intents: list[str]) -> list[str]:
        if "INFO_ONLY" in final_intents:
            return ["INFO_ONLY"]
        normalized = [intent for intent in final_intents if intent != "REPORT_FINAL_RESULT" or "EXIT_BE" not in final_intents]
        return normalized or list(final_intents)

    def _build_intent_candidates(
        self,
        *,
        prepared: dict[str, Any],
        message_type: str,
        intents: list[str],
    ) -> list[IntentCandidate]:
        normalized = str(prepared.get("normalized_text") or "")
        candidates: list[IntentCandidate] = []
        for legacy_intent in intents:
            canonical_intent = _LEGACY_TO_CANONICAL_INTENT.get(legacy_intent)
            if canonical_intent is None:
                continue
            evidence = self._intent_candidate_evidence(legacy_intent=legacy_intent, normalized_text=normalized)
            candidates.append(
                IntentCandidate(
                    intent=canonical_intent,
                    strength=self._intent_candidate_strength(
                        legacy_intent=legacy_intent,
                        normalized_text=normalized,
                        message_type=message_type,
                    ),
                    evidence=evidence or [f"legacy:{legacy_intent.lower()}"],
                )
            )
        return candidates

    def _intent_candidate_evidence(self, *, legacy_intent: str, normalized_text: str) -> list[str]:
        strong_markers, weak_markers = self._intent_marker_groups(legacy_intent)
        evidence: list[str] = []
        for marker in strong_markers:
            if _contains_any(normalized_text, (marker,)):
                evidence.append(f"strong:{marker}")
        for marker in weak_markers:
            if _contains_any(normalized_text, (marker,)):
                evidence.append(f"weak:{marker}")
        return evidence

    def _intent_candidate_strength(
        self,
        *,
        legacy_intent: str,
        normalized_text: str,
        message_type: str,
    ) -> str:
        strong_markers, weak_markers = self._intent_marker_groups(legacy_intent)
        if weak_markers and _contains_any(normalized_text, tuple(weak_markers)):
            return "weak"
        if strong_markers and _contains_any(normalized_text, tuple(strong_markers)):
            return "strong"
        if legacy_intent == "U_EXIT_BE":
            return "weak"
        if message_type in {"INFO_ONLY", "UNCLASSIFIED"}:
            return "weak"
        return "strong"

    def _intent_marker_groups(self, legacy_intent: str) -> tuple[list[str], list[str]]:
        marker_map = self._rules_engine.raw_rules.get("intent_markers", {})
        configured = None
        if isinstance(marker_map, dict):
            configured = _marker_values(marker_map, _LEGACY_TO_CANONICAL_INTENT.get(legacy_intent, legacy_intent), legacy_intent)
        if isinstance(configured, dict):
            strong = _as_str_list(configured.get("strong"))
            weak = _as_str_list(configured.get("weak"))
            return strong, weak
        if isinstance(configured, list):
            markers = _as_str_list(configured)
            if legacy_intent == "U_EXIT_BE":
                return [], markers
            return markers, []
        defaults = list(_DEFAULT_INTENT_MARKERS.get(legacy_intent, ()))
        if legacy_intent == "U_EXIT_BE":
            return [], defaults
        return defaults, []

    def _build_semantic_context(
        self,
        *,
        context: ParserContext,
        target_refs: list[dict[str, Any]],
        global_target_scope: str | None,
        message_type: str,
    ) -> ContextInput:
        has_target_ref = bool(target_refs) or global_target_scope is not None
        target_ref_kind = "unknown"
        if global_target_scope is not None:
            target_ref_kind = "global_scope"
        elif any(item.get("kind") == "reply" for item in target_refs):
            target_ref_kind = "reply_id"
        elif any(item.get("kind") == "telegram_link" for item in target_refs):
            target_ref_kind = "telegram_link"
        elif any(item.get("kind") in {"message_id", "explicit_id"} for item in target_refs):
            target_ref_kind = "explicit_id"
        return ContextInput(
            has_target_ref=has_target_ref,
            target_ref_kind=target_ref_kind,  # type: ignore[arg-type]
            target_exists=has_target_ref,
            target_history_intents=self._extract_target_history_intents(context=context),
            message_type_hint=message_type,
        )

    def _extract_target_history_intents(self, *, context: ParserContext) -> list[str]:
        if not isinstance(context.reply_raw_text, str) or not context.reply_raw_text.strip():
            return []

        reply_context = ParserContext(
            trader_code=context.trader_code,
            message_id=context.reply_to_message_id,
            reply_to_message_id=None,
            channel_id=context.channel_id,
            raw_text=context.reply_raw_text,
            extracted_links=[],
            hashtags=[],
        )
        prepared = self._preprocess(text=context.reply_raw_text, context=reply_context)
        reply_targets: list[dict[str, Any]] = []
        reply_message_type = self._classify_message(
            prepared=prepared,
            context=reply_context,
            target_refs=reply_targets,
        )
        reply_intents = self._extract_intents(
            prepared=prepared,
            context=reply_context,
            message_type=reply_message_type,
            target_refs=reply_targets,
            canonical_mode=True,
        )

        history: list[str] = []
        if reply_message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
            history.append("NEW_SETUP")
        for legacy_intent in reply_intents:
            canonical = _LEGACY_TO_CANONICAL_INTENT.get(legacy_intent)
            if canonical is not None and canonical not in history:
                history.append(canonical)
        return history

    def _map_resolved_intents(
        self,
        *,
        original_intents: list[str],
        final_intents: list[str],
    ) -> list[str]:
        resolved: list[str] = []
        for canonical_intent in final_intents:
            legacy_intent = _CANONICAL_TO_LEGACY_INTENT.get(canonical_intent, canonical_intent)
            if legacy_intent not in resolved:
                resolved.append(legacy_intent)
        for legacy_intent in original_intents:
            if legacy_intent in _LEGACY_TO_CANONICAL_INTENT:
                continue
            if legacy_intent not in resolved:
                resolved.append(legacy_intent)
        return resolved

    @staticmethod
    def _derive_message_type_from_resolved_intents(*, message_type: str, resolved_intents: list[str]) -> str:
        resolved_set = set(resolved_intents)
        if resolved_set == {"INFO_ONLY"}:
            return "INFO_ONLY"
        if "NEW_SETUP" in resolved_set:
            return message_type

        update_intents = {
            "MOVE_STOP_TO_BE",
            "MOVE_STOP",
            "CLOSE_FULL",
            "CLOSE_PARTIAL",
            "CANCEL_PENDING_ORDERS",
            "INVALIDATE_SETUP",
            "UPDATE_TAKE_PROFITS",
            "REENTER",
            "ADD_ENTRY",
        }
        report_intents = {
            "ENTRY_FILLED",
            "TP_HIT",
            "SL_HIT",
            "EXIT_BE",
            "REPORT_FINAL_RESULT",
            "REPORT_PARTIAL_RESULT",
        }
        if resolved_set & update_intents:
            return "UPDATE"
        if resolved_set & report_intents:
            return "REPORT"
        return message_type

    def _map_primary_intent(
        self,
        canonical_primary_intent: str | None,
        *,
        message_type: str,
        fallback_intents: list[str],
    ) -> str | None:
        if message_type == "INFO_ONLY":
            return "INFO_ONLY"
        if canonical_primary_intent is not None:
            return _CANONICAL_TO_LEGACY_INTENT.get(canonical_primary_intent, canonical_primary_intent)
        return self._derive_primary_intent(message_type=message_type, intents=fallback_intents)

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
            if not intent_policy_for_intent(intent).get("state_change") and intent not in {
                "U_TP_HIT",
                "U_STOP_HIT",
                "U_MARK_FILLED",
                "U_REPORT_FINAL_RESULT",
            }:
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
            elif intent == "U_EXIT_BE":
                continue
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
        entities: dict[str, Any],
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

        explicit_targets = self._explicit_action_targets(target_refs=target_refs)
        selector = None
        if global_target_scope in {"ALL_SHORTS", "ALL_LONGS"}:
            selector = {
                "side": "SHORT" if global_target_scope == "ALL_SHORTS" else "LONG",
                "status": "OPEN",
            }

        if explicit_targets and selector and any(intent in intents for intent in {"U_TP_HIT", "U_STOP_HIT"}) and any(
            intent in intents for intent in {"U_MOVE_STOP_TO_BE", "U_MOVE_STOP"}
        ):
            mixed_actions: list[dict[str, Any]] = []
            if "U_TP_HIT" in intents:
                mixed_actions.append(
                    {
                        "action": "TAKE_PROFIT",
                        "target": entities.get("hit_target", "TP"),
                        "targeting": {"mode": "EXPLICIT_TARGETS", "targets": explicit_targets},
                    }
                )
            if "U_STOP_HIT" in intents:
                mixed_actions.append(
                    {
                        "action": "CLOSE_POSITION",
                        "target": "STOP",
                        "targeting": {"mode": "EXPLICIT_TARGETS", "targets": explicit_targets},
                    }
                )
            if "U_MOVE_STOP_TO_BE" in intents:
                mixed_actions.append(
                    {
                        "action": "MOVE_STOP",
                        "new_stop_level": "ENTRY",
                        "targeting": {"mode": "SELECTOR", "selector": selector},
                    }
                )
            elif "U_MOVE_STOP" in intents:
                mixed_actions.append(
                    {
                        "action": "MOVE_STOP",
                        "new_stop_level": entities.get("new_stop_level"),
                        "targeting": {"mode": "SELECTOR", "selector": selector},
                    }
                )
            if mixed_actions:
                return mixed_actions

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
                    "scope": global_target_scope,
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

    @staticmethod
    def _explicit_action_targets(*, target_refs: list[dict[str, Any]]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for item in target_refs:
            if item.get("kind") not in {"message_id", "reply"}:
                continue
            ref = item.get("ref")
            if not isinstance(ref, int) or ref in seen:
                continue
            seen.add(ref)
            out.append(ref)
        return out

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
        out: list[dict[str, Any]] = []
        for item in items:
            action = str(item.get("action") or "")
            level = str(item.get("new_stop_level") or "")
            target = item.get("target")
            if not action or not level or not isinstance(target, int):
                continue
            out.append(
                {
                    "action": action,
                    "new_stop_level": level,
                    "targeting": {
                        "mode": "EXPLICIT_TARGETS",
                        "targets": [target],
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
        info_only_markers = _merge_markers(_class_markers(classification, "info_only"), _DEFAULT_CLASSIFICATION_MARKERS["info_only"])
        exit_be_markers = _merge_markers(
            _marker_values(
                self._rules.get("intent_markers", {}) if isinstance(self._rules.get("intent_markers"), dict) else {},
                "EXIT_BE",
                "U_EXIT_BE",
            ),
            (),
        )
        incomplete_markers = _merge_markers(
            classification.get("setup_incomplete") if isinstance(classification, dict) else None,
            _DEFAULT_CLASSIFICATION_MARKERS["setup_incomplete"],
        )

        if _contains_any(normalized, tuple(_ignore_markers(self._rules))):
            return "INFO_ONLY"

        has_direction = _contains_any(normalized, ("long", "short", "buy", "sell", "\u043b\u043e\u043d\u0433", "\u0448\u043e\u0440\u0442"))
        has_entry = _contains_any(normalized, ("entry", "entries", "\u0432\u0445\u043e\u0434", "\u0432\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445", "\u043b\u0438\u043c\u0438\u0442\u043d\u044b\u0439 \u043e\u0440\u0434\u0435\u0440")) or bool(_extract_signal_entry_levels(raw_text))
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
        if has_target and _contains_any(normalized, tuple(exit_be_markers)):
            return "UPDATE"
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
        if not has_target and _has_intermediate_result_language(normalized):
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
        canonical_mode: bool = False,
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
        report_explanation_context = _contains_any(
            normalized,
            (
                "\u0434\u043b\u044f \u0441\u043f\u0440\u0430\u0432\u043a\u0438",
                "\u0432\u043e \u0432\u0441\u0435\u0445 \u043f\u043e\u0441\u0442\u0430\u0445 \u043e \u0442\u0435\u0439\u043a\u0430\u0445 \u0438\u043b\u0438 \u0441\u0442\u043e\u043f\u0430\u0445",
                "\u043e \u0442\u0435\u0439\u043a\u0430\u0445 \u0438\u043b\u0438 \u0441\u0442\u043e\u043f\u0430\u0445",
                "r \u043d\u0430\u0440\u0430\u0441\u0442\u0430\u044e\u0449\u0438\u043c \u0438\u0442\u043e\u0433\u043e\u043c",
            ),
        )
        if _contains_any(normalized, tuple(_ignore_markers(self._rules))):
            _ = context
            return []
        if message_type == "NEW_SIGNAL":
            intents.append("NS_CREATE_SIGNAL")

        move_to_be_markers = _merge_markers(
            _strong_only(_marker_values(marker_map, "MOVE_STOP_TO_BE", "U_MOVE_STOP_TO_BE")),
            _DEFAULT_INTENT_MARKERS["U_MOVE_STOP_TO_BE"],
        )
        exit_be_markers = _merge_markers(_marker_values(marker_map, "EXIT_BE", "U_EXIT_BE"), ())
        move_markers = _merge_markers(
            _strong_only(_marker_values(marker_map, "MOVE_STOP", "U_MOVE_STOP")),
            _DEFAULT_INTENT_MARKERS["U_MOVE_STOP"],
        )
        cancel_markers = _merge_markers(
            _marker_values(marker_map, "CANCEL_PENDING_ORDERS", "U_CANCEL_PENDING_ORDERS"),
            _DEFAULT_INTENT_MARKERS["U_CANCEL_PENDING_ORDERS"],
        )
        invalidate_markers = _merge_markers(
            _marker_values(marker_map, "INVALIDATE_SETUP", "U_INVALIDATE_SETUP"),
            _DEFAULT_INTENT_MARKERS["U_INVALIDATE_SETUP"],
        )
        filled_markers = _merge_markers(
            _marker_values(marker_map, "ENTRY_FILLED", "U_MARK_FILLED"),
            _DEFAULT_INTENT_MARKERS["U_MARK_FILLED"],
        )
        future_management_context = False
        strong_move_without_target = (
            _contains_any(normalized, tuple(move_to_be_markers))
            or _contains_any(normalized, tuple(move_markers))
            or _contains_any(normalized, ("стопы в безубыток",))
        )
        strong_cancel_without_target = _contains_any(normalized, tuple(cancel_markers))

        allow_update_intents = (
            not report_only_context
            and (message_type == "UPDATE" or has_target or strong_move_without_target or strong_cancel_without_target)
        )
        if allow_update_intents:
            move_markers = _merge_markers(
                _marker_values(marker_map, "MOVE_STOP", "U_MOVE_STOP"),
                _DEFAULT_INTENT_MARKERS["U_MOVE_STOP"],
            )
            close_partial_markers = _merge_markers(
                _marker_values(marker_map, "CLOSE_PARTIAL", "U_CLOSE_PARTIAL"),
                _DEFAULT_INTENT_MARKERS["U_CLOSE_PARTIAL"],
            )
            close_full_markers = _merge_markers(
                _marker_values(marker_map, "CLOSE_FULL", "U_CLOSE_FULL"),
                _DEFAULT_INTENT_MARKERS["U_CLOSE_FULL"],
            )
            tp_hit_markers = _merge_markers(
                _strong_only(_marker_values(marker_map, "TP_HIT", "U_TP_HIT")),
                _DEFAULT_INTENT_MARKERS["U_TP_HIT"],
            )
            stop_hit_markers = _merge_markers(
                _strong_only(_marker_values(marker_map, "SL_HIT", "U_STOP_HIT")),
                _DEFAULT_INTENT_MARKERS["U_STOP_HIT"],
            )
            future_management_context = _has_future_management_language(normalized) and _contains_any(normalized, tuple(filled_markers))

            stop_to_tp_context = bool(_STOP_TO_TP1_RE.search(raw_text))
            has_move_stop_context = False
            if message_type != "NEW_SIGNAL":
                if canonical_mode:
                    if _contains_any(normalized, tuple(exit_be_markers)):
                        intents.append("U_EXIT_BE")
                    elif _contains_any(normalized, tuple(move_to_be_markers)):
                        intents.append("U_MOVE_STOP_TO_BE")
                        has_move_stop_context = True
                        if stop_to_tp_context:
                            intents.append("U_MOVE_STOP")
                            has_move_stop_context = True
                    elif _contains_any(normalized, tuple(move_markers)):
                        intents.append("U_MOVE_STOP")
                        has_move_stop_context = True
                else:
                    if _contains_any(normalized, tuple(move_to_be_markers)) or _contains_any(normalized, ("стопы в безубыток",)):
                        intents.append("U_MOVE_STOP_TO_BE")
                        has_move_stop_context = True
                        if stop_to_tp_context:
                            intents.append("U_MOVE_STOP")
                            has_move_stop_context = True
                    elif _contains_any(normalized, tuple(move_markers)):
                        intents.append("U_MOVE_STOP")
                        has_move_stop_context = True
                    elif _contains_any(normalized, tuple(exit_be_markers)) and not (
                        _contains_any(normalized, tuple(close_partial_markers))
                        or _contains_any(normalized, tuple(close_full_markers))
                        or _contains_any(normalized, ("закрываю все позиции", "закрываю остаток", "закрываю позиции"))
                    ):
                        intents.append("U_EXIT_BE")

            if _contains_any(normalized, tuple(cancel_markers)):
                intents.append("U_CANCEL_PENDING_ORDERS")
            if _contains_any(normalized, tuple(invalidate_markers)):
                intents.append("U_INVALIDATE_SETUP")
                if message_type == "NEW_SIGNAL" and "U_CANCEL_PENDING_ORDERS" not in intents:
                    intents.append("U_CANCEL_PENDING_ORDERS")
            if not future_management_context and _contains_any(normalized, tuple(close_partial_markers)):
                intents.append("U_CLOSE_PARTIAL")
            elif not future_management_context and (
                _contains_any(normalized, tuple(close_full_markers))
                or _contains_any(normalized, ("закрываю все позиции", "закрываю остаток", "закрываю позиции"))
            ):
                intents.append("U_CLOSE_FULL")

            tp_reply_fallback = not canonical_mode and has_target and (normalized.startswith("тейк") or normalized.startswith("1 тейк"))
            stop_reply_fallback = not canonical_mode and has_target and normalized.startswith("стоп")
            if message_type != "NEW_SIGNAL" and not future_management_context and not report_explanation_context and not stop_to_tp_context and (
                _contains_any(normalized, tuple(tp_hit_markers)) or tp_reply_fallback
            ):
                intents.append("U_TP_HIT")
            if message_type != "NEW_SIGNAL" and not future_management_context and not report_explanation_context and not stop_to_tp_context and not has_move_stop_context and (
                _contains_any(normalized, tuple(stop_hit_markers)) or stop_reply_fallback
            ):
                intents.append("U_STOP_HIT")
            if _contains_any(normalized, tuple(filled_markers)):
                intents.append("U_MARK_FILLED")

        if message_type == "UNCLASSIFIED" and not report_only_context and _contains_any(
            normalized,
            tuple(move_to_be_markers) + tuple(exit_be_markers) + tuple(move_markers) + tuple(cancel_markers) + tuple(filled_markers),
        ):
            message_type = "UPDATE"
        if message_type == "UNCLASSIFIED" and not report_only_context and not has_target and _has_intermediate_result_language(normalized):
            message_type = "INFO_ONLY"

        report_markers = _merge_markers(
            _marker_values(marker_map, "REPORT_FINAL_RESULT", "U_REPORT_FINAL_RESULT"),
            _DEFAULT_INTENT_MARKERS["U_REPORT_FINAL_RESULT"],
        )
        if _should_emit_report_final_result(raw_text=raw_text, normalized=normalized, report_markers=report_markers):
            intents.append("U_REPORT_FINAL_RESULT")

        if future_management_context:
            intents = [value for value in intents if value in {"NS_CREATE_SIGNAL", "U_MARK_FILLED", "U_CANCEL_PENDING_ORDERS", "U_INVALIDATE_SETUP"}]
        elif "U_EXIT_BE" in intents:
            intents = [value for value in intents if value not in ("U_MOVE_STOP_TO_BE", "U_MOVE_STOP", "U_STOP_HIT", "U_CLOSE_FULL", "U_CLOSE_PARTIAL")]
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
            risk_hint = _extract_risk_hint(raw_text)
            if risk_hint is not None:
                entities["risk_hint"] = risk_hint

        if "U_MOVE_STOP_TO_BE" in intents:
            entities["new_stop_level"] = "ENTRY"
        elif "U_MOVE_STOP" in intents:
            stop_level = _extract_stop_level(raw_text)
            if stop_level is not None:
                entities["new_stop_level"] = stop_level
        if "U_EXIT_BE" in intents:
            entities["result_mode"] = "BREAKEVEN"
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
        if "U_REPORT_FINAL_RESULT" in intents and "result_mode" not in entities:
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
                return data
        except (OSError, ValueError):
            return {}
        return {}

    def _has_global_target_scope(self, *, prepared: dict[str, Any]) -> bool:
        return self._resolve_global_target_scope(prepared=prepared) is not None

    def _resolve_cancel_scope(self, *, prepared: dict[str, Any], target_refs: list[dict[str, Any]]) -> str:
        if target_refs:
            return "TARGETED"
        raw_lower = str(prepared.get("raw_text") or "").lower()
        if "all limit" in raw_lower:
            return "ALL_ALL"
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
                "\u043f\u043e \u0448\u043e\u0440\u0442\u0430\u043c",
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


def _build_ta_targeting(
    *,
    message_type: str,
    target_refs: list[dict[str, Any]],
    global_target_scope: str | None,
    context: ParserContext,
) -> Targeting | None:
    if message_type in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
        return None

    refs: list[TargetRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(ref_type: str, value: int | str) -> None:
        key = (ref_type, str(value))
        if key in seen:
            return
        seen.add(key)
        refs.append(TargetRef(ref_type=ref_type, value=value))  # type: ignore[arg-type]

    for item in target_refs:
        kind = str(item.get("kind") or "")
        ref = item.get("ref")
        if kind == "reply" and isinstance(ref, int):
            _add("REPLY", ref)
        elif kind == "telegram_link" and isinstance(ref, str):
            _add("TELEGRAM_LINK", ref)
        elif kind == "message_id" and isinstance(ref, int):
            _add("MESSAGE_ID", ref)

    if context.reply_to_message_id is not None:
        _add("REPLY", context.reply_to_message_id)

    if global_target_scope is not None:
        if global_target_scope in {"ALL_LONGS", "ALL_REMAINING_LONGS"}:
            scope = TargetScope(
                kind="PORTFOLIO_SIDE",
                value=global_target_scope,
                side_filter="LONG",
                applies_to_all=True,
            )
        elif global_target_scope in {"ALL_SHORTS", "ALL_REMAINING_SHORTS"}:
            scope = TargetScope(
                kind="PORTFOLIO_SIDE",
                value=global_target_scope,
                side_filter="SHORT",
                applies_to_all=True,
            )
        else:
            scope = TargetScope(kind="ALL_OPEN", value=global_target_scope, applies_to_all=True)
        return Targeting(refs=refs, scope=scope, strategy="GLOBAL_SCOPE", targeted=True)

    if not refs:
        return None

    return Targeting(
        refs=refs,
        scope=TargetScope(kind="SINGLE_SIGNAL"),
        strategy="REPLY_OR_LINK",
        targeted=True,
    )


def _build_ta_signal_payload(*, entities: dict[str, Any]) -> SignalPayload:
    symbol = entities.get("symbol")
    side = entities.get("side")
    plan_entries = entities.get("entry_plan_entries") if isinstance(entities.get("entry_plan_entries"), list) else []
    fallback_entries = entities.get("entry") if isinstance(entities.get("entry"), list) else []
    entry_structure_raw = str(entities.get("entry_structure") or "").upper()

    entries: list[EntryLeg] = []
    if plan_entries:
        for idx, item in enumerate(plan_entries, start=1):
            if not isinstance(item, dict):
                continue
            sequence = int(item.get("sequence") or idx)
            order_type = str(item.get("order_type") or "LIMIT").upper()
            entry_type = "MARKET" if order_type == "MARKET" else "LIMIT"
            role = str(item.get("role") or "UNKNOWN").upper()
            if role not in {"PRIMARY", "AVERAGING"}:
                role = "UNKNOWN"
            price_val = item.get("price")
            price = Price.from_float(float(price_val)) if isinstance(price_val, (int, float)) else None
            if entry_type == "LIMIT" and price is None:
                continue
            entries.append(
                EntryLeg(
                    sequence=sequence,
                    entry_type=entry_type,  # type: ignore[arg-type]
                    price=price,
                    role=role,  # type: ignore[arg-type]
                    is_optional=bool(item.get("is_optional")),
                )
            )
    else:
        for idx, value in enumerate(fallback_entries, start=1):
            if not isinstance(value, (int, float)):
                continue
            entries.append(
                EntryLeg(
                    sequence=idx,
                    entry_type="LIMIT",
                    price=Price.from_float(float(value)),
                    role="PRIMARY" if idx == 1 else "AVERAGING",
                )
            )

    entry_structure = _resolve_ta_entry_structure(raw=entry_structure_raw, entries=entries)

    stop_val = entities.get("stop_loss")
    stop_loss = StopLoss(price=Price.from_float(float(stop_val))) if isinstance(stop_val, (int, float)) else None

    tps_raw = entities.get("take_profits") if isinstance(entities.get("take_profits"), list) else []
    take_profits = [
        TakeProfit(sequence=i + 1, price=Price.from_float(float(v)))
        for i, v in enumerate(tps_raw)
        if isinstance(v, (int, float))
    ]

    missing: list[str] = []
    if not symbol:
        missing.append("symbol")
    if side not in {"LONG", "SHORT"}:
        missing.append("side")
    if not entries:
        missing.append("entries")
    if stop_loss is None:
        missing.append("stop_loss")
    if not take_profits:
        missing.append("take_profits")
    if entry_structure is None and entries:
        missing.append("entry_structure")

    risk_hint = entities.get("risk_hint")
    if not isinstance(risk_hint, RiskHint):
        risk_hint = None

    return SignalPayload(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        entry_structure=entry_structure,  # type: ignore[arg-type]
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        risk_hint=risk_hint,
        completeness="COMPLETE" if not missing else "INCOMPLETE",
        missing_fields=missing,
    )


def _resolve_ta_entry_structure(*, raw: str, entries: list[EntryLeg]) -> str | None:
    if raw in {"ONE_SHOT", "SINGLE"}:
        return "ONE_SHOT"
    if raw == "TWO_STEP":
        return "TWO_STEP"
    if raw == "RANGE":
        return "RANGE"
    if raw == "LADDER":
        return "LADDER"

    count = len(entries)
    if count == 0:
        return None
    if count == 1:
        return "ONE_SHOT"
    if count == 2:
        return "TWO_STEP"
    return "LADDER"


def _build_ta_update_ops(*, intents: list[str], entities: dict[str, Any], warnings: list[str]) -> list[UpdateOperation]:
    ops: list[UpdateOperation] = []
    intent_set = set(intents)

    stop_op = _build_ta_set_stop_op(intent_set=intent_set, entities=entities, warnings=warnings)
    if stop_op is not None:
        ops.append(stop_op)

    for intent in intents:
        if intent in {"U_MOVE_STOP_TO_BE", "U_MOVE_STOP"}:
            continue
        if intent == "U_CLOSE_FULL":
            close_scope = str(entities.get("close_scope") or "FULL")
            ops.append(
                UpdateOperation(
                    op_type="CLOSE",
                    close=CloseOperation(close_scope=close_scope),
                )
            )
        elif intent == "U_CLOSE_PARTIAL":
            close_fraction = entities.get("close_fraction")
            fraction = float(close_fraction) if isinstance(close_fraction, (int, float)) else None
            ops.append(
                UpdateOperation(
                    op_type="CLOSE",
                    close=CloseOperation(close_scope="PARTIAL", close_fraction=fraction),
                )
            )
        elif intent == "U_CANCEL_PENDING_ORDERS":
            cancel_scope = entities.get("cancel_scope")
            ops.append(
                UpdateOperation(
                    op_type="CANCEL_PENDING",
                    cancel_pending=CancelPendingOperation(
                        cancel_scope=str(cancel_scope) if cancel_scope else "ALL_PENDING_ENTRIES"
                    ),
                )
            )
        elif intent == "U_INVALIDATE_SETUP":
            ops.append(
                UpdateOperation(
                    op_type="CANCEL_PENDING",
                    cancel_pending=CancelPendingOperation(cancel_scope="ALL_PENDING_ENTRIES"),
                )
            )
        elif intent == "U_UPDATE_TAKE_PROFITS":
            tps_raw = entities.get("take_profits") if isinstance(entities.get("take_profits"), list) else []
            take_profits = [
                TakeProfit(sequence=i + 1, price=Price.from_float(float(v)))
                for i, v in enumerate(tps_raw)
                if isinstance(v, (int, float))
            ]
            if take_profits:
                ops.append(
                    UpdateOperation(
                        op_type="MODIFY_TARGETS",
                        modify_targets=ModifyTargetsOperation(mode="REPLACE_ALL", take_profits=take_profits),
                    )
                )
            else:
                warnings.append("U_UPDATE_TAKE_PROFITS: no take_profits found")
        elif intent == "U_REVERSE_SIGNAL":
            warnings.append("U_REVERSE_SIGNAL: new signal component ignored; mapped to CLOSE only")
            ops.append(
                UpdateOperation(
                    op_type="CLOSE",
                    close=CloseOperation(close_scope="FULL"),
                )
            )

    return ops


def _build_ta_set_stop_op(
    *,
    intent_set: set[str],
    entities: dict[str, Any],
    warnings: list[str],
) -> UpdateOperation | None:
    has_move_to_be = "U_MOVE_STOP_TO_BE" in intent_set
    has_move_stop = "U_MOVE_STOP" in intent_set
    if not has_move_to_be and not has_move_stop:
        return None

    target: StopTarget | None = None
    if has_move_stop:
        target = _resolve_ta_stop_target(entities.get("new_stop_level"))
    if target is None and has_move_to_be:
        target = StopTarget(target_type="ENTRY")
    if target is None:
        warnings.append("U_MOVE_STOP: new_stop_level missing or unresolvable")
        return None
    return UpdateOperation(op_type="SET_STOP", set_stop=target)


def _resolve_ta_stop_target(value: Any) -> StopTarget | None:
    if isinstance(value, (int, float)):
        return StopTarget(target_type="PRICE", value=float(value))
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if normalized in {"ENTRY", "BE", "BREAKEVEN"}:
        return StopTarget(target_type="ENTRY")
    match = re.match(r"^TP(\d+)$", normalized)
    if match:
        return StopTarget(target_type="TP_LEVEL", value=int(match.group(1)))
    parsed = _to_float(normalized)
    if parsed is not None:
        return StopTarget(target_type="PRICE", value=parsed)
    return None


def _build_ta_report_payload(
    *,
    intents: list[str],
    entities: dict[str, Any],
    reported_results: list[dict[str, Any]],
) -> ReportPayload | None:
    events = _build_ta_report_events(intents=intents, entities=entities, reported_results=reported_results)
    reported_result = _build_ta_reported_result(reported_results=reported_results)
    if not events and reported_result is None:
        return None
    return ReportPayload(events=events, reported_result=reported_result)


def _build_ta_report_events(
    *,
    intents: list[str],
    entities: dict[str, Any],
    reported_results: list[dict[str, Any]],
) -> list[ReportEvent]:
    result = _build_ta_reported_result(reported_results=reported_results)
    events: list[ReportEvent] = []
    for intent in intents:
        if intent == "U_TP_HIT":
            level: int | None = None
            hit_target = entities.get("hit_target")
            if isinstance(hit_target, str):
                m = re.match(r"^TP(\d+)$", hit_target.upper())
                if m:
                    level = int(m.group(1))
            events.append(ReportEvent(event_type="TP_HIT", level=level, result=result))
        elif intent == "U_STOP_HIT":
            events.append(ReportEvent(event_type="STOP_HIT", result=result))
        elif intent == "U_REPORT_FINAL_RESULT":
            events.append(ReportEvent(event_type="FINAL_RESULT", result=result))
        elif intent == "U_MARK_FILLED":
            events.append(ReportEvent(event_type="ENTRY_FILLED"))
        elif intent == "U_EXIT_BE":
            events.append(ReportEvent(event_type="BREAKEVEN_EXIT", result=result))
    return events


def _build_ta_reported_result(*, reported_results: list[dict[str, Any]]) -> ReportedResult | None:
    if not reported_results:
        return None
    first = reported_results[0] if isinstance(reported_results[0], dict) else None
    if not isinstance(first, dict):
        return None
    value = first.get("value")
    unit = str(first.get("unit") or "UNKNOWN").upper()
    if unit not in {"R", "PERCENT", "TEXT", "UNKNOWN"}:
        unit = "UNKNOWN"
    text = first.get("text")
    return ReportedResult(
        value=float(value) if isinstance(value, (int, float)) else None,
        unit=unit,  # type: ignore[arg-type]
        text=str(text) if isinstance(text, str) and text.strip() else None,
    )


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


def _marker_values(marker_map: dict[str, Any], canonical_name: str, legacy_name: str | None = None) -> Any:
    value = marker_map.get(canonical_name)
    if value is not None:
        return value
    if legacy_name is not None:
        return marker_map.get(legacy_name)
    return None


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
            "убыток",
            "loss",
        ),
    )


def _extract_risk_hint(raw_text: str) -> RiskHint | None:
    range_match = _RISK_RANGE_RE.search(raw_text)
    if range_match:
        min_val = _to_float(range_match.group("min"))
        max_val = _to_float(range_match.group("max"))
        if min_val is not None and max_val is not None:
            return RiskHint(raw=range_match.group(0), min_value=min_val, max_value=max_val, unit="PERCENT")
    single_match = _RISK_SINGLE_RE.search(raw_text)
    if single_match:
        val = _to_float(single_match.group("value"))
        if val is not None:
            return RiskHint(raw=single_match.group(0), value=val, unit="PERCENT")
    return None


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
    if _BARE_RESULT_R_RE.search(raw_text):
        return True
    if _contains_any(
        normalized,
        (
            "закрылась в безубыток",
            "закрылась в бу",
            "закрыта в бу",
            "закрылись в бу",
            "также в бу закрылись",
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
