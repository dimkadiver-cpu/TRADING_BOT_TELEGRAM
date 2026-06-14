# src/runtime_v2/control_plane/outbox_writer.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

# Map internal lifecycle event_type -> CLEAN_LOG notification_type.
# Events absent from this map have policy "off" (CLEAN_LOG_SPEC §2).
_CLEAN_LOG_EVENT_MAP: dict[str, str] = {
    "SIGNAL_ACCEPTED": "SIGNAL_ACCEPTED",
    "SIGNAL_REJECTED": "SIGNAL_REJECTED",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
    "ENTRY_FILLED": "ENTRY_OPENED",
    "TP_FILLED": "TP_FILLED",
    "SL_FILLED": "SL_FILLED",
    "CLOSE_FULL_FILLED": "POSITION_CLOSED",
    "ENTRY_UPDATED": "ENTRY_UPDATED",
    "PENDING_TIMEOUT": "PENDING_ENTRY_EXPIRED",
    "CLOSE_PARTIAL_FILLED": "PARTIAL_CLOSE_EXECUTED",
    "PENDING_ENTRY_CANCELLED": "ENTRY_CANCELLED",
    "ENTRY_CANCEL_FAILED": "CANCEL_FAILED",
    "RECONCILIATION_WARNING": "RECONCILIATION_WARNING",
    "RECONCILIATION_FIXED": "RECONCILIATION_FIXED",
    "REENTRY_ACCEPTED": "REENTRY_ACCEPTED",
    "UPDATE_DONE": "UPDATE_DONE",
    "UPDATE_PARTIAL": "UPDATE_PARTIAL",
    "UPDATE_REJECTED": "UPDATE_REJECTED",
}

_SIGNAL_NOTIFICATION_TYPES: frozenset[str] = frozenset({
    "SIGNAL_ACCEPTED", "SIGNAL_REJECTED", "REVIEW_REQUIRED",
})

_PRIORITY_BY_TYPE: dict[str, str] = {
    "SL_FILLED": "HIGH",
    "POSITION_CLOSED": "HIGH",
    "REVIEW_REQUIRED": "HIGH",
    "SIGNAL_REJECTED": "HIGH",
}


def _side_pnl(side: str | None, entry_avg_price: float | None, fill_price, qty) -> float | None:
    if entry_avg_price is None or fill_price is None or qty is None:
        return None
    sign = 1.0 if str(side or "").upper() == "LONG" else -1.0
    return round(float(qty) * (float(fill_price) - float(entry_avg_price)) * sign, 8)


def _closed_pct(qty, filled_entry_qty: float | None) -> float | None:
    if qty is None or not filled_entry_qty:
        return None
    return round(float(qty) / float(filled_entry_qty) * 100.0, 2)


def _remaining_pct(open_position_qty: float | None, filled_entry_qty: float | None) -> float | None:
    if open_position_qty is None or not filled_entry_qty:
        return None
    return round(float(open_position_qty) / float(filled_entry_qty) * 100.0, 2)


def _remaining_risk(
    open_position_qty: float | None,
    entry_avg_price: float | None,
    current_stop_price: float | None,
) -> float | None:
    if open_position_qty is None or entry_avg_price is None or current_stop_price is None:
        return None
    return round(float(open_position_qty) * abs(float(entry_avg_price) - float(current_stop_price)), 8)


def _final_result(
    *,
    gross_pnl: float | None,
    fees: float | None,
    funding: float | None,
    peak_margin_used: float | None,
    initial_risk_amount: float | None,
    close_reason: str,
) -> dict:
    gross = float(gross_pnl) if gross_pnl is not None else None
    fee_total = float(fees) if fees is not None else None
    funding_total = float(funding) if funding is not None else None
    net = None
    if gross is not None and fee_total is not None:
        net = gross - fee_total - (funding_total or 0.0)
    roi = None
    if net is not None and peak_margin_used is not None and float(peak_margin_used) > 0.0:
        roi = round(net / float(peak_margin_used) * 100.0, 4)
    return_on_risk = None
    if net is not None and initial_risk_amount is not None and float(initial_risk_amount) > 0.0:
        return_on_risk = round(net / float(initial_risk_amount) * 100.0, 4)
    r_multiple = None
    if net is not None and initial_risk_amount is not None and float(initial_risk_amount) > 0.0:
        r_multiple = round(net / float(initial_risk_amount), 2)
    return {
        "roi_net_pct": roi,
        "return_on_risk_pct": return_on_risk,
        "r_multiple": r_multiple,
        "total_pnl_net": round(net, 8) if net is not None else None,
        "gross_pnl": round(gross, 8) if gross is not None else None,
        "fees": round(-fee_total, 8) if fee_total is not None else None,
        "funding": round(-funding_total, 8) if funding_total is not None else None,
        "close_reason": close_reason,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _send_after_for(notification_type: str) -> str:
    if notification_type == "MULTI_CHAIN_SUMMARY":
        return _iso_after(3)
    return _now()


def _agg_group(notification_type: str, chain_id: int | None, payload: dict) -> str | None:
    return None


def _record(
    conn: sqlite3.Connection,
    *,
    notification_type: str,
    destination: str,
    payload: dict,
    priority: str,
    dedupe_key: str,
    send_after: str | None = None,
    aggregation_group: str | None = None,
    source_message_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO ops_notification_outbox
            (notification_type, destination, payload_json, priority, status,
             dedupe_key, attempts, created_at, send_after, aggregation_group, source_message_id)
        VALUES (?,?,?,?, 'PENDING', ?, 0, ?, ?, ?, ?)
        """,
        (notification_type, destination, json.dumps(payload), priority,
         dedupe_key, _now(), send_after, aggregation_group, source_message_id),
    )



def write_clean_log_event(
    conn: sqlite3.Connection,
    *,
    notification_type: str,
    chain_id: int | None,
    payload: dict,
    priority: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    """Insert a CLEAN_LOG outbox row inside the caller's transaction."""
    key = dedupe_key or f"clean:{notification_type}:{chain_id}"
    pri = priority or _PRIORITY_BY_TYPE.get(notification_type, "MEDIUM")
    if chain_id is not None and "chain_id" not in payload:
        payload = {**payload, "chain_id": chain_id}
    _record(
        conn,
        notification_type=notification_type,
        destination="CLEAN_LOG",
        payload=payload,
        priority=pri,
        dedupe_key=key,
        send_after=_send_after_for(notification_type),
        aggregation_group=_agg_group(notification_type, chain_id, payload),
        source_message_id=payload.get("source_message_id"),
    )


def write_tech_log_event(
    conn: sqlite3.Connection,
    *,
    notification_type: str,
    payload: dict,
    dedupe_key: str,
    priority: str = "MEDIUM",
) -> None:
    """Insert a TECH_LOG outbox row inside the caller's transaction."""
    _record(conn, notification_type=notification_type, destination="TECH_LOG",
            payload=payload, priority=priority, dedupe_key=dedupe_key)


def notify_listener_edit_skipped(ops_db_path: str, context: dict) -> None:
    """TECH_LOG per un edit di segnale già eseguito, scartato dal listener.

    Apre una propria connessione: il chiamante (listener) non ha accesso
    all'ops DB. Dedupe per (chat, msg_id, edit_ts) così edit distinti dello
    stesso messaggio notificano di nuovo, ma lo stesso edit non duplica.
    """
    conn = sqlite3.connect(ops_db_path)
    try:
        with conn:
            write_tech_log_event(
                conn,
                notification_type="LISTENER_EDIT_SKIPPED",
                payload={
                    "level": "WARNING",
                    "category": "Listener",
                    "title": "edit_of_executed_signal_skipped",
                    "description": (
                        "Edit di un segnale con trade chain già creata — "
                        "non riprocessato."
                    ),
                    "context": context,
                    "action": "verifica il messaggio modificato e intervieni manualmente se serve",
                    "source": "telegram_listener",
                },
                dedupe_key=(
                    f"edit_skipped:{context.get('chat')}:"
                    f"{context.get('msg_id')}:{context.get('edit_ts')}"
                ),
                priority="HIGH",
            )
    finally:
        conn.close()


def _compute_entry_enrichment(
    ev: dict,
    legs: list,
    risk: dict,
    avg_for_calc: float | None,
    current_stop_price: float | None,
    filled_entry_qty: float | None,
    initial_risk_amount: float | None,
) -> dict:
    """Compute Position-section fields shared by ENTRY_OPENED and ENTRY_UPDATED."""
    filled_seq = ev.get("filled_leg_sequence")
    ev_qty_raw = ev.get("fill_qty") or ev.get("filled_qty")
    ev_qty = float(ev_qty_raw) if ev_qty_raw is not None else None

    risk_legs: list = risk.get("legs", []) if isinstance(risk, dict) else []
    plan_leg = next((l for l in legs if l.get("sequence") == filled_seq), {}) if filled_seq is not None else {}
    risk_leg = next((l for l in risk_legs if l.get("sequence") == filled_seq), {}) if filled_seq is not None else {}

    entry_type_for_leg: str = plan_leg.get("entry_type", "LIMIT")
    planned_qty_raw = risk_leg.get("qty")
    planned_qty = float(planned_qty_raw) if planned_qty_raw is not None else None

    total_planned = sum(float(l["qty"]) for l in risk_legs if l.get("qty") is not None)

    is_partial = False
    if planned_qty is not None and ev_qty is not None and float(planned_qty) > 0:
        is_partial = (float(planned_qty) - ev_qty) / float(planned_qty) > 0.005

    leg_fill_pct = None
    if is_partial and planned_qty is not None and ev_qty is not None and float(planned_qty) > 0:
        leg_fill_pct = round(ev_qty / float(planned_qty) * 100.0, 1)

    position_filled_pct = None
    if total_planned > 0 and filled_entry_qty is not None:
        position_filled_pct = round(float(filled_entry_qty) / total_planned * 100.0, 1)

    total_value = None
    if filled_entry_qty is not None and avg_for_calc is not None:
        total_value = round(float(filled_entry_qty) * float(avg_for_calc), 8)

    actual_risk = None
    if filled_entry_qty is not None and avg_for_calc is not None and current_stop_price is not None:
        actual_risk = round(float(filled_entry_qty) * abs(float(avg_for_calc) - float(current_stop_price)), 8)

    return {
        "entry_type_for_leg": entry_type_for_leg,
        "planned_qty": planned_qty,
        "is_partial_leg": is_partial,
        "_leg_fill_pct": leg_fill_pct,
        "_total_legs": len(legs),
        "total_filled_qty": filled_entry_qty,
        "total_value": total_value,
        "total_fees": risk.get("open_fee_residual") if isinstance(risk, dict) else None,
        "position_filled_pct": position_filled_pct,
        "actual_risk_usdt": actual_risk,
        "planned_risk_usdt": initial_risk_amount,
    }


def _build_payload(
    notification_type: str,
    chain_id: int,
    symbol: str | None,
    side: str | None,
    trader_id: str | None,
    account_id: str | None,
    plan: dict,
    risk: dict,
    entry_avg_price: float | None,
    current_stop_price: float | None,
    ev: dict,
    *,
    cumulative_gross_pnl: float | None = None,
    cumulative_fees: float | None = None,
    cumulative_funding: float | None = None,
    allocated_margin: float | None = None,
    initial_risk_amount: float | None = None,
    peak_margin_used: float | None = None,
    filled_entry_qty: float | None = None,
    open_position_qty: float | None = None,
    be_protection_status: str | None = None,
) -> dict:
    """Build the enriched notification payload for a given notification_type."""
    base: dict = {"chain_id": chain_id, "symbol": symbol, "side": side, "trader_id": trader_id, "account_id": account_id}

    legs = plan.get("legs", [])
    tps = list(plan.get("intermediate_tps", []) or [])
    if plan.get("final_tp") is not None:
        tps = tps + [plan["final_tp"]]

    if notification_type == "SIGNAL_ACCEPTED":
        risk_pct = None
        if risk.get("capital") and risk.get("risk_amount"):
            risk_pct = round(risk["risk_amount"] / risk["capital"] * 100, 2)
        payload = {
            **base,
            "trader_id": trader_id,
            "account_id": account_id,
            "entries": [
                {
                    "sequence": l["sequence"],
                    "entry_type": l["entry_type"],
                    "price": l.get("price"),
                }
                for l in legs
            ],
            "sl": plan.get("stop_loss"),
            "tps": tps,
            "risk_pct": risk_pct,
            "leverage": risk.get("leverage"),
            "source": ev.get("source", "trader_signal"),
            "link": ev.get("source_message_link"),
        }
        if len(legs) >= 2:
            payload["_entry_pcts"] = [
                round(float(l.get("weight", 1.0 / len(legs))) * 100) for l in legs
            ]
        plan_close_pcts = plan.get("close_pcts") or []
        if len(plan_close_pcts) >= 2:
            payload["_tp_pcts"] = [round(p) for p in plan_close_pcts]
        if ev.get("parse_status") == "PARTIAL":
            payload["parse_status"] = "PARTIAL"
            if ev.get("parse_warnings"):
                payload["parse_warnings"] = ev["parse_warnings"]
        if plan.get("range_derivation"):
            payload["range_derivation"] = plan["range_derivation"]
        if plan.get("risk_hint_applied"):
            payload["risk_hint_applied"] = plan["risk_hint_applied"]
        if plan.get("tp_trimmed"):
            payload["tp_trimmed"] = plan["tp_trimmed"]
        return payload

    if notification_type == "ENTRY_OPENED":
        pending = [
            {
                "sequence": l["sequence"],
                "entry_type": l["entry_type"],
                "price": l.get("price"),
            }
            for l in legs
            if l.get("status") == "PENDING"
        ]
        enrichment = _compute_entry_enrichment(
            ev=ev,
            legs=legs,
            risk=risk,
            avg_for_calc=entry_avg_price,
            current_stop_price=current_stop_price,
            filled_entry_qty=filled_entry_qty,
            initial_risk_amount=initial_risk_amount,
        )
        payload: dict = {
            **base,
            "fill_price": ev.get("fill_price"),
            "filled_qty": ev.get("fill_qty") or ev.get("filled_qty"),
            "fee": ev.get("exec_fee"),
            "filled_leg_sequence": ev.get("filled_leg_sequence"),
            "avg_entry": entry_avg_price,
            "pending_entries": pending,
            "source": ev.get("source", "exchange"),
            **enrichment,
        }
        if ev.get("fee_rate") is not None:
            payload["fee_rate"] = ev["fee_rate"]
        if ev.get("exec_value") is not None:
            payload["exec_value"] = ev["exec_value"]
        return payload

    if notification_type in ("TP_FILLED", "TP_FILLED_FINAL"):
        tp_level = ev.get("tp_level")
        tp_price = tps[tp_level - 1] if tp_level and 1 <= tp_level <= len(tps) else None
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        final_result_data = None
        if notification_type == "TP_FILLED_FINAL":
            final_result_data = _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                peak_margin_used=peak_margin_used,
                initial_risk_amount=initial_risk_amount,
                close_reason="TAKE_PROFIT",
            )
        payload: dict = {
            **base,
            "tp_level": tp_level,
            "tp_price": tp_price,
            "_total_tps": len(tps) or None,
            "fill_price": fill_price,
            "closed_qty": closed_qty,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "remaining_pct": _remaining_pct(open_position_qty, filled_entry_qty),
            "remaining_qty": open_position_qty,
            "avg_entry": entry_avg_price,
            "remaining_risk": _remaining_risk(open_position_qty, entry_avg_price, current_stop_price),
            "sl_current": current_stop_price,
            "be_protection_status": be_protection_status,
            "final_result": final_result_data,
            "source": ev.get("source", "exchange"),
            "link": ev.get("source_message_link"),
        }
        if ev.get("fee_rate") is not None:
            payload["fee_rate"] = ev["fee_rate"]
        if ev.get("exec_value") is not None:
            payload["exec_value"] = ev["exec_value"]
        return payload

    if notification_type == "SL_FILLED":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        event_source = ev.get("source", "exchange")
        if be_protection_status == "PROTECTED" and event_source == "exchange":
            close_reason = "BREAKEVEN_AFTER_TP"
        elif event_source == "trader_update":
            close_reason = "TRADER_COMMAND"
        else:
            close_reason = "STOP_LOSS"
        payload = {
            **base,
            "fill_price": fill_price,
            "sl_price": fill_price,
            "closed_qty": closed_qty,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "close_reason": close_reason,
            "final_result": _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                peak_margin_used=peak_margin_used,
                initial_risk_amount=initial_risk_amount,
                close_reason=close_reason,
            ),
            "source": ev.get("source", "exchange"),
            "link": ev.get("source_message_link"),
        }
        if "fee_rate" in ev:
            payload["fee_rate"] = ev.get("fee_rate")
        return payload

    if notification_type == "POSITION_CLOSED":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        payload = {
            **base,
            "fill_price": fill_price,
            "closed_qty": closed_qty,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "close_reason": ev.get("close_reason", "MANUAL_CLOSE"),
            "final_result": _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                peak_margin_used=peak_margin_used,
                initial_risk_amount=initial_risk_amount,
                close_reason=ev.get("close_reason", "MANUAL_CLOSE"),
            ),
            "source": ev.get("source", "exchange"),
            "link": ev.get("source_message_link"),
        }
        if "fee_rate" in ev:
            payload["fee_rate"] = ev.get("fee_rate")
        return payload

    if notification_type == "SIGNAL_REJECTED":
        risk_pct = None
        if risk.get("capital") and risk.get("risk_amount"):
            risk_pct = round(risk["risk_amount"] / risk["capital"] * 100, 2)
        rej_payload = {
            **base,
            "trader_id": trader_id,
            "account_id": account_id,
            "reason": ev.get("reason", "unknown"),
            "entries": [
                {
                    "sequence": l["sequence"],
                    "entry_type": l["entry_type"],
                    "price": l.get("price"),
                }
                for l in legs
            ],
            "sl": plan.get("stop_loss"),
            "tps": tps,
            "risk_pct": risk_pct,
            "leverage": risk.get("leverage"),
            "source": ev.get("source", "trader_signal"),
            "link": ev.get("source_message_link"),
        }
        if len(legs) >= 2:
            rej_payload["_entry_pcts"] = [
                round(float(l.get("weight", 1.0 / len(legs))) * 100) for l in legs
            ]
        plan_close_pcts = plan.get("close_pcts") or []
        if len(plan_close_pcts) >= 2:
            rej_payload["_tp_pcts"] = [round(p) for p in plan_close_pcts]
        return rej_payload

    if notification_type == "REVIEW_REQUIRED":
        risk_pct = None
        if risk.get("capital") and risk.get("risk_amount"):
            risk_pct = round(risk["risk_amount"] / risk["capital"] * 100, 2)
        return {
            **base,
            "trader_id": trader_id,
            "account_id": account_id,
            "reason": ev.get("reason", "unknown"),
            "entries": [
                {
                    "sequence": l["sequence"],
                    "entry_type": l["entry_type"],
                    "price": l.get("price"),
                }
                for l in legs
            ],
            "sl": plan.get("stop_loss"),
            "tps": tps,
            "risk_pct": risk_pct,
            "leverage": risk.get("leverage"),
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "ENTRY_UPDATED":
        pending = [
            {
                "sequence": l["sequence"],
                "entry_type": l["entry_type"],
                "price": l.get("price"),
            }
            for l in legs
            if l.get("status") == "PENDING"
        ]
        avg_for_updated = ev.get("new_avg_entry") if ev.get("new_avg_entry") is not None else entry_avg_price
        enrichment = _compute_entry_enrichment(
            ev=ev,
            legs=legs,
            risk=risk,
            avg_for_calc=avg_for_updated,
            current_stop_price=current_stop_price,
            filled_entry_qty=filled_entry_qty,
            initial_risk_amount=initial_risk_amount,
        )
        payload = {
            **base,
            "fill_price": ev.get("fill_price"),
            "filled_qty": ev.get("fill_qty") or ev.get("filled_qty"),
            "fee": ev.get("exec_fee"),
            "filled_leg_sequence": ev.get("filled_leg_sequence"),
            "new_avg_entry": ev.get("new_avg_entry"),
            "avg_entry": avg_for_updated,
            "pending_entries": pending,
            "source": ev.get("source", "exchange"),
            "link": ev.get("source_message_link"),
            **enrichment,
        }
        if ev.get("fee_rate") is not None:
            payload["fee_rate"] = ev["fee_rate"]
        if ev.get("exec_value") is not None:
            payload["exec_value"] = ev["exec_value"]
        return payload

    if notification_type == "UPDATE_DONE":
        return {
            **base,
            "applied_actions": ev.get("applied_actions", []),
            "changed": ev.get("changed", []),
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "UPDATE_PARTIAL":
        return {
            **base,
            "applied_actions": ev.get("applied_actions", []),
            "rejected_actions": ev.get("rejected_actions", []),
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "UPDATE_REJECTED":
        return {
            **base,
            "reason": ev.get("reason"),
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "PENDING_ENTRY_EXPIRED":
        return {
            **base,
            "source": ev.get("source", "timeout_worker"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "RECONCILIATION_WARNING":
        return {
            **base,
            "issue": ev.get("issue"),
            "risk": ev.get("risk"),
            "action": ev.get("action"),
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "RECONCILIATION_FIXED":
        return {
            **base,
            "issue": ev.get("issue"),
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "REENTRY_ACCEPTED":
        return {
            **base,
            "previous_chain_id": ev.get("previous_chain_id"),
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "ENTRY_CANCELLED":
        sequence = ev.get("sequence")
        # Price and entry_type may come from the event payload or, as fallback,
        # from plan_state_json (PENDING_ENTRY_CANCELLED_CONFIRMED never carries them).
        plan_leg = next(
            (l for l in plan.get("legs", []) if l.get("sequence") == sequence),
            {},
        ) if sequence is not None else {}
        cancelled_entry = {
            "sequence": sequence,
            "price": ev.get("price") or plan_leg.get("price"),
            "entry_type": ev.get("entry_type") or plan_leg.get("entry_type", "LIMIT"),
        }
        planned_qty = ev.get("planned_entry_qty")
        partial_qty = ev.get("partial_fill_qty", ev.get("filled_qty"))
        partial_pct = None
        if planned_qty and partial_qty is not None:
            partial_pct = round(float(partial_qty) / float(planned_qty) * 100.0, 2)
        cancel_origin = ev.get("cancel_origin")
        cancel_reason = ev.get("cancel_reason")
        if ev.get("source"):
            entry_cancel_source = ev["source"]
        elif cancel_origin == "engine_rule" and cancel_reason == "auto_cancel_averaging":
            entry_cancel_source = "operation_rules"
        elif cancel_origin in ("engine_rule", "trader_update"):
            entry_cancel_source = "trader_update"
        else:
            entry_cancel_source = "timeout_worker"
        return {
            **base,
            "cancelled_entry": cancelled_entry,
            "_total_legs": len(plan.get("legs", [])) or None,
            "partial_fill_pct": partial_pct,
            "partial_fill_qty": partial_qty,
            "avg_entry": entry_avg_price,
            "total_filled_qty": filled_entry_qty,
            "source": entry_cancel_source,
            "link": ev.get("source_message_link"),
        }

    if notification_type == "BE_EXIT":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        payload = {
            **base,
            "exit_price": fill_price,
            "closed_qty": closed_qty,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "close_reason": "BREAKEVEN_AFTER_TP",
            "final_result": _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                peak_margin_used=peak_margin_used,
                initial_risk_amount=initial_risk_amount,
                close_reason="BREAKEVEN_AFTER_TP",
            ),
            "source": ev.get("source", "exchange"),
            "link": ev.get("source_message_link"),
        }
        if "fee_rate" in ev:
            payload["fee_rate"] = ev.get("fee_rate")
        return payload

    if notification_type == "CANCEL_FAILED":
        return {
            **base,
            "entry_ref": ev.get("entry_ref"),
            "entry_price": ev.get("entry_price"),
            "attempts": ev.get("attempts", 3),
            "source": ev.get("source", "timeout_worker"),
        }

    if notification_type == "PARTIAL_CLOSE_EXECUTED":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        return {
            **base,
            "fill_price": fill_price,
            "closed_qty": closed_qty,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "remaining_qty": open_position_qty,
            "avg_entry": entry_avg_price,
            "remaining_risk": _remaining_risk(open_position_qty, entry_avg_price, current_stop_price),
            "source": ev.get("source", "manual_command"),
        }

    # fallback: merge base with event payload
    return {**base, **ev}


def project_clean_log_for_chain(conn: sqlite3.Connection, chain_id: int) -> int:
    """Read lifecycle events for `chain_id` and project CLEAN_LOG outbox rows.

    Idempotent: dedupe_key = "clean:<idempotency_key>" + UNIQUE constraint.
    Returns the number of rows attempted (including dedupe no-ops).
    Reads plan/risk/chain data from ops_trade_chains to enrich each payload.
    Side is always taken from ops_trade_chains (never from event payload) to
    avoid the SL_FILLED "Sell" bug.
    """
    chain_row = conn.execute(
        "SELECT symbol, side, entry_mode, trader_id, account_id, "
        "plan_state_json, risk_snapshot_json, "
        "entry_avg_price, current_stop_price, "
        "source_chat_id, telegram_message_id, "
        "cumulative_gross_pnl, cumulative_fees, cumulative_funding, allocated_margin, "
        "initial_risk_amount, peak_margin_used, "
        "filled_entry_qty, open_position_qty, be_protection_status "
        "FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    if not chain_row:
        return 0

    symbol = chain_row[0]
    side = chain_row[1]
    # entry_mode = chain_row[2]  # available if needed
    trader_id = chain_row[3]
    account_id = chain_row[4]
    plan = json.loads(chain_row[5] or "{}")
    risk = json.loads(chain_row[6] or "{}")
    entry_avg_price = chain_row[7]
    current_stop_price = chain_row[8]
    source_chat_id = chain_row[9]
    telegram_message_id = chain_row[10]
    cumulative_gross_pnl = chain_row[11]
    cumulative_fees = chain_row[12]
    cumulative_funding = chain_row[13]
    allocated_margin = chain_row[14]
    initial_risk_amount = chain_row[15]
    peak_margin_used = chain_row[16]
    filled_entry_qty = chain_row[17]
    open_position_qty = chain_row[18]
    be_protection_status = chain_row[19]
    chain_source_link: str | None = (
        f"https://t.me/c/{str(source_chat_id).removeprefix('-100')}/{telegram_message_id}"
        if source_chat_id and telegram_message_id else None
    )

    last_id = (conn.execute(
        "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone() or (0,))[0] or 0

    events = conn.execute(
        "SELECT event_id, event_type, payload_json, idempotency_key "
        "FROM ops_lifecycle_events "
        "WHERE trade_chain_id=? AND event_id > ? ORDER BY event_id",
        (chain_id, last_id),
    ).fetchall()

    written = 0
    max_event_id = last_id
    for row_event_id, event_type, payload_json, idem in events:
        max_event_id = max(max_event_id, row_event_id)
        notification_type = _CLEAN_LOG_EVENT_MAP.get(event_type)
        if notification_type is None:
            continue
        try:
            ev = json.loads(payload_json or "{}")
        except Exception:
            ev = {}

        if notification_type in _SIGNAL_NOTIFICATION_TYPES and chain_source_link:
            if "source_message_link" not in ev:
                ev = {**ev, "source_message_link": chain_source_link}

        # Promote terminal TP to TP_FILLED_FINAL.
        if notification_type == "TP_FILLED" and ev.get("is_final"):
            notification_type = "TP_FILLED_FINAL"

        # Filter: ENTRY_CANCELLED suppression rules
        # - position_closed: always suppress (chain already closed)
        # - timeout_worker: covered by PENDING_ENTRY_EXPIRED
        # - trader_update / engine_rule without partial fill: covered by UPDATE_DONE
        if notification_type == "ENTRY_CANCELLED":
            if ev.get("cancel_reason") == "position_closed":
                continue
            cancel_origin = ev.get("cancel_origin")
            if cancel_origin == "timeout_worker":
                continue
            if cancel_origin in ("trader_update", "engine_rule"):
                partial_pct = float(ev.get("partial_fill_pct") or 0.0)
                if partial_pct < 1.0:
                    continue

        # Filter: PARTIAL_CLOSE_EXECUTED only for bot-originated fills
        if notification_type == "PARTIAL_CLOSE_EXECUTED" and ev.get("source") != "manual_command":
            continue

        # Promote CLOSE_FULL_FILLED on PROTECTED chain → BE_EXIT only when the
        # exchange executed the SL automatically (source != "exchange_manual").
        # A manual close from the exchange UI has source="exchange_manual" and must
        # remain POSITION_CLOSED even if the chain has SL at breakeven.
        if (
            event_type == "CLOSE_FULL_FILLED"
            and be_protection_status == "PROTECTED"
            and ev.get("source") != "exchange_manual"
        ):
            notification_type = "BE_EXIT"

        payload = _build_payload(
            notification_type, chain_id, symbol, side, trader_id, account_id,
            plan, risk, entry_avg_price, current_stop_price, ev,
            cumulative_gross_pnl=cumulative_gross_pnl,
            cumulative_fees=cumulative_fees,
            cumulative_funding=cumulative_funding,
            allocated_margin=allocated_margin,
            initial_risk_amount=initial_risk_amount,
            peak_margin_used=peak_margin_used,
            filled_entry_qty=filled_entry_qty,
            open_position_qty=open_position_qty,
            be_protection_status=be_protection_status,
        )
        write_clean_log_event(
            conn,
            notification_type=notification_type,
            chain_id=chain_id,
            payload=payload,
            dedupe_key=f"clean:{idem}",
        )
        written += 1
    if events:
        conn.execute(
            "UPDATE ops_trade_chains SET last_projected_event_id=? WHERE trade_chain_id=?",
            (max_event_id, chain_id),
        )
    return written


def write_engine_rule_update_clean_log(
    conn,
    chain_id: int,
    events: list,
) -> None:
    """Write a single UPDATE_DONE CLEAN_LOG row from ENGINE_RULE_UPDATE_ACCEPTED events."""
    if not events:
        return

    applied_actions: list[str] = []
    changed: list[dict] = []

    for e in events:
        try:
            p = json.loads(e.payload_json or "{}")
        except Exception:
            p = {}
        action = p.get("action", "")
        if action:
            applied_actions.append(action)

        if p.get("is_breakeven"):
            changed.append({
                "field": "SL",
                "old": p.get("old_sl_price"),
                "new": p.get("new_sl_price"),
                "note": "BE",
            })
        elif action == "CANCEL_PENDING":
            for entry in p.get("cancelled_entries", []):
                changed.append({
                    "field": f"Entry_{entry.get('sequence', '?')}",
                    "old": entry.get("price"),
                    "new": "cancelled",
                })

    chain_row = conn.execute(
        "SELECT symbol, side, trader_id FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    symbol = chain_row[0] if chain_row else None
    side = chain_row[1] if chain_row else None
    trader_id = chain_row[2] if chain_row else None

    first = events[0]
    payload = {
        "chain_id": chain_id,
        "symbol": symbol,
        "side": side,
        "trader_id": trader_id,
        "applied_actions": applied_actions,
        "rejected_actions": [],
        "changed": changed,
        "source": "operation_rules",
        "link": None,
    }
    write_clean_log_event(
        conn,
        notification_type="UPDATE_DONE",
        chain_id=chain_id,
        payload=payload,
        dedupe_key=f"engine_rule_update:{chain_id}:{first.idempotency_key}",
    )


def try_release_pending_close_full_summaries(conn: sqlite3.Connection) -> int:
    """Emit MULTI_CHAIN_SUMMARY for any pending CLOSE_FULL summary where all chain links are now resolvable.

    Called by the dispatcher after each POSITION_CLOSED send. Scans all pending records and
    releases those where every chain has a confirmed POSITION_CLOSED message ID in tracking.
    Returns the number of summaries released.
    """
    try:
        rows = conn.execute(
            "SELECT canonical_message_id, payload_json FROM ops_pending_multi_chain_summaries"
        ).fetchall()
    except Exception:
        return 0

    released = 0
    for canonical_message_id, payload_json in rows:
        try:
            pending = json.loads(payload_json)
        except Exception:
            continue

        resolved_chains = []
        all_resolved = True
        for chain in pending.get("chains", []):
            chain_id = chain.get("chain_id")
            if chain_id is None:
                all_resolved = False
                break
            tracking_row = conn.execute(
                "SELECT clean_log_last_message_id, telegram_chat_id, last_clean_log_event_type "
                "FROM ops_clean_log_tracking WHERE trade_chain_id=?",
                (chain_id,),
            ).fetchone()
            if not tracking_row:
                all_resolved = False
                break
            last_msg_id, chat_id, last_event_type = tracking_row
            if last_event_type != "POSITION_CLOSED" or not last_msg_id or not chat_id:
                all_resolved = False
                break
            normalized_chat = str(chat_id).removeprefix("-100")
            resolved_chains.append({**chain, "link": f"https://t.me/c/{normalized_chat}/{last_msg_id}"})

        if not all_resolved:
            continue

        write_clean_log_event(
            conn,
            notification_type="MULTI_CHAIN_SUMMARY",
            chain_id=None,
            payload={**pending, "summary_kind": "final_close", "chains": resolved_chains},
            dedupe_key=f"clean:multi_summary_final:{canonical_message_id}",
        )
        conn.execute(
            "DELETE FROM ops_pending_multi_chain_summaries WHERE canonical_message_id=?",
            (canonical_message_id,),
        )
        conn.commit()
        released += 1
    return released


__all__ = [
    "write_clean_log_event",
    "write_tech_log_event",
    "notify_listener_edit_skipped",
    "project_clean_log_for_chain",
    "write_engine_rule_update_clean_log",
    "try_release_pending_close_full_summaries",
]
