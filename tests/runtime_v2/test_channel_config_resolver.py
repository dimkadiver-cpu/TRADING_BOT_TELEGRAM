from __future__ import annotations
import pytest
import yaml
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)

_SAMPLE_YAML = """
recovery:
  max_hours: 7
blacklist_global:
  - "#admin"
  - "#info"
channels:
  - chat_id: -1001111111111
    topic_id: 3
    label: "Trader_A_Topic"
    active: true
    trader_id: trader_a
    blacklist: []
  - chat_id: -1001111111111
    topic_id: 4
    label: "Trader_B_Topic"
    active: true
    trader_id: trader_b
    blacklist: ["#skip"]
  - chat_id: -1002222222222
    label: "Mono_C"
    active: true
    trader_id: trader_c
    blacklist: []
  - chat_id: -1003333333333
    label: "Inactive_D"
    active: false
    trader_id: trader_d
    blacklist: []
  - chat_id: -1004444444444
    label: "Custom_Profile"
    active: true
    trader_id: trader_e
    parser_profile: trader_e_v2
    blacklist: []
"""


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "channels.yaml"
    p.write_text(_SAMPLE_YAML)
    return str(p)


@pytest.fixture
def resolver(config_file):
    return ChannelConfigResolver(config_file)


def test_lookup_by_chat_and_topic(resolver):
    entry = resolver.lookup("-1001111111111", topic_id=3)
    assert entry is not None
    assert entry.trader_id == "trader_a"
    assert entry.active is True


def test_lookup_different_topic(resolver):
    entry = resolver.lookup("-1001111111111", topic_id=4)
    assert entry is not None
    assert entry.trader_id == "trader_b"


def test_lookup_no_topic_mono_trader(resolver):
    entry = resolver.lookup("-1002222222222", topic_id=None)
    assert entry is not None
    assert entry.trader_id == "trader_c"


def test_lookup_unknown_chat_returns_none(resolver):
    assert resolver.lookup("-9999999999", topic_id=None) is None


def test_lookup_inactive_returns_entry_with_active_false(resolver):
    entry = resolver.lookup("-1003333333333", topic_id=None)
    assert entry is not None
    assert entry.active is False


def test_lookup_parser_profile_override(resolver):
    entry = resolver.lookup("-1004444444444", topic_id=None)
    assert entry is not None
    assert entry.parser_profile == "trader_e_v2"


def test_lookup_parser_profile_defaults_to_trader_id(resolver):
    entry = resolver.lookup("-1002222222222", topic_id=None)
    assert entry is not None
    assert entry.parser_profile == "trader_c"


def test_global_blacklist_match(resolver):
    assert resolver.is_globally_blacklisted("#admin pinned") is True
    assert resolver.is_globally_blacklisted("#info message") is True


def test_global_blacklist_no_match(resolver):
    assert resolver.is_globally_blacklisted("BUY BTC 45000") is False


def test_reload_picks_up_changes(config_file):
    resolver = ChannelConfigResolver(config_file)
    assert resolver.lookup("-1002222222222", topic_id=None).trader_id == "trader_c"
    data = yaml.safe_load(open(config_file))
    for ch in data["channels"]:
        if str(ch["chat_id"]) == "-1002222222222":
            ch["trader_id"] = "trader_c_new"
    with open(config_file, "w") as f:
        yaml.dump(data, f)
    resolver.reload()
    assert resolver.lookup("-1002222222222", topic_id=None).trader_id == "trader_c_new"


def test_topic_fallback_to_chat_only(resolver):
    # topic_id=99 not configured; falls back to chat-level entry (no topic_id in yaml)
    entry = resolver.lookup("-1002222222222", topic_id=99)
    assert entry is not None
    assert entry.trader_id == "trader_c"
