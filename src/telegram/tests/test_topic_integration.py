"""Integration tests for topic-aware pipeline — WP7 consolidation.

Covers scenarios not fully exercised by unit tests:
  - General topic (topic_id=1) end-to-end through config → listener → queue
  - Mixed setup: forum with multiple topics + regular group, no trader collisions
  - Legacy config (no topic_id anywhere) → unchanged behaviour
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.listener import TelegramListener
from src.telegram.router import is_blacklisted_text
from src.telegram.topic_utils import extract_message_topic_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    chat_id: int,
    topic_id: int | None = None,
    *,
    active: bool = True,
    trader_id: str | None = None,
    blacklist: list[str] | None = None,
) -> ChannelEntry:
    return ChannelEntry(
        chat_id=chat_id,
        label=f"chat{chat_id}_t{topic_id}",
        active=active,
        trader_id=trader_id,
        topic_id=topic_id,
        blacklist=blacklist or [],
    )


def _cfg(channels: list[ChannelEntry], blacklist_global: list[str] | None = None) -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=blacklist_global or [],
        channels=channels,
    )


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


def _fake_message(reply_to: object | None, msg_id: int = 10, text: str = "signal") -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id
    msg.message = text
    msg.media = None
    msg.date = datetime.now(timezone.utc)
    msg.reply_to = reply_to
    return msg


# ---------------------------------------------------------------------------
# General topic (topic_id=1) — end-to-end
# ---------------------------------------------------------------------------


def test_general_topic_config_match() -> None:
    """Entry with topic_id=1 matches a message with General topic."""
    cfg = _cfg([
        _entry(-1001, topic_id=1, trader_id="trader_gen"),
        _entry(-1001, topic_id=3, trader_id="trader_specific"),
    ])
    entry = cfg.match_entry(-1001, 1)
    assert entry is not None
    assert entry.trader_id == "trader_gen"


def test_general_topic_does_not_match_forum_wide() -> None:
    """topic_id=1 (General) is distinct from forum-wide (topic_id=None)."""
    cfg = _cfg([_entry(-1001, topic_id=None, trader_id="forum_wide")])
    entry = cfg.match_entry(-1001, 1)
    # no topic-1 entry → falls back to forum-wide
    assert entry is not None
    assert entry.trader_id == "forum_wide"


def test_general_topic_extraction_no_top_id() -> None:
    """forum_topic=True with no reply_to_top_id → General topic (1)."""
    msg = _fake_message(_reply_to(forum_topic=True, reply_to_top_id=None))
    assert extract_message_topic_id(msg) == 1


def test_general_topic_extraction_explicit_top_id_1() -> None:
    """forum_topic=True with reply_to_top_id=1 → topic_id=1."""
    msg = _fake_message(_reply_to(forum_topic=True, reply_to_top_id=1))
    assert extract_message_topic_id(msg) == 1


@pytest.mark.asyncio
async def test_general_topic_message_allowed_and_enqueued() -> None:
    """Message from General topic (1) is allowed when a topic_id=1 entry exists."""
    cfg = _cfg([_entry(-1001, topic_id=1)])
    lst = _listener(cfg)
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=9)

    event = MagicMock()
    event.chat_id = -1001
    event.chat = MagicMock()
    event.message = _fake_message(_reply_to(forum_topic=True, reply_to_top_id=None))

    await lst._handle_new_message(event, acquisition_mode="live")

    assert lst._queue.qsize() == 1
    item = await lst._queue.get()
    assert item.source_topic_id == 1


@pytest.mark.asyncio
async def test_general_topic_message_rejected_when_only_other_topic_configured() -> None:
    """General topic message is rejected if only topic-3 is configured (no forum-wide fallback)."""
    cfg = _cfg([_entry(-1001, topic_id=3)])
    lst = _listener(cfg)

    event = MagicMock()
    event.chat_id = -1001
    event.chat = MagicMock()
    event.message = _fake_message(_reply_to(forum_topic=True, reply_to_top_id=None))

    await lst._handle_new_message(event, acquisition_mode="live")

    lst._ingestion.ingest.assert_not_called()
    assert lst._queue.qsize() == 0


# ---------------------------------------------------------------------------
# Mixed setup: forum multi-topic + regular group
# ---------------------------------------------------------------------------

# Config:
#   forum-1 (chat=-1001): topic 3 → trader_a, topic 4 → trader_b
#   group-2 (chat=-1002): forum-wide (no topic) → trader_c

_MIXED_CFG = _cfg([
    _entry(-1001, topic_id=3, trader_id="trader_a", blacklist=["blockedA"]),
    _entry(-1001, topic_id=4, trader_id="trader_b", blacklist=["blockedB"]),
    _entry(-1002, topic_id=None, trader_id="trader_c"),
])


def test_mixed_setup_topic3_matches_trader_a() -> None:
    entry = _MIXED_CFG.match_entry(-1001, 3)
    assert entry is not None
    assert entry.trader_id == "trader_a"


def test_mixed_setup_topic4_matches_trader_b() -> None:
    entry = _MIXED_CFG.match_entry(-1001, 4)
    assert entry is not None
    assert entry.trader_id == "trader_b"


def test_mixed_setup_group_matches_trader_c() -> None:
    entry = _MIXED_CFG.match_entry(-1002, None)
    assert entry is not None
    assert entry.trader_id == "trader_c"


def test_mixed_setup_unknown_topic_forum1_rejected() -> None:
    """Topic 99 in forum-1 has no match: no forum-wide fallback for that chat."""
    entry = _MIXED_CFG.match_entry(-1001, 99)
    # forum-1 has no forum-wide (None) entry → no match
    assert entry is None


def test_mixed_setup_no_collisions_listener() -> None:
    """topic-3 and topic-4 in same forum are both allowed, different entries."""
    lst = _listener(_MIXED_CFG)
    assert lst._is_allowed_message(-1001, 3) is True
    assert lst._is_allowed_message(-1001, 4) is True
    assert lst._is_allowed_message(-1001, 99) is False   # unknown topic
    assert lst._is_allowed_message(-1002, None) is True  # regular group


def test_mixed_setup_blacklist_no_cross_contamination() -> None:
    """topic-3 blacklist does not affect topic-4 messages and vice versa."""
    assert is_blacklisted_text(_MIXED_CFG, "blockedA text", -1001, topic_id=3) is True
    assert is_blacklisted_text(_MIXED_CFG, "blockedA text", -1001, topic_id=4) is False
    assert is_blacklisted_text(_MIXED_CFG, "blockedB text", -1001, topic_id=4) is True
    assert is_blacklisted_text(_MIXED_CFG, "blockedB text", -1001, topic_id=3) is False


@pytest.mark.asyncio
async def test_mixed_setup_messages_enqueued_with_correct_topic() -> None:
    """Messages from topic-3, topic-4, and regular group are all enqueued with correct topic_id."""
    lst = _listener(_MIXED_CFG)
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=1)

    async def _send(chat_id: int, reply_to: object | None, msg_id: int) -> None:
        event = MagicMock()
        event.chat_id = chat_id
        event.chat = MagicMock()
        event.message = _fake_message(reply_to, msg_id=msg_id)
        await lst._handle_new_message(event, acquisition_mode="live")

    # topic-3 message
    await _send(-1001, _reply_to(forum_topic=True, reply_to_top_id=3), 10)
    # topic-4 message
    await _send(-1001, _reply_to(forum_topic=True, reply_to_top_id=4), 11)
    # regular group message (no topic)
    await _send(-1002, None, 20)

    assert lst._queue.qsize() == 3
    items = [await lst._queue.get() for _ in range(3)]
    topic_ids = [i.source_topic_id for i in items]
    assert topic_ids == [3, 4, None]


# ---------------------------------------------------------------------------
# Legacy config — backward compat
# ---------------------------------------------------------------------------


def test_legacy_config_no_topic_id_still_works() -> None:
    """Config without any topic_id uses forum-wide semantics and behaves identically to pre-WP1."""
    cfg = _cfg([
        _entry(-1001, topic_id=None, trader_id="trader_legacy"),
        _entry(-1002, topic_id=None, trader_id="trader_legacy2"),
    ])
    # All entries have topic_id=None → forum-wide
    assert cfg.match_entry(-1001, None) is not None
    assert cfg.match_entry(-1002, None) is not None


def test_legacy_config_message_with_no_topic_allowed() -> None:
    cfg = _cfg([_entry(-1001, topic_id=None)])
    lst = _listener(cfg)
    assert lst._is_allowed_message(-1001, None) is True


def test_legacy_config_blacklist_still_applied() -> None:
    cfg = _cfg([_entry(-1001, topic_id=None, blacklist=["spam"])], blacklist_global=["global_bad"])
    assert is_blacklisted_text(cfg, "global_bad text", -1001, topic_id=None) is True
    assert is_blacklisted_text(cfg, "spam text", -1001, topic_id=None) is True
    assert is_blacklisted_text(cfg, "clean signal", -1001, topic_id=None) is False


@pytest.mark.asyncio
async def test_legacy_config_message_enqueued_with_none_topic() -> None:
    """In a legacy setup, messages are enqueued with source_topic_id=None."""
    cfg = _cfg([_entry(-1001, topic_id=None)])
    lst = _listener(cfg)
    lst._ingestion.ingest.return_value = MagicMock(saved=True, raw_message_id=1)

    event = MagicMock()
    event.chat_id = -1001
    event.chat = MagicMock()
    event.message = _fake_message(None)  # no reply_to → no topic

    await lst._handle_new_message(event, acquisition_mode="live")

    assert lst._queue.qsize() == 1
    item = await lst._queue.get()
    assert item.source_topic_id is None
