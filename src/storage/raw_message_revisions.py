from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3


@dataclass(slots=True)
class RawMessageRevisionRecord:
    revision_id: int
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    revision_kind: str
    run_context: str
    raw_text: str | None
    message_ts: str
    revision_ts: str
    telegram_edit_ts: str | None
    acquisition_status: str | None
    reply_to_message_id: int | None
    source_topic_id: int | None
    has_media: bool
    media_kind: str | None
    media_mime_type: str | None
    media_filename: str | None
    applied_to_current: bool


class RawMessageRevisionStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def append_initial(
        self,
        *,
        raw_message_id: int,
        source_chat_id: str,
        telegram_message_id: int,
        raw_text: str | None,
        message_ts: str,
        acquisition_status: str | None,
        reply_to_message_id: int | None,
        source_topic_id: int | None,
        has_media: bool,
        media_kind: str | None,
        media_mime_type: str | None,
        media_filename: str | None,
        run_context: str = "live",
    ) -> None:
        self._append(
            raw_message_id=raw_message_id,
            source_chat_id=source_chat_id,
            telegram_message_id=telegram_message_id,
            revision_kind="initial",
            run_context=run_context,
            raw_text=raw_text,
            message_ts=message_ts,
            telegram_edit_ts=None,
            acquisition_status=acquisition_status,
            reply_to_message_id=reply_to_message_id,
            source_topic_id=source_topic_id,
            has_media=has_media,
            media_kind=media_kind,
            media_mime_type=media_mime_type,
            media_filename=media_filename,
            applied_to_current=True,
        )

    def append_edit(
        self,
        *,
        raw_message_id: int,
        source_chat_id: str,
        telegram_message_id: int,
        raw_text: str | None,
        message_ts: str,
        run_context: str,
        telegram_edit_ts: str | None,
        acquisition_status: str | None,
        reply_to_message_id: int | None,
        source_topic_id: int | None,
        has_media: bool,
        media_kind: str | None,
        media_mime_type: str | None,
        media_filename: str | None,
        applied_to_current: bool,
    ) -> None:
        self._append(
            raw_message_id=raw_message_id,
            source_chat_id=source_chat_id,
            telegram_message_id=telegram_message_id,
            revision_kind="edit",
            run_context=run_context,
            raw_text=raw_text,
            message_ts=message_ts,
            telegram_edit_ts=telegram_edit_ts,
            acquisition_status=acquisition_status,
            reply_to_message_id=reply_to_message_id,
            source_topic_id=source_topic_id,
            has_media=has_media,
            media_kind=media_kind,
            media_mime_type=media_mime_type,
            media_filename=media_filename,
            applied_to_current=applied_to_current,
        )

    def append_deleted(
        self,
        *,
        raw_message_id: int,
        source_chat_id: str,
        telegram_message_id: int,
        raw_text: str | None,
        message_ts: str,
        run_context: str,
        acquisition_status: str | None,
        reply_to_message_id: int | None,
        source_topic_id: int | None,
        has_media: bool,
        media_kind: str | None,
        media_mime_type: str | None,
        media_filename: str | None,
        applied_to_current: bool,
    ) -> None:
        self._append(
            raw_message_id=raw_message_id,
            source_chat_id=source_chat_id,
            telegram_message_id=telegram_message_id,
            revision_kind="deleted",
            run_context=run_context,
            raw_text=raw_text,
            message_ts=message_ts,
            telegram_edit_ts=None,
            acquisition_status=acquisition_status,
            reply_to_message_id=reply_to_message_id,
            source_topic_id=source_topic_id,
            has_media=has_media,
            media_kind=media_kind,
            media_mime_type=media_mime_type,
            media_filename=media_filename,
            applied_to_current=applied_to_current,
        )

    def list_by_raw_message_id(self, raw_message_id: int) -> list[RawMessageRevisionRecord]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT revision_id, raw_message_id, source_chat_id, telegram_message_id,
                       revision_kind, run_context, raw_text, message_ts, revision_ts,
                       telegram_edit_ts, acquisition_status, reply_to_message_id,
                       source_topic_id, has_media, media_kind, media_mime_type,
                       media_filename, applied_to_current
                FROM raw_message_revisions
                WHERE raw_message_id = ?
                ORDER BY revision_id ASC
                """,
                (raw_message_id,),
            ).fetchall()
        return [
            RawMessageRevisionRecord(
                revision_id=int(row[0]),
                raw_message_id=int(row[1]),
                source_chat_id=row[2],
                telegram_message_id=int(row[3]),
                revision_kind=row[4],
                run_context=row[5],
                raw_text=row[6],
                message_ts=row[7],
                revision_ts=row[8],
                telegram_edit_ts=row[9],
                acquisition_status=row[10],
                reply_to_message_id=int(row[11]) if row[11] is not None else None,
                source_topic_id=int(row[12]) if row[12] is not None else None,
                has_media=bool(row[13]),
                media_kind=row[14],
                media_mime_type=row[15],
                media_filename=row[16],
                applied_to_current=bool(row[17]),
            )
            for row in rows
        ]

    def _append(
        self,
        *,
        raw_message_id: int,
        source_chat_id: str,
        telegram_message_id: int,
        revision_kind: str,
        run_context: str,
        raw_text: str | None,
        message_ts: str,
        telegram_edit_ts: str | None,
        acquisition_status: str | None,
        reply_to_message_id: int | None,
        source_topic_id: int | None,
        has_media: bool,
        media_kind: str | None,
        media_mime_type: str | None,
        media_filename: str | None,
        applied_to_current: bool,
    ) -> None:
        revision_ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO raw_message_revisions (
                    raw_message_id, source_chat_id, telegram_message_id, revision_kind,
                    run_context, raw_text, message_ts, revision_ts, telegram_edit_ts,
                    acquisition_status, reply_to_message_id, source_topic_id, has_media,
                    media_kind, media_mime_type, media_filename, applied_to_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_message_id,
                    source_chat_id,
                    telegram_message_id,
                    revision_kind,
                    run_context,
                    raw_text,
                    message_ts,
                    revision_ts,
                    telegram_edit_ts,
                    acquisition_status,
                    reply_to_message_id,
                    source_topic_id,
                    1 if has_media else 0,
                    media_kind,
                    media_mime_type,
                    media_filename,
                    1 if applied_to_current else 0,
                ),
            )
            conn.commit()
