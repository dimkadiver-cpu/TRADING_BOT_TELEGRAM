# src/runtime_v2/control_plane/outbox_writer.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

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
    "UPDATE_DONE": "UPDATE_DONE",
    "UPDATE_PARTIAL": "UPDATE_PARTIAL",
    "UPDATE_REJECTED": "UPDATE_REJECTED",
    "PENDING_TIMEOUT": "PENDING_ENTRY_EXPIRED",
    "RECONCILIATION_WARNING": "RECONCILIATION_WARNING",
    "RECONCILIATION_FIXED": "RECONCILIATION_FIXED",
    "REENTRY_ACCEPTED": "REENTRY_ACCEPTED",
}

_PRIORITY_BY_TYPE: dict[str, str] = {
    "SL_FILLED": "HIGH",
    "POSITION_CLOSED": "HIGH",
    "REVIEW_REQUIRED": "HIGH",
    "SIGNAL_REJECTED": "HIGH",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(
    conn: sqlite3.Connection,
    *,
    notification_type: str,
    destination: str,
    payload: dict,
    priority: str,
    dedupe_key: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO ops_notification_outbox
            (notification_type, destination, payload_json, priority, status,
             dedupe_key, attempts, created_at)
        VALUES (?,?,?,?, 'PENDING', ?, 0, ?)
        """,
        (notification_type, destination, json.dumps(payload), priority,
         dedupe_key, _now()),
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
    # Ensure chain_id is embedded in the payload for downstream tracking.
    if chain_id is not None and "chain_id" not in payload:
        payload = {**payload, "chain_id": chain_id}
    _record(conn, notification_type=notification_type, destination="CLEAN_LOG",
            payload=payload, priority=pri, dedupe_key=key)


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
        return {
            **base,
            "tp_level": tp_level,
            "tp_price": tp_price,
            "is_final": ev.get("is_final", False),
            "sl_current": current_stop_price,
            "source": ev.get("source", "exchange"),
        }

    if notification_type == "SL_FILLED":
        return {
            **base,
            "fill_price": ev.get("fill_price"),
            "filled_qty": ev.get("fill_qty") or ev.get("filled_qty"),
            "source": ev.get("source", "exchange"),
        }

    if notification_type == "POSITION_CLOSED":
        return {
            **base,
            "fill_price": ev.get("fill_price"),
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
        "entry_avg_price, current_stop_price "
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

    events = conn.execute(
        "SELECT event_type, payload_json, idempotency_key "
        "FROM ops_lifecycle_events "
        "WHERE trade_chain_id=? ORDER BY event_id",
        (chain_id,),
    ).fetchall()

    written = 0
    for event_type, payload_json, idem in events:
        notification_type = _CLEAN_LOG_EVENT_MAP.get(event_type)
        if notification_type is None:
            continue
        try:
            ev = json.loads(payload_json or "{}")
        except Exception:
            ev = {}

        # Promote terminal TP to TP_FILLED_FINAL.
        if notification_type == "TP_FILLED" and ev.get("is_final"):
            notification_type = "TP_FILLED_FINAL"

        payload = _build_payload(
            notification_type, chain_id, symbol, side, trader_id,
            plan, risk, entry_avg_price, current_stop_price, ev,
        )
        write_clean_log_event(
            conn,
            notification_type=notification_type,
            chain_id=chain_id,
            payload=payload,
            dedupe_key=f"clean:{idem}",
        )
        written += 1
    return written


__all__ = [
    "write_clean_log_event",
    "write_tech_log_event",
    "project_clean_log_for_chain",
]
