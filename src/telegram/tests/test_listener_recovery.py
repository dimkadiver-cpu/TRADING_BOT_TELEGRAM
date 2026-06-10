"""Tests for recovery logic in TelegramListener."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.processing_status import StaleMessage
from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.listener import TelegramListener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(channels: list[ChannelEntry] | None = None) -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=[],
        channels=channels or [],
    )


def _make_listener(config: ChannelsConfig) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        raw_repo=MagicMock(),
        channel_resolver=MagicMock(),
        parser_pipeline=MagicMock(),
        enrichment_processor=MagicMock(),
        trader_resolver=MagicMock(),
        logger=MagicMock(),
        channels_config=config,
    )


# ---------------------------------------------------------------------------
# Stale message re-enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_messages_reenqueued() -> None:
    cfg = _make_config()
    lst = _make_listener(cfg)

    stale = [
        StaleMessage(
            raw_message_id=1,
            source_chat_id="-100111",
            telegram_message_id=50,
            raw_text="signal text",
            source_trader_id="trader_a",
            reply_to_message_id=None,
        ),
        StaleMessage(
            raw_message_id=2,
            source_chat_id="-100111",
            telegram_message_id=51,
            raw_text="update text",
            source_trader_id="trader_a",
            reply_to_message_id=50,
        ),
    ]
    lst._status_store.get_stale_messages.return_value = stale

    await lst._reenqueue_stale()

    assert lst._queue.qsize() == 2
    item1 = await lst._queue.get()
    item2 = await lst._queue.get()
    assert item1.raw_message_id == 1
    assert item1.acquisition_mode == "catchup"
    assert item2.raw_message_id == 2
    assert item2.reply_to_message_id == 50


@pytest.mark.asyncio
async def test_no_stale_messages_nothing_enqueued() -> None:
    cfg = _make_config()
    lst = _make_listener(cfg)
    lst._status_store.get_stale_messages.return_value = []

    await lst._reenqueue_stale()

    assert lst._queue.qsize() == 0


# ---------------------------------------------------------------------------
# Telegram catchup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catchup_enqueues_new_messages() -> None:
    channel = ChannelEntry(chat_id=-100999, label="x", active=True, trader_id=None)
    cfg = _make_config(channels=[channel])
    lst = _make_listener(cfg)

    lst._status_store.get_last_telegram_message_id.return_value = 100
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=10)

    # Build a fake Telethon message within the recovery window
    fake_msg = MagicMock()
    fake_msg.id = 101
    fake_msg.message = "new signal"
    fake_msg.media = None
    fake_msg.date = datetime.now(timezone.utc)
    fake_msg.reply_to = None

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[fake_msg])

    # Patch isinstance check so our fake_msg passes
    with patch("src.telegram.listener.Message", MagicMock()):
        import src.telegram.listener as listener_mod
        orig = listener_mod.Message
        listener_mod.Message = type(fake_msg)
        try:
            await lst._catchup_from_telegram(client)
        finally:
            listener_mod.Message = orig

    client.get_messages.assert_called_once_with(-100999, min_id=100, limit=200)
    # The message should have been ingested and queued
    lst._ingestion.ingest.assert_called_once()
    assert lst._queue.qsize() == 1
    item = await lst._queue.get()
    assert item.acquisition_mode == "catchup"
    assert item.raw_text == "new signal"


@pytest.mark.asyncio
async def test_catchup_uses_min_id_zero_when_no_checkpoint() -> None:
    """When no checkpoint exists for a channel, catchup fetches from min_id=0."""
    channel = ChannelEntry(chat_id=-100999, label="x", active=True, trader_id=None)
    cfg = _make_config(channels=[channel])
    lst = _make_listener(cfg)
    lst._status_store.get_last_telegram_message_id.return_value = None

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])

    await lst._catchup_from_telegram(client)

    client.get_messages.assert_called_once_with(-100999, min_id=0, limit=200)
    assert lst._queue.qsize() == 0


@pytest.mark.asyncio
async def test_catchup_skipped_when_recovery_hours_zero() -> None:
    """recovery_max_hours=0 disables Telegram catchup entirely."""
    channel = ChannelEntry(chat_id=-100999, label="x", active=True, trader_id=None)
    cfg = ChannelsConfig(recovery_max_hours=0, blacklist_global=[], channels=[channel])
    lst = _make_listener(cfg)

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])

    await lst._catchup_from_telegram(client)

    client.get_messages.assert_not_called()
    assert lst._queue.qsize() == 0


@pytest.mark.asyncio
async def test_catchup_uses_min_id_zero_when_one_active_topic_has_no_checkpoint() -> None:
    """A newly enabled topic in a multi-topic chat must not inherit another topic's checkpoint."""
    channels = [
        ChannelEntry(chat_id=-100999, topic_id=3, label="a", active=True, trader_id="trader_a"),
        ChannelEntry(chat_id=-100999, topic_id=8, label="t3", active=True, trader_id="trader_3"),
    ]
    cfg = _make_config(channels=channels)
    lst = _make_listener(cfg)

    def _last_id(_chat_id: str, topic_id: int | None = None):
        if topic_id == 3:
            return 6230
        if topic_id == 8:
            return None
        return None

    lst._status_store.get_last_telegram_message_id.side_effect = _last_id

    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])

    await lst._catchup_from_telegram(client)

    client.get_messages.assert_called_once_with(-100999, min_id=0, limit=200)


# ---------------------------------------------------------------------------
# processing_status updates in worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_sets_done_on_success() -> None:
    cfg = _make_config()
    lst = _make_listener(cfg)

    # Patch _process_item to succeed
    lst._process_item = MagicMock()

    from src.telegram.listener import _QueueItem

    item = _QueueItem(
        raw_message_id=5,
        source_chat_id="-100x",
        telegram_message_id=10,
        raw_text="text",
        source_trader_id=None,
        reply_to_message_id=None,
        acquisition_mode="live",
    )
    await lst._queue.put(item)

    # Run worker for one iteration then cancel
    task = asyncio.create_task(lst.run_worker())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    lst._process_item.assert_called_once_with(item)
    assert lst._status_store.update.call_args_list[0].args == (5, "processing")
    assert lst._status_store.update.call_args_list[1].args == (5, "done")


@pytest.mark.asyncio
async def test_worker_sets_failed_on_exception() -> None:
    cfg = _make_config()
    lst = _make_listener(cfg)

    lst._process_item = MagicMock(side_effect=RuntimeError("boom"))

    from src.telegram.listener import _QueueItem

    item = _QueueItem(
        raw_message_id=6,
        source_chat_id="-100x",
        telegram_message_id=11,
        raw_text="text",
        source_trader_id=None,
        reply_to_message_id=None,
        acquisition_mode="live",
    )
    await lst._queue.put(item)

    task = asyncio.create_task(lst.run_worker())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    lst._status_store.update.assert_called_with(6, "failed")
