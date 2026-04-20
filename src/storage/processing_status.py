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
    source_topic_id: int | None = None


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

    def get_last_telegram_message_id(
        self,
        chat_id: str,
        topic_id: int | None = None,
    ) -> int | None:
        """Return the highest telegram_message_id seen for a given chat/topic scope.

        - topic_id=None → forum-wide scope (source_topic_id IS NULL)
        - topic_id=int  → specific topic scope (source_topic_id = ?)
        Falls back to chat-level query when source_topic_id column is absent.
        """
        with sqlite3.connect(self._db_path) as conn:
            has_topic_col = "source_topic_id" in self._table_columns(conn)
            if has_topic_col:
                if topic_id is None:
                    row = conn.execute(
                        """
                        SELECT MAX(telegram_message_id) FROM raw_messages
                        WHERE source_chat_id = ? AND source_topic_id IS NULL
                        """,
                        (chat_id,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT MAX(telegram_message_id) FROM raw_messages
                        WHERE source_chat_id = ? AND source_topic_id = ?
                        """,
                        (chat_id, topic_id),
                    ).fetchone()
            else:
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
            has_topic_col = "source_topic_id" in self._table_columns(conn)
            select_cols = (
                "raw_message_id, source_chat_id, telegram_message_id, "
                "raw_text, source_trader_id, reply_to_message_id"
            )
            if has_topic_col:
                select_cols += ", source_topic_id"
            rows = conn.execute(
                f"""
                SELECT {select_cols}
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
                source_topic_id=int(row[6]) if has_topic_col and row[6] is not None else None,
            )
            for row in rows
        ]

    def _table_columns(self, conn: sqlite3.Connection) -> set[str]:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(raw_messages)")}
