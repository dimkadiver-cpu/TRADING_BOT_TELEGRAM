# src/runtime_v2/control_plane/audit_store.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommandAuditStore:
    """Writes ops_telegram_control_commands (COMMANDS_SPEC §11)."""

    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def record(
        self,
        *,
        command_request_id: str,
        chat_id: str,
        message_thread_id: str,
        telegram_user_id: str,
        telegram_username: str | None,
        command_text: str,
        command_name: str | None,
        status: str,
        reject_reason: str | None = None,
        payload_json: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                """
                INSERT INTO ops_telegram_control_commands
                    (command_request_id, chat_id, message_thread_id, telegram_user_id,
                     telegram_username, command_text, command_name, payload_json,
                     received_at, status, reject_reason, idempotency_key,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(command_request_id) DO NOTHING
                """,
                (command_request_id, chat_id, message_thread_id, telegram_user_id,
                 telegram_username, command_text, command_name, payload_json,
                 now, status, reject_reason, idempotency_key, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def update_status(
        self,
        command_request_id: str,
        *,
        status: str,
        execution_result: str | None = None,
        reject_reason: str | None = None,
    ) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_telegram_control_commands "
                "SET status=?, execution_result=COALESCE(?, execution_result), "
                "    reject_reason=COALESCE(?, reject_reason), updated_at=? "
                "WHERE command_request_id=?",
                (status, execution_result, reject_reason, now, command_request_id),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = ["CommandAuditStore"]
