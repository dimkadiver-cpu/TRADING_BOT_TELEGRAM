"""Processing status lifecycle for raw_messages queue.

Tracks the worker lifecycle: pending → processing → done | failed | blacklisted | review.
Does not modify raw_messages.py — accesses the raw_messages table directly
only for processing_status and queue-related queries.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Literal

ProcessingStatus = Literal["pending", "processing", "done", "failed", "blacklisted", "review"]


@dataclass(slots=True)
class StaleMessage:
    """A message stuck in pending/processing that needs re-queuing on restart."""

    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    raw_text: str | None
    source_trader_id: str | None
    reply_to_message_id: int | None


class ProcessingStatusStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def update(self, raw_message_id: int, status: ProcessingStatus) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE raw_messages SET processing_status = ? WHERE raw_message_id = ?",
                (status, raw_message_id),
            )
            conn.commit()

    def get_last_telegram_message_id(self, chat_id: str) -> int | None:
        """Return the highest telegram_message_id seen for a given chat."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT MAX(telegram_message_id) FROM raw_messages WHERE source_chat_id = ?",
                (chat_id,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def get_stale_messages(self) -> list[StaleMessage]:
        """Return messages stuck in pending or processing — interrupted at previous restart."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT raw_message_id, source_chat_id, telegram_message_id,
                       raw_text, source_trader_id, reply_to_message_id
                FROM raw_messages
                WHERE processing_status IN ('pending', 'processing')
                ORDER BY raw_message_id ASC
                """,
            ).fetchall()
        return [
            StaleMessage(
                raw_message_id=int(row[0]),
                source_chat_id=row[1],
                telegram_message_id=int(row[2]),
                raw_text=row[3],
                source_trader_id=row[4],
                reply_to_message_id=int(row[5]) if row[5] is not None else None,
            )
            for row in rows
        ]
