from __future__ import annotations
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)
from src.runtime_v2.intake.models import RawMessageEnvelope

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _make_envelope(
    chat_id: str = "-100123",
    topic_id: int | None = None,
    text: str = "BUY BTC",
    reply_id: int | None = None,
) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=1,
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=topic_id,
        telegram_message_id=456,
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


def _make_channel_entry(trader_id: str, topic_id: int | None = None, active: bool = True) -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100123",
        topic_id=topic_id,
        label="Test",
        active=active,
        trader_id=trader_id,
        parser_profile=trader_id,
        blacklist=[],
    )


def _make_effective_result(trader_id, method, detail=None):
    result = MagicMock()
    result.trader_id = trader_id
    result.method = method
    result.detail = detail
    return result


@pytest.fixture
def channel_config():
    return MagicMock(spec=ChannelConfigResolver)


@pytest.fixture
def effective_resolver():
    return MagicMock()


@pytest.fixture
def resolver(channel_config, effective_resolver):
    return RuntimeV2TraderResolver(channel_config, effective_resolver)


def test_config_driven_chat_id(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = _make_channel_entry("trader_a")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_chat_id"
    assert not ctx.is_ambiguous
    effective_resolver.resolve.assert_not_called()


def test_config_driven_topic_config(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = _make_channel_entry("trader_a", topic_id=3)
    env = _make_envelope(topic_id=3)
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_topic_config"
    effective_resolver.resolve.assert_not_called()


def test_inactive_channel_falls_through_to_effective(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = _make_channel_entry("trader_a", active=False)
    effective_resolver.resolve.return_value = _make_effective_result("trader_b", "content_alias")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_b"
    assert ctx.method == "content_alias"


def test_no_config_entry_falls_through_to_effective(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result("trader_c", "content_alias")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_c"
    assert ctx.method == "content_alias"


def test_ambiguous_alias_sets_is_ambiguous(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result(
        None, "content_alias_ambiguous", detail="trader_a,trader_b"
    )
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id is None
    assert ctx.is_ambiguous is True
    assert ctx.method == "content_alias_ambiguous"


def test_unresolved_returns_unresolved_method(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result(None, "unresolved")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"
    assert not ctx.is_ambiguous


def test_reply_chain_method_maps_correctly(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result("trader_a", "reply_chain")
    env = _make_envelope(reply_id=100)
    ctx = resolver.resolve(env)
    assert ctx.method == "reply_chain"
    assert ctx.trader_id == "trader_a"
