"""Persistence for messages that require manual review."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from src.core.timeutils import utc_now_iso


@dataclass(slots=True)
class ReviewQueueEntry:
    id: int
    raw_message_id: int
    reason: str
    created_at: str
    resolved_at: str | None
    resolution: str | None


class ReviewQueueStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def insert(self, raw_message_id: int, reason: str) -> int:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO review_queue(raw_message_id, reason, created_at, resolved_at, resolution)
                VALUES (?, ?, ?, NULL, NULL)
                """,
                (raw_message_id, reason, utc_now_iso()),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def resolve(self, entry_id: int, resolution: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE review_queue
                SET resolved_at = ?, resolution = ?
                WHERE id = ?
                """,
                (utc_now_iso(), resolution, entry_id),
            )
            conn.commit()

    def get_pending(self) -> list[ReviewQueueEntry]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, raw_message_id, reason, created_at, resolved_at, resolution
                FROM review_queue
                WHERE resolved_at IS NULL
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        return [
            ReviewQueueEntry(
                id=int(row[0]),
                raw_message_id=int(row[1]),
                reason=row[2],
                created_at=row[3],
                resolved_at=row[4],
                resolution=row[5],
            )
            for row in rows
        ]
