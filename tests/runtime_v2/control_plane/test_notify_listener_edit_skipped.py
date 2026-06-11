from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.outbox_writer import notify_listener_edit_skipped


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


_CONTEXT = {
    "chat": "-100123",
    "topic": 4,
    "msg_id": 7306,
    "raw_message_id": 85,
    "edit_ts": 1781100000,
    "new_text_preview": "LONG AVAXUSDT entry 30",
}


def test_writes_pending_tech_log_row(ops_db):
    notify_listener_edit_skipped(ops_db, _CONTEXT)
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT notification_type, destination, priority, status, payload_json, dedupe_key "
        "FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    notification_type, destination, priority, status, payload_json, dedupe_key = row
    assert notification_type == "LISTENER_EDIT_SKIPPED"
    assert destination == "TECH_LOG"
    assert priority == "HIGH"
    assert status == "PENDING"
    assert dedupe_key == "edit_skipped:-100123:7306:1781100000"
    payload = json.loads(payload_json)
    assert payload["level"] == "WARNING"
    assert payload["category"] == "Listener"
    assert payload["context"]["raw_message_id"] == 85


def test_same_edit_is_deduplicated(ops_db):
    notify_listener_edit_skipped(ops_db, _CONTEXT)
    notify_listener_edit_skipped(ops_db, _CONTEXT)
    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 1


def test_distinct_edits_notify_again(ops_db):
    notify_listener_edit_skipped(ops_db, _CONTEXT)
    notify_listener_edit_skipped(ops_db, {**_CONTEXT, "edit_ts": 1781100060})
    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 2
