# tests/runtime_v2/control_plane/test_audit_store.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.audit_store import CommandAuditStore


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


def test_record_then_update(ops_db):
    store = CommandAuditStore(ops_db)
    store.record(
        command_request_id="-100999:55",
        chat_id="-100999", message_thread_id="101",
        telegram_user_id="42", telegram_username="op",
        command_text="/status", command_name="status",
        status="RECEIVED",
    )
    store.update_status("-100999:55", status="EXECUTED", execution_result="ok")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, execution_result, command_name FROM ops_telegram_control_commands "
        "WHERE command_request_id=?", ("-100999:55",),
    ).fetchone()
    conn.close()
    assert row == ("EXECUTED", "ok", "status")


def test_record_rejected_unauthorized(ops_db):
    store = CommandAuditStore(ops_db)
    store.record(
        command_request_id="-100999:77",
        chat_id="-100999", message_thread_id="101",
        telegram_user_id="7", telegram_username=None,
        command_text="/status", command_name=None,
        status="REJECTED", reject_reason="unauthorized_user",
    )
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, reject_reason FROM ops_telegram_control_commands "
        "WHERE command_request_id=?", ("-100999:77",),
    ).fetchone()
    conn.close()
    assert row == ("REJECTED", "unauthorized_user")


def test_record_is_idempotent_on_request_id(ops_db):
    store = CommandAuditStore(ops_db)
    for _ in range(2):
        store.record(
            command_request_id="-100999:88",
            chat_id="-100999", message_thread_id="101",
            telegram_user_id="42", telegram_username="op",
            command_text="/status", command_name="status",
            status="RECEIVED",
        )
    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_telegram_control_commands WHERE command_request_id=?",
        ("-100999:88",),
    ).fetchone()[0]
    conn.close()
    assert count == 1
