"""Tests for topic-aware filtering in TelegramListener (WP3)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.listener import TelegramListener
from src.telegram.topic_utils import extract_message_topic_id


# ---------------------------------------------------------------------------
# extract_message_topic_id
# ---------------------------------------------------------------------------


def _reply_to(*, forum_topic: bool, reply_to_top_id: int | None) -> object:
    rt = MagicMock()
    rt.forum_topic = forum_topic
    rt.reply_to_top_id = reply_to_top_id
    return rt


def test_extract_no_reply_to() -> None:
    msg = MagicMock()
    msg.reply_to = None
    assert extract_message_topic_id(msg) is None


def test_extract_reply_to_not_forum_topic() -> None:
    msg = MagicMock()
    msg.reply_to = _reply_to(forum_topic=False, reply_to_top_id=None)
    assert extract_message_topic_id(msg) is None


def test_extract_named_topic() -> None:
    msg = MagicMock()
    msg.reply_to = _reply_to(forum_topic=True, reply_to_top_id=3)
    assert extract_message_topic_id(msg) == 3


def test_extract_general_topic_no_top_id() -> None:
    msg = MagicMock()
    msg.reply_to = _reply_to(forum_topic=True, reply_to_top_id=None)
    assert extract_message_topic_id(msg) == 1


def test_extract_general_topic_explicit_id_1() -> None:
    msg = MagicMock()
    msg.reply_to = _reply_to(forum_topic=True, reply_to_top_id=1)
    assert extract_message_topic_id(msg) == 1


def test_extract_mock_without_explicit_reply_to() -> None:
    # MagicMock auto-creates attributes; forum_topic is not True → returns None
    msg = MagicMock()
    assert extract_message_topic_id(msg) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(channels: list[ChannelEntry]) -> ChannelsConfig:
    return ChannelsConfig(recovery_max_hours=4, blacklist_global=[], channels=channels)


def _listener(config: ChannelsConfig) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        router=MagicMock(),
        logger=MagicMock(),
        channels_config=config,
    )


def _entry(chat_id: int, topic_id: int | None = None, active: bool = True) -> ChannelEntry:
    return ChannelEntry(chat_id=chat_id, label="t", active=active, trader_id=None, topic_id=topic_id)


# ---------------------------------------------------------------------------
# _is_allowed_message
# ---------------------------------------------------------------------------


def test_allowed_none_chat_id_is_rejected() -> None:
    lst = _listener(_cfg([]))
    assert lst._is_allowed_message(None, None) is False


def test_allowed_empty_config_allows_all() -> None:
    lst = _listener(_cfg([]))
    assert lst._is_allowed_message(-1001, 3) is True
    assert lst._is_allowed_message(-1001, None) is True


def test_allowed_topic_specific_entry_correct_topic() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=3)]))
    assert lst._is_allowed_message(-1001, 3) is True


def test_allowed_topic_specific_entry_wrong_topic_no_wide_fallback() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=3)]))
    assert lst._is_allowed_message(-1001, 99) is False


def test_allowed_forum_wide_entry_accepts_topicless_message() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=None)]))
    assert lst._is_allowed_message(-1001, None) is True


def test_allowed_forum_wide_entry_fallback_for_unknown_topic() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=None)]))
    # topic=99 not configured, falls back to forum-wide
    assert lst._is_allowed_message(-1001, 99) is True


def test_allowed_inactive_entry_rejected() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=3, active=False)]))
    assert lst._is_allowed_message(-1001, 3) is False


def test_allowed_unknown_chat_rejected() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=3)]))
    assert lst._is_allowed_message(-9999, 3) is False


def test_allowed_fallback_ids_ignores_topic() -> None:
    lst = TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        router=MagicMock(),
        logger=MagicMock(),
        channels_config=_cfg([]),
        fallback_allowed_chat_ids=[-1001],
    )
    assert lst._is_allowed_message(-1001, 3) is True
    assert lst._is_allowed_message(-1001, None) is True
    assert lst._is_allowed_message(-9999, 3) is False


# ---------------------------------------------------------------------------
# _handle_new_message — topic-specific accept/reject
# ---------------------------------------------------------------------------


def _make_event(chat_id: int, msg_text: str, reply_to: object = None) -> MagicMock:
    msg = MagicMock()
    msg.id = 100
    msg.message = msg_text
    msg.media = None
    msg.date = datetime.now(timezone.utc)
    msg.reply_to = reply_to
    event = MagicMock()
    event.chat_id = chat_id
    event.chat = MagicMock()
    event.message = msg
    return event


@pytest.mark.asyncio
async def test_handle_topic_specific_message_allowed() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=3)]))
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=1)
    event = _make_event(-1001, "BTC LONG", _reply_to(forum_topic=True, reply_to_top_id=3))

    await lst._handle_new_message(event, acquisition_mode="live")

    lst._ingestion.ingest.assert_called_once()
    assert lst._queue.qsize() == 1


@pytest.mark.asyncio
async def test_handle_topic_specific_message_wrong_topic_rejected() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=3)]))
    event = _make_event(-1001, "BTC LONG", _reply_to(forum_topic=True, reply_to_top_id=99))

    await lst._handle_new_message(event, acquisition_mode="live")

    lst._ingestion.ingest.assert_not_called()
    assert lst._queue.qsize() == 0


@pytest.mark.asyncio
async def test_handle_forum_wide_accepts_topicless_message() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=None)]))
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=2)
    event = _make_event(-1001, "ETH SHORT", reply_to=None)

    await lst._handle_new_message(event, acquisition_mode="live")

    lst._ingestion.ingest.assert_called_once()
    assert lst._queue.qsize() == 1


@pytest.mark.asyncio
async def test_handle_general_topic_accepted() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=1)]))
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=3)
    event = _make_event(-1001, "SOL signal", _reply_to(forum_topic=True, reply_to_top_id=1))

    await lst._handle_new_message(event, acquisition_mode="live")

    lst._ingestion.ingest.assert_called_once()
    assert lst._queue.qsize() == 1


# ---------------------------------------------------------------------------
# source_topic_id propagation through ingestion → QueueItem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_topic_id_reaches_queue_item() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=4)]))
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=5)
    event = _make_event(-1001, "signal text", _reply_to(forum_topic=True, reply_to_top_id=4))

    await lst._handle_new_message(event, acquisition_mode="live")

    item = await lst._queue.get()
    assert item.source_topic_id == 4


@pytest.mark.asyncio
async def test_source_topic_id_none_for_topicless_message() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=None)]))
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=6)
    event = _make_event(-1001, "signal text", reply_to=None)

    await lst._handle_new_message(event, acquisition_mode="live")

    item = await lst._queue.get()
    assert item.source_topic_id is None


@pytest.mark.asyncio
async def test_source_topic_id_forwarded_to_ingestion() -> None:
    lst = _listener(_cfg([_entry(-1001, topic_id=3)]))
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=7)
    event = _make_event(-1001, "signal", _reply_to(forum_topic=True, reply_to_top_id=3))

    await lst._handle_new_message(event, acquisition_mode="live")

    call_args = lst._ingestion.ingest.call_args[0][0]
    assert call_args.source_topic_id == 3


# ---------------------------------------------------------------------------
# Catchup — topic-aware filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catchup_filters_message_by_topic() -> None:
    cfg = _cfg([_entry(-1001, topic_id=3)])
    lst = _listener(cfg)
    lst._status_store.get_last_telegram_message_id.return_value = 0
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=10)

    msg_topic3 = MagicMock()
    msg_topic3.id = 10
    msg_topic3.message = "signal t3"
    msg_topic3.media = None
    msg_topic3.date = datetime.now(timezone.utc)
    msg_topic3.reply_to = _reply_to(forum_topic=True, reply_to_top_id=3)

    msg_topic99 = MagicMock()
    msg_topic99.id = 11
    msg_topic99.message = "signal t99"
    msg_topic99.media = None
    msg_topic99.date = datetime.now(timezone.utc)
    msg_topic99.reply_to = _reply_to(forum_topic=True, reply_to_top_id=99)

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[msg_topic3, msg_topic99])

    import src.telegram.listener as listener_mod
    orig_message = listener_mod.Message
    listener_mod.Message = type(msg_topic3)
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig_message

    # Only topic=3 message should be ingested
    assert lst._ingestion.ingest.call_count == 1
    assert lst._queue.qsize() == 1
    item = await lst._queue.get()
    assert item.source_topic_id == 3


@pytest.mark.asyncio
async def test_catchup_deduplicates_chat_id_for_multi_topic_forum() -> None:
    cfg = _cfg([_entry(-1001, topic_id=3), _entry(-1001, topic_id=4)])
    lst = _listener(cfg)
    lst._status_store.get_last_telegram_message_id.return_value = 0
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=1)

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])

    import src.telegram.listener as listener_mod
    orig_message = listener_mod.Message
    listener_mod.Message = MagicMock
    try:
        await lst._catchup_from_telegram(client)
    finally:
        listener_mod.Message = orig_message

    # Only one API call despite two entries for the same chat_id
    client.get_messages.assert_called_once_with(-1001, min_id=0, limit=200)
