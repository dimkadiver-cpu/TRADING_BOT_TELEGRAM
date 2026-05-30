# tests/runtime_v2/control_plane/test_migration_008.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _apply_migrations(db_path: str, migrations_dir: Path) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(migrations_dir.glob("*.sql")):
        if f.name > "008_ops_clean_log_tracking.sql":
            break
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path, Path("db/ops_migrations"))
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_clean_log_tracking_table_exists(ops_db):
    conn = sqlite3.connect(ops_db)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "ops_clean_log_tracking" in tables


def test_clean_log_tracking_has_required_columns(ops_db):
    conn = sqlite3.connect(ops_db)
    cols = _columns(conn, "ops_clean_log_tracking")
    conn.close()

    required = {
        "trade_chain_id",
        "clean_log_root_message_id",
        "clean_log_last_message_id",
        "telegram_chat_id",
        "telegram_thread_id",
        "original_message_link",
        "last_clean_log_event_type",
        "last_clean_log_sent_at",
        "updated_at",
    }
    assert required <= cols


def test_clean_log_tracking_primary_key_is_trade_chain_id(ops_db):
    conn = sqlite3.connect(ops_db)
    pk_cols = {
        r[1] for r in conn.execute(
            "PRAGMA table_info(ops_clean_log_tracking)"
        ).fetchall() if r[5] == 1  # pk flag
    }
    conn.close()
    assert pk_cols == {"trade_chain_id"}


def test_clean_log_tracking_row_insert_and_query(ops_db):
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """INSERT INTO ops_clean_log_tracking
           (trade_chain_id, clean_log_root_message_id, clean_log_last_message_id,
            telegram_chat_id, telegram_thread_id, last_clean_log_event_type,
            last_clean_log_sent_at, updated_at)
           VALUES (1, '100', '100', '-1001234', '5', 'SIGNAL_ACCEPTED', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
    )
    conn.commit()
    row = conn.execute(
        "SELECT clean_log_root_message_id, clean_log_last_message_id FROM ops_clean_log_tracking WHERE trade_chain_id=1"
    ).fetchone()
    conn.close()
    assert row == ("100", "100")
