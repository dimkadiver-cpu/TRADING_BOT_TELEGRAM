from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.bootstrap import build_control_plane
from src.runtime_v2.control_plane.models import RuntimeSnapshot
from src.runtime_v2.control_plane.snapshot_store import SnapshotStore
from src.runtime_v2.control_plane.startup import resolve_startup


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


def _write_config(tmp_path: Path, *, enabled: bool = True, mode: str = "auto") -> str:
    path = tmp_path / "telegram_control.yaml"
    path.write_text(
        (
            f"enabled: {str(enabled).lower()}\n"
            "token: test-token\n"
            "chat_id: -100999\n"
            "authorized_users: [42]\n"
            "topics:\n"
            "  commands:\n"
            "    thread_id: 101\n"
            "  tech_log:\n"
            "    thread_id: 102\n"
            "    debug_max_duration_minutes: 15\n"
            "  clean_log:\n"
            "    thread_id: 103\n"
            "startup:\n"
            f"  mode: {mode}\n"
            "  restore_max_age_seconds: 300\n"
        ),
        encoding="utf-8",
    )
    return str(path)


def test_restore_stale_snapshot_falls_back_to_auto(ops_db: str) -> None:
    store = SnapshotStore(ops_db)
    store.save(
        control_mode="BLOCK_NEW_ENTRIES",
        active_blocks=["GLOBAL:BLOCK_NEW_ENTRIES"],
        open_chain_count=1,
        pending_command_count=0,
        shutdown_reason="SIGTERM",
    )

    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "UPDATE ops_runtime_snapshot "
            "SET snapshot_at='2026-05-30T11:51:40+00:00' "
            "WHERE id = (SELECT MAX(id) FROM ops_runtime_snapshot)"
        )
    conn.close()

    plan = resolve_startup(
        mode="restore",
        restore_max_age_seconds=300,
        latest_snapshot=store.get_latest(),
    )

    assert plan.mode == "auto"
    assert plan.apply_global_block is False
    assert plan.fell_back is True


def test_resolve_startup_auto_mode() -> None:
    plan = resolve_startup(
        mode="auto",
        restore_max_age_seconds=300,
        latest_snapshot=None,
    )

    assert plan.mode == "auto"
    assert plan.apply_global_block is False
    assert plan.fell_back is False


def test_resolve_startup_standby_mode() -> None:
    plan = resolve_startup(
        mode="standby",
        restore_max_age_seconds=300,
        latest_snapshot=None,
    )

    assert plan.mode == "standby"
    assert plan.apply_global_block is True
    assert plan.fell_back is False


def test_resolve_startup_restore_without_snapshot_falls_back() -> None:
    plan = resolve_startup(
        mode="restore",
        restore_max_age_seconds=300,
        latest_snapshot=None,
    )

    assert plan.mode == "auto"
    assert plan.apply_global_block is False
    assert plan.fell_back is True


def test_resolve_startup_restore_fresh_unblocked_snapshot() -> None:
    snapshot = RuntimeSnapshot(
        snapshot_at="2026-05-30T11:59:00+00:00",
        control_mode="NONE",
        active_blocks_json="[]",
        open_chain_count=0,
        pending_command_count=0,
    )

    plan = resolve_startup(
        mode="restore",
        restore_max_age_seconds=10_000_000,
        latest_snapshot=snapshot,
    )

    assert plan.mode == "restore"
    assert plan.apply_global_block is False
    assert plan.fell_back is False


def test_resolve_startup_restore_fresh_blocked_snapshot() -> None:
    snapshot = RuntimeSnapshot(
        snapshot_at="2026-05-30T11:59:00+00:00",
        control_mode="BLOCK_NEW_ENTRIES",
        active_blocks_json='["GLOBAL:BLOCK_NEW_ENTRIES"]',
        open_chain_count=1,
        pending_command_count=0,
    )

    plan = resolve_startup(
        mode="restore",
        restore_max_age_seconds=10_000_000,
        latest_snapshot=snapshot,
    )

    assert plan.mode == "restore"
    assert plan.apply_global_block is True
    assert plan.fell_back is False


def test_build_control_plane_returns_none_when_disabled(tmp_path: Path, ops_db: str) -> None:
    config_path = _write_config(tmp_path, enabled=False)

    control_plane = build_control_plane(
        config_path=config_path,
        ops_db_path=ops_db,
        log_path=str(tmp_path / "bot.log"),
    )

    assert control_plane is None


def test_build_control_plane_returns_none_when_config_is_invalid(
    tmp_path: Path,
    ops_db: str,
) -> None:
    config_path = tmp_path / "telegram_control.yaml"
    config_path.write_text("enabled: true\nchat_id: -100999\n", encoding="utf-8")

    control_plane = build_control_plane(
        config_path=str(config_path),
        ops_db_path=ops_db,
        log_path=str(tmp_path / "bot.log"),
    )

    assert control_plane is None


def test_build_control_plane_resets_sending_and_derives_restore_fallback(
    tmp_path: Path,
    ops_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, mode="restore")
    store = SnapshotStore(ops_db)
    store.save(
        control_mode="BLOCK_NEW_ENTRIES",
        active_blocks=["GLOBAL:BLOCK_NEW_ENTRIES"],
        open_chain_count=2,
        pending_command_count=1,
        shutdown_reason="SIGTERM",
    )

    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "UPDATE ops_runtime_snapshot "
            "SET snapshot_at='2026-05-30T11:51:40+00:00' "
            "WHERE id = (SELECT MAX(id) FROM ops_runtime_snapshot)"
        )
        conn.execute(
            "INSERT INTO ops_notification_outbox "
            "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at) "
            "VALUES ('X','TECH_LOG','{}','MEDIUM','SENDING','bootstrap:red',0,'2026-05-30T12:00:00+00:00')"
        )

    class FakeSender:
        async def send(self, *, chat_id, thread_id, text, silent=False, reply_to_message_id=None):
            return "123"

    monkeypatch.setattr(
        "src.runtime_v2.control_plane.bootstrap._create_sender",
        lambda token: FakeSender(),
    )

    control_plane = build_control_plane(
        config_path=config_path,
        ops_db_path=ops_db,
        log_path=str(tmp_path / "bot.log"),
    )

    assert control_plane is not None
    assert control_plane.startup_plan.mode == "auto"
    assert control_plane.startup_plan.fell_back is True

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_notification_outbox WHERE dedupe_key='bootstrap:red'"
    ).fetchone()
    conn.close()
    assert status == ("PENDING",)
