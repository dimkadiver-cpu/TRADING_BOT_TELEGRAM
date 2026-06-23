from __future__ import annotations
import sqlite3
from pathlib import Path


def test_migration_020_adds_account_snapshot_fields(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_account_snapshots)")}
    conn.close()
    assert "account_unrealized_pnl_usdt" in columns
    assert "snapshot_status" in columns
    assert "error_code" in columns


def test_migration_020_snapshot_status_default(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        "total_margin_used_usdt, source, captured_at, payload_json) "
        "VALUES ('main', 100.0, 90.0, 5.0, 10.0, 'test', '2026-01-01T00:00:00+00:00', '{}')"
    )
    conn.commit()
    row = conn.execute("SELECT snapshot_status FROM ops_account_snapshots").fetchone()
    conn.close()
    assert row[0] == "OK"
