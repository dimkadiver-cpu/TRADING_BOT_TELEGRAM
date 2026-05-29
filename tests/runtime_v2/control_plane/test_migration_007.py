from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _apply_migrations(db_path: str, migrations_dir: Path) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(migrations_dir.glob("*.sql")):
        if f.name > "007_ops_control_plane.sql":
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


def test_migration_creates_control_plane_tables(ops_db):
    conn = sqlite3.connect(ops_db)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert "ops_notification_outbox" in tables
    assert "ops_telegram_control_commands" in tables
    assert "ops_config_overrides" in tables
    assert "ops_runtime_snapshot" in tables


def test_outbox_has_unique_dedupe_key(ops_db):
    conn = sqlite3.connect(ops_db)
    cols = _columns(conn, "ops_notification_outbox")
    conn.execute(
        "INSERT INTO ops_notification_outbox "
        "(notification_type, destination, payload_json, priority, dedupe_key, created_at) "
        "VALUES ('X','CLEAN_LOG','{}','LOW','k1','t')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ops_notification_outbox "
            "(notification_type, destination, payload_json, priority, dedupe_key, created_at) "
            "VALUES ('Y','TECH_LOG','{}','LOW','k1','t')"
        )
        conn.commit()
    conn.close()

    assert {"notification_id", "destination", "dedupe_key", "status", "attempts"} <= cols


def test_control_commands_have_key_columns(ops_db):
    conn = sqlite3.connect(ops_db)
    cols = _columns(conn, "ops_telegram_control_commands")
    conn.close()

    assert {
        "command_request_id",
        "telegram_user_id",
        "command_text",
        "status",
    } <= cols
