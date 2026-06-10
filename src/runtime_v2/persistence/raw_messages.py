from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.storage.raw_messages import RawMessageStore, RawMessageRecord
from src.storage.processing_status import ProcessingStatusStore
from src.runtime_v2.intake.models import (
    RawIngestItem,
    RawMessageEnvelope,
    ProcessingStatusV2,
)
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext


@dataclass(slots=True)
class ChainNode:
    """Minimal view of a raw message for reply-chain walking."""
    source_trader_id: str | None
    resolved_trader_id: str | None
    raw_text: str | None
    reply_to_message_id: int | None


class RawMessageRepository:
    """Adapter over RawMessageStore + ProcessingStatusStore for runtime_v2.

    The existing storage layer handles core dedup and persistence.
    New columns (acquisition_mode, resolved_trader_id, etc.) are managed
    via direct SQL because RawMessageRecord is a legacy contract we don't modify.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._raw_store = RawMessageStore(db_path)
        self._status_store = ProcessingStatusStore(db_path)

    def save_raw(self, item: RawIngestItem) -> RawMessageEnvelope:
        """Save or retrieve raw message by dedup key (source_chat_id, telegram_message_id)."""
        record = RawMessageRecord(
            source_chat_id=item.source_chat_id,
            source_chat_title=item.source_chat_title,
            source_type=item.source_type,
            source_trader_id=None,
            source_topic_id=item.source_topic_id,
            telegram_message_id=item.telegram_message_id,
            reply_to_message_id=item.reply_to_message_id,
            raw_text=item.raw_text,
            message_ts=item.message_ts.isoformat(),
            acquired_at=datetime.now(timezone.utc).isoformat(),
            acquisition_status="ACQUIRED",
            has_media=item.has_media,
            media_kind=item.media_kind,
            media_mime_type=item.media_mime_type,
            media_filename=item.media_filename,
        )
        result = self._raw_store.save_with_id(record)
        self._update_column(result.raw_message_id, "acquisition_mode", item.acquisition_mode)
        return self.get_by_id(result.raw_message_id)

    def get_by_id(self, raw_message_id: int) -> RawMessageEnvelope:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM raw_messages WHERE raw_message_id = ?", (raw_message_id,)
        ).fetchone()
        conn.close()
        return self._row_to_envelope(row)

    def set_blacklisted(self, raw_message_id: int) -> None:
        self._update_column(raw_message_id, "acquisition_status", "BLACKLISTED")
        self._status_store.update(raw_message_id, "blacklisted")  # type: ignore[arg-type]

    def set_media_only_skipped(self, raw_message_id: int) -> None:
        self._update_column(raw_message_id, "acquisition_status", "MEDIA_ONLY_SKIPPED")
        self._status_store.update(raw_message_id, "skipped")  # type: ignore[arg-type]

    def update_processing_status(self, raw_message_id: int, status: ProcessingStatusV2) -> None:
        self._status_store.update(raw_message_id, status)  # type: ignore[arg-type]

    def update_trader_resolution(self, raw_message_id: int, ctx: ResolvedTraderContext) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "UPDATE raw_messages SET resolved_trader_id=?, resolution_method=?, resolution_detail=?"
            " WHERE raw_message_id=?",
            (ctx.trader_id, ctx.method, ctx.detail, raw_message_id),
        )
        conn.commit()
        conn.close()

    def get_chain_node(self, source_chat_id: str, telegram_message_id: int) -> ChainNode | None:
        """Read the minimal fields needed for reply-chain resolution."""
        conn = sqlite3.connect(self._db_path)
        row = conn.execute(
            "SELECT source_trader_id, resolved_trader_id, raw_text, reply_to_message_id "
            "FROM raw_messages "
            "WHERE source_chat_id = ? AND telegram_message_id = ? "
            "LIMIT 1",
            (source_chat_id, telegram_message_id),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return ChainNode(
            source_trader_id=row[0],
            resolved_trader_id=row[1],
            raw_text=row[2],
            reply_to_message_id=int(row[3]) if row[3] is not None else None,
        )

    def _update_column(self, raw_message_id: int, column: str, value: object) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            f"UPDATE raw_messages SET {column}=? WHERE raw_message_id=?",  # noqa: S608
            (value, raw_message_id),
        )
        conn.commit()
        conn.close()

    def _row_to_envelope(self, row: sqlite3.Row) -> RawMessageEnvelope:
        keys = set(row.keys())
        return RawMessageEnvelope(
            raw_message_id=row["raw_message_id"],
            source_chat_id=row["source_chat_id"],
            source_chat_title=row["source_chat_title"],
            source_type=row["source_type"],
            source_topic_id=row["source_topic_id"],
            telegram_message_id=row["telegram_message_id"],
            reply_to_message_id=row["reply_to_message_id"],
            raw_text=row["raw_text"],
            message_ts=datetime.fromisoformat(row["message_ts"]),
            acquired_at=datetime.fromisoformat(row["acquired_at"]),
            acquisition_mode=row["acquisition_mode"] if "acquisition_mode" in keys else "live",
            acquisition_status=row["acquisition_status"] or "ACQUIRED",
            processing_status=row["processing_status"] or "pending",
            source_trader_id=row["source_trader_id"],
            resolved_trader_id=row["resolved_trader_id"] if "resolved_trader_id" in keys else None,
            resolution_method=row["resolution_method"] if "resolution_method" in keys else None,
            resolution_detail=row["resolution_detail"] if "resolution_detail" in keys else None,
            has_media=bool(row["has_media"]),
            media_kind=row["media_kind"],
            media_mime_type=row["media_mime_type"],
            media_filename=row["media_filename"],
        )
