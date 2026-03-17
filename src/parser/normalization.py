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
_UPDATE_INTENTS = {
    "U_MOVE_STOP",
    "U_MOVE_STOP_TO_BE",
    "U_TP_HIT",
    "U_CLOSE_PARTIAL",
    "U_CLOSE_FULL",
    "U_STOP_HIT",
    "U_CANCEL_PENDING_ORDERS",
    "U_INVALIDATE_SETUP",
    "U_ADD_ENTRY",
    "U_MANUAL_CLOSE",
    "U_MARK_FILLED",
    "U_REPORT_FINAL_RESULT",
}

_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_HASH_REF_RE = re.compile(r"#(?P<id>\d{3,})")
_EXPLICIT_REF_RE = re.compile(r"(?:msg|message|ref|id)\s*#?:?\s*(?P<id>\d{2,})", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,64})")
_PERCENT_RE = re.compile(r"\b(?P<value>\d{1,3}(?:[.,]\d+)?)%")
_TP_INDEX_RE = re.compile(r"\btp(?P<index>\d+)\b", re.IGNORECASE)
_RESULT_R_RE = re.compile(
    r"\b(?P<symbol>[A-Z]{2,20}(?:USDT|USDC|USD|BTC|ETH)?)\s*[-:=]\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*R\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ParseResultNormalized:
    # Legacy compatibility envelope fields (derived from semantic fields).
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

    # Semantic source-of-truth fields.
    parser_used: str | None = None
    message_type: str | None = None
    intents: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    # Legacy/derived compatibility alias.
    message_subtype: str | None = None
    symbol: str | None = None
    direction: str | None = None
    entry_main: float | None = None
    entry_mode: str | None = None
    average_entry: float | None = None
    entry_plan_type: str | None = None
    entry_structure: str | None = None
    has_averaging_plan: bool = False
    stop_loss_price: float | None = None
    take_profit_prices: list[float] = field(default_factory=list)
    target_refs: list[int] = field(default_factory=list)
    reported_results: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(
        default_factory=lambda: {
            "hashtags": [],
            "links": [],
            "time_hint": None,
            "new_stop_level": None,
            "close_fraction": None,
            "close_scope": None,
            "hit_target": None,
            "fill_state": None,
            "result_mode": None,
            "cancel_scope": None,
            "entry_plan_entries": [],
            "entry_plan_type": None,
            "entry_structure": None,
            "has_averaging_plan": False,
        }
    )
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

    # v2 semantic source-of-truth envelope (additive, backward compatible).
    schema_version: str = "2.0"
    message_class: str | None = None
    primary_intent: str | None = None
    actions_structured: list[dict[str, Any]] = field(default_factory=list)
    instrument_obj: dict[str, Any] = field(default_factory=dict)
    position_obj: dict[str, Any] = field(default_factory=dict)
    entry_plan: dict[str, Any] = field(default_factory=dict)
    risk_plan: dict[str, Any] = field(default_factory=dict)
    results_v2: list[dict[str, Any]] = field(default_factory=list)
    target_scope: dict[str, Any] = field(default_factory=dict)
    linking: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

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
            "intents": self.intents,
            "actions": self.actions,
            "message_subtype": self.message_subtype,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_main": self.entry_main,
            "entry_mode": self.entry_mode,
            "average_entry": self.average_entry,
            "entry_plan_type": self.entry_plan_type,
            "entry_structure": self.entry_structure,
            "has_averaging_plan": self.has_averaging_plan,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_prices": self.take_profit_prices,
            "target_refs": self.target_refs,
            "reported_results": self.reported_results,
            "notes": self.notes,
            "entities": self.entities,
            "raw_entities": self.raw_entities,
            "parser_mode_legacy": self.parser_mode_legacy,
            "selection_metadata": self.selection_metadata,
            "schema_version": self.schema_version,
            "message_class": self.message_class,
            "primary_intent": self.primary_intent,
            "actions_structured": self.actions_structured,
            "instrument_obj": self.instrument_obj,
            "position_obj": self.position_obj,
            "entry_plan": self.entry_plan,
            "risk_plan": self.risk_plan,
            "results_v2": self.results_v2,
            "target_scope": self.target_scope,
            "linking": self.linking,
            "diagnostics": self.diagnostics,
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
    intents: list[str] | None = None,
    actions: list[str] | None = None,
    entities: dict[str, Any] | None = None,
) -> ParseResultNormalized:
    existing_warning_list = _normalize_warning_list(existing_warnings)
    semantic_message_type = _to_semantic_message_type(message_type)
    semantic_intents = _normalize_intents(intents)
    semantic_actions = _normalize_actions(actions)

    event_type = _map_to_canonical_event_type(
        message_type=message_type,
    )
    semantic_entries = _parse_entries(entry_raw, entities)
    stop_loss = _parse_level(stop_raw, label="SL", kind="STOP_LOSS")
    take_profits = [_parse_level(value, label=f"TP{index + 1}", kind="TAKE_PROFIT") for index, value in enumerate(targets)]
    take_profits = [value for value in take_profits if value is not None]
    confidence = _estimate_confidence(
        message_type=semantic_message_type,
        trader_id=trader_id,
        instrument=instrument,
        side=side,
        entries=semantic_entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        root_ref=root_ref,
    )

    semantic_direction = _map_direction(side)
    if semantic_message_type == "UPDATE" and not semantic_intents:
        semantic_intents = _infer_update_intents_from_legacy_actions(semantic_actions)
    message_subtype = _infer_message_subtype(
        semantic_message_type=semantic_message_type,
        intents=semantic_intents,
        actions=semantic_actions,
    )
    links = _extract_links(raw_text)
    target_refs = _extract_target_refs(raw_text=raw_text, root_ref=root_ref)
    reported_results = _extract_reported_results(raw_text)
    entities_out = entities or {
        "hashtags": _extract_hashtags(raw_text),
        "links": links,
        "time_hint": _extract_time_hint(normalized_text),
    }
    entities_out = _normalize_entities(entities_out)
    entities_out = _enrich_operational_entities(
        entities=entities_out,
        message_type=semantic_message_type,
        intents=semantic_intents,
        actions=semantic_actions,
        raw_text=raw_text,
        normalized_text=normalized_text,
        stop_raw=stop_raw,
        reported_results=reported_results,
    )
    notes_out = _build_notes(notes=notes, raw_text=raw_text, semantic_message_type=semantic_message_type, reported_results=reported_results)

    entry_prices = [value.get("price") for value in semantic_entries if isinstance(value.get("price"), float)]
    averaging_price = _extract_averaging_price(semantic_entries)
    entry_plan_type = _infer_entry_plan_type(semantic_entries)
    entry_structure = _infer_entry_structure(semantic_entries)
    has_averaging_plan = averaging_price is not None
    stop_loss_price = stop_loss.get("price") if stop_loss else None
    take_profit_prices = [value.get("price") for value in take_profits if isinstance(value.get("price"), float)]

    semantic_parser_mode = parser_mode if parser_mode in _PARSER_MODES else _normalize_parser_mode(parser_mode)
    parser_mode_legacy = _to_legacy_parser_mode(semantic_parser_mode)
    v2 = _derive_v2_fields(
        raw_text=raw_text,
        message_type=semantic_message_type,
        intents=semantic_intents,
        actions=semantic_actions,
        entities=entities_out,
        entries=semantic_entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        reported_results=reported_results,
        target_refs=target_refs,
        instrument=instrument,
        side=side,
        market_type=_infer_market_type(instrument),
        root_ref=root_ref,
        parser_mode=semantic_parser_mode,
        parser_used=parser_used,
        confidence=confidence,
        parse_status=parse_status,
    )

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
        status="PARSED_WITH_WARNINGS" if existing_warning_list else parse_status,
        parser_used=parser_used,
        message_type=semantic_message_type,
        intents=semantic_intents,
        actions=semantic_actions,
        message_subtype=message_subtype,
        symbol=instrument,
        direction=semantic_direction,
        entry_main=entry_prices[0] if entry_prices else None,
        entry_mode=_infer_entry_mode(semantic_entries),
        average_entry=averaging_price if averaging_price is not None else (round(sum(entry_prices) / len(entry_prices), 8) if entry_prices else None),
        entry_plan_type=entry_plan_type,
        entry_structure=entry_structure,
        has_averaging_plan=has_averaging_plan,
        stop_loss_price=stop_loss_price if isinstance(stop_loss_price, float) else None,
        take_profit_prices=take_profit_prices,
        target_refs=target_refs,
        reported_results=reported_results,
        notes=notes_out,
        entities=entities_out,
        raw_entities=entities_out,
        parser_mode_legacy=parser_mode_legacy,
        schema_version=v2["schema_version"],
        message_class=v2["message_class"],
        primary_intent=v2["primary_intent"],
        actions_structured=v2["actions_structured"],
        instrument_obj=v2["instrument_obj"],
        position_obj=v2["position_obj"],
        entry_plan=v2["entry_plan"],
        risk_plan=v2["risk_plan"],
        results_v2=v2["results_v2"],
        target_scope=v2["target_scope"],
        linking=v2["linking"],
        diagnostics=v2["diagnostics"],
    )
    validation_warnings = validate_parse_result_normalized(result)
    merged_warnings = _merge_ordered_unique(existing_warning_list, validation_warnings)
    if merged_warnings:
        result.validation_warnings = merged_warnings
        result.status = "PARSED_WITH_WARNINGS"
        result.diagnostics["validation_warnings"] = list(merged_warnings)
    return result


def _derive_v2_fields(
    *,
    raw_text: str,
    message_type: str | None,
    intents: list[str],
    actions: list[str],
    entities: dict[str, Any],
    entries: list[dict[str, Any]],
    stop_loss: dict[str, Any] | None,
    take_profits: list[dict[str, Any]],
    reported_results: list[dict[str, Any]],
    target_refs: list[int],
    instrument: str | None,
    side: str | None,
    market_type: str | None,
    root_ref: int | None,
    parser_mode: str | None,
    parser_used: str | None,
    confidence: float,
    parse_status: str,
) -> dict[str, Any]:
    message_class = _derive_message_class(message_type=message_type)
    primary_intent = _derive_primary_intent(intents)
    target_scope = _derive_target_scope(entities=entities, target_refs=target_refs, root_ref=root_ref, raw_text=raw_text)
    actions_structured = _build_actions_structured(
        intents=intents,
        entities=entities,
        raw_text=raw_text,
        target_scope=target_scope,
        legacy_actions=actions,
    )
    base_asset, quote_asset = _split_symbol_assets(instrument)
    instrument_obj = {
        "symbol": instrument,
        "symbol_raw": entities.get("symbol_raw") or instrument,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "market_type": market_type,
        "exchange_hint": entities.get("exchange_hint"),
    }
    position_obj = {
        "side": side,
        "direction": side,
        "entry_mode": entities.get("entry_mode"),
        "entry_plan_type": entities.get("entry_plan_type"),
        "entry_structure": entities.get("entry_structure"),
        "has_averaging_plan": bool(entities.get("has_averaging_plan")),
    }
    entry_plan = {
        "entries": entries,
        "entry_plan_type": entities.get("entry_plan_type"),
        "entry_structure": entities.get("entry_structure"),
        "has_averaging_plan": bool(entities.get("has_averaging_plan")),
    }
    risk_plan = {
        "stop_loss": stop_loss,
        "take_profits": take_profits,
        "invalidation": entities.get("invalidation") or entities.get("new_stop_level"),
        "risk_hint": entities.get("risk_hint"),
        "risk_percent": entities.get("risk_percent"),
    }
    results_v2 = _derive_results_v2(reported_results, raw_text=raw_text, side=side)
    linking = _derive_linking(target_refs=target_refs, root_ref=root_ref, raw_text=raw_text)
    diagnostics = {
        "parser_mode": parser_mode,
        "parser_used": parser_used,
        "confidence": confidence,
        "parse_status_input": parse_status,
        "intents_count": len(intents),
        "actions_count": len(actions),
    }
    return {
        "schema_version": "2.0",
        "message_class": message_class,
        "primary_intent": primary_intent,
        "actions_structured": actions_structured,
        "instrument_obj": instrument_obj,
        "position_obj": position_obj,
        "entry_plan": entry_plan,
        "risk_plan": risk_plan,
        "results_v2": results_v2,
        "target_scope": target_scope,
        "linking": linking,
        "diagnostics": diagnostics,
    }


def _derive_message_class(*, message_type: str | None) -> str | None:
    # v2 message_class stays aligned with canonical parser message types.
    if message_type in {"NEW_SIGNAL", "UPDATE", "INFO_ONLY", "SETUP_INCOMPLETE", "UNCLASSIFIED"}:
        return message_type
    return None


def _derive_primary_intent(intents: list[str]) -> str | None:
    for intent in intents:
        if isinstance(intent, str) and intent.startswith("NS_"):
            return intent
    for intent in intents:
        if isinstance(intent, str) and intent.startswith("U_"):
            return intent
    for intent in intents:
        if isinstance(intent, str) and intent.strip():
            return intent
    return None


def _build_actions_structured(
    *,
    intents: list[str],
    entities: dict[str, Any],
    raw_text: str,
    target_scope: dict[str, Any],
    legacy_actions: list[str],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for intent in intents:
        if intent == "NS_CREATE_SIGNAL":
            actions.append({"action": "CREATE_SIGNAL"})
        elif intent == "U_MOVE_STOP_TO_BE":
            actions.append({"action": "MOVE_STOP", "new_stop_level": "ENTRY"})
        elif intent == "U_MOVE_STOP":
            actions.append({"action": "MOVE_STOP", "new_stop_level": entities.get("new_stop_level")})
        elif intent == "U_CLOSE_PARTIAL":
            actions.append({"action": "CLOSE_POSITION", "scope": "PARTIAL", "close_fraction": entities.get("close_fraction")})
        elif intent == "U_CLOSE_FULL":
            actions.append({"action": "CLOSE_POSITION", "scope": entities.get("close_scope", "FULL")})
        elif intent == "U_TP_HIT":
            actions.append({"action": "TAKE_PROFIT", "target": entities.get("hit_target", "TP")})
        elif intent == "U_STOP_HIT":
            actions.append({"action": "CLOSE_POSITION", "target": "STOP"})
        elif intent == "U_CANCEL_PENDING_ORDERS":
            actions.append({"action": "CANCEL_PENDING", "scope": entities.get("cancel_scope", "ALL_PENDING_ENTRIES")})
        elif intent == "U_MARK_FILLED":
            actions.append({"action": "MARK_FILLED", "fill_state": entities.get("fill_state", "FILLED")})
        elif intent == "U_REPORT_FINAL_RESULT":
            actions.append({"action": "REPORT_RESULT", "mode": entities.get("result_mode", "TEXT_SUMMARY")})
    if not actions:
        actions = [{"action": action, "kind": "legacy_action"} for action in legacy_actions if isinstance(action, str) and action]
    for item in actions:
        item.setdefault("target_scope", target_scope)
        item.setdefault("raw_fragment", raw_text[:160])
    return actions


def _split_symbol_assets(symbol: str | None) -> tuple[str | None, str | None]:
    if not isinstance(symbol, str) or not symbol:
        return None, None
    upper = symbol.upper()
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if upper.endswith(quote) and len(upper) > len(quote):
            return upper[: -len(quote)], quote
    return upper, None


def _derive_target_scope(*, entities: dict[str, Any], target_refs: list[int], root_ref: int | None, raw_text: str) -> dict[str, Any]:
    links = _extract_links(raw_text)
    extracted_refs = _extract_target_refs(raw_text=raw_text, root_ref=root_ref)
    close_scope = entities.get("close_scope")
    if close_scope in {"ALL_LONGS", "ALL_SHORTS"}:
        kind = "portfolio_side"
        scope = close_scope
    elif _extract_hashtags(raw_text):
        kind = "signal_group"
        scope = "hashtag"
    else:
        kind = "signal"
        scope = "single" if (target_refs or root_ref is not None or extracted_refs) else "unknown"
    return {
        "kind": kind,
        "scope": scope,
        "target_refs": target_refs,
        "root_ref": root_ref,
        "link_count": len(links),
        "extracted_target_refs": extracted_refs,
    }


def _derive_linking(*, target_refs: list[int], root_ref: int | None, raw_text: str) -> dict[str, Any]:
    links = _extract_links(raw_text)
    extracted_refs = _extract_target_refs(raw_text=raw_text, root_ref=root_ref)
    strategy = "reply_or_link" if (root_ref is not None or links or target_refs) else "unresolved"
    return {
        "targeted": bool(target_refs or root_ref is not None or extracted_refs),
        "has_target_refs": bool(target_refs),
        "target_ref_count": len(target_refs),
        "root_ref": root_ref,
        "link_count": len(links),
        "extracted_target_refs": extracted_refs,
        "strategy": strategy,
    }


def _derive_results_v2(reported_results: list[dict[str, Any]], *, raw_text: str, side: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    fragments: dict[tuple[str, float], str] = {}
    for match in _RESULT_R_RE.finditer(raw_text):
        symbol = str(match.group("symbol") or "").upper()
        value = _to_float(match.group("value"))
        if symbol and value is not None:
            fragments[(symbol, float(value))] = match.group(0)
    for item in reported_results:
        symbol = item.get("symbol")
        value = item.get("r_multiple")
        unit = item.get("unit") or "R"
        sym_norm = str(symbol).upper() if isinstance(symbol, str) else None
        raw_fragment = None
        if sym_norm is not None and isinstance(value, (int, float)):
            raw_fragment = fragments.get((sym_norm, float(value)))
        out.append(
            {
                "symbol": sym_norm,
                "value": value,
                "unit": str(unit).upper() if unit is not None else None,
                "direction": side,
                "raw_fragment": raw_fragment,
                "result_type": "R_MULTIPLE" if str(unit).upper() == "R" else "UNKNOWN",
            }
        )
    return out


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
        if not result.intents:
            warnings.append("normalized_update_missing_intents")
        if not result.actions:
            warnings.append("normalized_update_missing_actions")
        if result.root_ref is None and not result.target_refs:
            warnings.append("normalized_update_missing_target_ref")
        for intent in result.intents:
            if intent not in _UPDATE_INTENTS:
                warnings.append(f"normalized_update_intent_unknown:{intent}")

    if result.message_type == "INFO_ONLY":
        has_notes = any((note or "").strip() for note in result.notes)
        if not result.reported_results and not has_notes and not result.target_refs:
            warnings.append("normalized_info_only_missing_supporting_content")

    return warnings


def _to_semantic_message_type(message_type: str) -> str | None:
    if message_type in _MESSAGE_TYPES:
        return message_type
    if message_type == "INVALID":
        return "UNCLASSIFIED"
    return None


def _map_to_canonical_event_type(
    *,
    message_type: str,
) -> str:
    # Legacy projection only: keep a stable backward-compatible envelope
    # without driving semantic decisions.
    if message_type == "NEW_SIGNAL":
        return "NEW_SIGNAL"
    if message_type == "SETUP_INCOMPLETE":
        return "SETUP_INCOMPLETE"
    if message_type == "INFO_ONLY":
        return "INFO_ONLY"
    if message_type == "UPDATE":
        return "UPDATE"
    if message_type == "UNCLASSIFIED":
        return "INVALID"
    return "INVALID"


def _infer_message_subtype(
    *,
    semantic_message_type: str | None,
    intents: list[str],
    actions: list[str],
) -> str | None:
    if semantic_message_type == "UPDATE":
        if intents:
            return intents[0]
        if actions:
            return "ACTIONABLE_UPDATE"
    if semantic_message_type == "INFO_ONLY":
        return "RESULT_REPORT" if actions == [] else None
    return None


def _estimate_confidence(
    *,
    message_type: str | None,
    trader_id: str | None,
    instrument: str | None,
    side: str | None,
    entries: list[dict[str, Any]],
    stop_loss: dict[str, Any] | None,
    take_profits: list[dict[str, Any]],
    root_ref: int | None,
) -> float:
    base_by_message_type = {
        "NEW_SIGNAL": 0.92,
        "UPDATE": 0.78,
        "INFO_ONLY": 0.75,
        "SETUP_INCOMPLETE": 0.55,
        "UNCLASSIFIED": 0.2,
    }
    confidence = base_by_message_type.get(message_type, 0.5)
    if trader_id is None:
        confidence -= 0.15
    if instrument is None:
        confidence -= 0.1
    if side is None and message_type == "NEW_SIGNAL":
        confidence -= 0.1
    if message_type == "NEW_SIGNAL":
        if not entries:
            confidence -= 0.15
        if stop_loss is None:
            confidence -= 0.15
        if not take_profits:
            confidence -= 0.1
    if message_type == "UPDATE" and root_ref is None:
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


def _parse_entries(raw: str | None, entities: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    canonical_entries = _extract_canonical_entries_from_entities(entities)
    if canonical_entries:
        return canonical_entries
    if raw is None:
        return []
    chunks = [part.strip() for part in raw.split("-") if part.strip()]
    if not chunks:
        return []
    values: list[dict[str, Any]] = []
    for index, value in enumerate(chunks):
        parsed = _parse_level(value, label=f"E{index + 1}", kind="ENTRY")
        if parsed is not None:
            values.append(
                {
                    **parsed,
                    "sequence": index + 1,
                    "role": "PRIMARY" if index == 0 else "AVERAGING",
                    "order_type": "UNKNOWN",
                    "raw_label": "ENTRY" if index == 0 else "AVERAGING",
                    "source_style": "SINGLE" if len(chunks) == 1 else "UNKNOWN",
                    "is_optional": index > 0,
                }
            )
    return values


def _extract_canonical_entries_from_entities(entities: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entities, dict):
        return []
    raw_entries = entities.get("entry_plan_entries")
    if not isinstance(raw_entries, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw_entries, start=1):
        if not isinstance(item, dict):
            continue
        price = item.get("price")
        if not isinstance(price, (int, float)):
            continue
        sequence = item.get("sequence")
        role = item.get("role")
        order_type = item.get("order_type")
        raw_label = item.get("raw_label")
        source_style = item.get("source_style")
        is_optional = item.get("is_optional")
        out.append(
            {
                "label": f"E{int(sequence) if isinstance(sequence, int) else index}",
                "price": float(price),
                "kind": "ENTRY",
                "raw": str(float(price)),
                "sequence": int(sequence) if isinstance(sequence, int) else index,
                "role": role if isinstance(role, str) else ("PRIMARY" if index == 1 else "AVERAGING"),
                "order_type": order_type if isinstance(order_type, str) else "UNKNOWN",
                "raw_label": raw_label if isinstance(raw_label, str) else None,
                "source_style": source_style if isinstance(source_style, str) else "UNKNOWN",
                "is_optional": bool(is_optional) if isinstance(is_optional, bool) else index > 1,
            }
        )
    return out


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


def _normalize_intents(intents: list[str] | None) -> list[str]:
    if not intents:
        return []
    return _unique([item.strip() for item in intents if isinstance(item, str) and item.strip()])


def _normalize_actions(actions: list[str] | None) -> list[str]:
    if not actions:
        return []
    return _unique([item.strip() for item in actions if isinstance(item, str) and item.strip()])


def _normalize_warning_list(warnings: list[str] | None) -> list[str]:
    if not warnings:
        return []
    out: list[str] = []
    for warning in warnings:
        if not isinstance(warning, str):
            continue
        value = warning.strip()
        if value:
            out.append(value)
    return _unique(out)


def _merge_ordered_unique(primary: list[str], secondary: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in [*primary, *secondary]:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_entities(value: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "hashtags": [],
        "links": [],
        "time_hint": None,
        "new_stop_level": None,
        "close_fraction": None,
        "close_scope": None,
        "hit_target": None,
        "fill_state": None,
            "result_mode": None,
            "cancel_scope": None,
            "entry_plan_entries": [],
            "entry_plan_type": None,
            "entry_structure": None,
            "has_averaging_plan": False,
        }
    if not isinstance(value, dict):
        return base
    out = dict(base)
    out.update(value)
    return out


def _enrich_operational_entities(
    *,
    entities: dict[str, Any],
    message_type: str | None,
    intents: list[str],
    actions: list[str],
    raw_text: str,
    normalized_text: str,
    stop_raw: str | None,
    reported_results: list[dict[str, Any]],
) -> dict[str, Any]:
    out = dict(entities)
    if "U_REPORT_FINAL_RESULT" in intents:
        out["result_mode"] = "R_MULTIPLE" if reported_results else "TEXT_SUMMARY"
    if message_type != "UPDATE":
        return out

    if "U_MOVE_STOP_TO_BE" in intents:
        out["new_stop_level"] = "ENTRY"
    elif "U_MOVE_STOP" in intents and stop_raw:
        out["new_stop_level"] = stop_raw

    if "U_CLOSE_FULL" in intents:
        out["close_scope"] = "FULL"
    if "U_CLOSE_PARTIAL" in intents:
        out["close_scope"] = "PARTIAL"
        out["close_fraction"] = _extract_close_fraction(raw_text=raw_text, normalized_text=normalized_text)

    if "U_TP_HIT" in intents:
        out["hit_target"] = _extract_hit_target(raw_text=raw_text) or "TP"
    if "U_STOP_HIT" in intents:
        out["hit_target"] = "STOP"

    if "U_MARK_FILLED" in intents:
        out["fill_state"] = "FILLED"

    if "U_CANCEL_PENDING_ORDERS" in intents or "ACT_CANCEL_ALL_PENDING_ENTRIES" in actions:
        out["cancel_scope"] = "ALL_PENDING_ENTRIES"

    return out


def _extract_close_fraction(*, raw_text: str, normalized_text: str) -> float | None:
    match = _PERCENT_RE.search(raw_text)
    if match:
        text = match.group("value").replace(",", ".")
        try:
            value = float(text)
        except ValueError:
            value = None
        if value is not None:
            return round(max(0.0, min(1.0, value / 100.0)), 6)
    if any(marker in normalized_text for marker in ("half", "1/2", "50-50")):
        return 0.5
    return None


def _extract_hit_target(*, raw_text: str) -> str | None:
    match = _TP_INDEX_RE.search(raw_text)
    if match:
        return f"TP{match.group('index')}"
    return None


def _infer_update_intents_from_legacy_actions(actions: list[str]) -> list[str]:
    mapped: list[str] = []
    for action in actions:
        if action == "ACT_MOVE_STOP_LOSS":
            mapped.append("U_MOVE_STOP")
        elif action == "ACT_CANCEL_ALL_PENDING_ENTRIES":
            mapped.append("U_CANCEL_PENDING_ORDERS")
        elif action == "ACT_CLOSE_FULL":
            mapped.append("U_CLOSE_FULL")
        elif action == "ACT_MARK_TP_HIT":
            mapped.append("U_TP_HIT")
        elif action == "ACT_MARK_STOP_HIT":
            mapped.append("U_STOP_HIT")
    return _unique(mapped)


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
    if any(str(entry.get("order_type") or "").upper() == "MARKET" for entry in entries):
        return "MARKET"
    if len(entries) > 1:
        return "RANGE"
    return "SINGLE"


def _extract_averaging_price(entries: list[dict[str, Any]]) -> float | None:
    for item in entries:
        role = str(item.get("role") or "").upper()
        if role != "AVERAGING":
            continue
        price = item.get("price")
        if isinstance(price, (int, float)):
            return float(price)
    return None


def _infer_entry_structure(entries: list[dict[str, Any]]) -> str | None:
    if not entries:
        return None
    if len(entries) > 1:
        return "TWO_STEP"
    return "SINGLE"


def _infer_entry_plan_type(entries: list[dict[str, Any]]) -> str | None:
    if not entries:
        return None
    first_order_type = str(entries[0].get("order_type") or "").upper()
    has_averaging = _extract_averaging_price(entries) is not None
    if has_averaging:
        if first_order_type == "MARKET":
            return "MARKET_WITH_LIMIT_AVERAGING"
        if first_order_type == "LIMIT":
            return "LIMIT_WITH_LIMIT_AVERAGING"
        return "UNKNOWN"
    if first_order_type == "MARKET":
        return "SINGLE_MARKET"
    if first_order_type == "LIMIT":
        return "SINGLE_LIMIT"
    return "UNKNOWN"


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
