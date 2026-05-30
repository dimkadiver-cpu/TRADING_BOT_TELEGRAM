# src/runtime_v2/control_plane/outbox_writer.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

# Map internal lifecycle event_type -> CLEAN_LOG notification_type.
# Events absent from this map have policy "off" (CLEAN_LOG_SPEC §2).
_CLEAN_LOG_EVENT_MAP: dict[str, str] = {
    "SIGNAL_ACCEPTED": "SIGNAL_ACCEPTED",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
    "ENTRY_FILLED": "ENTRY_OPENED",
    "TP_FILLED": "TP_FILLED",
    "SL_FILLED": "SL_FILLED",
    "CLOSE_FULL_FILLED": "POSITION_CLOSED",
}

_PRIORITY_BY_TYPE: dict[str, str] = {
    "SL_FILLED": "HIGH",
    "POSITION_CLOSED": "HIGH",
    "REVIEW_REQUIRED": "HIGH",
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


def project_clean_log_for_chain(conn: sqlite3.Connection, chain_id: int) -> int:
    """Read lifecycle events for `chain_id` and project CLEAN_LOG outbox rows.

    Idempotent: dedupe_key = "clean:<idempotency_key>" + UNIQUE constraint.
    Returns the number of rows attempted (including dedupe no-ops).
    """
    chain_row = conn.execute(
        "SELECT symbol, side, entry_mode FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    symbol = chain_row[0] if chain_row else None
    side = chain_row[1] if chain_row else None
    entry_mode = chain_row[2] if chain_row else None

    events = conn.execute(
        """
        SELECT event_type, payload_json, idempotency_key
        FROM ops_lifecycle_events
        WHERE trade_chain_id=?
        ORDER BY event_id
        """,
        (chain_id,),
    ).fetchall()

    written = 0
    for event_type, payload_json, idem in events:
        notification_type = _CLEAN_LOG_EVENT_MAP.get(event_type)
        if notification_type is None:
            continue
        try:
            ev_payload = json.loads(payload_json or "{}")
        except Exception:
            ev_payload = {}

        # Promote terminal TP to TP_FILLED_FINAL.
        if notification_type == "TP_FILLED" and ev_payload.get("is_final"):
            notification_type = "TP_FILLED_FINAL"

        payload = {
            "chain_id": chain_id,
            "symbol": symbol,
            "side": side,
            "entry_mode": entry_mode,
            **ev_payload,
        }
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
