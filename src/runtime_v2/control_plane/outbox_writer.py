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


def _final_result(
    *,
    gross_pnl: float | None,
    fees: float | None,
    funding: float | None,
    allocated_margin: float | None,
    close_reason: str,
) -> dict:
    gross = float(gross_pnl or 0.0)
    fee_total = float(fees or 0.0)
    funding_total = float(funding or 0.0)
    net = gross - fee_total + funding_total
    roi = None
    if allocated_margin and float(allocated_margin) > 0.0:
        roi = round(net / float(allocated_margin) * 100.0, 4)
    return {
        "roi_net_pct": roi,
        "total_pnl_net": round(net, 8),
        "gross_pnl": round(gross, 8),
        "fees": round(-fee_total, 8),
        "funding": round(funding_total, 8),
        "close_reason": close_reason,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _send_after_for(notification_type: str) -> str:
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return _iso_after(20)
    if notification_type in {"TP_FILLED", "TP_FILLED_FINAL"}:
        return _iso_after(30)
    return _now()


def _agg_group(notification_type: str, chain_id: int | None, payload: dict) -> str | None:
    if chain_id is None:
        return None
    if notification_type in {"TP_FILLED", "TP_FILLED_FINAL"}:
        return f"{chain_id}:tp_batch"
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return f"{chain_id}:{payload.get('source_message_id') or 'update_batch'}"
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


def _build_payload(
    notification_type: str,
    chain_id: int,
    symbol: str | None,
    side: str | None,
    trader_id: str | None,
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
    filled_entry_qty: float | None = None,
    open_position_qty: float | None = None,
    be_protection_status: str | None = None,
) -> dict:
    """Build the enriched notification payload for a given notification_type."""
    base: dict = {"chain_id": chain_id, "symbol": symbol, "side": side}

    legs = plan.get("legs", [])
    tps = list(plan.get("intermediate_tps", []) or [])
    if plan.get("final_tp") is not None:
        tps = tps + [plan["final_tp"]]

    if notification_type == "SIGNAL_ACCEPTED":
        risk_pct = None
        if risk.get("capital") and risk.get("risk_amount"):
            risk_pct = round(risk["risk_amount"] / risk["capital"] * 100, 2)
        return {
            **base,
            "trader_id": trader_id,
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
            "source": ev.get("source", "original_message"),
            "link": ev.get("source_message_link"),
        }

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
        return {
            **base,
            "fill_price": ev.get("fill_price"),
            "filled_qty": ev.get("fill_qty") or ev.get("filled_qty"),
            "avg_entry": entry_avg_price,
            "pending_entries": pending,
            "source": ev.get("source", "exchange"),
        }

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
                allocated_margin=allocated_margin,
                close_reason="TAKE_PROFIT",
            )
        return {
            **base,
            "tp_level": tp_level,
            "tp_price": tp_price,
            "fill_price": fill_price,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "fee_rate": ev.get("fee_rate"),
            "exec_value": ev.get("exec_value"),
            "remaining_pct": _remaining_pct(open_position_qty, filled_entry_qty),
            "sl_current": current_stop_price,
            "be_protection_status": be_protection_status,
            "final_result": final_result_data,
            "source": ev.get("source", "exchange"),
        }

    if notification_type == "SL_FILLED":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        return {
            **base,
            "fill_price": fill_price,
            "sl_price": fill_price,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "final_result": _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                allocated_margin=allocated_margin,
                close_reason="STOP_LOSS",
            ),
            "source": ev.get("source", "exchange"),
        }

    if notification_type == "POSITION_CLOSED":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        return {
            **base,
            "fill_price": fill_price,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "close_reason": ev.get("close_reason", "MANUAL"),
            "final_result": _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                allocated_margin=allocated_margin,
                close_reason=ev.get("close_reason", "MANUAL"),
            ),
            "source": ev.get("source", "exchange"),
        }

    if notification_type == "SIGNAL_REJECTED":
        return {
            **base,
            "trader_id": trader_id,
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
            "source": ev.get("source", "original_message"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "REVIEW_REQUIRED":
        return {
            **base,
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
            "source": ev.get("source", "runtime"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "ENTRY_UPDATED":
        return {
            **base,
            "fill_price": ev.get("fill_price"),
            "filled_qty": ev.get("fill_qty") or ev.get("filled_qty"),
            "new_avg_entry": ev.get("new_avg_entry"),
            "source": ev.get("source", "exchange"),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "UPDATE_DONE":
        return {
            **base,
            "applied_actions": ev.get("applied_actions", []),
            "changed_fields": ev.get("changed_fields", []),
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
            "source": ev.get("source", "worker"),
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
        cancelled_entry = {
            "sequence": sequence,
            "price": ev.get("price"),
            "entry_type": ev.get("entry_type", "LIMIT"),
        }
        planned_qty = ev.get("planned_entry_qty")
        partial_qty = ev.get("partial_fill_qty", ev.get("filled_qty"))
        partial_pct = None
        if planned_qty and partial_qty is not None:
            partial_pct = round(float(partial_qty) / float(planned_qty) * 100.0, 2)
        return {
            **base,
            "cancelled_entry": cancelled_entry,
            "partial_fill_pct": partial_pct,
            "partial_fill_qty": partial_qty,
            "avg_entry": entry_avg_price,
            "total_filled_qty": filled_entry_qty,
            "source": ev.get("source", ev.get("cancel_reason", "runtime")),
            "link": ev.get("source_message_link"),
        }

    if notification_type == "BE_EXIT":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        return {
            **base,
            "exit_price": fill_price,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "close_reason": "BREAKEVEN_AFTER_TP",
            "final_result": _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                allocated_margin=allocated_margin,
                close_reason="BREAKEVEN_AFTER_TP",
            ),
            "source": ev.get("source", "exchange"),
        }

    if notification_type == "CANCEL_FAILED":
        return {
            **base,
            "entry_ref": ev.get("entry_ref"),
            "entry_price": ev.get("entry_price"),
            "attempts": ev.get("attempts", 3),
            "source": ev.get("source", "timeout_worker"),
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
        "SELECT symbol, side, entry_mode, trader_id, "
        "plan_state_json, risk_snapshot_json, "
        "entry_avg_price, current_stop_price, "
        "source_chat_id, telegram_message_id, "
        "cumulative_gross_pnl, cumulative_fees, cumulative_funding, allocated_margin, "
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
    plan = json.loads(chain_row[4] or "{}")
    risk = json.loads(chain_row[5] or "{}")
    entry_avg_price = chain_row[6]
    current_stop_price = chain_row[7]
    source_chat_id = chain_row[8]
    telegram_message_id = chain_row[9]
    cumulative_gross_pnl = chain_row[10]
    cumulative_fees = chain_row[11]
    cumulative_funding = chain_row[12]
    allocated_margin = chain_row[13]
    filled_entry_qty = chain_row[14]
    open_position_qty = chain_row[15]
    be_protection_status = chain_row[16]
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

        # Filter: ENTRY_CANCELLED caused by position close should not be shown
        if notification_type == "ENTRY_CANCELLED" and ev.get("cancel_reason") == "position_closed":
            continue

        # Promote CLOSE_FULL_FILLED on PROTECTED chain → BE_EXIT
        if event_type == "CLOSE_FULL_FILLED" and be_protection_status == "PROTECTED":
            notification_type = "BE_EXIT"

        payload = _build_payload(
            notification_type, chain_id, symbol, side, trader_id,
            plan, risk, entry_avg_price, current_stop_price, ev,
            cumulative_gross_pnl=cumulative_gross_pnl,
            cumulative_fees=cumulative_fees,
            cumulative_funding=cumulative_funding,
            allocated_margin=allocated_margin,
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


__all__ = [
    "write_clean_log_event",
    "write_tech_log_event",
    "project_clean_log_for_chain",
]
