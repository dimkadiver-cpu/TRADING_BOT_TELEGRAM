from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.telegram.pattern_extractors import TextPatternMatch
from src.telegram.trader_resolver import TraderResolver
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.runtime_v2.persistence.raw_messages import ChainNode
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelEntry

_TS = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _envelope(
    chat_id: str = "-100123",
    topic_id: int | None = 9,
    text: str | None = "buy btc",
    reply_id: int | None = None,
    raw_msg_id: int = 1,
) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=raw_msg_id,
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=topic_id,
        telegram_message_id=500,
        reply_to_message_id=reply_id,
        raw_text=text,
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="pending",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _entry(
    trader_id: str | None,
    topic_id: int | None = 9,
    aliases: dict | None = None,
    max_depth: int = 5,
    resolution_mode: str = "default",
    pattern_group: str | None = None,
) -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100123",
        topic_id=topic_id,
        label="Test",
        active=True,
        trader_id=trader_id,
        parser_profile=trader_id or "",
        blacklist=[],
        aliases=aliases or {},
        resolution_max_depth=max_depth,
        resolution_mode=resolution_mode,
        pattern_group=pattern_group,
    )


@pytest.fixture
def channel_config():
    return MagicMock()


@pytest.fixture
def raw_repo():
    return MagicMock()


@pytest.fixture
def pattern_catalog():
    catalog = MagicMock()
    catalog.resolve.return_value = TextPatternMatch(trader_id=None, is_ambiguous=False)
    return catalog


@pytest.fixture
def resolver(channel_config, raw_repo, pattern_catalog):
    return TraderResolver(
        channel_config=channel_config,
        raw_repo=raw_repo,
        pattern_catalog=pattern_catalog,
    )


# --- Step 1: config statico ---

def test_config_single_trader_chat(resolver, channel_config):
    channel_config.lookup.return_value = _entry("trader_a", topic_id=None)
    ctx = resolver.resolve(_envelope(topic_id=None))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_chat_id"
    assert not ctx.is_ambiguous


def test_config_single_trader_topic(resolver, channel_config):
    channel_config.lookup.return_value = _entry("trader_a", topic_id=9)
    ctx = resolver.resolve(_envelope(topic_id=9))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_topic_config"


# --- Step 2: alias nel testo ---

def test_alias_in_text_resolved(resolver, channel_config):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    ctx = resolver.resolve(_envelope(text="Trader #A signal buy btc"))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "content_alias"


def test_alias_ambiguous_two_tags(resolver, channel_config):
    channel_config.lookup.return_value = _entry(
        None, aliases={"trader#a": "trader_a", "trader#b": "trader_b"}
    )
    ctx = resolver.resolve(_envelope(text="[trader#A] e [trader#B]"))
    assert ctx.trader_id is None
    assert ctx.is_ambiguous is True
    assert ctx.method == "content_alias_ambiguous"


def test_alias_same_trader_twice_not_ambiguous(resolver, channel_config):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    ctx = resolver.resolve(_envelope(text="[trader#A] buy btc trader #A confirmed"))
    assert ctx.trader_id == "trader_a"
    assert not ctx.is_ambiguous


def test_text_patterns_resolved_when_aliases_miss(resolver, channel_config, pattern_catalog):
    channel_config.lookup.return_value = _entry(None, aliases={}, pattern_group="multi_strategy_ru")
    pattern_catalog.resolve.return_value = TextPatternMatch(
        trader_id="sma_intraday",
        is_ambiguous=False,
    )
    text = "Кросс SMA 21/55 · интрадей (1H)"
    ctx = resolver.resolve(_envelope(text=text))
    assert ctx.trader_id == "sma_intraday"
    assert ctx.method == "content_alias"
    pattern_catalog.resolve.assert_called_once_with("multi_strategy_ru", text)


def test_text_patterns_ambiguous_marks_review(resolver, channel_config, pattern_catalog):
    channel_config.lookup.return_value = _entry(None, aliases={}, pattern_group="multi_strategy_ru")
    pattern_catalog.resolve.return_value = TextPatternMatch(trader_id=None, is_ambiguous=True)
    ctx = resolver.resolve(_envelope(text="ambiguous strategy"))
    assert ctx.trader_id is None
    assert ctx.is_ambiguous is True
    assert ctx.method == "content_alias_ambiguous"


def test_patterns_only_skips_reply_chain_and_links(resolver, channel_config, raw_repo, pattern_catalog):
    channel_config.lookup.return_value = _entry(
        None,
        aliases={},
        resolution_mode="patterns_only",
        pattern_group="multi_strategy_ru",
    )
    pattern_catalog.resolve.return_value = TextPatternMatch(trader_id=None, is_ambiguous=False)
    ctx = resolver.resolve(_envelope(text="no match", reply_id=42))
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"
    raw_repo.get_chain_node.assert_not_called()


# --- Step 3: reply chain ---

def test_reply_chain_resolved_trader_id(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id="trader_b",
        raw_text="old signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="sl moved", reply_id=42))
    assert ctx.trader_id == "trader_b"
    assert ctx.method == "reply_chain"
    assert ctx.detail == "42"


def test_reply_chain_uses_source_trader_id_when_resolved_is_none(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id="trader_c",
        resolved_trader_id=None,
        raw_text="old signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="close", reply_id=55))
    assert ctx.trader_id == "trader_c"
    assert ctx.method == "reply_chain"


def test_reply_chain_parent_not_in_db_returns_unresolved(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = None
    ctx = resolver.resolve(_envelope(text="close", reply_id=55))
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"


def test_reply_chain_walks_to_grandparent(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.side_effect = [
        ChainNode(source_trader_id=None, resolved_trader_id=None, raw_text="update", reply_to_message_id=10),
        ChainNode(source_trader_id=None, resolved_trader_id="trader_d", raw_text="signal", reply_to_message_id=None),
    ]
    ctx = resolver.resolve(_envelope(text="close", reply_id=20))
    assert ctx.trader_id == "trader_d"
    assert ctx.method == "reply_chain"
    assert ctx.detail == "10"


def test_reply_chain_alias_in_parent_text(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id=None,
        raw_text="Trader #A buy btc",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="sl moved", reply_id=42))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "reply_chain_alias"


def test_reply_chain_respects_max_depth(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={}, max_depth=2)
    raw_repo.get_chain_node.side_effect = [
        ChainNode(None, None, "msg", reply_to_message_id=i)
        for i in range(10, 5, -1)
    ]
    ctx = resolver.resolve(_envelope(text="close", reply_id=15))
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"
    assert raw_repo.get_chain_node.call_count == 2


# --- Step 4: link singolo ---

def test_single_link_resolved_via_chain(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id="trader_e",
        raw_text="signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="see https://t.me/c/12345678/99"))
    assert ctx.trader_id == "trader_e"
    assert ctx.method == "link"


# --- Step 5: link multipli ---

def test_multi_link_concordant(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id="trader_f",
        raw_text="signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="https://t.me/c/1/10 and https://t.me/c/1/20"))
    assert ctx.trader_id == "trader_f"
    assert ctx.method == "link_multi"


def test_multi_link_discordant_ambiguous(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.side_effect = [
        ChainNode(None, "trader_a", "sig", None),
        ChainNode(None, "trader_b", "sig", None),
    ]
    ctx = resolver.resolve(_envelope(text="https://t.me/c/1/10 and https://t.me/c/1/20"))
    assert ctx.trader_id is None
    assert ctx.is_ambiguous is True
    assert ctx.method == "content_alias_ambiguous"


# --- Tag vince su reply ---

def test_text_tag_wins_over_reply_chain(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    raw_repo.get_chain_node.return_value = ChainNode(None, "trader_b", "signal", None)
    ctx = resolver.resolve(_envelope(text="Trader #A update", reply_id=42))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "content_alias"
    raw_repo.get_chain_node.assert_not_called()


# --- Unresolved ---

def test_no_signal_unresolved(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = None
    ctx = resolver.resolve(_envelope(text="ciao come va"))
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"
    assert not ctx.is_ambiguous
