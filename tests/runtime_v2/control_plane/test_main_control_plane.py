# tests/runtime_v2/control_plane/test_main_control_plane.py
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.runtime_v2.control_plane.bootstrap import build_control_plane


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _write_config(tmp_path: Path, *, mode: str = "auto", enabled: bool = True) -> Path:
    content = f"""\
enabled: {str(enabled).lower()}
token: "fake_token_for_test"
chat_id: -100999
delivery_mode: supergroup_topics
topics:
  commands:
    thread_id: 101
  tech_log:
    thread_id: 102
    enabled: true
    min_level: WARNING
  clean_log:
    thread_id: 103
authorized_users: []
startup:
  mode: {mode}
  restore_max_age_seconds: 300
"""
    config_file = tmp_path / "telegram_control.yaml"
    config_file.write_text(content, encoding="utf-8")
    return config_file


def test_build_control_plane_returns_none_when_disabled(tmp_path):
    config_file = _write_config(tmp_path, enabled=False)
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)

    result = build_control_plane(
        config_path=str(config_file),
        ops_db_path=db_path,
        log_path=None,
    )

    assert result is None


def test_standby_mode_applies_global_pause(tmp_path):
    config_file = _write_config(tmp_path, mode="standby")
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)

    with patch("telegram.Bot", return_value=MagicMock()):
        cp = build_control_plane(
            config_path=str(config_file),
            ops_db_path=db_path,
            log_path=None,
        )

    assert cp is not None
    assert cp.startup_plan.apply_global_block is True

    # Apply the global block as main.py would do
    cp.service.pause(scope_value=None, created_by="startup")

    # Verify active GLOBAL block exists in DB
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT scope_type, active FROM ops_control_state "
            "WHERE scope_type='GLOBAL' AND active=1"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Expected an active GLOBAL block in ops_control_state"
    assert row[0] == "GLOBAL"
    assert row[1] == 1


def test_shutdown_saves_runtime_snapshot_and_enqueues_tech_log(tmp_path):
    config_file = _write_config(tmp_path, mode="auto")
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)

    with patch("telegram.Bot", return_value=MagicMock()):
        cp = build_control_plane(
            config_path=str(config_file),
            ops_db_path=db_path,
            log_path=None,
        )

    assert cp is not None

    # Save a runtime snapshot as main.py shutdown block would do
    cp.snapshot_store.save(
        control_mode="BLOCK_NEW_ENTRIES",
        active_blocks=[],
        open_chain_count=2,
        pending_command_count=0,
        shutdown_reason="SIGTERM",
    )

    # Verify the snapshot row was persisted
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT control_mode, open_chain_count, shutdown_reason "
            "FROM ops_runtime_snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Expected a row in ops_runtime_snapshot"
    assert row[0] == "BLOCK_NEW_ENTRIES"
    assert row[1] == 2
    assert row[2] == "SIGTERM"

    # Send shutdown notification and verify outbox row
    cp.service.send_shutdown_notification()

    conn = sqlite3.connect(db_path)
    try:
        outbox_row = conn.execute(
            "SELECT destination FROM ops_notification_outbox "
            "WHERE destination='TECH_LOG' ORDER BY notification_id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert outbox_row is not None, "Expected an outbox row with destination=TECH_LOG"
    assert outbox_row[0] == "TECH_LOG"
