from __future__ import annotations

import sqlite3

from src.core.migrations import apply_migrations


def test_ops_db_is_wal_after_migrations(tmp_path):
    # ops.sqlite3 must be in WAL journal mode so the many short-lived control-plane
    # connections do not take a whole-database lock that freezes the shared event loop.
    db = str(tmp_path / "ops.sqlite3")
    apply_migrations(db_path=db, migrations_dir="db/ops_migrations")

    conn = sqlite3.connect(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    assert mode == "wal"
