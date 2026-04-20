"""Tests for topic-aware blacklist, trader fallback, and inactive scope in router (WP5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.effective_trader import EffectiveTraderResult
from src.telegram.router import MessageRouter, QueueItem, is_blacklisted_text


def _cfg(
    channels: list[ChannelEntry],
    blacklist_global: list[str] | None = None,
) -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=blacklist_global or [],
        channels=channels,
    )


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
        label="t",
        active=active,
        trader_id=trader_id,
        topic_id=topic_id,
        blacklist=blacklist or [],
    )


def _router(config: ChannelsConfig) -> MessageRouter:
    return MessageRouter(
        effective_trader_resolver=MagicMock(),
        eligibility_evaluator=MagicMock(),
        parse_results_store=MagicMock(),
        processing_status_store=MagicMock(),
        review_queue_store=MagicMock(),
        raw_message_store=MagicMock(),
        logger=MagicMock(),
        channels_config=config,
    )


def _item(
    source_chat_id: str = "-1001",
    source_topic_id: int | None = None,
) -> QueueItem:
    return QueueItem(
        raw_message_id=1,
        source_chat_id=source_chat_id,
        telegram_message_id=10,
        raw_text="signal",
        source_trader_id=None,
        reply_to_message_id=None,
        acquisition_mode="live",
        source_topic_id=source_topic_id,
    )


# ---------------------------------------------------------------------------
# is_blacklisted_text — blacklist_global
# ---------------------------------------------------------------------------


def test_blacklist_global_matches_regardless_of_topic() -> None:
    cfg = _cfg([_entry(-1001, topic_id=3)], blacklist_global=["spam"])
    assert is_blacklisted_text(cfg, "this is spam text", -1001, topic_id=3) is True


def test_blacklist_global_matches_with_no_entry() -> None:
    cfg = _cfg([], blacklist_global=["spam"])
    assert is_blacklisted_text(cfg, "spam here", None) is True


def test_blacklist_global_case_insensitive() -> None:
    cfg = _cfg([], blacklist_global=["SPAM"])
    assert is_blacklisted_text(cfg, "this is spam", None) is True


# ---------------------------------------------------------------------------
# is_blacklisted_text — scope-matched entry blacklist
# ---------------------------------------------------------------------------


def test_blacklist_topic_specific_applies_for_matching_topic() -> None:
    cfg = _cfg([_entry(-1001, topic_id=3, blacklist=["banned"])])
    assert is_blacklisted_text(cfg, "banned text", -1001, topic_id=3) is True


def test_blacklist_topic_specific_not_applied_for_different_topic() -> None:
    """topic-3 blacklist must NOT apply when message is from topic-4 (no implicit merge)."""
    cfg = _cfg([
        _entry(-1001, topic_id=3, blacklist=["banned"]),
        _entry(-1001, topic_id=4, blacklist=[]),
    ])
    assert is_blacklisted_text(cfg, "banned text", -1001, topic_id=4) is False


def test_blacklist_no_merge_forum_wide_into_topic_specific() -> None:
    """forum-wide blacklist must NOT bleed into a topic-specific message when a topic entry exists."""
    cfg = _cfg([
        _entry(-1001, topic_id=None, blacklist=["forum_bad"]),
        _entry(-1001, topic_id=3, blacklist=[]),
    ])
    # topic-3 message matches topic-3 entry (empty blacklist) — forum-wide must not apply
    assert is_blacklisted_text(cfg, "forum_bad text", -1001, topic_id=3) is False


def test_blacklist_topic_message_fallback_to_forum_wide_when_no_topic_entry() -> None:
    """If no topic-specific entry, match_entry falls back to forum-wide → its blacklist applies."""
    cfg = _cfg([_entry(-1001, topic_id=None, blacklist=["blocked"])])
    assert is_blacklisted_text(cfg, "blocked text", -1001, topic_id=99) is True


def test_blacklist_forum_wide_applies_for_forum_wide_message() -> None:
    cfg = _cfg([_entry(-1001, topic_id=None, blacklist=["blocked"])])
    assert is_blacklisted_text(cfg, "blocked word", -1001, topic_id=None) is True


def test_blacklist_clean_text_returns_false() -> None:
    cfg = _cfg([_entry(-1001, blacklist=["bad"])], blacklist_global=["spam"])
    assert is_blacklisted_text(cfg, "clean signal BTCUSDT LONG", -1001, topic_id=None) is False


# ---------------------------------------------------------------------------
# _is_inactive_channel — scope-aware
# ---------------------------------------------------------------------------


def test_is_inactive_channel_none_chat_id_returns_false() -> None:
    router = _router(_cfg([]))
    assert router._is_inactive_channel(None) is False


def test_is_inactive_channel_no_entry_returns_false() -> None:
    router = _router(_cfg([]))
    assert router._is_inactive_channel(-1001, topic_id=3) is False


def test_is_inactive_channel_topic_specific_inactive() -> None:
    cfg = _cfg([
        _entry(-1001, topic_id=None, active=True),
        _entry(-1001, topic_id=3, active=False),
    ])
    router = _router(cfg)
    assert router._is_inactive_channel(-1001, topic_id=3) is True


def test_is_inactive_channel_topic_specific_active_forum_wide_inactive() -> None:
    cfg = _cfg([
        _entry(-1001, topic_id=None, active=False),
        _entry(-1001, topic_id=3, active=True),
    ])
    router = _router(cfg)
    assert router._is_inactive_channel(-1001, topic_id=3) is False


def test_is_inactive_channel_forum_wide_inactive() -> None:
    cfg = _cfg([_entry(-1001, topic_id=None, active=False)])
    router = _router(cfg)
    assert router._is_inactive_channel(-1001, topic_id=None) is True


# ---------------------------------------------------------------------------
# _resolve_trader — topic-aware fallback
# ---------------------------------------------------------------------------


def _no_resolution() -> EffectiveTraderResult:
    return EffectiveTraderResult(trader_id=None, method="not_found", detail=None)


def test_resolve_trader_topic_entry_takes_precedence_over_forum_wide() -> None:
    cfg = _cfg([
        _entry(-1001, topic_id=None, trader_id="trader_a"),
        _entry(-1001, topic_id=3, trader_id="trader_b"),
    ])
    router = _router(cfg)
    router._trader_resolver.resolve.return_value = _no_resolution()

    result = router._resolve_trader(_item(source_topic_id=3))

    assert result.trader_id == "trader_b"
    assert result.method == "channels_yaml"


def test_resolve_trader_forum_wide_entry_used_for_topicless_message() -> None:
    cfg = _cfg([_entry(-1001, topic_id=None, trader_id="trader_a")])
    router = _router(cfg)
    router._trader_resolver.resolve.return_value = _no_resolution()

    result = router._resolve_trader(_item(source_topic_id=None))

    assert result.trader_id == "trader_a"
    assert result.method == "channels_yaml"


def test_resolve_trader_topic_fallback_to_forum_wide_when_no_topic_entry() -> None:
    """If message topic-3 but only forum-wide entry, forum-wide trader is used."""
    cfg = _cfg([_entry(-1001, topic_id=None, trader_id="trader_a")])
    router = _router(cfg)
    router._trader_resolver.resolve.return_value = _no_resolution()

    result = router._resolve_trader(_item(source_topic_id=3))

    assert result.trader_id == "trader_a"


def test_resolve_trader_no_entry_returns_original_resolution() -> None:
    cfg = _cfg([])
    router = _router(cfg)
    router._trader_resolver.resolve.return_value = _no_resolution()

    result = router._resolve_trader(_item(source_topic_id=3))

    assert result.trader_id is None
    assert result.method == "not_found"


def test_resolve_trader_primary_resolver_wins_over_entry_fallback() -> None:
    """If primary resolver finds a trader, entry fallback is not consulted."""
    cfg = _cfg([_entry(-1001, topic_id=3, trader_id="entry_trader")])
    router = _router(cfg)
    router._trader_resolver.resolve.return_value = EffectiveTraderResult(
        trader_id="primary_trader", method="signal_id", detail=None
    )

    result = router._resolve_trader(_item(source_topic_id=3))

    assert result.trader_id == "primary_trader"
    assert result.method == "signal_id"
