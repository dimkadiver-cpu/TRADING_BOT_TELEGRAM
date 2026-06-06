from __future__ import annotations

import sqlite3
from pathlib import Path


def test_migration_013_adds_roi_peak_margin_columns(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_trade_chains)")}
    conn.close()
    assert {"initial_risk_amount", "peak_margin_used"} <= columns
