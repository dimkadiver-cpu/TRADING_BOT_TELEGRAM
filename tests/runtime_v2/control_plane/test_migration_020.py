from __future__ import annotations
import sqlite3
from pathlib import Path
import pytest


def test_migration_020_adds_account_snapshot_fields(tmp_path):
    """Verify columns exist with correct types and constraints."""
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))

    # Get column info: (cid, name, type, notnull, dflt_value, pk)
    col_info = {
        row[1]: {"type": row[2], "notnull": row[3], "dflt_value": row[4]}
        for row in conn.execute("PRAGMA table_info(ops_account_snapshots)")
    }
    conn.close()

    # Check columns exist
    assert "account_unrealized_pnl_usdt" in col_info
    assert "snapshot_status" in col_info
    assert "error_code" in col_info

    # Check column types
    assert col_info["account_unrealized_pnl_usdt"]["type"].upper() == "REAL"
    assert col_info["snapshot_status"]["type"].upper() == "TEXT"
    assert col_info["error_code"]["type"].upper() == "TEXT"

    # Check snapshot_status constraints
    assert col_info["snapshot_status"]["notnull"] == 1, "snapshot_status must be NOT NULL"
    assert col_info["snapshot_status"]["dflt_value"] in ("'OK'", "OK"), "snapshot_status must have DEFAULT 'OK'"


def test_migration_020_snapshot_status_default(tmp_path):
    """Verify snapshot_status defaults to 'OK' when not provided."""
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


def test_migration_020_snapshot_status_not_null_enforced(tmp_path):
    """Verify that NULL value for snapshot_status raises IntegrityError."""
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ops_account_snapshots "
            "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
            "total_margin_used_usdt, snapshot_status, source, captured_at, payload_json) "
            "VALUES ('main', 100.0, 90.0, 5.0, 10.0, NULL, 'test', '2026-01-01T00:00:00+00:00', '{}')"
        )
        conn.commit()
    conn.close()


def test_migration_020_index_created(tmp_path):
    """Verify that the index idx_ops_account_snapshots_account_captured exists."""
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))

    # Get index info: (seq, name, unique, origin, partial)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(ops_account_snapshots)")}
    conn.close()

    assert "idx_ops_account_snapshots_account_captured" in indexes
