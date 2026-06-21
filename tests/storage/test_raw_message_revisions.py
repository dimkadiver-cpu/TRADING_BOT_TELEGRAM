from __future__ import annotations

from datetime import datetime, timezone
import logging
import sqlite3
from pathlib import Path

from src.storage.raw_message_revisions import RawMessageRevisionStore
from src.storage.raw_messages import RawMessageStore
from src.telegram.ingestion import RawMessageIngestionService, TelegramIncomingMessage


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_ingest_persists_initial_raw_revision(tmp_path) -> None:
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)

    store = RawMessageStore(db_path)
    revision_store = RawMessageRevisionStore(db_path)
    service = RawMessageIngestionService(
        store=store,
        revision_store=revision_store,
        logger=logging.getLogger("test"),
    )

    incoming = TelegramIncomingMessage(
        source_chat_id="-100123",
        telegram_message_id=42,
        message_ts=datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc),
        raw_text="BUY BTCUSDT",
        source_chat_title="Test",
        source_type="channel",
        reply_to_message_id=None,
        acquisition_status="ACQUIRED_ELIGIBLE",
        source_topic_id=7,
        has_media=False,
    )

    result = service.ingest(incoming)

    assert result.saved is True
    assert result.raw_message_id is not None
    revisions = revision_store.list_by_raw_message_id(result.raw_message_id)
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.revision_kind == "initial"
    assert revision.run_context == "live"
    assert revision.raw_text == "BUY BTCUSDT"
    assert revision.applied_to_current is True


def test_duplicate_ingest_does_not_duplicate_initial_revision(tmp_path) -> None:
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)

    store = RawMessageStore(db_path)
    revision_store = RawMessageRevisionStore(db_path)
    service = RawMessageIngestionService(
        store=store,
        revision_store=revision_store,
        logger=logging.getLogger("test"),
    )

    incoming = TelegramIncomingMessage(
        source_chat_id="-100123",
        telegram_message_id=42,
        message_ts=datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc),
        raw_text="BUY BTCUSDT",
        source_chat_title="Test",
        source_type="channel",
        reply_to_message_id=None,
        acquisition_status="ACQUIRED_ELIGIBLE",
        source_topic_id=7,
        has_media=False,
    )

    first = service.ingest(incoming)
    second = service.ingest(incoming)

    assert first.saved is True
    assert second.saved is False
    assert first.raw_message_id == second.raw_message_id
    revisions = revision_store.list_by_raw_message_id(first.raw_message_id)
    assert len(revisions) == 1


def test_append_deleted_persists_deleted_revision(tmp_path) -> None:
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)

    store = RawMessageStore(db_path)
    revision_store = RawMessageRevisionStore(db_path)
    service = RawMessageIngestionService(
        store=store,
        revision_store=revision_store,
        logger=logging.getLogger("test"),
    )

    incoming = TelegramIncomingMessage(
        source_chat_id="-100123",
        telegram_message_id=42,
        message_ts=datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc),
        raw_text="BUY BTCUSDT",
        source_chat_title="Test",
        source_type="channel",
        reply_to_message_id=None,
        acquisition_status="ACQUIRED_ELIGIBLE",
        source_topic_id=7,
        has_media=False,
    )

    result = service.ingest(incoming)
    assert result.raw_message_id is not None

    revision_store.append_deleted(
        raw_message_id=result.raw_message_id,
        source_chat_id="-100123",
        telegram_message_id=42,
        raw_text="BUY BTCUSDT",
        message_ts=datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc).isoformat(),
        run_context="delete:1750503600",
        acquisition_status="ACQUIRED_ELIGIBLE",
        reply_to_message_id=None,
        source_topic_id=7,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
        applied_to_current=False,
    )

    revisions = revision_store.list_by_raw_message_id(result.raw_message_id)
    assert len(revisions) == 2
    revision = revisions[1]
    assert revision.revision_kind == "deleted"
    assert revision.run_context == "delete:1750503600"
    assert revision.raw_text == "BUY BTCUSDT"
    assert revision.applied_to_current is False
