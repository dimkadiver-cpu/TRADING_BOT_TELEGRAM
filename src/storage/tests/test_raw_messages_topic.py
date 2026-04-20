"""Tests for source_topic_id persistence in RawMessageStore."""

from __future__ import annotations

import sqlite3

import pytest

from src.storage.raw_messages import RawMessageRecord, RawMessageStore


def _schema_without_topic(conn: sqlite3.Connection) -> None:
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


def _schema_with_topic(conn: sqlite3.Connection) -> None:
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
          source_topic_id INTEGER
        );
        CREATE UNIQUE INDEX idx_raw_messages_dedup
        ON raw_messages(source_chat_id, telegram_message_id);
        CREATE INDEX idx_raw_messages_topic
        ON raw_messages(source_chat_id, source_topic_id, telegram_message_id);
        """
    )
    conn.commit()


def _record(
    source_chat_id: str = "chat-1",
    telegram_message_id: int = 1,
    source_topic_id: int | None = None,
) -> RawMessageRecord:
    return RawMessageRecord(
        source_chat_id=source_chat_id,
        telegram_message_id=telegram_message_id,
        message_ts="2026-04-20T10:00:00+00:00",
        acquired_at="2026-04-20T10:00:01+00:00",
        source_topic_id=source_topic_id,
    )


# ---------------------------------------------------------------------------
# save + retrieve with topic column present
# ---------------------------------------------------------------------------


def test_save_and_get_with_topic_id(tmp_path) -> None:
    db_path = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db_path) as conn:
        _schema_with_topic(conn)

    store = RawMessageStore(db_path=db_path)
    result = store.save_with_id(_record(source_topic_id=3))

    assert result.saved is True
    stored = store.get_by_source_and_message_id("chat-1", 1)
    assert stored is not None
    assert stored.source_topic_id == 3


def test_save_and_get_with_topic_id_none(tmp_path) -> None:
    db_path = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db_path) as conn:
        _schema_with_topic(conn)

    store = RawMessageStore(db_path=db_path)
    store.save_with_id(_record(source_topic_id=None))

    stored = store.get_by_source_and_message_id("chat-1", 1)
    assert stored is not None
    assert stored.source_topic_id is None


def test_save_topic_id_general(tmp_path) -> None:
    db_path = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db_path) as conn:
        _schema_with_topic(conn)

    store = RawMessageStore(db_path=db_path)
    store.save_with_id(_record(source_topic_id=1))

    stored = store.get_by_source_and_message_id("chat-1", 1)
    assert stored is not None
    assert stored.source_topic_id == 1


# ---------------------------------------------------------------------------
# backward compatibility — legacy schema without source_topic_id column
# ---------------------------------------------------------------------------


def test_save_with_topic_id_on_legacy_schema_still_saves(tmp_path) -> None:
    db_path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(db_path) as conn:
        _schema_without_topic(conn)

    store = RawMessageStore(db_path=db_path)
    result = store.save_with_id(_record(source_topic_id=5))

    assert result.saved is True


def test_get_on_legacy_schema_returns_topic_id_none(tmp_path) -> None:
    db_path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(db_path) as conn:
        _schema_without_topic(conn)

    store = RawMessageStore(db_path=db_path)
    store.save_with_id(_record(source_topic_id=5))

    stored = store.get_by_source_and_message_id("chat-1", 1)
    assert stored is not None
    assert stored.source_topic_id is None


# ---------------------------------------------------------------------------
# multiple messages, different topics
# ---------------------------------------------------------------------------


def test_multiple_messages_different_topics(tmp_path) -> None:
    db_path = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db_path) as conn:
        _schema_with_topic(conn)

    store = RawMessageStore(db_path=db_path)
    store.save_with_id(_record(telegram_message_id=10, source_topic_id=3))
    store.save_with_id(_record(telegram_message_id=11, source_topic_id=4))
    store.save_with_id(_record(telegram_message_id=12, source_topic_id=None))

    assert store.get_by_source_and_message_id("chat-1", 10).source_topic_id == 3
    assert store.get_by_source_and_message_id("chat-1", 11).source_topic_id == 4
    assert store.get_by_source_and_message_id("chat-1", 12).source_topic_id is None
