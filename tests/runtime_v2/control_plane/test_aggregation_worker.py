from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.aggregation_worker import AggregationWorker


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _mature() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def _seed_outbox(conn, *, chain_id: int, notification_type: str, payload: dict,
                  key: str, group: str, source_message_id: str | None = None):
    conn.execute(
        """
        INSERT INTO ops_notification_outbox
            (notification_type, destination, payload_json, priority, status, dedupe_key,
             attempts, created_at, send_after, aggregation_group, source_message_id)
        VALUES (?, 'CLEAN_LOG', ?, 'MEDIUM', 'PENDING', ?, 0, ?, ?, ?, ?)
        """,
        (notification_type, json.dumps({"chain_id": chain_id, **payload}), key,
         _mature(), _mature(), group, source_message_id),
    )


def test_single_tp_not_aggregated(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)
    with conn:
        _seed_outbox(conn, chain_id=145, notification_type="TP_FILLED",
                     payload={"tp_level": 1}, key="tp1", group="145:tp_batch")
    conn.close()
    assert AggregationWorker(db_path).run_once() == 0
