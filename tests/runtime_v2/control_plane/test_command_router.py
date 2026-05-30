# tests/runtime_v2/control_plane/test_command_router.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.models import (
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig, TopicsConfig,
)
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.telegram_bot import CommandRouter


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


def _config():
    return ControlPlaneConfig(
        token="t", chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[42],
    )


def _router(ops_db):
    cfg = _config()
    service = RuntimeControlService(ops_db_path=ops_db)
    return CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=service,
    )


def _last_status(ops_db, request_id):
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, reject_reason FROM ops_telegram_control_commands "
        "WHERE command_request_id=?", (request_id,),
    ).fetchone()
    conn.close()
    return row


def test_authorized_status_returns_reply(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=1,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is not None
    assert "STATUS" in res.reply_text
    assert _last_status(ops_db, "-100999:1")[0] == "EXECUTED"


def test_wrong_chat_ignored_no_reply(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=2,
        chat_id=-1, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is None
    assert res.decision == "IGNORE"


def test_wrong_topic_ignored(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=3,
        chat_id=-100999, thread_id=999, user_id=42, username="op",
    )
    assert res.reply_text is None
    assert res.decision == "IGNORE"


def test_unauthorized_rejected_no_reply_but_audited(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=4,
        chat_id=-100999, thread_id=101, user_id=7, username="intruder",
    )
    assert res.reply_text is None
    assert res.decision == "REJECT_UNAUTHORIZED"
    assert _last_status(ops_db, "-100999:4") == ("REJECTED", "unauthorized_user")


def test_unknown_command_replies_and_audits_rejected(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/wat", message_id=5,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is not None
    assert "riconosciuto" in res.reply_text.lower()
    assert _last_status(ops_db, "-100999:5") == ("REJECTED", "unknown_command")


def test_help_lists_commands(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/help", message_id=6,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "/status" in res.reply_text
    assert "/trades" in res.reply_text


def test_trade_with_id_arg(ops_db):
    # seed one chain
    conn = sqlite3.connect(ops_db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
            "VALUES (77,77,77,77,'trader_a','main','BTC/USDT','LONG','OPEN','ONE_SHOT','{}','{}','{}',?,?)",
            (now, now),
        )
    conn.close()
    router = _router(ops_db)
    res = router.route(
        command_text="/trade 77", message_id=7,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "TRADE #77" in res.reply_text


def test_version(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/version", message_id=8,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "VERSION" in res.reply_text
    assert "v2" in res.reply_text
