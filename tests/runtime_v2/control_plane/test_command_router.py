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


def _apply_raw_messages_migration(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("db/migrations/006_raw_messages.sql").read_text(encoding="utf-8"))
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
    assert "/pnl" in res.reply_text
    assert "/debug_on [<duration>]" in res.reply_text
    assert "/debug_off" in res.reply_text


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


def test_trade_reply_includes_original_message_link_when_available(ops_db):
    _apply_raw_messages_migration(ops_db)
    conn = sqlite3.connect(ops_db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
            "VALUES (78,78,78,7800,'trader_a','main','BTC/USDT','LONG','OPEN','ONE_SHOT','{}','{}','{}',?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO raw_messages "
            "(raw_message_id, source_chat_id, telegram_message_id, message_ts, acquired_at) "
            "VALUES (7800, '-1001234567890', 456, ?, ?)",
            (now, now),
        )
    conn.close()

    router = _router(ops_db)
    res = router.route(
        command_text="/trade 78", message_id=79,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "Source link: https://t.me/c/1234567890/456" in res.reply_text


def test_trade_with_invalid_id_is_rejected_and_returns_usage(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/trade nope", message_id=71,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.decision == "REJECTED"
    assert "Usage: /trade <chain_id>" == res.reply_text
    assert _last_status(ops_db, "-100999:71") == ("REJECTED", "invalid_arguments")


def test_version(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/version", message_id=8,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "VERSION" in res.reply_text
    assert "v2" in res.reply_text


# ── Gap 1: wrong-topic audit / wrong-chat no-audit ────────────────────────────

def test_wrong_topic_audited_as_ignored(ops_db):
    router = _router(ops_db)
    router.route(
        command_text="/status", message_id=10,
        chat_id=-100999, thread_id=999, user_id=42, username="op",
    )
    row = _last_status(ops_db, "-100999:10")
    assert row is not None
    assert row[0] == "IGNORED"


def test_wrong_chat_produces_no_audit(ops_db):
    router = _router(ops_db)
    router.route(
        command_text="/status", message_id=11,
        chat_id=-1, thread_id=101, user_id=42, username="op",
    )
    assert _last_status(ops_db, "-1:11") is None


# ── Gap 2: _send_reply_keyboard behavior ─────────────────────────────────────

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.runtime_v2.control_plane.telegram_bot import TelegramControlBot


def _make_bot(ops_db, delivery_mode="private_bot", keyboard=None):
    cfg = ControlPlaneConfig(
        token="t", chat_id=-100999,
        delivery_mode=delivery_mode,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=None if delivery_mode == "private_bot" else 101),
            tech_log=TechLogConfig(thread_id=None if delivery_mode == "private_bot" else 102),
            clean_log=CleanLogConfig(thread_id=None if delivery_mode == "private_bot" else 103),
        ),
        authorized_users=[42],
        keyboard=keyboard or [],
    )
    router = CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=RuntimeControlService(ops_db_path=ops_db),
    )
    return TelegramControlBot(config=cfg, router=router)


def test_send_reply_keyboard_noop_in_supergroup_topics(ops_db):
    bot = _make_bot(ops_db, delivery_mode="supergroup_topics", keyboard=[["/status", "/trades"]])
    update = MagicMock()
    asyncio.run(bot._send_reply_keyboard(update))
    update.message.reply_text.assert_not_called()


def test_send_reply_keyboard_noop_when_keyboard_empty(ops_db):
    bot = _make_bot(ops_db, delivery_mode="private_bot", keyboard=[])
    update = MagicMock()
    asyncio.run(bot._send_reply_keyboard(update))
    update.message.reply_text.assert_not_called()


def test_send_reply_keyboard_sends_in_private_bot(ops_db):
    bot = _make_bot(ops_db, delivery_mode="private_bot", keyboard=[["/status", "/trades"]])
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    asyncio.run(bot._send_reply_keyboard(update, user_id=42))
    update.message.reply_text.assert_called_once()
    call_kwargs = update.message.reply_text.call_args
    from telegram import ReplyKeyboardMarkup
    assert isinstance(call_kwargs.kwargs.get("reply_markup"), ReplyKeyboardMarkup)
    assert call_kwargs.args[0] == "Control Plane attivo."


def test_send_reply_keyboard_skips_repeat_for_same_user(ops_db):
    bot = _make_bot(ops_db, delivery_mode="private_bot", keyboard=[["/status", "/trades"]])
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    asyncio.run(bot._send_reply_keyboard(update, user_id=42))
    asyncio.run(bot._send_reply_keyboard(update, user_id=42))
    update.message.reply_text.assert_called_once()


def test_private_bot_status_audits_without_thread_id(ops_db):
    cfg = ControlPlaneConfig(
        token="t",
        chat_id=42,
        delivery_mode="private_bot",
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=None),
            tech_log=TechLogConfig(thread_id=None),
            clean_log=CleanLogConfig(thread_id=None),
        ),
        authorized_users=[42],
        keyboard=[["/status"]],
    )
    router = CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=RuntimeControlService(ops_db_path=ops_db),
    )
    res = router.route(
        command_text="/status",
        message_id=12,
        chat_id=42,
        thread_id=None,
        user_id=42,
        username="op",
    )
    assert res.decision == "EXECUTED"
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, message_thread_id FROM ops_telegram_control_commands "
        "WHERE command_request_id='42:12'"
    ).fetchone()
    conn.close()
    assert row == ("EXECUTED", "")


def test_private_bot_first_text_message_sends_keyboard(ops_db):
    bot = _make_bot(ops_db, delivery_mode="private_bot", keyboard=[["/status", "/trades"]])
    update = MagicMock()
    update.effective_message = MagicMock(chat_id=-100999, message_thread_id=None)
    update.effective_user = MagicMock(id=42)
    update.message.reply_text = AsyncMock()
    asyncio.run(bot._on_text_message(update, MagicMock()))
    update.message.reply_text.assert_called_once()


def test_private_bot_start_command_sends_keyboard_once_and_reply(ops_db):
    bot = _make_bot(ops_db, delivery_mode="private_bot", keyboard=[["/status", "/trades"]])
    update = MagicMock()
    update.effective_message = MagicMock(
        text="/start",
        message_id=20,
        chat_id=-100999,
        message_thread_id=None,
    )
    update.effective_user = MagicMock(id=42, username="op")
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    asyncio.run(bot._on_command(update, context))
    update.message.reply_text.assert_called_once()
    context.bot.send_message.assert_called_once()


def test_private_bot_non_start_command_does_not_push_keyboard(ops_db):
    bot = _make_bot(ops_db, delivery_mode="private_bot", keyboard=[["/status", "/trades"]])
    update = MagicMock()
    update.effective_message = MagicMock(
        text="/status",
        message_id=21,
        chat_id=-100999,
        message_thread_id=None,
    )
    update.effective_user = MagicMock(id=42, username="op")
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    asyncio.run(bot._on_command(update, context))
    update.message.reply_text.assert_not_called()
    context.bot.send_message.assert_called_once()


# ── New commands: logs, debug_on, debug_off ───────────────────────────────────

def test_logs_command_returns_log_content_or_not_found(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/logs 5", message_id=20,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is not None
    assert "LOGS" in res.reply_text


def test_debug_on_responds_not_available(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/debug_on 5m", message_id=21,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is not None
    assert res.decision == "EXECUTED"
    assert "DEBUG MODE ATTIVATO" in res.reply_text


def test_debug_off_responds(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/debug_off", message_id=22,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is not None
    assert res.decision == "EXECUTED"
    assert "DEBUG MODE DISATTIVATO" in res.reply_text
