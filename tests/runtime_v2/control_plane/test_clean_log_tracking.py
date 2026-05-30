# tests/runtime_v2/control_plane/test_clean_log_tracking.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.models import (
    CleanLogConfig,
    CleanLogTracking,
    ControlPlaneConfig,
    TechLogConfig,
    TopicConfig,
    TopicsConfig,
)
from src.runtime_v2.control_plane.notification_dispatcher import (
    TelegramNotificationDispatcher,
)
from src.runtime_v2.control_plane.outbox_writer import write_clean_log_event
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
        token="t",
        chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
    )


def _make_dispatcher(ops_db: str, sender) -> TelegramNotificationDispatcher:
    cfg = _config()
    return TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )


class CapturingSender:
    """Fake sender that returns a predictable message ID."""

    def __init__(self, start_id: int = 1000) -> None:
        self.sent: list[dict] = []
        self._next_id = start_id

    async def send(
        self,
        *,
        chat_id: int,
        thread_id: int | None,
        text: str,
        silent: bool = False,
        reply_to_message_id: str | None = None,
    ) -> str | None:
        msg_id = str(self._next_id)
        self._next_id += 1
        self.sent.append(
            {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "msg_id": msg_id,
            }
        )
        return msg_id


def _get_tracking(ops_db: str, chain_id: int) -> dict | None:
    conn = sqlite3.connect(ops_db)
    try:
        row = conn.execute(
            "SELECT trade_chain_id, clean_log_root_message_id, clean_log_last_message_id "
            "FROM ops_clean_log_tracking WHERE trade_chain_id=?",
            (chain_id,),
        ).fetchone()
        if row:
            return {"chain_id": row[0], "root": row[1], "last": row[2]}
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


def test_clean_log_tracking_model_instantiation():
    tracking = CleanLogTracking(
        trade_chain_id=1,
        clean_log_root_message_id="100",
        clean_log_last_message_id="101",
        telegram_chat_id="-1001234",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert tracking.trade_chain_id == 1
    assert tracking.clean_log_root_message_id == "100"
    assert tracking.telegram_thread_id is None


def test_clean_log_tracking_model_optional_fields():
    tracking = CleanLogTracking(
        trade_chain_id=5,
        telegram_chat_id="-1001234",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert tracking.clean_log_root_message_id is None
    assert tracking.clean_log_last_message_id is None
    assert tracking.original_message_link is None
    assert tracking.last_clean_log_event_type is None
    assert tracking.last_clean_log_sent_at is None


# ---------------------------------------------------------------------------
# Dispatcher tracking logic — sync DB layer helpers
# ---------------------------------------------------------------------------


def test_get_clean_log_tracking_returns_none_for_unknown_chain(ops_db):
    cfg = _config()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CapturingSender(),
    )
    result = disp._get_clean_log_tracking(chain_id=999)
    assert result is None


def test_update_then_get_clean_log_tracking(ops_db):
    cfg = _config()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CapturingSender(),
    )
    # First insert
    disp._update_clean_log_tracking(
        chain_id=10,
        notification_type="SIGNAL_ACCEPTED",
        chat_id=-100999,
        thread_id=103,
        sent_message_id="500",
    )
    result = disp._get_clean_log_tracking(chain_id=10)
    assert result is not None
    assert result["root"] == "500"
    assert result["last"] == "500"


def test_update_clean_log_tracking_second_send_updates_last_only(ops_db):
    cfg = _config()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CapturingSender(),
    )
    disp._update_clean_log_tracking(
        chain_id=11,
        notification_type="SIGNAL_ACCEPTED",
        chat_id=-100999,
        thread_id=103,
        sent_message_id="600",
    )
    disp._update_clean_log_tracking(
        chain_id=11,
        notification_type="ENTRY_OPENED",
        chat_id=-100999,
        thread_id=103,
        sent_message_id="601",
    )
    result = disp._get_clean_log_tracking(chain_id=11)
    assert result["root"] == "600"
    assert result["last"] == "601"


def test_resolve_reply_target_no_tracking_returns_none(ops_db):
    cfg = _config()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CapturingSender(),
    )
    reply_to = disp._resolve_clean_log_reply_target(
        chain_id=20, notification_type="SIGNAL_ACCEPTED", payload={}
    )
    assert reply_to is None


def test_resolve_reply_target_with_root_returns_root(ops_db):
    cfg = _config()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CapturingSender(),
    )
    disp._update_clean_log_tracking(
        chain_id=21,
        notification_type="SIGNAL_ACCEPTED",
        chat_id=-100999,
        thread_id=103,
        sent_message_id="700",
    )
    reply_to = disp._resolve_clean_log_reply_target(
        chain_id=21, notification_type="ENTRY_OPENED", payload={}
    )
    assert reply_to == "700"


def test_resolve_reply_target_same_update_group_returns_last(ops_db):
    cfg = _config()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CapturingSender(),
    )
    # Establish root at 800, then last at 801
    disp._update_clean_log_tracking(
        chain_id=22,
        notification_type="SIGNAL_ACCEPTED",
        chat_id=-100999,
        thread_id=103,
        sent_message_id="800",
    )
    disp._update_clean_log_tracking(
        chain_id=22,
        notification_type="ENTRY_OPENED",
        chat_id=-100999,
        thread_id=103,
        sent_message_id="801",
    )
    reply_to = disp._resolve_clean_log_reply_target(
        chain_id=22,
        notification_type="TP_FILLED",
        payload={"update_group_id": "grp-abc"},
    )
    assert reply_to == "801"


def test_resolve_reply_target_none_chain_id_returns_none(ops_db):
    cfg = _config()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=CapturingSender(),
    )
    reply_to = disp._resolve_clean_log_reply_target(
        chain_id=None, notification_type="SIGNAL_ACCEPTED", payload={}
    )
    assert reply_to is None


# ---------------------------------------------------------------------------
# Full drain_once() integration — first / followup / same-update-group
# ---------------------------------------------------------------------------


async def test_first_chain_message_becomes_root(ops_db):
    sender = CapturingSender(start_id=1000)
    disp = _make_dispatcher(ops_db, sender)

    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SIGNAL_ACCEPTED",
            chain_id=1,
            payload={"symbol": "BTC/USDT", "side": "LONG"},
            dedupe_key="chain1:first",
        )
    conn.close()

    n = await disp.drain_once()
    assert n == 1

    tracking = _get_tracking(ops_db, chain_id=1)
    assert tracking is not None
    # Root and last should both be set to the first sent message
    assert tracking["root"] == "1000"
    assert tracking["last"] == "1000"
    # The first message has no reply_to
    assert sender.sent[0]["reply_to_message_id"] is None


async def test_followup_chain_message_replies_to_root(ops_db):
    sender = CapturingSender(start_id=2000)
    disp = _make_dispatcher(ops_db, sender)

    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SIGNAL_ACCEPTED",
            chain_id=2,
            payload={"symbol": "ETH/USDT", "side": "LONG"},
            dedupe_key="chain2:first",
        )
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=2,
            payload={"symbol": "ETH/USDT", "side": "LONG"},
            dedupe_key="chain2:second",
        )
    conn.close()

    # First drain — sends first message
    await disp.drain_once()
    # Second drain — sends second message
    await disp.drain_once()

    assert len(sender.sent) == 2
    # First message: no reply_to
    assert sender.sent[0]["reply_to_message_id"] is None
    # Second message: reply to root (first message id = 2000)
    assert sender.sent[1]["reply_to_message_id"] == "2000"

    tracking = _get_tracking(ops_db, chain_id=2)
    assert tracking["root"] == "2000"
    assert tracking["last"] == "2001"


async def test_same_update_group_reuses_last_message(ops_db):
    sender = CapturingSender(start_id=3000)
    disp = _make_dispatcher(ops_db, sender)

    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SIGNAL_ACCEPTED",
            chain_id=3,
            payload={"symbol": "SOL/USDT", "side": "SHORT"},
            dedupe_key="chain3:first",
        )
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=3,
            payload={"symbol": "SOL/USDT", "side": "SHORT"},
            dedupe_key="chain3:second",
        )
        write_clean_log_event(
            conn,
            notification_type="TP_FILLED",
            chain_id=3,
            payload={"symbol": "SOL/USDT", "side": "SHORT", "update_group_id": "grp-xyz"},
            dedupe_key="chain3:third",
        )
    conn.close()

    # Drain all three in sequence
    await disp.drain_once()
    await disp.drain_once()
    await disp.drain_once()

    assert len(sender.sent) == 3
    # Message 1 (root): no reply
    assert sender.sent[0]["reply_to_message_id"] is None
    # Message 2 (followup): reply to root
    assert sender.sent[1]["reply_to_message_id"] == "3000"
    # Message 3 (same update_group_id): reply to last (msg 2)
    assert sender.sent[2]["reply_to_message_id"] == "3001"

    tracking = _get_tracking(ops_db, chain_id=3)
    assert tracking["root"] == "3000"
    assert tracking["last"] == "3002"


# ---------------------------------------------------------------------------
# Backward compat — TECH_LOG / COMMANDS_REPLY unaffected
# ---------------------------------------------------------------------------


async def test_non_clean_log_sends_unchanged(ops_db):
    """TECH_LOG messages must pass through without tracking side-effects."""
    sender = CapturingSender(start_id=9000)
    disp = _make_dispatcher(ops_db, sender)

    conn = sqlite3.connect(ops_db)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO ops_notification_outbox "
            "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at) "
            "VALUES ('RUNTIME_WARNING','TECH_LOG',"
            "'{\"level\":\"WARNING\",\"category\":\"Test\",\"description\":\"ok\",\"source\":\"test\"}'"
            ",'MEDIUM','PENDING','tech:compat1',0,?)",
            (now,),
        )
    conn.close()

    n = await disp.drain_once()
    assert n == 1
    assert len(sender.sent) == 1
    # No tracking row should exist for TECH_LOG
    conn2 = sqlite3.connect(ops_db)
    count = conn2.execute("SELECT COUNT(*) FROM ops_clean_log_tracking").fetchone()[0]
    conn2.close()
    assert count == 0
