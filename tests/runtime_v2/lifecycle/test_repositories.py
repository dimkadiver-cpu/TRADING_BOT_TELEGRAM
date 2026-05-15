# tests/runtime_v2/lifecycle/test_repositories.py
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def test_migration_creates_ops_tables(ops_db):
    conn = sqlite3.connect(ops_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "ops_trade_chains" in tables
    assert "ops_lifecycle_events" in tables
    assert "ops_execution_commands" in tables
    assert "ops_exchange_events" in tables
    assert "ops_control_state" in tables
