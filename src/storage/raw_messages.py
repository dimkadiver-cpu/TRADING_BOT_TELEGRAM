"""Persistence for Telegram raw messages."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass(slots=True)
class RawMessageRecord:
    source_chat_id: str
    telegram_message_id: int
    message_ts: str
    acquired_at: str
    raw_text: str | None = None
    source_chat_title: str | None = None
    source_type: str | None = None
    source_trader_id: str | None = None
    reply_to_message_id: int | None = None
    acquisition_status: str = "ACQUIRED"
    has_media: bool = False
    media_kind: str | None = None
    media_mime_type: str | None = None
    media_filename: str | None = None
    media_blob: bytes | None = None


@dataclass(slots=True)
class StoredRawMessage:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    source_trader_id: str | None
    raw_text: str | None
    reply_to_message_id: int | None = None
    has_media: bool = False
    media_kind: str | None = None
    media_mime_type: str | None = None
    media_filename: str | None = None
    media_blob: bytes | None = None


@dataclass(slots=True)
class RawMessageSaveResult:
    saved: bool
    raw_message_id: int | None


class RawMessageStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, record: RawMessageRecord) -> bool:
        return self.save_with_id(record).saved

    def save_with_id(self, record: RawMessageRecord) -> RawMessageSaveResult:
        available_columns = self._table_columns("raw_messages")
        insert_columns = [
            "source_chat_id",
            "source_chat_title",
            "source_type",
            "source_trader_id",
            "telegram_message_id",
            "reply_to_message_id",
            "raw_text",
            "message_ts",
            "acquired_at",
            "acquisition_status",
        ]
        insert_values: list[object] = [
            record.source_chat_id,
            record.source_chat_title,
            record.source_type,
            record.source_trader_id,
            record.telegram_message_id,
            record.reply_to_message_id,
            record.raw_text,
            record.message_ts,
            record.acquired_at,
            record.acquisition_status,
        ]
        for column_name, value in [
            ("has_media", 1 if record.has_media else 0),
            ("media_kind", record.media_kind),
            ("media_mime_type", record.media_mime_type),
            ("media_filename", record.media_filename),
            ("media_blob", record.media_blob),
        ]:
            if column_name not in available_columns:
                continue
            insert_columns.append(column_name)
            insert_values.append(value)

        rendered_columns = ",\n              ".join(insert_columns)
        placeholders = ", ".join("?" for _ in insert_columns)
        query = f"""
            INSERT OR IGNORE INTO raw_messages(
              {rendered_columns}
            )
            VALUES ({placeholders})
        """
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(query, insert_values)
            conn.commit()
            row = conn.execute(
                """
                SELECT raw_message_id
                FROM raw_messages
                WHERE source_chat_id = ? AND telegram_message_id = ?
                LIMIT 1
                """,
                (record.source_chat_id, record.telegram_message_id),
            ).fetchone()
            raw_message_id = int(row[0]) if row is not None else None
            return RawMessageSaveResult(saved=cursor.rowcount == 1, raw_message_id=raw_message_id)

    def get_by_source_and_message_id(
        self,
        source_chat_id: str,
        telegram_message_id: int,
    ) -> StoredRawMessage | None:
        available_columns = self._table_columns("raw_messages")
        include_media = {
            "has_media",
            "media_kind",
            "media_mime_type",
            "media_filename",
            "media_blob",
        }.issubset(available_columns)
        query = """
            SELECT raw_message_id, source_chat_id, telegram_message_id,
                   source_trader_id, raw_text, reply_to_message_id
            FROM raw_messages
            WHERE source_chat_id = ? AND telegram_message_id = ?
            LIMIT 1
        """
        if include_media:
            query = """
                SELECT raw_message_id, source_chat_id, telegram_message_id,
                       source_trader_id, raw_text, reply_to_message_id,
                       has_media, media_kind, media_mime_type, media_filename, media_blob
                FROM raw_messages
                WHERE source_chat_id = ? AND telegram_message_id = ?
                LIMIT 1
            """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(query, (source_chat_id, telegram_message_id)).fetchone()
            if row is None:
                return None
            return StoredRawMessage(
                raw_message_id=int(row[0]),
                source_chat_id=row[1],
                telegram_message_id=int(row[2]),
                source_trader_id=row[3],
                raw_text=row[4],
                reply_to_message_id=int(row[5]) if row[5] is not None else None,
                has_media=bool(row[6]) if include_media else False,
                media_kind=row[7] if include_media else None,
                media_mime_type=row[8] if include_media else None,
                media_filename=row[9] if include_media else None,
                media_blob=row[10] if include_media else None,
            )

    def _table_columns(self, table_name: str) -> set[str]:
        with sqlite3.connect(self._db_path) as conn:
            return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}
