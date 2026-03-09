"""Trader A (TA) parser profile v1.1."""

from __future__ import annotations

from dataclasses import dataclass
import re

_TA_LONG_MARKERS = ("\u043b\u043e\u043d\u0433", " long ", " buy ")
_TA_SHORT_MARKERS = ("\u0448\u043e\u0440\u0442", " short ", " sell ")
_TA_SETUP_MARKERS = (
    "\u0432\u0445\u043e\u0434",
    "\u0443\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435",
    "\u0441\u0442\u043e\u043f",
    "sl",
    "tp1",
    "tp2",
    "tp3",
    "#",
)
_TA_UPDATE_MARKERS = (
    "\u0442\u0435\u0439\u043a",
    "\u0442\u0435\u0439\u043a\u043d\u0443\u043b\u043e\u0441\u044c",
    "\u0443\u0432\u044b \u0441\u0442\u043e\u043f",
    "\u043a \u0441\u043e\u0436\u0430\u043b\u0435\u043d\u0438\u044e \u0441\u0442\u043e\u043f",
    "\u043e\u0447\u0435\u043d\u044c \u043d\u0435\u043f\u0440\u0438\u044f\u0442\u043d\u044b\u0439 \u0441\u0442\u043e\u043f",
    "\u0441\u0442\u043e\u043f\u044b \u0432 \u0431\u0443",
    "\u043f\u0435\u0440\u0435\u0432\u0435\u0441\u0442\u0438 \u0441\u0442\u043e\u043f \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a",
    "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443",
    "\u043f\u0435\u0440\u0435\u0432\u0435\u043b \u0441\u0442\u043e\u043f \u0432 \u0431\u0443",
    "\u0443\u0431\u0440\u0430\u0442\u044c \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
    "\u0443\u0431\u0438\u0440\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
    "\u0443\u0431\u0438\u0440\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
    "\u0441\u043d\u0438\u043c\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
    "\u0443\u0431\u0435\u0440\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438",
    "\u0432\u0437\u044f\u043b\u0438 \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
    "\u0436\u0434\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
    "\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043b\u0438\u043c\u0438\u0442\u043a\u0443",
    "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e",
    "\u0437\u0430\u043a\u0440\u044b\u0442\u044c",
    "\u043f\u0440\u0438\u043a\u0440\u043e\u0435\u043c",
    "\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
    "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
    "\u0432 \u0440\u0430\u0431\u043e\u0442\u0435",
    "\u0434\u0435\u0440\u0436\u0438\u043c",
    "\u0443\u0431\u044b\u0442\u043e\u043a",
    "\u043f\u0440\u043e\u0444\u0438\u0442",
    "\u0444\u0438\u043a\u0441\u0438\u0440\u0443\u044e 100%",
    "\u043b\u0438\u043c\u0438\u0442\u043a\u0430. \u043c\u043e\u044f \u0441\u0440\u0435\u0434\u043d\u044f\u044f",
    "breakeven",
)
_TA_INFO_HINTS = ("\u0436\u0434\u0443", "\u043d\u0430\u0431\u043b\u044e\u0434\u0430\u044e", "\u043a\u043e\u043c\u043c\u0435\u043d\u0442")
_TA_OVERVIEW_HINTS = (
    "\u043e\u0431\u0437\u043e\u0440",
    "\u043d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435",
    "\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0438",
    "\u0438\u043d\u0432\u0430\u043b\u0438\u0434\u0430\u0446\u0438\u044f",
)
_TA_TEASER_HINTS = (
    "\u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0447\u0443\u0442\u044c \u043f\u043e\u0437\u0436\u0435",
    "\u043f\u043e\u043b\u043d\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u0447\u0443\u0442\u044c \u043f\u043e\u0437\u0436\u0435",
    "\u043f\u043e\u043b\u043d\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u0447\u0435\u0440\u0435\u0437 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u043c\u0438\u043d\u0443\u0442",
)
_TA_ADMIN_INFO_HINTS = (
    "#\u0430\u0434\u043c\u0438\u043d",
    "\u0430\u0434\u043c\u0438\u043d",
    "\u043d\u0430\u0447\u0438\u043d\u0430\u0435\u043c \u043c\u0430\u0440\u0430\u0444\u043e\u043d",
    "\u0447\u0435\u0440\u0435\u0437 10 \u043c\u0438\u043d\u0443\u0442 \u043d\u0430\u0447\u0438\u043d\u0430\u0435\u043c",
    "\u0447\u0435\u0440\u0435\u0437 2 \u0447\u0430\u0441\u0430 \u043d\u0430\u0447\u0438\u043d\u0430\u0435\u043c",
    "\u043f\u043e\u043a\u0430 \u0436\u0434\u0435\u043c",
    "\u043f\u0435\u0440\u0435\u0440\u044b\u0432",
    "\u043d\u0435 \u0431\u0443\u0434\u0435\u0442 \u043d\u043e\u0432\u044b\u0445 \u0441\u0438\u0433\u043d\u0430\u043b\u043e\u0432",
    "\u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442\u0441\u044f \u0441 \u043d\u043e\u0432\u044b\u043c\u0438 \u0441\u0438\u0433\u043d\u0430\u043b\u0430\u043c\u0438",
)
_TA_SESSION_HINTS = ("\u043c\u0430\u0440\u0430\u0444\u043e\u043d", "\u0441\u0435\u0441\u0441\u0438\u044f")

_TA_HASHTAG_SYMBOL_RE = re.compile(r"#\s*([A-Z0-9]{2,20}USDT(?:\.P)?)\b", re.IGNORECASE)
_TA_SYMBOL_RE = re.compile(r"\b([A-Z0-9]{2,20}USDT(?:\.P)?)\b", re.IGNORECASE)
_TA_ENTRY_RE = re.compile(
    r"\b\u0432\u0445\u043e\u0434(?:\s*\([ab\u0430\u0431]\))?(?:\s+\u0441\s+\u0442\u0435\u043a\u0443\u0449\u0438\u0445|\s+\u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439)?\s*[:=@-]?\s*([0-9][0-9.,]*(?:\s*-\s*[0-9][0-9.,]*)?)",
    re.IGNORECASE,
)
_TA_ENTRY_MARKET_RE = re.compile(
    r"(?:\(\s*\u0432\u0445\u043e\u0434\s+\u0441\s+\u0442\u0435\u043a\u0443\u0449\u0438\u0445\s*\)|\b\u0432\u0445\u043e\u0434\s+\u0441\s+\u0442\u0435\u043a\u0443\u0449\u0438\u0445\b)",
    re.IGNORECASE,
)
_TA_AVERAGING_RE = re.compile(
    r"(?:\u0443\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435|\u0432\u0442\u043e\u0440(?:\u043e\u0439|\u0430\u044f)\s+\u0432\u0445\u043e\u0434)\s*[:=@-]?\s*([0-9][0-9.,]*(?:\s*-\s*[0-9][0-9.,]*)?)",
    re.IGNORECASE,
)
_TA_STOP_RE = re.compile(
    r"(?:\bsl\b|\b\u0441\u0442\u043e\u043f\b)\s*[:=@-]?\s*([0-9][0-9.,]*)",
    re.IGNORECASE,
)
_TA_TP_RE = re.compile(r"\btp\d*\s*[:=@-]?\s*([0-9][0-9.,]*)", re.IGNORECASE)
_TA_RISK_RE = re.compile(
    r"(?:\u0440\u0438\u0441\u043a\s+\u043d\u0430\s+\u0441\u0434\u0435\u043b\u043a\u0443|risk)\s*[:=@-]?\s*([0-9]+(?:[.,][0-9]+)?\s*%)",
    re.IGNORECASE,
)
_TA_CANCEL_ENTRY_RE = re.compile(
    r"(\u043e\u0442\u043c\u0435\u043d\u0430\s+\u0432\u0445\u043e\u0434\u0430[^\n\r]*)",
    re.IGNORECASE,
)
_TA_STOP_OUT_RE = re.compile(
    r"(?:\u043a\s+\u0441\u043e\u0436\u0430\u043b\u0435\u043d\u0438\u044e\s+\u0441\u0442\u043e\u043f|\u0441\u043b\u043e\u0432\u0438\u043b\u0438\s*-\s*\d+(?:[.,]\d+)?%|\u0441\u0442\u043e\u043f\s*[-:]\s*-\d+(?:[.,]\d+)?%)",
    re.IGNORECASE,
)
_TA_LIMIT_AVG_UPDATE_RE = re.compile(
    r"\u043b\u0438\u043c\u0438\u0442\u043a\u0430\W+\u043c\u043e\u044f\s+\u0441\u0440\u0435\u0434\u043d\u044f\u044f",
    re.IGNORECASE,
)
_TA_LINKED_UPDATE_HINT_RE = re.compile(
    r"(?:\u043b\u0438\u043c\u0438\u0442\u043a\u0430|\u0441\u0440\u0435\u0434\u043d\u044f\u044f|\u0442\u0435\u0439\u043a|\u0441\u0442\u043e\u043f\s+\u0432\s+\u0431\u0443|breakeven|\u0443\u0431\u0440\u0430(?:\u0442\u044c|\u0435\u043c)\s+\u043b\u0438\u043c\u0438\u0442\u043a\u0443)",
    re.IGNORECASE,
)


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


def extract_ta_fields(text: str, normalized: str) -> TAExtractedFields:
    symbols = _extract_symbols(text)
    symbol = symbols[0] if symbols else None
    direction = None
    padded = f" {normalized} "
    if any(marker in padded or marker in normalized for marker in _TA_LONG_MARKERS):
        direction = "BUY"
    elif any(marker in padded or marker in normalized for marker in _TA_SHORT_MARKERS):
        direction = "SELL"

    entry_match = _TA_ENTRY_RE.search(text)
    entry_market = _TA_ENTRY_MARKET_RE.search(text)
    averaging_match = _TA_AVERAGING_RE.search(text)
    stop_match = _TA_STOP_RE.search(text)
    targets = [m.group(1).strip() for m in _TA_TP_RE.finditer(text)]
    risk_match = _TA_RISK_RE.search(text)
    cancel_match = _TA_CANCEL_ENTRY_RE.search(text)

    update_hits = sum(1 for marker in _TA_UPDATE_MARKERS if marker in normalized)
    if _TA_STOP_OUT_RE.search(text):
        update_hits += 1
    if _TA_LIMIT_AVG_UPDATE_RE.search(text):
        update_hits += 1
    multi_action_update = update_hits >= 2
    multi_symbol_update = len(set(symbols)) >= 2
    setup_intent = any(marker in normalized for marker in _TA_SETUP_MARKERS)
    entry_intent = entry_match is not None or entry_market is not None
    overview_hint = any(hint in normalized for hint in _TA_OVERVIEW_HINTS)
    teaser_hint = any(hint in normalized for hint in _TA_TEASER_HINTS)
    admin_info_hint = any(hint in normalized for hint in _TA_ADMIN_INFO_HINTS)
    session_hint = any(hint in normalized for hint in _TA_SESSION_HINTS)

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

    if ta_fields.teaser_hint:
        return "SETUP_INCOMPLETE"

    if ta_fields.update_hits > 0:
        return "UPDATE"
    if has_strong_link and _TA_LINKED_UPDATE_HINT_RE.search(normalized):
        return "UPDATE"

    if ta_fields.session_hint and (ta_fields.update_hits > 0 or _TA_LINKED_UPDATE_HINT_RE.search(normalized)):
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

    if any(hint in normalized for hint in _TA_INFO_HINTS):
        return "INFO_ONLY"
    return "INFO_ONLY"


def _extract_symbols(text: str) -> list[str]:
    hits = [m.group(1).upper() for m in _TA_HASHTAG_SYMBOL_RE.finditer(text)]
    if hits:
        return hits
    return [m.group(1).upper() for m in _TA_SYMBOL_RE.finditer(text)]
