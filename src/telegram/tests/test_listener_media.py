"""Tests for media handling in TelegramListener."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.telegram.channel_config import ChannelsConfig
from src.telegram.listener import TelegramListener, _is_media_only


# ---------------------------------------------------------------------------
# _is_media_only unit tests
# ---------------------------------------------------------------------------


def _msg(media=None, message: str | None = None) -> MagicMock:
    m = MagicMock()
    m.media = media
    m.message = message
    return m


def test_text_only_not_media_only() -> None:
    assert _is_media_only(_msg(media=None, message="signal text")) is False


def test_media_no_caption_is_media_only() -> None:
    assert _is_media_only(_msg(media=object(), message=None)) is True


def test_media_empty_caption_is_media_only() -> None:
    assert _is_media_only(_msg(media=object(), message="")) is True


def test_media_with_caption_not_media_only() -> None:
    assert _is_media_only(_msg(media=object(), message="BTC signal")) is False


# ---------------------------------------------------------------------------
# Integration: media-only messages are skipped
# ---------------------------------------------------------------------------


def _make_listener() -> TelegramListener:
    cfg = ChannelsConfig(recovery_max_hours=4, blacklist_global=[], channels=[])
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        router=MagicMock(),
        logger=MagicMock(),
        channels_config=cfg,
    )


@pytest.mark.asyncio
async def test_media_only_message_is_skipped() -> None:
    lst = _make_listener()

    event = MagicMock()
    event.chat_id = -100123
    event.chat = MagicMock()
    event.message = _msg(media=object(), message=None)
    event.message.id = 55

    await lst._handle_new_message(event, acquisition_mode="live")

    # Nothing ingested, nothing queued
    lst._ingestion.ingest.assert_not_called()
    assert lst._queue.qsize() == 0


@pytest.mark.asyncio
async def test_media_with_caption_is_processed() -> None:
    lst = _make_listener()
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=10)

    event = MagicMock()
    event.chat_id = -100123
    event.chat = MagicMock()
    msg = _msg(media=object(), message="BTC/USDT LONG")
    msg.id = 56
    msg.date = None
    msg.reply_to = None
    event.message = msg

    await lst._handle_new_message(event, acquisition_mode="live")

    lst._ingestion.ingest.assert_called_once()
    assert lst._queue.qsize() == 1
