# src/runtime_v2/execution_gateway/repositories.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.lifecycle.models import ExecutionCommand


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cmd_from_row(row: tuple) -> ExecutionCommand:
    (command_id, trade_chain_id, command_type, status, payload_json,
     idempotency_key, created_at, updated_at) = row[:8]
    return ExecutionCommand(
        command_id=command_id,
        trade_chain_id=trade_chain_id,
        command_type=command_type,
        status=status,
        payload_json=payload_json or "{}",
        idempotency_key=idempotency_key,
        created_at=datetime.fromisoformat(created_at) if created_at else None,
        updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
    )


_BASE_COLS = (
    "command_id, trade_chain_id, command_type, status, payload_json, "
    "idempotency_key, created_at, updated_at"
)


class GatewayCommandRepository:
    def __init__(self, db_path: str) -> None:
        self._db = db_path

    def get_pending_batch(self, limit: int = 100) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS} FROM ops_execution_commands "
                "WHERE status='PENDING' ORDER BY created_at LIMIT ?", (limit,)
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_retry_batch(self, limit: int = 100) -> list[ExecutionCommand]:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS} FROM ops_execution_commands "
                "WHERE status='SENT' AND next_retry_at IS NOT NULL "
                "AND next_retry_at <= ? ORDER BY next_retry_at LIMIT ?",
                (now, limit),
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_waiting_on_open_chains(self, limit: int = 100) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT c.command_id, c.trade_chain_id, c.command_type, c.status, "
                "c.payload_json, c.idempotency_key, c.created_at, c.updated_at "
                "FROM ops_execution_commands c "
                "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
                "WHERE c.status='WAITING_POSITION' AND t.lifecycle_state='OPEN' "
                "ORDER BY c.created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_sent_or_ack(self, limit: int = 500) -> list[tuple[ExecutionCommand, str | None]]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS}, client_order_id FROM ops_execution_commands "
                "WHERE status IN ('SENT','ACK') AND client_order_id IS NOT NULL "
                "ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [(_cmd_from_row(r[:8]), r[8]) for r in rows]
        finally:
            conn.close()

    def count_active_tps(self, trade_chain_id: int) -> int:
        conn = sqlite3.connect(self._db)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM ops_execution_commands "
                "WHERE trade_chain_id=? AND command_type='PLACE_TAKE_PROFIT' "
                "AND status IN ('PENDING','SENT','ACK','WAITING_POSITION')",
                (trade_chain_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    def mark_sent(
        self,
        command_id: int,
        *,
        client_order_id: str,
        adapter: str,
        execution_account_id: str,
        adapter_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        now = _now()
        result = {"adapter_order_id": adapter_order_id,
                  "exchange_order_id": exchange_order_id, "error": None,
                  "reason": None, "warnings": []}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='SENT', adapter=?, "
                "execution_account_id=?, client_order_id=?, result_payload_json=?, "
                "sent_at=?, updated_at=? WHERE command_id=?",
                (adapter, execution_account_id, client_order_id,
                 json.dumps(result), now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_ack(self, command_id: int) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='ACK', "
                "acknowledged_at=?, updated_at=? WHERE command_id=?",
                (now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_done(self, command_id: int, result: dict | None = None) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='DONE', "
                "result_payload_json=?, completed_at=?, updated_at=? WHERE command_id=?",
                (json.dumps(result or {}), now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(self, command_id: int, *, reason: str) -> None:
        now = _now()
        result = {"error": reason, "reason": reason}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='FAILED', "
                "result_payload_json=?, updated_at=? WHERE command_id=?",
                (json.dumps(result), now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_review_required(self, command_id: int, *, reason: str) -> None:
        now = _now()
        result = {"error": None, "reason": reason}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='REVIEW_REQUIRED', "
                "result_payload_json=?, updated_at=? WHERE command_id=?",
                (json.dumps(result), now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_waiting_position(self, command_id: int) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='WAITING_POSITION', "
                "updated_at=? WHERE command_id=?",
                (now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_retry(self, command_id: int, *, retry_count: int, next_retry_at: str) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET retry_count=?, "
                "next_retry_at=?, updated_at=? WHERE command_id=?",
                (retry_count, next_retry_at, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_entry_client_order_id(self, trade_chain_id: int) -> str | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT client_order_id FROM ops_execution_commands "
                "WHERE trade_chain_id=? AND command_type='PLACE_ENTRY' "
                "AND client_order_id IS NOT NULL LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()


__all__ = ["GatewayCommandRepository"]
