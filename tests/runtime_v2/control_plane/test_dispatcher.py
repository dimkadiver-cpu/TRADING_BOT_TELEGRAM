# tests/runtime_v2/control_plane/test_dispatcher.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.models import (
    AccountConfig, AccountTopicsConfig, CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig,
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
    )


def _private_bot_config():
    return ControlPlaneConfig(
        token="t",
        delivery_mode="private_bot",
        default_account="main",
        per_account={"main": AccountConfig(
            chat_id=42,
            topics=AccountTopicsConfig(
                commands=TopicConfig(thread_id=None),
                tech_log=TechLogConfig(thread_id=None),
                clean_log=CleanLogConfig(thread_id=None),
            ),
        )},
    )


class FakeSender:
    def __init__(self, fail_times: int = 0):
        self.sent: list[dict] = []
        self._fail_times = fail_times
        self.calls = 0

    async def send(self, *, chat_id, thread_id, text, silent=False, reply_to_message_id=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("telegram down")
        message_id = str(100 + self.calls)
        self.sent.append({
            "chat_id": chat_id,
            "thread_id": thread_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id,
            "message_id": message_id,
        })
        return message_id


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
                "level": "WARNING",
                "category": "Runtime",
                "description": "Runtime shutdown. Snapshot saved.",
                "source": "runtime_main",
            },
            dedupe_key=dedupe_key,
        )
    conn.close()


def _make_due(ops_db: str) -> None:
    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "UPDATE ops_notification_outbox SET send_after=? WHERE status='PENDING'",
            (datetime.now(timezone.utc).isoformat(),),
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
    _make_due(ops_db)
    await disp.drain_once()
    _make_due(ops_db)
    await disp.drain_once()
    conn = sqlite3.connect(ops_db)
    status, attempts = conn.execute(
        "SELECT status, attempts FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert status == "FAILED"
    assert attempts == 3


async def test_drain_failure_sets_retry_backoff(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=1)
    disp = _dispatcher(ops_db, sender)

    await disp.drain_once()

    conn = sqlite3.connect(ops_db)
    status, attempts, send_after = conn.execute(
        "SELECT status, attempts, send_after FROM ops_notification_outbox"
    ).fetchone()
    conn.close()

    assert status == "PENDING"
    assert attempts == 1
    assert send_after is not None
    assert datetime.fromisoformat(send_after) > datetime.now(timezone.utc)


async def test_failed_entry_not_resent(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=99)
    disp = _dispatcher(ops_db, sender)
    for _ in range(5):
        await disp.drain_once()
        _make_due(ops_db)
    assert sender.calls == 3  # stops attempting after FAILED


async def test_recovers_after_transient_failure(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=1)
    disp = _dispatcher(ops_db, sender)
    await disp.drain_once()   # fails once
    _make_due(ops_db)
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
        async def send(self, *, chat_id, thread_id, text, silent=False, reply_to_message_id=None):
            sent_texts.append(text)
            return "123"

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

    max_msgs = cfg.get_account("main").topics.tech_log.max_messages_per_minute
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
        async def send(self, *, chat_id, thread_id, text, silent=False, reply_to_message_id=None):
            sent_texts.append(text)
            return "123"

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
        async def send(self, *, chat_id, thread_id, text, silent=False, reply_to_message_id=None):
            sent_texts.append(text)
            return "123"

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


@pytest.mark.asyncio
async def test_dispatcher_skips_future_send_after(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="TP_FILLED",
            chain_id=1,
            payload={"chain_id": 1, "symbol": "BTC/USDT", "side": "LONG"},
        )
    # Set send_after to 5 minutes in the future
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_notification_outbox SET send_after=?",
        ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),),
    )
    conn.commit()
    conn.close()

    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    result = await disp.drain_once()
    assert result == 0
    assert sender.sent == []


@pytest.mark.asyncio
async def test_dispatcher_ignores_suppressed_rows(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SL_FILLED",
            chain_id=1,
            payload={"chain_id": 1, "symbol": "BTC/USDT", "side": "LONG"},
        )
    conn = sqlite3.connect(ops_db)
    conn.execute("UPDATE ops_notification_outbox SET status='SUPPRESSED'")
    conn.commit()
    conn.close()

    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    result = await disp.drain_once()
    assert result == 0


@pytest.mark.asyncio
async def test_dispatcher_enriches_multi_chain_summary_with_links(ops_db):
    conn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO ops_clean_log_tracking "
            "(trade_chain_id, clean_log_root_message_id, clean_log_last_message_id, "
            " telegram_chat_id, telegram_thread_id, last_clean_log_event_type, "
            " last_clean_log_sent_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (42, "10", "55", "-10012345", None, "UPDATE_DONE", now, now),
        )
        conn.execute(
            "INSERT INTO ops_notification_outbox "
            "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at) "
            "VALUES ('MULTI_CHAIN_SUMMARY', 'CLEAN_LOG', ?, 'MEDIUM', 'PENDING', 'test:mcs:1', 0, ?)",
            (
                json.dumps({
                    "operations": ["Move SL to BE"],
                    "chains": [
                        {"chain_id": 42, "symbol": "BTC/USDT", "side": "LONG", "status": "DONE"},
                    ],
                }),
                now,
            ),
        )

    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    n = await disp.drain_once()

    assert n == 1
    assert len(sender.sent) == 1
    assert "t.me/c/12345/55" in sender.sent[0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_releases_resolvable_pending_close_full_summary_without_new_close(ops_db):
    conn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ops_pending_multi_chain_summaries "
            "(pending_id INTEGER PRIMARY KEY, canonical_message_id INTEGER UNIQUE, payload_json TEXT)"
        )
        conn.execute(
            "INSERT INTO ops_clean_log_tracking "
            "(trade_chain_id, clean_log_root_message_id, clean_log_last_message_id, "
            " telegram_chat_id, telegram_thread_id, last_clean_log_event_type, "
            " last_clean_log_sent_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (6, "453", "468", "-1003897279123", None, "POSITION_CLOSED", now, now),
        )
        conn.execute(
            "INSERT INTO ops_clean_log_tracking "
            "(trade_chain_id, clean_log_root_message_id, clean_log_last_message_id, "
            " telegram_chat_id, telegram_thread_id, last_clean_log_event_type, "
            " last_clean_log_sent_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (7, "454", "469", "-1003897279123", None, "POSITION_CLOSED", now, now),
        )
        conn.execute(
            "INSERT INTO ops_pending_multi_chain_summaries (canonical_message_id, payload_json) VALUES (?, ?)",
            (
                365,
                json.dumps({
                    "summary_kind": "pending_final_close_links",
                    "requested_operations": ["Close full"],
                    "chains": [
                        {"chain_id": 6, "symbol": "WLD", "side": "LONG", "status": "DONE", "link_mode": "final_close", "link": None, "display_lines": []},
                        {"chain_id": 7, "symbol": "ICNT", "side": "LONG", "status": "DONE", "link_mode": "final_close", "link": None, "display_lines": []},
                    ],
                    "counts": {"done": 2, "partial": 0, "skipped": 0, "error": 0},
                    "source": "trader_update",
                    "link": "https://t.me/c/3927267771/365",
                }),
            ),
        )

    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    first = await disp.drain_once()

    assert first == 0
    assert len(sender.sent) == 0

    conn = sqlite3.connect(ops_db)
    pending_count = conn.execute(
        "SELECT COUNT(*) FROM ops_pending_multi_chain_summaries WHERE canonical_message_id=365"
    ).fetchone()[0]
    status, payload_json = conn.execute(
        "SELECT status, payload_json FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()
    payload = json.loads(payload_json)
    conn.execute(
        "UPDATE ops_notification_outbox SET send_after=? WHERE notification_type='MULTI_CHAIN_SUMMARY'",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()
    assert pending_count == 0
    assert status == "PENDING"
    assert payload["summary_kind"] == "final_close"
    assert payload["chains"][0]["link"] == "https://t.me/c/3897279123/468"
    assert payload["chains"][1]["link"] == "https://t.me/c/3897279123/469"

    second = await disp.drain_once()

    assert second == 1
    assert len(sender.sent) == 1
    assert "Close full" in sender.sent[0]["text"]
    assert "https://t.me/c/3897279123/468" in sender.sent[0]["text"]
    assert "https://t.me/c/3897279123/469" in sender.sent[0]["text"]


def _config_per_trader(trader_thread: int = 77):
    """Config with per_trader override: trader_a → thread 77, global clean_log → 103."""
    from src.runtime_v2.control_plane.models import (
        AccountConfig, AccountTopicsConfig, CleanLogConfig, ControlPlaneConfig,
        TechLogConfig, TopicConfig,
    )
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={"main": AccountConfig(
            chat_id=-100999,
            topics=AccountTopicsConfig(
                commands=TopicConfig(thread_id=101),
                tech_log=TechLogConfig(thread_id=102),
                clean_log=CleanLogConfig(thread_id=103, per_trader={"trader_a": trader_thread}),
            ),
        )},
    )


@pytest.mark.asyncio
async def test_update_done_with_trader_id_routes_to_per_trader_thread(ops_db):
    """UPDATE_DONE payload must carry trader_id so the dispatcher routes to the per-trader thread."""
    cfg = _config_per_trader(trader_thread=77)
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )
    conn = sqlite3.connect(ops_db)
    with conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO ops_clean_log_tracking "
            "(trade_chain_id, clean_log_root_message_id, clean_log_last_message_id, "
            " telegram_chat_id, telegram_thread_id, last_clean_log_event_type, "
            " last_clean_log_sent_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (200, "501", "501", "-100999", "77", "SIGNAL_ACCEPTED", now, now),
        )
        write_clean_log_event(
            conn,
            notification_type="UPDATE_DONE",
            chain_id=200,
            payload={
                "chain_id": 200,
                "symbol": "ETH/USDT",
                "side": "LONG",
                "trader_id": "trader_a",
                "applied_actions": ["MOVE_STOP"],
                "rejected_actions": [],
                "failed_actions": [],
                "changed": [{"field": "SL", "old": 1500, "new": 1550, "note": None}],
                "source": "trader_update",
                "link": None,
            },
            dedupe_key="clean:update:msg1:200",
        )
    conn.close()

    await disp.drain_once()

    assert len(sender.sent) == 1
    assert sender.sent[0]["thread_id"] == 77, (
        "UPDATE_DONE must route to per-trader thread when trader_id is present in payload"
    )


@pytest.mark.asyncio
async def test_thread_pinning_keeps_subsequent_events_on_first_thread(ops_db):
    """Once the first event for a chain is sent, all subsequent events must go to
    the same (chat_id, thread_id) stored in ops_clean_log_tracking — even if the
    per_trader config were to change."""
    cfg = _config_per_trader(trader_thread=77)
    sender = FakeSender()
    disp = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        # Simulate: first event (SIGNAL_ACCEPTED) was already sent to thread 77 and tracking row created
        conn.execute(
            "INSERT INTO ops_clean_log_tracking "
            "(trade_chain_id, clean_log_root_message_id, clean_log_last_message_id, "
            " telegram_chat_id, telegram_thread_id, last_clean_log_event_type, "
            " last_clean_log_sent_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (300, "501", "501", "-100999", "77", "SIGNAL_ACCEPTED", now, now),
        )
        # Second event without trader_id in payload (simulates old bug or engine_rule path)
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=300,
            payload={
                "chain_id": 300,
                "symbol": "SOL/USDT",
                "side": "LONG",
                # No trader_id — pinning must still route to thread 77
                "fill_price": 150.0,
                "filled_qty": 10.0,
                "fee": 0.01,
            },
            dedupe_key="clean:entry:300:1",
        )
    conn.close()

    await disp.drain_once()

    assert len(sender.sent) == 1
    assert sender.sent[0]["thread_id"] == 77, (
        "Subsequent CLEAN_LOG events must be pinned to the thread used by the first event"
    )


@pytest.mark.asyncio
async def test_non_signal_clean_log_waits_for_signal_root_before_send(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SIGNAL_ACCEPTED",
            chain_id=32,
            payload={"chain_id": 32, "symbol": "MRVL/USDT", "side": "SHORT"},
            dedupe_key="clean:signal:32",
        )
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=32,
            payload={
                "chain_id": 32,
                "symbol": "MRVL/USDT",
                "side": "SHORT",
                "fill_price": 312.11,
                "filled_qty": 0.8,
                "fee": 0.01,
            },
            dedupe_key="clean:entry:32",
        )
    conn.close()

    sender = FakeSender(fail_times=1)
    disp = _dispatcher(ops_db, sender)

    first = await disp.drain_once()
    assert first == 0
    assert sender.sent == []

    _make_due(ops_db)
    second = await disp.drain_once()
    assert second == 2
    assert [msg["text"].splitlines()[0] for msg in sender.sent] == [
        "✅ #32 — SIGNAL ACCEPTED",
        "📊 #32 — ENTRY OPENED",
    ]
    assert sender.sent[1]["reply_to_message_id"] is None
    assert f"https://t.me/c/999/{sender.sent[0]['message_id']}" in sender.sent[1]["text"]

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT clean_log_root_message_id, clean_log_last_message_id, last_clean_log_event_type "
        "FROM ops_clean_log_tracking WHERE trade_chain_id=32"
    ).fetchone()
    conn.close()
    assert row == (sender.sent[0]["message_id"], sender.sent[1]["message_id"], "ENTRY_OPENED")


@pytest.mark.asyncio
async def test_non_signal_clean_log_sends_without_link_when_signal_root_failed(ops_db):
    conn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=40,
            payload={
                "chain_id": 40,
                "symbol": "XAUTUSDT",
                "side": "LONG",
                "fill_price": 4139.6,
                "filled_qty": 4.807,
                "fee": 0.01,
            },
            dedupe_key="clean:entry:40",
        )
        conn.execute(
            "INSERT INTO ops_notification_outbox "
            "(notification_type, destination, payload_json, priority, status, "
            "dedupe_key, attempts, created_at, chain_id) "
            "VALUES ('SIGNAL_ACCEPTED', 'CLEAN_LOG', ?, 'MEDIUM', 'FAILED', "
            "'clean:signal:40', 3, ?, 40)",
            (json.dumps({"chain_id": 40, "symbol": "XAUTUSDT", "side": "LONG"}), now),
        )
    conn.close()

    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)

    sent = await disp.drain_once()

    assert sent == 1
    assert len(sender.sent) == 1
    assert sender.sent[0]["text"].splitlines()[0] == "📊 #40 — ENTRY OPENED"
    assert "t.me/c/" not in sender.sent[0]["text"]


@pytest.mark.asyncio
async def test_update_done_uses_signal_link_but_not_telegram_reply(ops_db):
    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "INSERT INTO ops_clean_log_tracking "
            "(trade_chain_id, clean_log_root_message_id, clean_log_last_message_id, "
            " telegram_chat_id, telegram_thread_id, last_clean_log_event_type, "
            " last_clean_log_sent_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (36, "1292", "1292", "-1004240829081", "1024", "ENTRY_OPENED", now, now),
        )
        write_clean_log_event(
            conn,
            notification_type="UPDATE_DONE",
            chain_id=36,
            payload={
                "chain_id": 36,
                "symbol": "XLMUSDT",
                "side": "SHORT",
                "trader_id": "trader_devos_crypto",
                "applied_actions": ["MOVE_SL_TO_BE"],
                "rejected_actions": [],
                "changed": [{"field": "SL", "old": None, "new": 0.22709, "note": "BE"}],
                "source": "operation_rules",
                "link": None,
            },
            dedupe_key="clean:update:36",
        )
    conn.close()

    sent = await disp.drain_once()

    assert sent == 1
    assert len(sender.sent) == 1
    assert "https://t.me/c/4240829081/1292" in sender.sent[0]["text"]
    assert sender.sent[0]["reply_to_message_id"] is None
