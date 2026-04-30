"""Flatten a ParsedMessage JSON dict (schema_version='parsed_message_v1') to a CSV row."""

from __future__ import annotations

from typing import Any

from parser_test.reporting.report_schema_v1 import schema_for_scope_v1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_report_row_v1(
    *,
    raw_message_id: int | str,
    parsed_message: dict[str, Any],
    scope: str,
) -> dict[str, str]:
    """Return an ordered CSV row for the given ParsedMessage dict and report scope."""
    schema = schema_for_scope_v1(scope)
    raw_context = _dict(parsed_message.get("raw_context"))
    signal = _dict(parsed_message.get("signal"))
    targeting = _dict(parsed_message.get("targeting"))
    intents: list[dict[str, Any]] = _list(parsed_message.get("intents"))
    warnings: list[Any] = _list(parsed_message.get("warnings"))
    diagnostics = _dict(parsed_message.get("diagnostics"))

    confirmed = [i for i in intents if _scalar(i.get("status")) == "CONFIRMED"]
    invalid = [i for i in intents if _scalar(i.get("status")) == "INVALID"]

    row: dict[str, str] = {
        # COMMON
        "raw_message_id": _scalar(raw_message_id),
        "reply_to_message_id": _scalar(raw_context.get("reply_to_message_id")),
        "raw_text": _scalar(raw_context.get("raw_text")),
        "parse_status": _scalar(parsed_message.get("parse_status")),
        "primary_class": _scalar(parsed_message.get("primary_class")),
        # UPDATE / REPORT / INFO / UNCLASSIFIED
        "warnings_summary": _join(warnings),
        "primary_intent": _scalar(parsed_message.get("primary_intent")),
        "intents_confirmed": _join(_intent_names(confirmed)),
        "intents_candidate": _join(_intent_names(intents)),
        "intents_invalid": _join(_intent_names(invalid)),
        "intents_invalid_reason": _join(_invalid_reasons(invalid)),
        "target_scope_scope": _target_scope_scope(targeting),
        "target_refs": _target_refs(targeting),
        "new_stop_level": _new_stop_level(confirmed),
        "close_scope": _close_scope(confirmed),
        "close_fraction": _close_fraction(confirmed),
        "hit_target": _hit_target(confirmed),
        "fill_state": _fill_state(confirmed),
        "cancel_scope": _cancel_scope(confirmed),
        "reported_results": _reported_results(confirmed),
        # NEW_SIGNAL / SETUP_INCOMPLETE
        "symbol": _scalar(signal.get("symbol")),
        "direction": _scalar(signal.get("side")),
        "risk_hint_value": _risk_hint_value(signal),
        "market_type": _scalar(diagnostics.get("market_type")),
        "completeness": _scalar(signal.get("completeness")),
        "entry_plan_type": _scalar(diagnostics.get("entry_plan_type")),
        "entry_structure": _scalar(signal.get("entry_structure")),
        "entry_count": str(len(_list(signal.get("entries")))),
        "entries_summary": _entries_summary(_list(signal.get("entries"))),
        "stop_loss_price": _stop_loss_price(signal),
        "tp_prices": _tp_prices(_list(signal.get("take_profits"))),
        "signal_id": _signal_id(
            raw_message_id=raw_message_id,
            primary_class=_scalar(parsed_message.get("primary_class")),
            targeting=targeting,
        ),
    }

    ordered: dict[str, str] = {}
    for col in schema.columns:
        ordered[col] = row.get(col, "")
    return ordered


# ---------------------------------------------------------------------------
# Intent helpers
# ---------------------------------------------------------------------------

def _intent_names(intents: list[dict[str, Any]]) -> list[str]:
    return [s for i in intents if (s := _scalar(i.get("type")))]


def _invalid_reasons(invalid: list[dict[str, Any]]) -> list[str]:
    return [s for i in invalid if (s := _scalar(i.get("invalid_reason")))]


# ---------------------------------------------------------------------------
# Targeting helpers
# ---------------------------------------------------------------------------

def _target_scope_scope(targeting: dict[str, Any]) -> str:
    scope_obj = _dict(targeting.get("scope"))
    kind = _scalar(scope_obj.get("kind"))
    return kind


def _target_refs(targeting: dict[str, Any]) -> str:
    refs: list[dict[str, Any]] = _list(targeting.get("refs"))
    parts = [_scalar(r.get("value")) for r in refs if _scalar(r.get("value"))]
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Intent-entity field extractors
# ---------------------------------------------------------------------------

def _entities_for_type(confirmed: list[dict[str, Any]], intent_type: str) -> dict[str, Any]:
    for intent in confirmed:
        if _scalar(intent.get("type")) == intent_type:
            return _dict(intent.get("entities"))
    return {}


def _new_stop_level(confirmed: list[dict[str, Any]]) -> str:
    # MOVE_STOP_TO_BE → "BE"
    be = _entities_for_type(confirmed, "MOVE_STOP_TO_BE")
    if be is not None and _has_intent_type(confirmed, "MOVE_STOP_TO_BE"):
        return "BE"
    # MOVE_STOP → price or TP level
    ents = _entities_for_type(confirmed, "MOVE_STOP")
    if ents:
        price_obj = _dict(ents.get("new_stop_price"))
        if price_obj:
            return _format_float(price_obj.get("value"))
        tp_level = ents.get("stop_to_tp_level")
        if tp_level is not None:
            return f"TP{_scalar(tp_level)}"
    return ""


def _has_intent_type(confirmed: list[dict[str, Any]], intent_type: str) -> bool:
    return any(_scalar(i.get("type")) == intent_type for i in confirmed)


def _close_scope(confirmed: list[dict[str, Any]]) -> str:
    if _has_intent_type(confirmed, "CLOSE_FULL"):
        return "FULL"
    if _has_intent_type(confirmed, "CLOSE_PARTIAL"):
        return "PARTIAL"
    if _has_intent_type(confirmed, "INVALIDATE_SETUP"):
        return "INVALIDATED"
    return ""


def _close_fraction(confirmed: list[dict[str, Any]]) -> str:
    ents = _entities_for_type(confirmed, "CLOSE_PARTIAL")
    if ents:
        frac = ents.get("fraction")
        if frac is not None:
            return _format_float(frac)
    return ""


def _hit_target(confirmed: list[dict[str, Any]]) -> str:
    ents_tp = _entities_for_type(confirmed, "TP_HIT")
    if ents_tp is not None and _has_intent_type(confirmed, "TP_HIT"):
        level = ents_tp.get("level")
        return f"TP{_scalar(level)}" if level is not None else "TP"
    if _has_intent_type(confirmed, "SL_HIT"):
        return "SL"
    if _has_intent_type(confirmed, "EXIT_BE"):
        return "BE"
    return ""


def _fill_state(confirmed: list[dict[str, Any]]) -> str:
    ents = _entities_for_type(confirmed, "ENTRY_FILLED")
    if ents is not None and _has_intent_type(confirmed, "ENTRY_FILLED"):
        level = ents.get("level")
        return f"FILL{_scalar(level)}" if level is not None else "FILLED"
    return ""


def _cancel_scope(confirmed: list[dict[str, Any]]) -> str:
    ents = _entities_for_type(confirmed, "CANCEL_PENDING")
    if ents is not None and _has_intent_type(confirmed, "CANCEL_PENDING"):
        scope = ents.get("scope")
        return _scalar(scope) if scope is not None else "ALL_PENDING"
    return ""


def _reported_results(confirmed: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for intent_type in ("REPORT_FINAL_RESULT", "REPORT_PARTIAL_RESULT"):
        for intent in confirmed:
            if _scalar(intent.get("type")) != intent_type:
                continue
            ents = _dict(intent.get("entities"))
            result_obj = _dict(ents.get("result"))
            if not result_obj:
                parts.append(intent_type)
                continue
            value = result_obj.get("value")
            unit = _scalar(result_obj.get("unit")) or "R"
            text = _scalar(result_obj.get("text"))
            if value is not None:
                label = f"{_format_float(value)}{unit}"
            elif text:
                label = text
            else:
                label = intent_type
            parts.append(label)
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Signal field extractors
# ---------------------------------------------------------------------------

def _risk_hint_value(signal: dict[str, Any]) -> str:
    risk_hint = _dict(signal.get("risk_hint"))
    if not risk_hint:
        return ""
    value = risk_hint.get("value")
    if value is not None:
        return _format_float(value)
    return ""


def _stop_loss_price(signal: dict[str, Any]) -> str:
    stop_loss = _dict(signal.get("stop_loss"))
    if not stop_loss:
        return ""
    price_obj = _dict(stop_loss.get("price"))
    if not price_obj:
        return ""
    return _format_float(price_obj.get("value"))


def _tp_prices(take_profits: list[dict[str, Any]]) -> str:
    prices: list[str] = []
    for tp in take_profits:
        price_obj = _dict(tp.get("price"))
        if price_obj:
            v = _format_float(price_obj.get("value"))
            if v:
                prices.append(v)
    return " | ".join(prices)


def _entries_summary(entries: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for entry in entries:
        seq = _scalar(entry.get("sequence"))
        entry_type = _scalar(entry.get("entry_type"))
        price_obj = _dict(entry.get("price"))
        price_str = _format_float(price_obj.get("value")) if price_obj else ""
        role = _scalar(entry.get("role"))
        chunk = f"{seq}:{entry_type}"
        if role and role not in ("UNKNOWN", ""):
            chunk += f":{role}"
        if price_str:
            chunk += f"@{price_str}"
        parts.append(chunk)
    return " | ".join(parts)


def _signal_id(
    *,
    raw_message_id: int | str,
    primary_class: str,
    targeting: dict[str, Any],
) -> str:
    if primary_class == "SIGNAL":
        return _scalar(raw_message_id)
    refs: list[dict[str, Any]] = _list(targeting.get("refs"))
    if refs:
        v = _scalar(refs[0].get("value"))
        if v:
            return v
    return ""


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _format_float(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    if isinstance(value, str):
        return value
    return _scalar(value)


def _join(items: list[Any]) -> str:
    return " | ".join(_scalar(i) for i in items if _scalar(i))


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}
