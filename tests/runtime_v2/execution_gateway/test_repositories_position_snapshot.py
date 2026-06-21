from __future__ import annotations

import sqlite3
from pathlib import Path

from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _apply_migrations_through(db_path: str, version: int) -> None:
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        if int(migration.stem[:3]) > version:
            break
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_upsert_position_snapshot_creates_row(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    repo = GatewayCommandRepository(db_path)

    repo.upsert_position_snapshot(
        account_id="acc-main",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.25,
        mark_price=67123.4,
        unrealized_pnl=42.5,
        cum_realized_pnl=12.75,
        source="rest_reconciliation",
        captured_at="2026-06-20T10:00:00+00:00",
    )

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        "cum_realized_pnl, source, captured_at "
        "FROM ops_position_snapshots"
    ).fetchone()
    conn.close()

    assert row == (
        "acc-main",
        "BTCUSDT",
        "LONG",
        0.25,
        67123.4,
        42.5,
        12.75,
        "rest_reconciliation",
        "2026-06-20T10:00:00+00:00",
    )


def test_upsert_position_snapshot_overwrites_existing_row(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    repo = GatewayCommandRepository(db_path)

    repo.upsert_position_snapshot(
        account_id="acc-main",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.25,
        mark_price=67123.4,
        unrealized_pnl=42.5,
        cum_realized_pnl=12.75,
        source="rest_reconciliation",
        captured_at="2026-06-20T10:00:00+00:00",
    )
    repo.upsert_position_snapshot(
        account_id="acc-main",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.10,
        mark_price=67000.0,
        unrealized_pnl=20.0,
        cum_realized_pnl=15.5,
        source="bulk_fetch_positions",
        captured_at="2026-06-20T10:05:00+00:00",
    )

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM ops_position_snapshots").fetchone()[0]
    row = conn.execute(
        "SELECT account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        "cum_realized_pnl, source, captured_at "
        "FROM ops_position_snapshots"
    ).fetchone()
    conn.close()

    assert count == 1
    assert row == (
        "acc-main",
        "BTCUSDT",
        "LONG",
        0.10,
        67000.0,
        20.0,
        15.5,
        "bulk_fetch_positions",
        "2026-06-20T10:05:00+00:00",
    )


def test_position_snapshot_schema_uses_composite_key_without_snapshot_id(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)

    conn = sqlite3.connect(db_path)
    table_info = conn.execute("PRAGMA table_info(ops_position_snapshots)").fetchall()
    conn.close()

    columns = {row[1] for row in table_info}
    pk_columns = [row[1] for row in table_info if row[5] > 0]

    assert "snapshot_id" not in columns
    assert {"account_id", "symbol", "side", "qty", "mark_price", "unrealized_pnl", "cum_realized_pnl"} <= columns
    assert pk_columns == ["account_id", "symbol", "side"]


def test_position_snapshot_migration_keeps_latest_legacy_row_per_key(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations_through(db_path, 18)

    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO ops_position_snapshots "
        "(account_id, symbol, side, payload_json, source, captured_at) "
        "VALUES (?,?,?,?,?,?)",
        [
            (
                "acc-main",
                "BTCUSDT",
                "LONG",
                '{"positions_count": 2, "sides_found": ["Sell", "Buy"], "qty_found": 0.25, "matched": true}',
                "rest_reconciliation",
                "2026-06-20T10:00:00+00:00",
            ),
            (
                "acc-main",
                "BTCUSDT",
                "LONG",
                '{"positions_count": 1, "sides_found": ["Buy"], "qty_found": 0.10, "matched": true}',
                "bulk_fetch_positions",
                "2026-06-20T10:05:00+00:00",
            ),
            (
                "acc-main",
                "ETHUSDT",
                "SHORT",
                '{"qty": 1.2, "mark_price": 2500.0, "unrealized_pnl": -8.0, "cum_realized_pnl": 4.5}',
                "bulk_fetch_positions",
                "2026-06-20T11:00:00+00:00",
            ),
        ],
    )
    conn.executescript(
        Path("db/ops_migrations/019_ops_position_snapshots_upsert.sql").read_text(encoding="utf-8")
    )
    btc_row = conn.execute(
        "SELECT account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        "cum_realized_pnl, source, captured_at "
        "FROM ops_position_snapshots WHERE account_id=? AND symbol=? AND side=?",
        ("acc-main", "BTCUSDT", "LONG"),
    ).fetchone()
    eth_row = conn.execute(
        "SELECT account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        "cum_realized_pnl, source, captured_at "
        "FROM ops_position_snapshots WHERE account_id=? AND symbol=? AND side=?",
        ("acc-main", "ETHUSDT", "SHORT"),
    ).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM ops_position_snapshots").fetchone()[0]
    conn.close()

    assert count == 2
    assert btc_row == (
        "acc-main",
        "BTCUSDT",
        "LONG",
        0.10,
        None,
        None,
        None,
        "bulk_fetch_positions",
        "2026-06-20T10:05:00+00:00",
    )
    assert eth_row == (
        "acc-main",
        "ETHUSDT",
        "SHORT",
        1.2,
        2500.0,
        -8.0,
        4.5,
        "bulk_fetch_positions",
        "2026-06-20T11:00:00+00:00",
    )
