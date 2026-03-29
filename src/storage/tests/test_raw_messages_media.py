from __future__ import annotations

import sqlite3

from src.storage.raw_messages import RawMessageRecord, RawMessageStore


def test_save_with_id_supports_legacy_schema_without_media_columns(tmp_path) -> None:
    db_path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE raw_messages (
              raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_chat_id TEXT NOT NULL,
              source_chat_title TEXT,
              source_type TEXT,
              source_trader_id TEXT,
              telegram_message_id INTEGER NOT NULL,
              reply_to_message_id INTEGER,
              raw_text TEXT,
              message_ts TEXT NOT NULL,
              acquired_at TEXT NOT NULL,
              acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED'
            );
            CREATE UNIQUE INDEX idx_raw_messages_dedup
            ON raw_messages(source_chat_id, telegram_message_id);
            """
        )
        conn.commit()

    store = RawMessageStore(db_path=db_path)
    result = store.save_with_id(
        RawMessageRecord(
            source_chat_id="chat-1",
            telegram_message_id=10,
            raw_text="text",
            message_ts="2026-03-29T20:00:00+00:00",
            acquired_at="2026-03-29T20:00:01+00:00",
            acquisition_status="ACQUIRED_HISTORY",
            has_media=True,
            media_kind="photo",
            media_mime_type="image/jpeg",
            media_filename="signal.jpg",
            media_blob=b"jpeg-bytes",
        )
    )

    assert result.saved is True
    stored = store.get_by_source_and_message_id("chat-1", 10)
    assert stored is not None
    assert stored.has_media is False
    assert stored.media_blob is None


def test_save_with_id_persists_media_blob_when_columns_exist(tmp_path) -> None:
    db_path = str(tmp_path / "media.sqlite3")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE raw_messages (
              raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_chat_id TEXT NOT NULL,
              source_chat_title TEXT,
              source_type TEXT,
              source_trader_id TEXT,
              telegram_message_id INTEGER NOT NULL,
              reply_to_message_id INTEGER,
              raw_text TEXT,
              message_ts TEXT NOT NULL,
              acquired_at TEXT NOT NULL,
              acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED',
              has_media INTEGER NOT NULL DEFAULT 0,
              media_kind TEXT,
              media_mime_type TEXT,
              media_filename TEXT,
              media_blob BLOB
            );
            CREATE UNIQUE INDEX idx_raw_messages_dedup
            ON raw_messages(source_chat_id, telegram_message_id);
            """
        )
        conn.commit()

    blob = b"\x89PNG\r\n\x1a\nmock"
    store = RawMessageStore(db_path=db_path)
    result = store.save_with_id(
        RawMessageRecord(
            source_chat_id="chat-1",
            telegram_message_id=11,
            raw_text="caption",
            message_ts="2026-03-29T20:00:00+00:00",
            acquired_at="2026-03-29T20:00:01+00:00",
            acquisition_status="ACQUIRED_HISTORY",
            has_media=True,
            media_kind="photo",
            media_mime_type="image/png",
            media_filename="signal.png",
            media_blob=blob,
        )
    )

    assert result.saved is True
    stored = store.get_by_source_and_message_id("chat-1", 11)
    assert stored is not None
    assert stored.has_media is True
    assert stored.media_kind == "photo"
    assert stored.media_mime_type == "image/png"
    assert stored.media_filename == "signal.png"
    assert stored.media_blob == blob
