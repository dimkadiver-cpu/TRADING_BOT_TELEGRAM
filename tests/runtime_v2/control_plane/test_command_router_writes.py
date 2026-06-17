from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.models import (
    AccountConfig, AccountTopicsConfig, CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig,
)
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.telegram_bot import CommandRouter
from src.runtime_v2.lifecycle.repositories import ControlStateRepository


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _router(ops_db):
    cfg = ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={"main": AccountConfig(
            chat_id=-100999,
            topics=AccountTopicsConfig(
                commands=TopicConfig(thread_id=101),
                tech_log=TechLogConfig(thread_id=102),
                clean_log=CleanLogConfig(thread_id=103),
            ),
        )},
        authorized_users=[42],
    )
    return CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=RuntimeControlService(ops_db_path=ops_db),
    )


def _route(router, text, mid):
    return router.route(
        command_text=text,
        message_id=mid,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )


def test_pause_command_blocks(ops_db):
    router = _router(ops_db)
    res = _route(router, "/pause", 1)
    assert "BLOCCATE" in res.reply_text
    assert ControlStateRepository(ops_db).get_effective_mode(
        "main",
        "trader_a",
        "BTC/USDT",
        "LONG",
    ) == "BLOCK_NEW_ENTRIES"


def test_pause_trader_then_resume(ops_db):
    router = _router(ops_db)
    _route(router, "/pause trader_a", 2)
    repo = ControlStateRepository(ops_db)
    assert repo.get_effective_mode("main", "trader_a", "X", "LONG") == "BLOCK_NEW_ENTRIES"
    res = _route(router, "/resume trader_a", 3)
    assert "RIABILITATE" in res.reply_text
    assert repo.get_effective_mode("main", "trader_a", "X", "LONG") == "NONE"


def test_block_then_control_shows_it(ops_db):
    router = _router(ops_db)
    _route(router, "/block BTCUSDT", 4)
    control = _route(router, "/control", 5)
    # i simboli sono resi in formato display (BTC/USDT), non raw
    assert "BTC/USDT" in control.reply_text


def test_block_per_trader(ops_db):
    router = _router(ops_db)
    res = _route(router, "/block trader_a SOLUSDT", 6)
    assert "SOLUSDT" in res.reply_text
    assert "trader_a" in res.reply_text


def test_pause_is_audited_executed(ops_db):
    router = _router(ops_db)
    _route(router, "/pause", 7)
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_telegram_control_commands WHERE command_request_id='-100999:7'"
    ).fetchone()[0]
    conn.close()
    assert status == "EXECUTED"


def test_block_missing_arg_usage(ops_db):
    router = _router(ops_db)
    res = _route(router, "/block", 8)
    assert res.decision == "REJECTED"
    assert "Usage" in res.reply_text
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, reject_reason FROM ops_telegram_control_commands "
        "WHERE command_request_id='-100999:8'"
    ).fetchone()
    conn.close()
    assert row == ("REJECTED", "invalid_arguments")


def test_pause_with_too_many_args_returns_usage(ops_db):
    router = _router(ops_db)
    res = _route(router, "/pause trader_a extra", 9)
    assert res.decision == "REJECTED"
    assert "Usage" in res.reply_text
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, reject_reason FROM ops_telegram_control_commands "
        "WHERE command_request_id='-100999:9'"
    ).fetchone()
    conn.close()
    assert row == ("REJECTED", "invalid_arguments")


def test_resume_with_too_many_args_returns_usage(ops_db):
    router = _router(ops_db)
    res = _route(router, "/resume trader_a extra", 10)
    assert res.decision == "REJECTED"
    assert "Usage" in res.reply_text
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, reject_reason FROM ops_telegram_control_commands "
        "WHERE command_request_id='-100999:10'"
    ).fetchone()
    conn.close()
    assert row == ("REJECTED", "invalid_arguments")
