"""Tests for blacklist filtering in TelegramListener."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.listener import TelegramListener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    blacklist_global: list[str] | None = None,
    channels: list[ChannelEntry] | None = None,
) -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=blacklist_global or [],
        channels=channels or [],
    )


def _make_listener(config: ChannelsConfig) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        router=MagicMock(),
        logger=MagicMock(),
        channels_config=config,
    )


# ---------------------------------------------------------------------------
# _is_blacklisted
# ---------------------------------------------------------------------------


def test_global_blacklist_match() -> None:
    cfg = _make_config(blacklist_global=["#admin", "#pinned"])
    lst = _make_listener(cfg)
    assert lst._is_blacklisted("hello #admin world", chat_id=1) is True


def test_global_blacklist_no_match() -> None:
    cfg = _make_config(blacklist_global=["#admin"])
    lst = _make_listener(cfg)
    assert lst._is_blacklisted("regular signal text", chat_id=1) is False


def test_global_blacklist_case_insensitive() -> None:
    cfg = _make_config(blacklist_global=["#Admin"])
    lst = _make_listener(cfg)
    assert lst._is_blacklisted("text #admin here", chat_id=1) is True


def test_per_channel_blacklist_match() -> None:
    channel = ChannelEntry(chat_id=42, label="c", active=True, trader_id=None, blacklist=["#weekly"])
    cfg = _make_config(channels=[channel])
    lst = _make_listener(cfg)
    assert lst._is_blacklisted("results #weekly update", chat_id=42) is True


def test_per_channel_blacklist_no_match_other_channel() -> None:
    channel = ChannelEntry(chat_id=42, label="c", active=True, trader_id=None, blacklist=["#weekly"])
    cfg = _make_config(channels=[channel])
    lst = _make_listener(cfg)
    # Same text but from a different channel — not blacklisted
    assert lst._is_blacklisted("results #weekly update", chat_id=99) is False


def test_per_channel_blacklist_no_match_text() -> None:
    channel = ChannelEntry(chat_id=42, label="c", active=True, trader_id=None, blacklist=["#weekly"])
    cfg = _make_config(channels=[channel])
    lst = _make_listener(cfg)
    assert lst._is_blacklisted("BTC long signal", chat_id=42) is False


def test_empty_blacklists_never_block() -> None:
    cfg = _make_config()
    lst = _make_listener(cfg)
    assert lst._is_blacklisted("anything at all", chat_id=1) is False


# ---------------------------------------------------------------------------
# _ingest_and_enqueue — blacklisted messages set processing_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blacklisted_message_sets_status() -> None:
    cfg = _make_config(blacklist_global=["#pinned"])
    lst = _make_listener(cfg)

    # Mock a Telethon Message
    msg = MagicMock()
    msg.id = 100
    msg.message = "Price update #pinned"
    msg.media = None
    msg.date = None
    msg.reply_to = None

    # Ingestion returns a raw_message_id
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=7)

    await lst._ingest_and_enqueue(
        message=msg,
        chat_id=-10099,
        chat_title=None,
        chat_username=None,
        acquisition_mode="live",
    )

    lst._status_store.update.assert_called_once_with(7, "blacklisted")
    # Must NOT have been queued
    assert lst._queue.qsize() == 0
