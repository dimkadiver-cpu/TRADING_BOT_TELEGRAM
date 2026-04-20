"""Tests for topic-aware recovery in TelegramListener (WP4)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.processing_status import StaleMessage
from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.listener import TelegramListener
from src.telegram.topic_utils import extract_message_topic_id


def _cfg(channels: list[ChannelEntry]) -> ChannelsConfig:
    return ChannelsConfig(recovery_max_hours=4, blacklist_global=[], channels=channels)


def _entry(chat_id: int, topic_id: int | None = None, active: bool = True) -> ChannelEntry:
    return ChannelEntry(chat_id=chat_id, label="t", active=active, trader_id=None, topic_id=topic_id)


def _listener(config: ChannelsConfig) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        router=MagicMock(),
        logger=MagicMock(),
        channels_config=config,
    )


def _reply_to(*, forum_topic: bool, reply_to_top_id: int | None) -> object:
    rt = MagicMock()
    rt.forum_topic = forum_topic
    rt.reply_to_top_id = reply_to_top_id
    return rt


# ---------------------------------------------------------------------------
# _reenqueue_stale — topic propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reenqueue_stale_propagates_topic_id() -> None:
    lst = _listener(_cfg([]))
    lst._status_store.get_stale_messages.return_value = [
        StaleMessage(
            raw_message_id=1,
            source_chat_id="-1001",
            telegram_message_id=10,
            raw_text="signal",
            source_trader_id=None,
            reply_to_message_id=None,
            source_topic_id=3,
        ),
    ]
    await lst._reenqueue_stale()

    item = await lst._queue.get()
    assert item.source_topic_id == 3


@pytest.mark.asyncio
async def test_reenqueue_stale_none_topic_propagated() -> None:
    lst = _listener(_cfg([]))
    lst._status_store.get_stale_messages.return_value = [
        StaleMessage(
            raw_message_id=2,
            source_chat_id="-1001",
            telegram_message_id=11,
            raw_text="signal",
            source_trader_id=None,
            reply_to_message_id=None,
            source_topic_id=None,
        ),
    ]
    await lst._reenqueue_stale()

    item = await lst._queue.get()
    assert item.source_topic_id is None


@pytest.mark.asyncio
async def test_reenqueue_stale_multiple_topics() -> None:
    lst = _listener(_cfg([]))
    lst._status_store.get_stale_messages.return_value = [
        StaleMessage(1, "-1001", 10, "txt", None, None, source_topic_id=3),
        StaleMessage(2, "-1001", 11, "txt", None, None, source_topic_id=4),
        StaleMessage(3, "-1001", 12, "txt", None, None, source_topic_id=None),
    ]
    await lst._reenqueue_stale()

    assert lst._queue.qsize() == 3
    items = [await lst._queue.get() for _ in range(3)]
    assert [i.source_topic_id for i in items] == [3, 4, None]


# ---------------------------------------------------------------------------
# _catchup_from_telegram — per-entry topic checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catchup_uses_per_entry_checkpoint() -> None:
    """Each entry gets its own topic-aware last_id query."""
    cfg = _cfg([_entry(-1001, topic_id=3), _entry(-1001, topic_id=4)])
    lst = _listener(cfg)

    # topic=3 has last_id=50, topic=4 has last_id=100
    def _last_id(chat_id: str, topic_id: int | None = None) -> int | None:
        return {("-1001", 3): 50, ("-1001", 4): 100}.get((chat_id, topic_id))

    lst._status_store.get_last_telegram_message_id.side_effect = _last_id
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=1)

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])

    import src.telegram.listener as listener_mod
    orig = listener_mod.Message
    listener_mod.Message = MagicMock
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig

    # min_last_id = min(50, 100) = 50
    client.get_messages.assert_called_once_with(-1001, min_id=50, limit=200)


@pytest.mark.asyncio
async def test_catchup_min_id_is_minimum_across_topics() -> None:
    """When topics have different last_ids, use the minimum for the API call."""
    cfg = _cfg([_entry(-1001, topic_id=3), _entry(-1001, topic_id=4)])
    lst = _listener(cfg)

    def _last_id(chat_id: str, topic_id: int | None = None) -> int | None:
        return {("-1001", 3): 200, ("-1001", 4): 10}.get((chat_id, topic_id))

    lst._status_store.get_last_telegram_message_id.side_effect = _last_id
    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])

    import src.telegram.listener as listener_mod
    orig = listener_mod.Message
    listener_mod.Message = MagicMock
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig

    # min(200, 10) = 10
    client.get_messages.assert_called_once_with(-1001, min_id=10, limit=200)


@pytest.mark.asyncio
async def test_catchup_none_last_id_uses_zero() -> None:
    """If no checkpoint exists for any entry, min_id=0."""
    cfg = _cfg([_entry(-1001, topic_id=3)])
    lst = _listener(cfg)
    lst._status_store.get_last_telegram_message_id.return_value = None
    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])

    import src.telegram.listener as listener_mod
    orig = listener_mod.Message
    listener_mod.Message = MagicMock
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig

    client.get_messages.assert_called_once_with(-1001, min_id=0, limit=200)


@pytest.mark.asyncio
async def test_catchup_enqueues_correct_topic_message() -> None:
    """Message matching topic scope is ingested and carries correct source_topic_id."""
    cfg = _cfg([_entry(-1001, topic_id=3)])
    lst = _listener(cfg)
    lst._status_store.get_last_telegram_message_id.return_value = 0
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=5)

    fake_msg = MagicMock()
    fake_msg.id = 10
    fake_msg.message = "signal"
    fake_msg.media = None
    fake_msg.date = datetime.now(timezone.utc)
    fake_msg.reply_to = _reply_to(forum_topic=True, reply_to_top_id=3)

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[fake_msg])

    import src.telegram.listener as listener_mod
    orig = listener_mod.Message
    listener_mod.Message = type(fake_msg)
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig

    assert lst._queue.qsize() == 1
    item = await lst._queue.get()
    assert item.source_topic_id == 3


@pytest.mark.asyncio
async def test_catchup_skips_message_not_matching_any_scope() -> None:
    """Message from a topic not in config is skipped."""
    cfg = _cfg([_entry(-1001, topic_id=3)])
    lst = _listener(cfg)
    lst._status_store.get_last_telegram_message_id.return_value = 0

    fake_msg = MagicMock()
    fake_msg.id = 10
    fake_msg.message = "signal"
    fake_msg.media = None
    fake_msg.date = datetime.now(timezone.utc)
    fake_msg.reply_to = _reply_to(forum_topic=True, reply_to_top_id=99)

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[fake_msg])

    import src.telegram.listener as listener_mod
    orig = listener_mod.Message
    listener_mod.Message = type(fake_msg)
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig

    lst._ingestion.ingest.assert_not_called()
    assert lst._queue.qsize() == 0


@pytest.mark.asyncio
async def test_catchup_forum_wide_entry_accepts_topicless_message() -> None:
    """Forum-wide entry (topic_id=None) accepts messages with no topic."""
    cfg = _cfg([_entry(-1001, topic_id=None)])
    lst = _listener(cfg)
    lst._status_store.get_last_telegram_message_id.return_value = 0
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=7)

    fake_msg = MagicMock()
    fake_msg.id = 20
    fake_msg.message = "signal"
    fake_msg.media = None
    fake_msg.date = datetime.now(timezone.utc)
    fake_msg.reply_to = None  # no topic

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[fake_msg])

    import src.telegram.listener as listener_mod
    orig = listener_mod.Message
    listener_mod.Message = type(fake_msg)
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig

    assert lst._queue.qsize() == 1
    item = await lst._queue.get()
    assert item.source_topic_id is None
