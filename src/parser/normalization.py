"""Parse result normalization for parser outputs.

This module provides a stable normalized shape shared by regex parsing and
future LLM-based extraction paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

_CANONICAL_EVENT_TYPES = {
    "NEW_SIGNAL",
    "UPDATE",
    "CANCEL_PENDING",
    "MOVE_STOP",
    "TAKE_PROFIT",
    "CLOSE_POSITION",
    "INFO_ONLY",
    "SETUP_INCOMPLETE",
    "INVALID",
}
_PARSER_USED = {"regex", "llm", None}
_PARSER_MODES = {"regex_only", "llm_only", "hybrid_auto", None}
_MESSAGE_TYPES = {"NEW_SIGNAL", "UPDATE", "INFO_ONLY", "SETUP_INCOMPLETE", "UNCLASSIFIED", None}
_DIRECTIONS = {"LONG", "SHORT", None}

_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_HASH_REF_RE = re.compile(r"#(?P<id>\d{3,})")
_EXPLICIT_REF_RE = re.compile(r"(?:msg|message|ref|id)\s*#?:?\s*(?P<id>\d{2,})", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,64})")
_RESULT_R_RE = re.compile(
    r"\b(?P<symbol>[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?)\s*[-:=]\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*R\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ParseResultNormalized:
    # Legacy/event envelope fields (kept for backward compatibility)
    event_type: str
    trader_id: str | None
    source_chat_id: str | None
    source_message_id: int | None
    raw_text: str
    parser_mode: str | None
    confidence: float
    instrument: str | None
    side: str | None
    market_type: str | None
    entries: list[dict[str, Any]] = field(default_factory=list)
    stop_loss: dict[str, Any] | None = None
    take_profits: list[dict[str, Any]] = field(default_factory=list)
    root_ref: int | None = None
    status: str = "PARSED"
    validation_warnings: list[str] = field(default_factory=list)

    # Semantic parser contract fields (hybrid-ready)
    parser_used: str | None = None
    message_type: str | None = None
    message_subtype: str | None = None
    symbol: str | None = None
    direction: str | None = None
    entry_main: float | None = None
    entry_mode: str | None = None
    average_entry: float | None = None
    stop_loss_price: float | None = None
    take_profit_prices: list[float] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    target_refs: list[int] = field(default_factory=list)
    reported_results: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw_entities: dict[str, Any] = field(
        default_factory=lambda: {
            "hashtags": [],
            "links": [],
            "time_hint": None,
        }
    )

    # Compatibility alias for previous uppercase enum style
    parser_mode_legacy: str | None = None
    selection_metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "trader_id": self.trader_id,
            "source_chat_id": self.source_chat_id,
            "source_message_id": self.source_message_id,
            "raw_text": self.raw_text,
            "parser_mode": self.parser_mode,
            "confidence": self.confidence,
            "instrument": self.instrument,
            "side": self.side,
            "market_type": self.market_type,
            "entries": self.entries,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "root_ref": self.root_ref,
            "status": self.status,
            "validation_warnings": self.validation_warnings,
            "parser_used": self.parser_used,
            "message_type": self.message_type,
            "message_subtype": self.message_subtype,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_main": self.entry_main,
            "entry_mode": self.entry_mode,
            "average_entry": self.average_entry,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_prices": self.take_profit_prices,
            "actions": self.actions,
            "target_refs": self.target_refs,
            "reported_results": self.reported_results,
            "notes": self.notes,
            "raw_entities": self.raw_entities,
            "parser_mode_legacy": self.parser_mode_legacy,
            "selection_metadata": self.selection_metadata,
        }


def build_parse_result_normalized(
    *,
    message_type: str,
    normalized_text: str,
    trader_id: str | None,
    source_chat_id: str | None,
    source_message_id: int | None,
    raw_text: str,
    parser_used: str | None,
    parser_mode: str | None,
    parse_status: str,
    instrument: str | None,
    side: str | None,
    entry_raw: str | None,
    stop_raw: str | None,
    targets: list[str],
    root_ref: int | None,
    existing_warnings: list[str],
    notes: list[str],
) -> ParseResultNormalized:
    event_type = _map_to_canonical_event_type(message_type=message_type, normalized_text=normalized_text)
    semantic_entries = _parse_entries(entry_raw)
    stop_loss = _parse_level(stop_raw, label="SL", kind="STOP_LOSS")
    take_profits = [_parse_level(value, label=f"TP{index + 1}", kind="TAKE_PROFIT") for index, value in enumerate(targets)]
    take_profits = [value for value in take_profits if value is not None]
    confidence = _estimate_confidence(
        event_type=event_type,
        trader_id=trader_id,
        instrument=instrument,
        side=side,
        entries=semantic_entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        root_ref=root_ref,
    )

    semantic_direction = _map_direction(side)
    semantic_message_type = _to_semantic_message_type(message_type)
    actions = _extract_actions(normalized_text)
    message_subtype = _infer_message_subtype(event_type=event_type, semantic_message_type=semantic_message_type, actions=actions)
    links = _extract_links(raw_text)
    target_refs = _extract_target_refs(raw_text=raw_text, root_ref=root_ref)
    reported_results = _extract_reported_results(raw_text)
    notes_out = _build_notes(notes=notes, raw_text=raw_text, semantic_message_type=semantic_message_type, reported_results=reported_results)
    raw_entities = {
        "hashtags": _extract_hashtags(raw_text),
        "links": links,
        "time_hint": _extract_time_hint(normalized_text),
    }

    entry_prices = [value.get("price") for value in semantic_entries if isinstance(value.get("price"), float)]
    stop_loss_price = stop_loss.get("price") if stop_loss else None
    take_profit_prices = [value.get("price") for value in take_profits if isinstance(value.get("price"), float)]

    semantic_parser_mode = parser_mode if parser_mode in _PARSER_MODES else _normalize_parser_mode(parser_mode)
    parser_mode_legacy = _to_legacy_parser_mode(semantic_parser_mode)

    result = ParseResultNormalized(
        event_type=event_type,
        trader_id=trader_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        raw_text=raw_text,
        parser_mode=semantic_parser_mode,
        confidence=confidence,
        instrument=instrument,
        side=side,
        market_type=_infer_market_type(instrument),
        entries=semantic_entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        root_ref=root_ref,
        status="PARSED_WITH_WARNINGS" if existing_warnings else parse_status,
        parser_used=parser_used,
        message_type=semantic_message_type,
        message_subtype=message_subtype,
        symbol=instrument,
        direction=semantic_direction,
        entry_main=entry_prices[0] if entry_prices else None,
        entry_mode=_infer_entry_mode(semantic_entries),
        average_entry=round(sum(entry_prices) / len(entry_prices), 8) if entry_prices else None,
        stop_loss_price=stop_loss_price if isinstance(stop_loss_price, float) else None,
        take_profit_prices=take_profit_prices,
        actions=actions,
        target_refs=target_refs,
        reported_results=reported_results,
        notes=notes_out,
        raw_entities=raw_entities,
        parser_mode_legacy=parser_mode_legacy,
    )
    validation_warnings = validate_parse_result_normalized(result)
    if validation_warnings:
        result.validation_warnings = validation_warnings
        result.status = "PARSED_WITH_WARNINGS"
    return result


def validate_parse_result_normalized(result: ParseResultNormalized) -> list[str]:
    warnings: list[str] = []
    if result.event_type not in _CANONICAL_EVENT_TYPES:
        warnings.append(f"normalized_event_type_not_canonical:{result.event_type}")
    if result.parser_used not in _PARSER_USED:
        warnings.append(f"normalized_parser_used_unknown:{result.parser_used}")
    if result.parser_mode not in _PARSER_MODES:
        warnings.append(f"normalized_parser_mode_unknown:{result.parser_mode}")
    if result.message_type not in _MESSAGE_TYPES:
        warnings.append(f"normalized_message_type_unknown:{result.message_type}")
    if result.direction not in _DIRECTIONS:
        warnings.append(f"normalized_direction_unknown:{result.direction}")
    if not (0.0 <= result.confidence <= 1.0):
        warnings.append("normalized_confidence_out_of_range")

    if result.source_chat_id is None:
        warnings.append("normalized_missing_source_chat_id")
    if result.source_message_id is None:
        warnings.append("normalized_missing_source_message_id")

    if result.message_type == "NEW_SIGNAL":
        has_entry = bool(result.entries) or isinstance(result.entry_main, float)
        if not result.symbol:
            warnings.append("normalized_new_signal_missing_symbol")
        if not result.direction:
            warnings.append("normalized_new_signal_missing_direction")
        if not has_entry:
            warnings.append("normalized_new_signal_missing_entries")
        if result.stop_loss_price is None:
            warnings.append("normalized_new_signal_missing_stop_loss")
        if not result.take_profit_prices:
            warnings.append("normalized_new_signal_missing_take_profits")

    if result.message_type == "UPDATE":
        if not result.actions and not result.message_subtype:
            warnings.append("normalized_update_missing_actions_or_subtype")
        if result.root_ref is None and not result.target_refs:
            warnings.append("normalized_update_missing_target_ref")

    if result.message_type == "INFO_ONLY":
        has_notes = any((note or "").strip() for note in result.notes)
        if not result.message_subtype and not result.reported_results and not has_notes and not result.target_refs:
            warnings.append("normalized_info_only_missing_supporting_content")

    return warnings


def _to_semantic_message_type(message_type: str) -> str | None:
    if message_type in _MESSAGE_TYPES:
        return message_type
    if message_type == "INVALID":
        return "UNCLASSIFIED"
    return None


def _map_to_canonical_event_type(*, message_type: str, normalized_text: str) -> str:
    if message_type == "NEW_SIGNAL":
        return "NEW_SIGNAL"
    if message_type == "SETUP_INCOMPLETE":
        return "SETUP_INCOMPLETE"
    if message_type == "INFO_ONLY":
        return "INFO_ONLY"
    if message_type == "UPDATE":
        if any(marker in normalized_text for marker in ("cancel", "remove limit", "delete entry", "cancel pending")):
            return "CANCEL_PENDING"
        if any(marker in normalized_text for marker in ("move sl", "move stop", "breakeven", "move to be", "to entry")):
            return "MOVE_STOP"
        if any(marker in normalized_text for marker in ("tp", "take", "target hit", "profit")):
            return "TAKE_PROFIT"
        if any(marker in normalized_text for marker in ("close", "exit", "fixed 100", "close all")):
            return "CLOSE_POSITION"
        return "UPDATE"
    if message_type == "UNCLASSIFIED":
        return "INVALID"
    return "INVALID"


def _infer_message_subtype(*, event_type: str, semantic_message_type: str | None, actions: list[str]) -> str | None:
    if semantic_message_type == "UPDATE":
        by_event = {
            "MOVE_STOP": "MOVE_STOP",
            "CANCEL_PENDING": "CANCEL_PENDING",
            "TAKE_PROFIT": "TAKE_PROFIT",
            "CLOSE_POSITION": "CLOSE_POSITION",
        }
        if event_type in by_event:
            return by_event[event_type]
        if actions:
            return "ACTIONABLE_UPDATE"
    if semantic_message_type == "INFO_ONLY":
        return "RESULT_REPORT" if actions == [] else None
    return None


def _estimate_confidence(
    *,
    event_type: str,
    trader_id: str | None,
    instrument: str | None,
    side: str | None,
    entries: list[dict[str, Any]],
    stop_loss: dict[str, Any] | None,
    take_profits: list[dict[str, Any]],
    root_ref: int | None,
) -> float:
    base_by_event = {
        "NEW_SIGNAL": 0.92,
        "UPDATE": 0.78,
        "CANCEL_PENDING": 0.8,
        "MOVE_STOP": 0.82,
        "TAKE_PROFIT": 0.84,
        "CLOSE_POSITION": 0.84,
        "INFO_ONLY": 0.75,
        "SETUP_INCOMPLETE": 0.55,
        "INVALID": 0.2,
    }
    confidence = base_by_event.get(event_type, 0.5)
    if trader_id is None:
        confidence -= 0.15
    if instrument is None:
        confidence -= 0.1
    if side is None and event_type == "NEW_SIGNAL":
        confidence -= 0.1
    if event_type == "NEW_SIGNAL":
        if not entries:
            confidence -= 0.15
        if stop_loss is None:
            confidence -= 0.15
        if not take_profits:
            confidence -= 0.1
    if event_type in {"UPDATE", "CANCEL_PENDING", "MOVE_STOP", "TAKE_PROFIT", "CLOSE_POSITION"} and root_ref is None:
        confidence -= 0.2
    return max(0.0, min(1.0, round(confidence, 4)))


def _infer_market_type(instrument: str | None) -> str | None:
    if not instrument:
        return None
    symbol = instrument.upper()
    if symbol.endswith(".P"):
        return "PERP"
    if symbol.endswith("USDT") or symbol.endswith("USDC") or symbol.endswith("USD"):
        return "LINEAR"
    return "UNKNOWN"


def _parse_entries(raw: str | None) -> list[dict[str, Any]]:
    if raw is None:
        return []
    chunks = [part.strip() for part in raw.split("-") if part.strip()]
    if not chunks:
        return []
    values: list[dict[str, Any]] = []
    for index, value in enumerate(chunks):
        parsed = _parse_level(value, label=f"E{index + 1}", kind="ENTRY")
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_level(raw: str | None, *, label: str | None, kind: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    return {
        "label": label,
        "price": _to_float(cleaned),
        "kind": kind,
        "raw": cleaned,
    }


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


def _map_direction(side: str | None) -> str | None:
    if side == "BUY":
        return "LONG"
    if side == "SELL":
        return "SHORT"
    return None


def _normalize_parser_mode(value: str | None) -> str | None:
    if value is None:
        return "regex_only"
    lowered = value.strip().lower()
    if lowered in {"regex", "regex_only"}:
        return "regex_only"
    if lowered in {"llm", "llm_only"}:
        return "llm_only"
    if lowered in {"hybrid", "hybrid_auto"}:
        return "hybrid_auto"
    return None


def _to_legacy_parser_mode(parser_mode: str | None) -> str | None:
    if parser_mode == "regex_only":
        return "REGEX"
    if parser_mode == "llm_only":
        return "LLM"
    if parser_mode == "hybrid_auto":
        return "HYBRID"
    return None


def _extract_actions(normalized_text: str) -> list[str]:
    actions: list[str] = []
    if any(marker in normalized_text for marker in ("move sl", "move stop", "breakeven", "to entry", "move to be")):
        actions.append("MOVE_SL_TO_ENTRY")
    if any(marker in normalized_text for marker in ("cancel", "remove limit", "delete entry", "cancel pending")):
        actions.append("CANCEL_PENDING_ORDERS")
    if any(marker in normalized_text for marker in ("close", "close all", "exit", "fixed 100")):
        actions.append("CLOSE_ALL_OPEN_POSITIONS_AT_MARKET")
    if any(marker in normalized_text for marker in ("hold", "keep running", "continues", "in work")):
        actions.append("HOLD_CONTINUES")
    return _unique(actions)


def _extract_target_refs(*, raw_text: str, root_ref: int | None) -> list[int]:
    refs: list[int] = []
    for match in _LINK_RE.finditer(raw_text):
        refs.append(int(match.group("id")))
    for match in _EXPLICIT_REF_RE.finditer(raw_text):
        refs.append(int(match.group("id")))
    for match in _HASH_REF_RE.finditer(raw_text):
        refs.append(int(match.group("id")))
    if root_ref is not None:
        refs.append(root_ref)
    return _unique_ints(refs)


def _extract_links(raw_text: str) -> list[str]:
    return _unique([match.group(0) for match in _LINK_RE.finditer(raw_text)])


def _extract_hashtags(raw_text: str) -> list[str]:
    return _unique([match.group(1) for match in _HASHTAG_RE.finditer(raw_text)])


def _extract_reported_results(raw_text: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for match in _RESULT_R_RE.finditer(raw_text):
        value = match.group("value").replace(",", ".")
        r_multiple: float | None
        try:
            r_multiple = float(value)
        except ValueError:
            r_multiple = None
        results.append(
            {
                "symbol": match.group("symbol").upper(),
                "r_multiple": r_multiple,
            }
        )
    return results


def _extract_time_hint(normalized_text: str) -> str | None:
    hint_map = {
        "today": "today",
        "tomorrow": "tomorrow",
        "yesterday": "yesterday",
        "daily": "daily",
        "weekly": "weekly",
        "asia": "asia_session",
        "london": "london_session",
        "new york": "new_york_session",
    }
    for marker, hint in hint_map.items():
        if marker in normalized_text:
            return hint
    return None


def _build_notes(*, notes: list[str], raw_text: str, semantic_message_type: str | None, reported_results: list[dict[str, Any]]) -> list[str]:
    values = [value.strip() for value in notes if value and value.strip()]
    if semantic_message_type == "INFO_ONLY" and not values and not reported_results and raw_text.strip():
        values.append(raw_text.strip())
    return _unique(values)


def _infer_entry_mode(entries: list[dict[str, Any]]) -> str | None:
    if not entries:
        return None
    if any((entry.get("raw") or "").upper() == "MARKET_CURRENT" for entry in entries):
        return "MARKET"
    if len(entries) > 1:
        return "RANGE"
    return "SINGLE"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    output: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output

