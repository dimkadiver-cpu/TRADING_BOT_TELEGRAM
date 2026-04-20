"""Tests for topic-aware checkpoint and stale messages in ProcessingStatusStore (WP4)."""

from __future__ import annotations

import sqlite3

import pytest

from src.storage.processing_status import ProcessingStatusStore, StaleMessage


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SCHEMA_WITH_TOPIC = """
CREATE TABLE raw_messages (
  raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_chat_id TEXT NOT NULL,
  telegram_message_id INTEGER NOT NULL,
  raw_text TEXT,
  message_ts TEXT NOT NULL DEFAULT '2026-01-01',
  acquired_at TEXT NOT NULL DEFAULT '2026-01-01',
  acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED',
  processing_status TEXT NOT NULL DEFAULT 'pending',
  source_trader_id TEXT,
  reply_to_message_id INTEGER,
  source_topic_id INTEGER
);
CREATE UNIQUE INDEX idx_raw_messages_dedup
ON raw_messages(source_chat_id, telegram_message_id);
"""

_SCHEMA_WITHOUT_TOPIC = """
CREATE TABLE raw_messages (
  raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_chat_id TEXT NOT NULL,
  telegram_message_id INTEGER NOT NULL,
  raw_text TEXT,
  message_ts TEXT NOT NULL DEFAULT '2026-01-01',
  acquired_at TEXT NOT NULL DEFAULT '2026-01-01',
  acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED',
  processing_status TEXT NOT NULL DEFAULT 'pending',
  source_trader_id TEXT,
  reply_to_message_id INTEGER
);
CREATE UNIQUE INDEX idx_raw_messages_dedup
ON raw_messages(source_chat_id, telegram_message_id);
"""


def _db_with_topic(tmp_path, rows: list[tuple]) -> str:
    """Create DB with source_topic_id column and insert rows.
    Row format: (source_chat_id, telegram_message_id, source_topic_id, processing_status)
    """
    db_path = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA_WITH_TOPIC)
        conn.executemany(
            "INSERT INTO raw_messages(source_chat_id, telegram_message_id, source_topic_id, processing_status) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
    return db_path


def _db_without_topic(tmp_path, rows: list[tuple]) -> str:
    """Create legacy DB without source_topic_id and insert rows.
    Row format: (source_chat_id, telegram_message_id, processing_status)
    """
    db_path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA_WITHOUT_TOPIC)
        conn.executemany(
            "INSERT INTO raw_messages(source_chat_id, telegram_message_id, processing_status) VALUES (?,?,?)",
            rows,
        )
        conn.commit()
    return db_path


# ---------------------------------------------------------------------------
# get_last_telegram_message_id — topic-aware
# ---------------------------------------------------------------------------


def test_last_id_topic_specific(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [
        ("-1001", 10, 3, "done"),
        ("-1001", 11, 3, "done"),
        ("-1001", 5, 4, "done"),
    ])
    store = ProcessingStatusStore(db_path=db_path)
    assert store.get_last_telegram_message_id("-1001", topic_id=3) == 11
    assert store.get_last_telegram_message_id("-1001", topic_id=4) == 5


def test_last_id_forum_wide_only_null_rows(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [
        ("-1001", 20, None, "done"),
        ("-1001", 25, None, "done"),
        ("-1001", 7, 3, "done"),   # topic-specific, should not affect forum-wide
    ])
    store = ProcessingStatusStore(db_path=db_path)
    assert store.get_last_telegram_message_id("-1001", topic_id=None) == 25


def test_last_id_topic_specific_vs_forum_wide_independent(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [
        ("-1001", 100, None, "done"),
        ("-1001", 50, 3, "done"),
    ])
    store = ProcessingStatusStore(db_path=db_path)
    assert store.get_last_telegram_message_id("-1001", topic_id=None) == 100
    assert store.get_last_telegram_message_id("-1001", topic_id=3) == 50


def test_last_id_no_messages_for_topic_returns_none(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [
        ("-1001", 10, 3, "done"),
    ])
    store = ProcessingStatusStore(db_path=db_path)
    assert store.get_last_telegram_message_id("-1001", topic_id=99) is None


def test_last_id_legacy_schema_falls_back_to_chat_level(tmp_path) -> None:
    db_path = _db_without_topic(tmp_path, [
        ("-1001", 10, "done"),
        ("-1001", 20, "done"),
    ])
    store = ProcessingStatusStore(db_path=db_path)
    # Falls back to chat-level — topic_id param is ignored
    assert store.get_last_telegram_message_id("-1001", topic_id=3) == 20
    assert store.get_last_telegram_message_id("-1001", topic_id=None) == 20


def test_last_id_unknown_chat_returns_none(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [("-1001", 10, 3, "done")])
    store = ProcessingStatusStore(db_path=db_path)
    assert store.get_last_telegram_message_id("-9999", topic_id=3) is None


# ---------------------------------------------------------------------------
# get_stale_messages — source_topic_id propagation
# ---------------------------------------------------------------------------


def test_stale_messages_carry_topic_id(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [
        ("-1001", 10, 3, "pending"),
        ("-1001", 11, None, "processing"),
    ])
    store = ProcessingStatusStore(db_path=db_path)
    stale = store.get_stale_messages()
    assert len(stale) == 2
    assert stale[0].source_topic_id == 3
    assert stale[1].source_topic_id is None


def test_stale_messages_done_not_included(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [
        ("-1001", 10, 3, "done"),
        ("-1001", 11, 3, "pending"),
    ])
    store = ProcessingStatusStore(db_path=db_path)
    stale = store.get_stale_messages()
    assert len(stale) == 1
    assert stale[0].telegram_message_id == 11


def test_stale_messages_legacy_schema_topic_is_none(tmp_path) -> None:
    db_path = _db_without_topic(tmp_path, [
        ("-1001", 10, "pending"),
        ("-1001", 11, "processing"),
    ])
    store = ProcessingStatusStore(db_path=db_path)
    stale = store.get_stale_messages()
    assert len(stale) == 2
    assert all(s.source_topic_id is None for s in stale)


def test_stale_messages_empty(tmp_path) -> None:
    db_path = _db_with_topic(tmp_path, [("-1001", 10, 3, "done")])
    store = ProcessingStatusStore(db_path=db_path)
    assert store.get_stale_messages() == []
