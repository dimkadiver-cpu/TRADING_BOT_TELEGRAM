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
    entry = resolver.lookup("-1002222222222", topic_id=99)
    assert entry is not None
    assert entry.trader_id == "trader_c"


_MULTI_TRADER_YAML = """
channels:
  - chat_id: -1009999999999
    topic_id: 9
    label: "MultiTopic"
    active: true
    trader_id: null
    parser_profile: null
    resolution:
      max_depth: 3
      aliases:
        "trader#a": trader_a
        "trader#b": trader_b
    blacklist: []
  - chat_id: -1009999999999
    topic_id: 10
    label: "SingleTopic"
    active: true
    trader_id: trader_c
    blacklist: []
"""


@pytest.fixture
def multi_config_file(tmp_path):
    p = tmp_path / "channels.yaml"
    p.write_text(_MULTI_TRADER_YAML)
    return str(p)


@pytest.fixture
def multi_resolver(multi_config_file):
    return ChannelConfigResolver(multi_config_file)


def test_multi_trader_topic_has_null_trader_id(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.trader_id is None


def test_multi_trader_topic_aliases_loaded(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.aliases == {"trader#a": "trader_a", "trader#b": "trader_b"}


def test_multi_trader_topic_max_depth(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.resolution_max_depth == 3


def test_multi_trader_topic_defaults_to_resolution_mode_default(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.resolution_mode == "default"
    assert entry.pattern_group is None


def test_single_trader_topic_empty_aliases(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=10)
    assert entry is not None
    assert entry.aliases == {}
    assert entry.resolution_max_depth == 5


def test_existing_entries_unaffected_by_aliases_field(resolver):
    entry = resolver.lookup("-1001111111111", topic_id=3)
    assert entry is not None
    assert entry.aliases == {}
    assert entry.resolution_max_depth == 5


def test_multi_trader_topic_aliases_normalized(tmp_path):
    yaml_content = """
channels:
  - chat_id: -1009999999999
    topic_id: 9
    label: "NormTest"
    active: true
    trader_id: null
    resolution:
      aliases:
        "Trader [#А]": trader_a
    blacklist: []
"""
    p = tmp_path / "channels.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    resolver = ChannelConfigResolver(p)
    entry = resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert "trader#a" in entry.aliases
    assert entry.aliases["trader#a"] == "trader_a"


def test_multi_trader_topic_loads_resolution_mode_and_pattern_group(tmp_path):
    yaml_content = """
channels:
  - chat_id: -1009999999999
    topic_id: 9
    label: "PatternTopic"
    active: true
    trader_id: null
    resolution:
      mode: patterns_only
      pattern_group: multi_strategy_ru
      max_depth: 7
    blacklist: []
"""
    p = tmp_path / "channels.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    resolver = ChannelConfigResolver(p)
    entry = resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.resolution_mode == "patterns_only"
    assert entry.pattern_group == "multi_strategy_ru"
    assert entry.resolution_max_depth == 7
