# src/runtime_v2/lifecycle/cancel_expander.py
"""
Shared logic for expanding CANCEL_PENDING_ENTRY commands.

Used by both entry_gate (UPDATE path) and workers (_persist_result path),
so that auto-cancel averaging emitted by the lifecycle processor reaches
the gateway with the real exchange client_order_id instead of a plan placeholder.
"""
from __future__ import annotations

import json
import sqlite3


def expand_cancel_pending_commands(
    conn: sqlite3.Connection,
    *,
    trade_chain_id: int,
    command_type: str,
    payload_json: str,
    idempotency_key: str,
) -> list[tuple[str, str]]:
    """Expand CANCEL_PENDING_ENTRY into one concrete command per active real order."""
    if command_type != "CANCEL_PENDING_ENTRY":
        return [(payload_json, idempotency_key)]

    payload = json.loads(payload_json or "{}")
    if payload.get("entry_client_order_id"):
        resolved = resolve_entry_client_order_id(
            conn,
            trade_chain_id=trade_chain_id,
            entry_client_order_id=str(payload["entry_client_order_id"]),
        )
        if resolved is None:
            return [(payload_json, idempotency_key)]
        concrete_payload = dict(payload)
        concrete_payload["entry_client_order_id"] = resolved
        return [(json.dumps(concrete_payload), idempotency_key)]

    entry_client_order_ids = load_pending_entry_client_order_ids(conn, trade_chain_id)
    if not entry_client_order_ids:
        return [(payload_json, idempotency_key)]

    expanded: list[tuple[str, str]] = []
    for entry_client_order_id in entry_client_order_ids:
        item = dict(payload)
        item["entry_client_order_id"] = entry_client_order_id
        expanded.append(
            (
                json.dumps(item),
                f"{idempotency_key}:{entry_client_order_id}",
            )
        )
    return expanded


def load_pending_entry_client_order_ids(
    conn: sqlite3.Connection,
    trade_chain_id: int,
) -> list[str]:
    """Return active real client_order_id values (tsb:...) for entry commands."""
    rows = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING', 'SENT', 'ACK')
          AND client_order_id IS NOT NULL
          AND client_order_id NOT IN (
              SELECT json_extract(payload_json, '$.entry_client_order_id')
              FROM ops_execution_commands
              WHERE trade_chain_id = ?
                AND command_type = 'CANCEL_PENDING_ENTRY'
                AND status IN ('PENDING', 'SENT', 'ACK', 'DONE')
                AND json_extract(payload_json, '$.entry_client_order_id') IS NOT NULL
          )
          AND json_extract(payload_json, '$.sequence') NOT IN (
              SELECT json_extract(le.payload_json, '$.filled_leg_sequence')
              FROM ops_lifecycle_events le
              WHERE le.trade_chain_id = ?
                AND le.event_type = 'ENTRY_FILLED'
                AND json_extract(le.payload_json, '$.filled_leg_sequence') IS NOT NULL
          )
        ORDER BY command_id
        """,
        (trade_chain_id, trade_chain_id, trade_chain_id),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def resolve_entry_client_order_id(
    conn: sqlite3.Connection,
    *,
    trade_chain_id: int,
    entry_client_order_id: str,
) -> str | None:
    """Resolve plan placeholder `place_entry...` into real `tsb:...` client_order_id."""
    if not entry_client_order_id.startswith(("place_entry:", "place_entry_attached:")):
        return entry_client_order_id

    row = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND idempotency_key = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING', 'SENT', 'ACK')
          AND client_order_id IS NOT NULL
        ORDER BY command_id DESC
        LIMIT 1
        """,
        (trade_chain_id, entry_client_order_id),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


__all__ = [
    "expand_cancel_pending_commands",
    "load_pending_entry_client_order_ids",
    "resolve_entry_client_order_id",
]
