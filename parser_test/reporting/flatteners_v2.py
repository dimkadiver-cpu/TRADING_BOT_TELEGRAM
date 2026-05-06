from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from parser_test.reporting.report_schema_v2 import SCOPE_COLUMNS


@dataclass(slots=True)
class ReportRow:
    run_id: int
    raw_message_id: int
    trader_id: str | None
    parser_profile: str | None
    primary_class: str | None
    parse_status: str | None
    primary_intent: str | None
    confidence: float | None
    canonical_json: str | None
    warnings_json: str | None
    diagnostics_json: str | None
    error_status: str
    error_message: str | None
    telegram_message_id: int
    source_chat_id: str
    source_topic_id: int | None
    reply_to_message_id: int | None
    message_ts: str
    raw_text: str | None


def flatten_for_scope(scope: str, row: ReportRow) -> dict[str, Any]:
    canonical: dict[str, Any] = json.loads(row.canonical_json) if row.canonical_json else {}
    all_fields = _build_all_fields(row, canonical)
    if scope not in SCOPE_COLUMNS:
        raise ValueError(f"Unknown scope {scope!r}. Valid: {list(SCOPE_COLUMNS)}")
    columns = SCOPE_COLUMNS[scope]
    return {col: all_fields.get(col) for col in columns}


def _build_all_fields(row: ReportRow, canonical: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}

    fields.update(_common(row, canonical))

    signal = canonical.get("signal") or {}
    if signal:
        fields.update(_signal_fields(signal))

    update = canonical.get("update") or {}
    targeted_actions = canonical.get("targeted_actions") or []
    target_hints = canonical.get("target_hints") or {}
    if update or targeted_actions:
        fields.update(_update_fields(update, targeted_actions, target_hints))

    report = canonical.get("report") or {}
    if report:
        fields.update(_report_fields(report))

    info = canonical.get("info") or {}
    fields["info_raw_fragment"] = info.get("raw_fragment")

    return fields


def _common(row: ReportRow, canonical: dict[str, Any]) -> dict[str, Any]:
    warnings = canonical.get("warnings") or []
    diagnostics = canonical.get("diagnostics") or {}
    return {
        "run_id": row.run_id,
        "raw_message_id": row.raw_message_id,
        "telegram_message_id": row.telegram_message_id,
        "source_chat_id": row.source_chat_id,
        "source_topic_id": row.source_topic_id,
        "reply_to_message_id": row.reply_to_message_id,
        "message_ts": row.message_ts,
        "trader_id": row.trader_id,
        "parser_profile": canonical.get("parser_profile") or row.parser_profile,
        "schema_version": canonical.get("schema_version"),
        "raw_text": (canonical.get("raw_context") or {}).get("raw_text") or row.raw_text,
        "primary_class": canonical.get("primary_class") or row.primary_class,
        "parse_status": canonical.get("parse_status") or row.parse_status,
        "primary_intent": canonical.get("primary_intent") or row.primary_intent,
        "intents": "|".join(canonical.get("intents") or []),
        "confidence": canonical["confidence"] if "confidence" in canonical else row.confidence,
        "warnings": "|".join(warnings),
        "diagnostics_summary": _diagnostics_summary(diagnostics),
        "error_status": row.error_status,
        "error_message": row.error_message,
    }


def _diagnostics_summary(diagnostics: dict[str, Any]) -> str | None:
    if not diagnostics:
        return None
    try:
        text = json.dumps(diagnostics, ensure_ascii=False)
        return text[:300] if len(text) > 300 else text
    except Exception:
        return str(diagnostics)[:300]


def _signal_fields(signal: dict[str, Any]) -> dict[str, Any]:
    entries = signal.get("entries") or []
    tps = signal.get("take_profits") or []
    risk_hint = signal.get("risk_hint") or {}
    stop_loss = signal.get("stop_loss") or {}
    return {
        "symbol": signal.get("symbol"),
        "side": signal.get("side"),
        "entry_structure": signal.get("entry_structure"),
        "entries_count": len(entries),
        "entries_summary": "|".join(
            f"{e.get('sequence')}:{e.get('entry_type')}:{e.get('role', '')}@{(e.get('price') or {}).get('value', '')}"
            for e in entries
        ),
        "stop_loss_price": (stop_loss.get("price") or {}).get("value"),
        "take_profit_count": len(tps),
        "take_profit_prices": "|".join(
            str((tp.get("price") or {}).get("value", "")) for tp in tps
        ),
        "risk_hint_raw": risk_hint.get("raw"),
        "risk_hint_value": risk_hint.get("value"),
        "risk_hint_min_value": risk_hint.get("min_value"),
        "risk_hint_max_value": risk_hint.get("max_value"),
        "leverage_hint": signal.get("leverage_hint"),
        "missing_fields": "|".join(signal.get("missing_fields") or []),
        "completeness": signal.get("completeness"),
    }


def _update_fields(
    update: dict[str, Any],
    targeted_actions: list[dict[str, Any]],
    target_hints: dict[str, Any],
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = update.get("operations") or []
    first_set_stop = next((v for o in operations if (v := o.get("set_stop"))), {})
    first_close = next((v for o in operations if (v := o.get("close"))), {})
    first_cancel = next((v for o in operations if (v := o.get("cancel_pending"))), {})
    first_mod_entries = next((v for o in operations if (v := o.get("modify_entries"))), {})
    first_mod_targets = next((v for o in operations if (v := o.get("modify_targets"))), {})
    first_invalidate = next((v for o in operations if (v := o.get("invalidate_setup"))), {})
    mod_entries_entries = first_mod_entries.get("entries") or []
    mod_targets_tps = first_mod_targets.get("take_profits") or []

    return {
        "operations_count": len(operations),
        "operations_summary": "|".join(f"{o.get('op_type')}({o.get('source_intent')})" for o in operations),
        "operation_types": "|".join(o.get("op_type", "") for o in operations),
        "source_intents": "|".join(o.get("source_intent", "") for o in operations),
        "operation_confidences": "|".join(str(o.get("confidence", "")) for o in operations),
        "operation_raw_fragments": "|".join(o.get("raw_fragment", "") or "" for o in operations),
        "target_scope_hint": target_hints.get("scope_hint"),
        "target_reply_to_message_id": target_hints.get("reply_to_message_id"),
        "target_telegram_message_ids": "|".join(str(v) for v in (target_hints.get("telegram_message_ids") or [])),
        "target_telegram_links": "|".join(target_hints.get("telegram_links") or []),
        "target_explicit_ids": "|".join(target_hints.get("explicit_ids") or []),
        "target_symbols": "|".join(target_hints.get("symbols") or []),
        "set_stop_target_type": first_set_stop.get("target_type"),
        "set_stop_price": (first_set_stop.get("price") or {}).get("value"),
        "set_stop_tp_level": first_set_stop.get("tp_level"),
        "close_scope": first_close.get("close_scope"),
        "close_fraction": first_close.get("fraction"),
        "close_price": (first_close.get("close_price") or {}).get("value"),
        "cancel_scope_hint": first_cancel.get("cancel_scope_hint"),
        "modify_entries_kind": first_mod_entries.get("kind"),
        "modify_entries_count": len(mod_entries_entries),
        "modify_entries_summary": "|".join(
            f"{e.get('sequence')}:{e.get('entry_type')}@{(e.get('price') or {}).get('value', '')}"
            for e in mod_entries_entries
        ),
        "modify_entries_entry_structure": first_mod_entries.get("entry_structure"),
        "modify_targets_mode": first_mod_targets.get("mode"),
        "modify_targets_count": len(mod_targets_tps),
        "modify_targets_prices": "|".join(
            str((tp.get("price") or {}).get("value", "")) for tp in mod_targets_tps
        ),
        "modify_targets_target_tp_level": first_mod_targets.get("target_tp_level"),
        "invalidate_reason_text": first_invalidate.get("reason_text"),
        "targeted_actions_count": len(targeted_actions),
        "targeted_actions_summary": "|".join(
            f"{a.get('action_type')}({a.get('source_intent')})" for a in targeted_actions
        ),
    }


def _report_fields(report: dict[str, Any]) -> dict[str, Any]:
    events: list[dict[str, Any]] = report.get("events") or []
    result = report.get("result") or {}

    hit_target = None
    hit_price = None
    for ev in events:
        et = ev.get("event_type")
        level = ev.get("level")
        price_val = (ev.get("price") or {}).get("value")
        if hit_target is None:
            if et == "TP_HIT":
                hit_target = f"TP{level}" if level else "TP"
            elif et == "SL_HIT":
                hit_target = "SL"
            elif et == "EXIT_BE":
                hit_target = "BE"
            elif et == "ENTRY_FILLED":
                hit_target = f"ENTRY{level}" if level else "ENTRY"
        if hit_price is None and price_val is not None:
            hit_price = price_val

    return {
        "report_events_count": len(events),
        "report_events_summary": "|".join(
            f"{e.get('event_type')}(lvl={e.get('level')})" for e in events
        ),
        "report_event_types": "|".join(e.get("event_type", "") for e in events),
        "report_event_levels": "|".join(str(e.get("level", "")) for e in events),
        "report_event_prices": "|".join(str((e.get("price") or {}).get("value", "")) for e in events),
        "report_event_source_intents": "|".join(e.get("source_intent", "") for e in events),
        "report_event_raw_fragments": "|".join(e.get("raw_fragment", "") or "" for e in events),
        "report_result_raw_fragment": result.get("raw_fragment"),
        "hit_target": hit_target,
        "hit_price": hit_price,
    }


__all__ = ["ReportRow", "flatten_for_scope"]
