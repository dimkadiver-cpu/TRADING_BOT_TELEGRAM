from __future__ import annotations

import json
from typing import Any

from parser_test.reporting.report_schema import schema_for_scope
from src.parser.action_builders.canonical_v2 import derive_legacy_actions


def build_report_row(
    *,
    raw_message_id: int | str,
    parse_status: str | None,
    reply_to_message_id: int | str | None,
    raw_text: str | None,
    warning_text: str | None,
    normalized: dict[str, Any] | None,
    scope: str,
    include_legacy_debug: bool = False,
    include_json_debug: bool = False,
) -> dict[str, str]:
    normalized_obj = normalized if isinstance(normalized, dict) else {}
    schema = schema_for_scope(
        scope,
        include_legacy_debug=include_legacy_debug,
        include_json_debug=include_json_debug,
    )
    derived = _derive_fields(normalized_obj, raw_message_id=raw_message_id)
    actions_structured = _as_list(normalized_obj.get("actions_structured"))
    row: dict[str, str] = {
        "raw_message_id": _scalar(raw_message_id),
        "parse_status": _scalar(parse_status),
        "reply_to_message_id": "" if reply_to_message_id is None else _scalar(reply_to_message_id),
        "raw_text": _scalar(raw_text),
        "warning_text": _scalar(warning_text),
        "warnings_summary": _join_list(normalized_obj.get("warnings")),
        "primary_intent": _scalar(
            normalized_obj.get("primary_intent")
            or _intent_name(_first_value(normalized_obj.get("intents")))
        ),
        "intents": " | ".join(
            n
            for item in _coerce_list(normalized_obj.get("intents"))
            if (n := _intent_name(item))
        ),
        "action_types": _summarize_action_types(actions_structured),
        "actions_structured_summary": _summarize_actions_structured(actions_structured),
    }
    if include_legacy_debug:
        row["legacy_actions"] = _join_list(normalized_obj.get("actions") or derive_legacy_actions(actions_structured))
    if include_json_debug:
        row["normalized_json_debug"] = json.dumps(normalized_obj, ensure_ascii=False)
    for column in schema.columns:
        if column in row:
            continue
        row[column] = _scalar(derived.get(column))
    ordered: dict[str, str] = {}
    for column in schema.columns:
        ordered[column] = row.get(column, "")
    return ordered


def _derive_fields(normalized: dict[str, Any], *, raw_message_id: int | str | None = None) -> dict[str, str]:
    entities = normalized.get("entities") if isinstance(normalized.get("entities"), dict) else {}
    diagnostics = normalized.get("diagnostics") if isinstance(normalized.get("diagnostics"), dict) else {}
    target_scope = normalized.get("target_scope") if isinstance(normalized.get("target_scope"), dict) else {}
    entry_plan = normalized.get("entry_plan") if isinstance(normalized.get("entry_plan"), dict) else {}
    linking = normalized.get("linking") if isinstance(normalized.get("linking"), dict) else {}
    risk_plan = normalized.get("risk_plan") if isinstance(normalized.get("risk_plan"), dict) else {}
    results_v2 = normalized.get("results_v2") if isinstance(normalized.get("results_v2"), list) else []
    actions_structured = _as_list(normalized.get("actions_structured"))

    entries = _coerce_entries(
        _entries_from_entity_range(entities),
        _entries_from_actions(actions_structured),
        entry_plan.get("entries") if isinstance(entry_plan.get("entries"), list) else None,
        normalized.get("entries"),
        entities.get("entry_plan_entries"),
    )
    take_profits = _coerce_take_profits(
        entities.get("take_profits"),
        _take_profits_from_actions(actions_structured),
        risk_plan.get("take_profits") if isinstance(risk_plan.get("take_profits"), list) else None,
        normalized.get("take_profit_prices"),
        entities.get("take_profit_prices"),
    )
    target_refs = _canonical_target_refs(
        normalized=normalized,
        actions_structured=actions_structured,
        target_scope=target_scope,
        linking=linking,
        entities=entities,
    )
    signal_id = _derive_signal_id(
        normalized=normalized,
        raw_message_id=raw_message_id,
        target_refs=target_refs,
        target_scope=target_scope,
        linking=linking,
        entities=entities,
    )
    target_scope_kind, target_scope_value = _derive_target_scope_summary(normalized=normalized, target_scope=target_scope, target_refs=target_refs)
    new_stop_level = _first_action_field(normalized, "new_stop_level") or _scalar(entities.get("new_stop_level"))
    close_scope = _first_action_field(normalized, "close_scope") or _scalar(entities.get("close_scope"))
    close_fraction = _first_action_field(normalized, "close_fraction") or _format_float(entities.get("close_fraction"))
    hit_target = _first_action_field(normalized, "hit_target") or _scalar(entities.get("hit_target"))
    fill_state = _first_action_field(normalized, "fill_state") or _scalar(entities.get("fill_state"))
    result_mode = _first_action_field(normalized, "result_mode") or _scalar(entities.get("result_mode"))
    cancel_scope = _first_action_field(normalized, "cancel_scope") or _scalar(entities.get("cancel_scope"))
    reported_results = _coerce_reported_results(results_v2, normalized.get("reported_results"))
    reported_profit_percent = _first_numeric_value(
        entities.get("reported_profit_percent"),
        _reported_metric_from_results(results_v2, key="profit_percent"),
    )
    reported_leverage_hint = _first_numeric_value(
        entities.get("reported_leverage_hint"),
        _reported_metric_from_results(results_v2, key="leverage_hint"),
    )
    linking_strategy = _scalar(linking.get("strategy") or linking.get("mode") or "")

    # Completeness flags used by the SETUP_INCOMPLETE schema.
    # True = that field is absent in the parse result (= what made the signal incomplete).
    missing_stop_flag = normalized.get("stop_loss_price") is None and _risk_stop_price(risk_plan) is None
    missing_entry_flag = len(entries) == 0 and normalized.get("entry_main") is None
    missing_tp_flag = len(take_profits) == 0

    derived: dict[str, str] = {
        "completeness": _scalar(normalized.get("completeness")),
        "missing_fields": _join_list(normalized.get("missing_fields")),
        "event_type": _scalar(normalized.get("event_type")),
        "message_class": _scalar(normalized.get("message_class")),
        "symbol": _scalar(normalized.get("symbol") or entities.get("symbol")),
        "direction": _scalar(normalized.get("direction") or entities.get("side") or entities.get("direction")),
        "market_type": _scalar(normalized.get("market_type")),
        "status": _scalar(normalized.get("status")),
        "confidence": _format_float(normalized.get("confidence")),
        "parser_used": _scalar(normalized.get("parser_used")),
        "parser_mode": _scalar(normalized.get("parser_mode")),
        "entry_plan_type": _scalar(normalized.get("entry_plan_type") or entry_plan.get("entry_plan_type") or entities.get("entry_plan_type")),
        "entry_structure": _scalar(normalized.get("entry_structure") or entry_plan.get("entry_structure") or entities.get("entry_structure")),
        "has_averaging_plan": _bool_scalar(normalized.get("has_averaging_plan") if normalized.get("has_averaging_plan") is not None else entry_plan.get("has_averaging_plan") if entry_plan else entities.get("has_averaging_plan")),
        "entry_count": str(len(entries)),
        "entries_summary": _summarize_entries(entries),
        "stop_loss_price": _format_float(_first_numeric_value(entities.get("stop_loss"), normalized.get("stop_loss_price"), _risk_stop_price(risk_plan))),
        "tp_prices": _join_number_list(take_profits),
        "tp_count": str(len(take_profits)),
        "signal_id": signal_id,
        "target_scope_kind": target_scope_kind,
        "target_scope_scope": target_scope_value,
        "target_refs": _join_number_list(target_refs),
        "target_refs_count": str(len(target_refs)),
        "linking_strategy": linking_strategy,
        "new_stop_level": new_stop_level,
        "close_scope": close_scope,
        "close_fraction": close_fraction,
        "hit_target": hit_target,
        "fill_state": fill_state,
        "result_mode": result_mode,
        "cancel_scope": cancel_scope,
        "reported_results": reported_results,
        "reported_profit_percent": _format_float(reported_profit_percent),
        "reported_leverage_hint": _format_float(reported_leverage_hint),
        "notes_summary": _join_list(normalized.get("notes") or normalized.get("parsing_notes")),
        "links_count": str(len(_coerce_list(entities.get("links")) or _coerce_list(normalized.get("links")))),
        "hashtags_count": str(len(_coerce_list(entities.get("hashtags")) or _coerce_list(normalized.get("hashtags")))),
        "validation_warning_count": str(len(_coerce_list(normalized.get("validation_warnings")))),
        "diagnostics_summary": _summarize_diagnostics(diagnostics),
        "missing_stop_flag": _bool_scalar(missing_stop_flag),
        "missing_tp_flag": _bool_scalar(missing_tp_flag),
        "missing_entry_flag": _bool_scalar(missing_entry_flag),
    }
    return derived


def _canonical_target_refs(
    *,
    normalized: dict[str, Any],
    actions_structured: list[Any],
    target_scope: dict[str, Any],
    linking: dict[str, Any],
    entities: dict[str, Any],
) -> list[int]:
    sources: list[list[int]] = []
    sources.append(_extract_action_target_refs(actions_structured))
    sources.append(_extract_int_list(target_scope.get("target_refs")))
    sources.append(_extract_int_list(target_scope.get("extracted_target_refs")))
    sources.append(_extract_int_list(linking.get("target_refs")))
    sources.append(_extract_int_list(linking.get("extracted_target_refs")))
    sources.append(_extract_int_list(normalized.get("target_refs")))
    sources.append(_extract_int_list(entities.get("target_refs")))
    sources.append(_extract_int_list([normalized.get("root_ref")]))
    sources.append(_extract_int_list([normalized.get("target_ref")]))
    for source in sources:
        if source:
            return source
    return []


def _derive_signal_id(
    *,
    normalized: dict[str, Any],
    raw_message_id: int | str | None,
    target_refs: list[int],
    target_scope: dict[str, Any],
    linking: dict[str, Any],
    entities: dict[str, Any],
) -> str:
    entity_signal_id = entities.get("signal_id")
    if entity_signal_id is not None and _scalar(entity_signal_id):
        return _scalar(entity_signal_id)
    if _scalar(normalized.get("message_type")) == "NEW_SIGNAL":
        return _scalar(normalized.get("root_ref") or raw_message_id)
    for candidate in (
        normalized.get("root_ref"),
        target_scope.get("root_ref") if isinstance(target_scope, dict) else None,
        linking.get("root_ref") if isinstance(linking, dict) else None,
        target_refs[0] if len(target_refs) == 1 else None,
    ):
        if candidate is not None and _scalar(candidate):
            return _scalar(candidate)
    return ""


def _derive_target_scope_summary(
    *,
    normalized: dict[str, Any],
    target_scope: dict[str, Any],
    target_refs: list[int],
) -> tuple[str, str]:
    kind = _scalar(target_scope.get("kind") or "")
    scope = _scalar(target_scope.get("scope") or "")
    if len(target_refs) > 1 and kind == "signal" and scope == "single":
        return "signal_group", "multiple"
    if kind or scope:
        return kind, scope
    if len(target_refs) > 1:
        return "signal_group", "multiple"
    if target_refs:
        return "signal", "single"
    if _scalar(normalized.get("message_type")) == "UPDATE":
        return "signal", "unknown"
    return "", ""


def _first_action_field(normalized: dict[str, Any], key: str) -> str:
    actions_structured = _as_list(normalized.get("actions_structured"))
    for action in actions_structured:
        if not isinstance(action, dict):
            continue
        value = action.get(key)
        rendered = _scalar(value)
        if rendered:
            return rendered
    return ""


def _reported_metric_from_results(results_v2: list[Any], *, key: str) -> Any:
    for item in results_v2:
        if not isinstance(item, dict):
            continue
        if key == "profit_percent" and str(item.get("unit") or "").upper() == "PERCENT":
            return item.get("value")
        if key == "leverage_hint" and item.get("leverage_hint") is not None:
            return item.get("leverage_hint")
    return None


def _risk_stop_price(risk_plan: dict[str, Any]) -> Any:
    stop_loss = risk_plan.get("stop_loss") if isinstance(risk_plan, dict) else None
    if isinstance(stop_loss, dict):
        return stop_loss.get("price")
    return None


def _coerce_entries(*values: Any) -> list[dict[str, Any]]:
    for value in values:
        items = _coerce_list(value)
        if not items:
            continue
        entries: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                entries.append(item)
        if entries:
            return entries
    return []


def _entries_from_entity_range(entities: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(entities, dict):
        return []
    entry_range = entities.get("entry")
    if isinstance(entry_range, list):
        prices = [float(item) for item in entry_range if isinstance(item, (int, float))]
        if len(prices) >= 2:
            return [
                {"role": "RANGE_LOW", "order_type": "LIMIT", "price": prices[0]},
                {"role": "RANGE_HIGH", "order_type": "LIMIT", "price": prices[1]},
            ]
    low = entities.get("entry_range_low")
    high = entities.get("entry_range_high")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return [
            {"role": "RANGE_LOW", "order_type": "LIMIT", "price": float(low)},
            {"role": "RANGE_HIGH", "order_type": "LIMIT", "price": float(high)},
        ]
    return []


def _entries_from_actions(actions_structured: list[Any]) -> list[dict[str, Any]]:
    for action in actions_structured:
        if not isinstance(action, dict):
            continue
        entries = action.get("entries")
        if isinstance(entries, list) and any(isinstance(item, dict) for item in entries):
            return [item for item in entries if isinstance(item, dict)]
        entry_range = action.get("entry_range")
        if isinstance(entry_range, list):
            prices = [float(item) for item in entry_range if isinstance(item, (int, float))]
            if len(prices) >= 2:
                return [
                    {"role": "RANGE_LOW", "order_type": "LIMIT", "price": prices[0]},
                    {"role": "RANGE_HIGH", "order_type": "LIMIT", "price": prices[1]},
                ]
    return []


def _coerce_take_profits(*values: Any) -> list[float]:
    for value in values:
        items = _coerce_list(value)
        if not items:
            continue
        prices: list[float] = []
        for item in items:
            if isinstance(item, dict):
                price = item.get("price")
                if isinstance(price, (int, float)):
                    prices.append(float(price))
            elif isinstance(item, (int, float)):
                prices.append(float(item))
        if prices:
            return prices
    return []


def _take_profits_from_actions(actions_structured: list[Any]) -> list[float]:
    for action in actions_structured:
        if not isinstance(action, dict):
            continue
        items = action.get("take_profits")
        if not isinstance(items, list):
            continue
        prices = [float(item) for item in items if isinstance(item, (int, float))]
        if prices:
            return prices
    return []


def _coerce_reported_results(*values: Any) -> str:
    for value in values:
        items = _coerce_list(value)
        if not items:
            continue
        return _summarize_reported_results(items)
    return ""


def _extract_action_target_refs(actions_structured: list[Any]) -> list[int]:
    refs: list[int] = []
    for action in actions_structured:
        if not isinstance(action, dict):
            continue
        refs.extend(_extract_int_list(action.get("target_refs")))
        targeting = action.get("targeting") if isinstance(action.get("targeting"), dict) else {}
        refs.extend(_extract_int_list(targeting.get("targets")))
    return _unique_ints(refs)


def _extract_int_list(value: Any) -> list[int]:
    items = _coerce_list(value)
    out: list[int] = []
    for item in items:
        if isinstance(item, dict):
            ref = item.get("ref")
            if isinstance(ref, bool):
                continue
            if isinstance(ref, int):
                out.append(ref)
                continue
            if isinstance(ref, float) and ref.is_integer():
                out.append(int(ref))
                continue
            if isinstance(ref, str):
                try:
                    out.append(int(ref.strip()))
                except ValueError:
                    continue
            continue
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
            continue
        if isinstance(item, float) and item.is_integer():
            out.append(int(item))
            continue
        if isinstance(item, str):
            try:
                out.append(int(item.strip()))
            except ValueError:
                continue
    return _unique_ints(out)


def _summarize_entries(entries: list[Any]) -> str:
    parts: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        role = _scalar(item.get("role")) or "ENTRY"
        order_type = _scalar(item.get("order_type")) or "UNKNOWN"
        price = item.get("price")
        if isinstance(price, (int, float)):
            parts.append(f"{role}:{order_type}:{_format_float(price)}")
        else:
            parts.append(f"{role}:{order_type}")
    return " | ".join(parts)


def _summarize_reported_results(results: list[Any]) -> str:
    parts: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        symbol = _scalar(item.get("symbol"))
        value = item.get("value") if "value" in item else item.get("r_multiple")
        unit = _scalar(item.get("unit") or "R")
        if symbol and isinstance(value, (int, float)):
            parts.append(f"{symbol}:{_format_float(value)}{unit}")
        elif symbol:
            parts.append(symbol)
    return " | ".join(parts)


def _summarize_diagnostics(diagnostics: dict[str, Any]) -> str:
    if not diagnostics:
        return ""
    parts: list[str] = []
    for key in ("parser_mode", "parser_used", "confidence", "parse_status_input", "intents_count", "actions_count", "warning_count"):
        if key not in diagnostics:
            continue
        value = diagnostics.get(key)
        if isinstance(value, float):
            parts.append(f"{key}={_format_float(value)}")
        else:
            parts.append(f"{key}={_scalar(value)}")
    return "; ".join(parts)


def _summarize_actions_structured(actions_structured: list[Any]) -> str:
    if not actions_structured:
        return ""
    parts: list[str] = []
    for item in actions_structured:
        if not isinstance(item, dict):
            continue
        action_type = _scalar(item.get("action_type") or item.get("action") or item.get("type"))
        if not action_type:
            continue
        detail_parts: list[str] = []
        for key in (
            "intent",
            "symbol",
            "side",
            "new_stop_level",
            "new_stop_price",
            "close_scope",
            "close_fraction",
            "cancel_scope",
            "hit_target",
            "result_mode",
            "target_refs",
            "target_refs_count",
            "reported_results",
            "take_profits",
            "entries",
            "entry_plan_type",
            "entry_structure",
        ):
            if key not in item:
                continue
            rendered = _render_action_value(item.get(key))
            if rendered:
                detail_parts.append(f"{key}={rendered}")
        if item.get("applies_to"):
            detail_parts.append(f"applies_to={_render_action_value(item.get('applies_to'))}")
        if item.get("targeting"):
            detail_parts.append(f"targeting={_render_action_value(item.get('targeting'))}")
        parts.append(f"{action_type}({'; '.join(detail_parts)})" if detail_parts else action_type)
    return " | ".join(parts)


def _summarize_action_types(actions_structured: list[Any]) -> str:
    if not actions_structured:
        return ""
    values: list[str] = []
    for item in actions_structured:
        if not isinstance(item, dict):
            continue
        action_type = _scalar(item.get("action_type") or item.get("action") or item.get("type"))
        if action_type and action_type not in values:
            values.append(action_type)
    return " | ".join(values)


def _intent_name(value: Any) -> str:
    """Return the intent name from either a plain string or a dict {name, kind}."""
    if isinstance(value, dict):
        return _scalar(value.get("name"))
    return _scalar(value)


def _join_list(value: Any) -> str:
    items = _coerce_list(value)
    return " | ".join(_scalar(item) for item in items if _scalar(item))


def _join_number_list(value: Any) -> str:
    items = _coerce_list(value)
    out: list[str] = []
    for item in items:
        if isinstance(item, bool):
            out.append("True" if item else "False")
        elif isinstance(item, (int, float)):
            out.append(_format_float(item))
        elif item is not None:
            out.append(_scalar(item))
    return " | ".join(out)


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _as_list(value: Any) -> list[Any]:
    return _coerce_list(value)


def _first_value(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return None


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _format_float(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.8f}".rstrip("0").rstrip(".")
        return text if text else "0"
    if isinstance(value, str):
        return value
    return _scalar(value)


def _first_numeric_value(*values: Any) -> Any:
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _bool_scalar(value: Any) -> str:
    return "True" if bool(value) else "False"


def _render_action_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return _format_float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if {"kind", "scope"} <= set(value.keys()):
            kind = _scalar(value.get("kind"))
            scope = _scalar(value.get("scope"))
            if kind or scope:
                return f"{kind}:{scope}".strip(":")
        if {"scope_type", "scope_value"} <= set(value.keys()):
            scope_type = _scalar(value.get("scope_type"))
            scope_value = _scalar(value.get("scope_value"))
            if scope_type or scope_value:
                return f"{scope_type}:{scope_value}".strip(":")
        parts: list[str] = []
        for key in ("kind", "scope", "scope_type", "scope_value", "mode", "selector"):
            if key in value and value.get(key) is not None:
                rendered = _render_action_value(value.get(key))
                if rendered:
                    parts.append(f"{key}={rendered}")
        return "{" + ", ".join(parts) + "}" if parts else ""
    if isinstance(value, list):
        items = [_render_action_value(item) for item in value]
        return " | ".join(item for item in items if item)
    return _scalar(value)


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    output: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
