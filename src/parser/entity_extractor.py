"""Shared deterministic entity extractor helpers for parser v2 migration."""

from __future__ import annotations

import re
from typing import Any

_SYMBOL_RE = re.compile(r"\$?(?P<symbol>[A-Z0-9]{2,24}(?:USDT|USDC|USD|BTC|ETH)(?:\.P)?)\b", re.IGNORECASE)
_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_RESULT_R_RE = re.compile(
    r"\b(?P<symbol>[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?)\s*[-:=]\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*R\b",
    re.IGNORECASE,
)
_RESULT_PCT_RE = re.compile(r"(?P<value>[+-]?\d+(?:[.,]\d+)?)%")

_ENTRY_VALUE_RE = re.compile(
    r"(?:entry|entries|вход(?:\s+с\s+текущих|\s+лимиткой|\s+лимитным\s+ордером)?)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_ENTRY_RANGE_RE = re.compile(
    r"(?:entry|entries|вход|zone|диапазон)\s*[:=@-]?\s*(?P<low>\d[\d\s]*(?:[.,]\d+)?)\s*(?:-|–|—|to|до)\s*(?P<high>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_ENTRY_AB_RE = re.compile(
    r"(?:^|\n)\s*(?:[-—•]\s*)?(?:entry\s*)?(?P<label>[abаб])(?:\s*\((?P<qual>[^)]*)\))?\s*[:=@-]\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_AVERAGING_RE = re.compile(r"(?:averaging|усреднение)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_STOP_RE = re.compile(r"(?:\bsl\b|stop(?:\s*loss)?|стоп\s*лосс|стоп)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_TP_RE = re.compile(r"(?:\btp\d*\b|\bтп\d*\b|тейк\s*профит|take\s*profit|target\s*\d*)\s*[:=@-]?\s*(?P<value>\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)

_MOVE_STOP_LEVEL_RE = re.compile(
    r"(?:move\s*(?:sl|stop)\s*(?:to)?|переносим\s*(?:на|в)\s*(?:уровень\s*)?|на\s*уровень\s*|на\s*отмет\w*\s*)(?P<value>\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)


def extract_symbol(raw_text: str) -> str | None:
    match = _SYMBOL_RE.search((raw_text or "").upper())
    return str(match.group("symbol")).upper() if match else None


def extract_side(normalized_text: str) -> str | None:
    text = (normalized_text or "").lower()
    if any(marker in text for marker in (" long ", "лонг", " buy ")):
        return "LONG"
    if any(marker in text for marker in (" short ", "шорт", " sell ")):
        return "SHORT"
    return None


def extract_entry_levels(raw_text: str, normalized_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # 1) explicit entry range / zone
    for match in _ENTRY_RANGE_RE.finditer(raw_text or ""):
        low = _to_float(match.group("low"))
        high = _to_float(match.group("high"))
        if low is None or high is None:
            continue
        lo, hi = (low, high) if low <= high else (high, low)
        out.append(
            {
                "kind": "ENTRY_ZONE",
                "range": {"low": lo, "high": hi},
                "role": "PRIMARY",
                "order_type": "LIMIT",
                "source": "range",
            }
        )

    # 2) A/B entries
    seen_prices: set[float] = set()
    for match in _ENTRY_AB_RE.finditer(raw_text or ""):
        value = _to_float(match.group("value"))
        if value is None:
            continue
        label = str(match.group("label") or "").lower()
        qual = str(match.group("qual") or "").lower()
        role = "PRIMARY"
        if label in ("b", "б") or any(marker in qual for marker in ("усред", "averag", "добор", "top up")):
            role = "AVERAGING"
        if value in seen_prices:
            continue
        seen_prices.add(value)
        out.append(
            {
                "kind": "ENTRY_LEVEL",
                "price": value,
                "role": role,
                "order_type": "LIMIT",
                "source": "ab",
            }
        )

    # 3) explicit averaging entry
    avg_match = _AVERAGING_RE.search(raw_text or "")
    if avg_match:
        value = _to_float(avg_match.group("value"))
        if value is not None and value not in seen_prices:
            seen_prices.add(value)
            out.append(
                {
                    "kind": "ENTRY_LEVEL",
                    "price": value,
                    "role": "AVERAGING",
                    "order_type": "LIMIT",
                    "source": "averaging",
                }
            )

    # 4) simple entry level(s)
    for match in _ENTRY_VALUE_RE.finditer(raw_text or ""):
        value = _to_float(match.group("value"))
        if value is None or value in seen_prices:
            continue
        seen_prices.add(value)
        order_type = "MARKET" if "по текущим" in (normalized_text or "") or "at market" in (normalized_text or "") else "LIMIT"
        out.append(
            {
                "kind": "ENTRY_LEVEL",
                "price": value,
                "role": "PRIMARY",
                "order_type": order_type,
                "source": "single",
            }
        )

    return out


def extract_stop_loss(raw_text: str) -> dict[str, Any] | None:
    match = _STOP_RE.search(raw_text or "")
    if not match:
        return None
    value = _to_float(match.group("value"))
    if value is None:
        return None
    return {"price": value, "kind": "STOP_LOSS"}


def extract_take_profits(raw_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, match in enumerate(_TP_RE.finditer(raw_text or ""), start=1):
        value = _to_float(match.group("value"))
        if value is None:
            continue
        if any(tp.get("price") == value for tp in out):
            continue
        out.append({"price": value, "kind": "TAKE_PROFIT", "level": index})
    return out


def extract_reported_results(raw_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text = raw_text or ""
    for match in _RESULT_R_RE.finditer(text):
        value = _to_float(match.group("value"))
        out.append(
            {
                "symbol": str(match.group("symbol") or "").upper(),
                "result_type": "R_MULTIPLE",
                "value": value,
                "unit": "R",
            }
        )

    if out:
        return out

    # fallback percent-only results (conservative)
    for match in _RESULT_PCT_RE.finditer(text):
        value = _to_float(match.group("value"))
        if value is None:
            continue
        out.append({"symbol": None, "result_type": "PERCENT", "value": value, "unit": "PERCENT"})
    return out


def extract_target_scope(raw_text: str, reply_to_message_id: int | None = None, links: list[str] | None = None) -> dict[str, Any]:
    normalized = (raw_text or "").lower()
    refs = _extract_refs(raw_text=raw_text, reply_to_message_id=reply_to_message_id, links=links)

    scope = "UNKNOWN"
    if any(marker in normalized for marker in ("all longs", "все лонги", "лонги")):
        scope = "ALL_LONGS"
    elif any(marker in normalized for marker in ("all shorts", "все шорты", "шорты")):
        scope = "ALL_SHORTS"
    elif any(marker in normalized for marker in ("all positions", "все позиции", "все сделки")):
        scope = "ALL_POSITIONS"
    elif refs:
        scope = "TARGETED"

    return {
        "scope": scope,
        "target_refs": refs,
        "reply_to_message_id": reply_to_message_id,
        "has_strong_target": bool(refs),
    }


def extract_linking(raw_text: str, reply_to_message_id: int | None = None, links: list[str] | None = None) -> dict[str, Any]:
    refs = _extract_refs(raw_text=raw_text, reply_to_message_id=reply_to_message_id, links=links)
    methods: list[str] = []
    if reply_to_message_id is not None:
        methods.append("reply")
    if any(link for link in (links or [])) or _extract_links(raw_text):
        methods.append("telegram_link")
    return {
        "methods": methods,
        "target_refs": refs,
        "root_ref": refs[0] if refs else None,
        "has_linking": bool(refs),
    }


def extract_operational_entities(raw_text: str, normalized_text: str, intents: list[str]) -> dict[str, Any]:
    text = raw_text or ""
    normalized = (normalized_text or "").lower()
    out: dict[str, Any] = {
        "new_stop_level": None,
        "close_fraction": None,
        "close_scope": None,
        "hit_target": None,
        "cancel_scope": None,
        "result_value": None,
        "result_unit": None,
    }

    if "U_MOVE_STOP_TO_BE" in intents:
        level = _extract_move_stop_level(text)
        out["new_stop_level"] = level if level is not None else "ENTRY"
    elif "U_MOVE_STOP" in intents:
        out["new_stop_level"] = _extract_move_stop_level(text)

    if "U_CLOSE_PARTIAL" in intents:
        out["close_scope"] = "PARTIAL"
        out["close_fraction"] = _extract_fraction(text, normalized)
    elif "U_CLOSE_FULL" in intents:
        out["close_scope"] = "FULL"

    if "U_TP_HIT" in intents:
        out["hit_target"] = _extract_hit_target(text) or "TP"
    elif "U_STOP_HIT" in intents:
        out["hit_target"] = "STOP"

    if "U_CANCEL_PENDING_ORDERS" in intents:
        out["cancel_scope"] = "ALL_PENDING_ENTRIES"

    # result extraction (conservative): first explicit percent from text
    pct = _extract_first_percent(text)
    if pct is not None:
        out["result_value"] = pct
        out["result_unit"] = "PERCENT"

    return out


def _extract_refs(raw_text: str, reply_to_message_id: int | None, links: list[str] | None) -> list[int]:
    refs: list[int] = []
    if reply_to_message_id is not None:
        refs.append(int(reply_to_message_id))
    for link in _extract_links(raw_text, links=links):
        match = _LINK_RE.search(link)
        if match:
            refs.append(int(match.group("id")))
    return _unique_ints(refs)


def _extract_links(raw_text: str, links: list[str] | None = None) -> list[str]:
    found = list(links or [])
    found.extend(match.group(0) for match in _LINK_RE.finditer(raw_text or ""))
    return _unique(found)


def _extract_move_stop_level(raw_text: str) -> float | None:
    match = _MOVE_STOP_LEVEL_RE.search(raw_text or "")
    if not match:
        return None
    return _to_float(match.group("value"))


def _extract_fraction(raw_text: str, normalized_text: str) -> float | None:
    match = _RESULT_PCT_RE.search(raw_text or "")
    if match:
        value = _to_float(match.group("value"))
        if value is not None:
            return round(max(0.0, min(1.0, value / 100.0)), 6)
    if any(marker in normalized_text for marker in ("half", "1/2", "половин")):
        return 0.5
    return None


def _extract_hit_target(raw_text: str) -> str | None:
    match = re.search(r"\b(?:tp|тп)(?P<index>\d+)\b", raw_text or "", re.IGNORECASE)
    if match:
        return f"TP{match.group('index')}"
    return None


def _extract_first_percent(raw_text: str) -> float | None:
    match = _RESULT_PCT_RE.search(raw_text or "")
    if not match:
        return None
    return _to_float(match.group("value"))


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


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _unique_ints(values: list[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
