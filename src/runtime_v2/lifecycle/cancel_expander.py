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
    """Espande CANCEL_PENDING_ENTRY in un comando per ogni ordine pending reale.

    Ritorna lista di (payload_json, idempotency_key) da inserire in DB.
    Per tutti gli altri tipi di comando ritorna il comando originale invariato
    come lista con un solo elemento.
    """
    if command_type != "CANCEL_PENDING_ENTRY":
        return [(payload_json, idempotency_key)]

    entry_client_order_ids = load_pending_entry_client_order_ids(conn, trade_chain_id)
    if not entry_client_order_ids:
        return [(payload_json, idempotency_key)]

    payload = json.loads(payload_json or "{}")
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
    """Legge i client_order_id reali (tsb:...) dei comandi PLACE_ENTRY ancora attivi."""
    rows = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING', 'SENT', 'ACK')
          AND client_order_id IS NOT NULL
        ORDER BY command_id
        """,
        (trade_chain_id,),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


__all__ = ["expand_cancel_pending_commands", "load_pending_entry_client_order_ids"]
