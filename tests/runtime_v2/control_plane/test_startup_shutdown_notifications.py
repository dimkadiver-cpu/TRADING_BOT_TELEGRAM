from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.service import RuntimeControlService


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def test_startup_notification_writes_to_outbox(ops_db):
    service = RuntimeControlService(ops_db_path=ops_db)
    service.send_startup_notification()

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT notification_type, destination, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "RUNTIME_STARTUP"
    assert row[1] == "TECH_LOG"
    p = json.loads(row[2])
    assert p["level"] == "INFO"
    assert "Runtime" in p["category"]


def test_shutdown_notification_writes_to_outbox(ops_db):
    service = RuntimeControlService(ops_db_path=ops_db)
    service.send_shutdown_notification(reason="SIGTERM")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT notification_type, destination, payload_json FROM ops_notification_outbox "
        "WHERE notification_type='RUNTIME_SHUTDOWN'"
    ).fetchone()
    conn.close()

    assert row is not None
    p = json.loads(row[2])
    assert p["level"] == "INFO"
    assert "SIGTERM" in p["description"]


def test_shutdown_reads_open_chains_count(ops_db):
    # Seed one open chain
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            "management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
            "VALUES (1,1,1,1,'t','m','BTC/USDT','LONG','OPEN','ONE_SHOT','{}','{}','{}',?,?)",
            (now, now),
        )
    conn.close()

    service = RuntimeControlService(ops_db_path=ops_db)
    service.send_shutdown_notification(reason="TEST")

    conn = sqlite3.connect(ops_db)
    payload_json = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='RUNTIME_SHUTDOWN'"
    ).fetchone()[0]
    conn.close()
    p = json.loads(payload_json)
    assert p["details"]["open_chains"] == 1
