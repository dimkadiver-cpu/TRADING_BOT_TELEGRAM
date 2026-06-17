# tests/runtime_v2/control_plane/test_tech_log_policy.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.models import (
    AccountConfig,
    AccountTopicsConfig,
    CleanLogConfig,
    ControlPlaneConfig,
    TechLogConfig,
    TopicConfig,
)
from src.runtime_v2.control_plane.notification_dispatcher import (
    TelegramNotificationDispatcher,
)
from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event
from src.runtime_v2.control_plane.topic_router import TopicRouter


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


class FakeSender:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, *, chat_id, thread_id, text, silent=False, reply_to_message_id=None):
        self.sent.append({"chat_id": chat_id, "thread_id": thread_id, "text": text})
        return "123"


def _make_config(**tech_log_kwargs) -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={
            "main": AccountConfig(
                chat_id=-100999,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=101),
                    tech_log=TechLogConfig(thread_id=102, **tech_log_kwargs),
                    clean_log=CleanLogConfig(thread_id=103),
                ),
            )
        },
    )


def _make_private_config(**tech_log_kwargs) -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        delivery_mode="private_bot",
        per_account={
            "main": AccountConfig(
                chat_id=42,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=None),
                    tech_log=TechLogConfig(thread_id=None, **tech_log_kwargs),
                    clean_log=CleanLogConfig(thread_id=None),
                ),
            )
        },
    )


def _make_dispatcher(ops_db, cfg, debug_status=None) -> TelegramNotificationDispatcher:
    return TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=FakeSender(),
        debug_status=debug_status,
    )


def _seed_tech_log(ops_db, *, level: str, dedupe_key: str = "tech:test") -> None:
    conn = sqlite3.connect(ops_db)
    with conn:
        write_tech_log_event(
            conn,
            notification_type="RUNTIME_EVENT",
            payload={
                "level": level,
                "category": "Runtime",
                "description": f"Test {level} message",
                "source": "test",
            },
            dedupe_key=dedupe_key,
        )
    conn.close()


async def test_tech_log_disabled_suppresses_message(ops_db):
    """When enabled=False, TECH_LOG messages are silently marked sent without delivery."""
    _seed_tech_log(ops_db, level="ERROR")
    cfg = _make_config(enabled=False)
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )
    n = await disp.drain_once()
    # drain returns 0 (policy suppressed, not sent)
    assert n == 0
    assert len(sender.sent) == 0
    # row should be marked SENT (consumed, not retried)
    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert status == "SENT"


async def test_warning_blocked_when_min_level_error(ops_db):
    """A DEBUG or INFO message is suppressed when min_level=WARNING (the default)."""
    # Seed an INFO message — with min_level=WARNING and operational_events=True,
    # INFO (order 20) is below WARNING (order 30) so it should be blocked.
    _seed_tech_log(ops_db, level="INFO")
    cfg = _make_config(min_level="WARNING", operational_events=True)
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )
    n = await disp.drain_once()
    assert n == 0
    assert len(sender.sent) == 0


async def test_debug_message_suppressed_when_debug_inactive(ops_db):
    """DEBUG level messages are suppressed when debug mode is off."""
    _seed_tech_log(ops_db, level="DEBUG")
    cfg = _make_config(min_level="DEBUG")
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
        debug_status=lambda: False,  # debug inactive
    )
    n = await disp.drain_once()
    assert n == 0
    assert len(sender.sent) == 0


async def test_operational_event_requires_flag(ops_db):
    """INFO-level messages are suppressed when operational_events=False (default)."""
    _seed_tech_log(ops_db, level="INFO")
    cfg = _make_config(min_level="INFO", operational_events=False)
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )
    n = await disp.drain_once()
    assert n == 0
    assert len(sender.sent) == 0


async def test_operational_event_allowed_when_flag_set(ops_db):
    """INFO-level messages pass when operational_events=True and min_level=INFO."""
    _seed_tech_log(ops_db, level="INFO")
    cfg = _make_config(min_level="INFO", operational_events=True)
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )
    n = await disp.drain_once()
    assert n == 1
    assert len(sender.sent) == 1


async def test_private_bot_adds_system_prefix(ops_db):
    """In private_bot mode, TECH_LOG messages get the ⚠️ --SYSTEM-- prefix."""
    _seed_tech_log(ops_db, level="WARNING")
    cfg = _make_private_config(min_level="WARNING")
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )
    n = await disp.drain_once()
    assert n == 1
    assert len(sender.sent) == 1
    assert sender.sent[0]["text"].startswith("⚠️ --SYSTEM--\n")
