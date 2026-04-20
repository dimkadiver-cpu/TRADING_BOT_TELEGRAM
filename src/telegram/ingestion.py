"""Single-entry raw message ingestion from Telegram listener."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from src.core.timeutils import utc_now_iso
from src.storage.raw_messages import RawMessageRecord, RawMessageStore


@dataclass(slots=True)
class TelegramIncomingMessage:
    source_chat_id: str
    telegram_message_id: int
    message_ts: datetime
    raw_text: str | None = None
    source_chat_title: str | None = None
    source_type: str | None = None
    source_trader_id: str | None = None
    reply_to_message_id: int | None = None
    acquisition_status: str = "ACQUIRED_ELIGIBLE"
    source_topic_id: int | None = None
    has_media: bool = False
    media_kind: str | None = None
    media_mime_type: str | None = None
    media_filename: str | None = None
    media_blob: bytes | None = None


@dataclass(slots=True)
class IngestionResult:
    saved: bool
    raw_message_id: int | None


class RawMessageIngestionService:
    def __init__(self, store: RawMessageStore, logger: logging.Logger) -> None:
        self._store = store
        self._logger = logger

    @property
    def store(self) -> RawMessageStore:
        return self._store

    def ingest(self, incoming: TelegramIncomingMessage) -> IngestionResult:
        try:
            text = incoming.raw_text
            if text is None:
                self._logger.warning(
                    "raw message without text | chat=%s msg_id=%s",
                    incoming.source_chat_id,
                    incoming.telegram_message_id,
                )

            record = RawMessageRecord(
                source_chat_id=incoming.source_chat_id,
                source_chat_title=incoming.source_chat_title,
                source_type=incoming.source_type,
                source_trader_id=incoming.source_trader_id,
                telegram_message_id=incoming.telegram_message_id,
                reply_to_message_id=incoming.reply_to_message_id,
                raw_text=text,
                message_ts=self._as_utc_iso(incoming.message_ts),
                acquired_at=utc_now_iso(),
                acquisition_status=incoming.acquisition_status,
                source_topic_id=incoming.source_topic_id,
                has_media=incoming.has_media,
                media_kind=incoming.media_kind,
                media_mime_type=incoming.media_mime_type,
                media_filename=incoming.media_filename,
                media_blob=incoming.media_blob,
            )
            save_result = self._store.save_with_id(record)
            if not save_result.saved:
                self._logger.info(
                    "duplicate raw message skipped | chat=%s msg_id=%s",
                    incoming.source_chat_id,
                    incoming.telegram_message_id,
                )
            return IngestionResult(saved=save_result.saved, raw_message_id=save_result.raw_message_id)
        except Exception:
            self._logger.exception(
                "failed to persist raw message | chat=%s msg_id=%s",
                incoming.source_chat_id,
                incoming.telegram_message_id,
            )
            return IngestionResult(saved=False, raw_message_id=None)

    @staticmethod
    def _as_utc_iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="seconds")
