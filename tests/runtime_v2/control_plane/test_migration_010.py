from __future__ import annotations

import sqlite3
from pathlib import Path


def test_migration_010_adds_pnl_columns(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(ops_trade_chains)")}
    conn.close()
    assert "cumulative_gross_pnl" in columns
    assert "cumulative_fees" in columns
    assert "cumulative_funding" in columns
    assert "allocated_margin" in columns
