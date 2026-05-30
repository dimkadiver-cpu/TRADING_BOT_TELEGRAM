from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.snapshot_store import SnapshotStore


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path) -> str:
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def test_save_and_get_latest_snapshot(ops_db: str) -> None:
    store = SnapshotStore(ops_db)
    store.save(
        control_mode="BLOCK_NEW_ENTRIES",
        active_blocks=["GLOBAL:BLOCK_NEW_ENTRIES"],
        open_chain_count=3,
        pending_command_count=2,
        shutdown_reason="SIGTERM",
    )

    snap = store.get_latest()

    assert snap is not None
    assert snap.control_mode == "BLOCK_NEW_ENTRIES"
    assert snap.open_chain_count == 3
    assert snap.pending_command_count == 2
    assert snap.shutdown_reason == "SIGTERM"
    assert snap.active_blocks_json == '["GLOBAL:BLOCK_NEW_ENTRIES"]'


def test_is_stale_uses_snapshot_age(ops_db: str) -> None:
    store = SnapshotStore(ops_db)
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=301)
    fresh_at = datetime.now(timezone.utc) - timedelta(seconds=299)

    assert store.is_stale(stale_at, max_age_seconds=300) is True
    assert store.is_stale(fresh_at, max_age_seconds=300) is False

