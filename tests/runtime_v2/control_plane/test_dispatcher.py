# tests/runtime_v2/control_plane/test_dispatcher.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.models import (
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig, TopicsConfig,
)
from src.runtime_v2.control_plane.notification_dispatcher import (
    TelegramNotificationDispatcher,
)
from src.runtime_v2.control_plane.outbox_writer import (
    write_clean_log_event,
    write_tech_log_event,
)
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


def _config():
    return ControlPlaneConfig(
        token="t", chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
    )


def _private_bot_config():
    return ControlPlaneConfig(
        token="t",
        chat_id=42,
        delivery_mode="private_bot",
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=None),
            tech_log=TechLogConfig(thread_id=None),
            clean_log=CleanLogConfig(thread_id=None),
        ),
    )


class FakeSender:
    def __init__(self, fail_times: int = 0):
        self.sent: list[dict] = []
        self._fail_times = fail_times
        self.calls = 0

    async def send(self, *, chat_id, thread_id, text, silent=False):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("telegram down")
        self.sent.append({"chat_id": chat_id, "thread_id": thread_id, "text": text})


def _dispatcher(ops_db, sender):
    cfg = _config()
    return TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )


def _private_dispatcher(ops_db, sender):
    cfg = _private_bot_config()
    return TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )


def _seed(ops_db, dedupe_key="clean:k1"):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(conn, notification_type="SIGNAL_ACCEPTED",
                              chain_id=145,
                              payload={"symbol": "BTC/USDT", "side": "LONG"},
                              dedupe_key=dedupe_key)
    conn.close()


def _seed_tech_log(ops_db, dedupe_key="tech:k1"):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_tech_log_event(
            conn,
            notification_type="RUNTIME_SHUTDOWN",
            payload={
                "level": "INFO",
                "category": "Runtime",
                "description": "Runtime shutdown. Snapshot saved.",
                "source": "runtime_main",
            },
            dedupe_key=dedupe_key,
        )
    conn.close()


async def test_drain_sends_and_marks_sent(ops_db):
    _seed(ops_db)
    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    n = await disp.drain_once()
    assert n == 1
    assert len(sender.sent) == 1
    assert sender.sent[0]["thread_id"] == 103
    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert status == "SENT"


async def test_drain_retries_then_fails(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=99)
    disp = _dispatcher(ops_db, sender)
    # 3 drain passes -> attempts reaches max -> FAILED
    await disp.drain_once()
    await disp.drain_once()
    await disp.drain_once()
    conn = sqlite3.connect(ops_db)
    status, attempts = conn.execute(
        "SELECT status, attempts FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert status == "FAILED"
    assert attempts == 3


async def test_failed_entry_not_resent(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=99)
    disp = _dispatcher(ops_db, sender)
    for _ in range(5):
        await disp.drain_once()
    assert sender.calls == 3  # stops attempting after FAILED


async def test_recovers_after_transient_failure(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=1)
    disp = _dispatcher(ops_db, sender)
    await disp.drain_once()   # fails once
    await disp.drain_once()   # succeeds
    assert len(sender.sent) == 1
    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert status == "SENT"


async def test_private_bot_dispatches_without_thread_id(ops_db):
    _seed(ops_db)
    sender = FakeSender()
    disp = _private_dispatcher(ops_db, sender)
    n = await disp.drain_once()
    assert n == 1
    assert sender.sent[0]["chat_id"] == 42
    assert sender.sent[0]["thread_id"] is None


async def test_private_bot_tech_log_uses_system_prefix(ops_db):
    _seed_tech_log(ops_db)
    sender = FakeSender()
    disp = _private_dispatcher(ops_db, sender)
    n = await disp.drain_once()
    assert n == 1
    assert sender.sent[0]["thread_id"] is None
    assert sender.sent[0]["text"].startswith("⚠️ --SYSTEM--\n")


async def test_supergroup_tech_log_keeps_thread_and_no_system_prefix(ops_db):
    _seed_tech_log(ops_db)
    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    n = await disp.drain_once()
    assert n == 1
    assert sender.sent[0]["thread_id"] == 102
    assert not sender.sent[0]["text"].startswith("⚠️ --SYSTEM--\n")


async def test_tech_log_rate_limit_suppresses_excess(ops_db):
    """Messages beyond max_messages_per_minute are suppressed with one warning."""
    sent_texts: list[str] = []

    class CaptureSender:
        async def send(self, *, chat_id, thread_id, text, silent=False):
            sent_texts.append(text)

    # Insert 25 TECH_LOG notifications
    conn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for i in range(25):
            conn.execute(
                "INSERT INTO ops_notification_outbox "
                "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at) "
                "VALUES ('TECH_ERROR','TECH_LOG','{\"level\":\"ERROR\",\"category\":\"Test\",\"description\":\"err\",\"source\":\"test\"}','MEDIUM','PENDING',?,0,?)",
                (f"key:{i}", now),
            )
    conn.close()

    cfg = _config()
    dispatcher = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CaptureSender(),
    )
    await dispatcher.drain_once()

    max_msgs = cfg.topics.tech_log.max_messages_per_minute
    # Should have sent at most max_msgs + 1 (the rate limit warning)
    assert len(sent_texts) <= max_msgs + 1
    # Exactly max_msgs normal messages + 1 warning
    assert len(sent_texts) == max_msgs + 1
    # One of the messages should be the rate limit warning
    assert any("Rate limit" in t or "rate limit" in t.lower() for t in sent_texts)


async def test_tech_log_rate_limit_sends_only_one_warning(ops_db):
    """Only one warning is sent even when many messages exceed the limit."""
    sent_texts: list[str] = []

    class CaptureSender:
        async def send(self, *, chat_id, thread_id, text, silent=False):
            sent_texts.append(text)

    conn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for i in range(30):
            conn.execute(
                "INSERT INTO ops_notification_outbox "
                "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at) "
                "VALUES ('TECH_ERROR','TECH_LOG','{\"level\":\"ERROR\",\"category\":\"Test\",\"description\":\"err\",\"source\":\"test\"}','MEDIUM','PENDING',?,0,?)",
                (f"warn_key:{i}", now),
            )
    conn.close()

    cfg = _config()
    dispatcher = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CaptureSender(),
    )
    await dispatcher.drain_once()

    warning_count = sum(1 for t in sent_texts if "Rate limit" in t or "rate limit" in t.lower())
    assert warning_count == 1


async def test_clean_log_not_rate_limited(ops_db):
    """CLEAN_LOG messages are not subject to TECH_LOG rate limiting."""
    sent_texts: list[str] = []

    class CaptureSender:
        async def send(self, *, chat_id, thread_id, text, silent=False):
            sent_texts.append(text)

    conn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for i in range(25):
            conn.execute(
                "INSERT INTO ops_notification_outbox "
                "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at) "
                "VALUES ('SIGNAL_ACCEPTED','CLEAN_LOG','{\"symbol\":\"BTC/USDT\",\"side\":\"LONG\"}','MEDIUM','PENDING',?,0,?)",
                (f"clean_key:{i}", now),
            )
    conn.close()

    cfg = _config()
    dispatcher = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CaptureSender(),
    )
    n = await dispatcher.drain_once()

    # All 25 CLEAN_LOG messages should be sent without suppression
    assert n == 25
    assert len(sent_texts) == 25
