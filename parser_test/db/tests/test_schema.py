from __future__ import annotations

import sqlite3

import pytest

from parser_test.db.schema import apply_parser_test_schema


def _make_memory_conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def test_apply_schema_creates_raw_messages():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "raw_messages" in tables


def test_apply_schema_creates_parser_runs():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "parser_runs" in tables


def test_apply_schema_creates_parser_results_v2():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "parser_results_v2" in tables


def test_apply_schema_is_idempotent():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "parser_results_v2" in tables


def test_raw_messages_has_source_topic_id_column():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_messages)")}
    assert "source_topic_id" in cols


def test_parser_results_v2_unique_run_raw_message():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    conn.execute(
        "INSERT INTO raw_messages (source_chat_id, telegram_message_id, message_ts, acquired_at) VALUES ('c1', 1, '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO parser_runs (started_at, parser_system) VALUES ('2026-01-01', 'parser_v2')"
    )
    conn.execute(
        "INSERT INTO parser_results_v2 (run_id, raw_message_id, error_status, created_at) VALUES (1, 1, 'OK', '2026-01-01')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO parser_results_v2 (run_id, raw_message_id, error_status, created_at) VALUES (1, 1, 'OK', '2026-01-01')"
        )
