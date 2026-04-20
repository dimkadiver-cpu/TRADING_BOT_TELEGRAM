"""Tests for channel_config: loading, validation, hot reload."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.telegram.channel_config import (
    ChannelConfigWatcher,
    ChannelsConfig,
    ChannelEntry,
    load_channels_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def yaml_path(tmp_path: Path) -> Path:
    return tmp_path / "channels.yaml"


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_channels_config
# ---------------------------------------------------------------------------


def test_load_empty_channels(yaml_path: Path) -> None:
    _write_yaml(yaml_path, "recovery:\n  max_hours: 4\nblacklist_global: []\nchannels: []\n")
    cfg = load_channels_config(str(yaml_path))
    assert cfg.channels == []
    assert cfg.recovery_max_hours == 4
    assert cfg.blacklist_global == []


def test_load_single_channel(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
recovery:
  max_hours: 2
blacklist_global:
  - "#admin"
channels:
  - chat_id: -100123
    label: alpha
    active: true
    trader_id: trader_a
    blacklist:
      - "#weekly"
""",
    )
    cfg = load_channels_config(str(yaml_path))
    assert cfg.recovery_max_hours == 2
    assert cfg.blacklist_global == ["#admin"]
    assert len(cfg.channels) == 1
    ch = cfg.channels[0]
    assert ch.chat_id == -100123
    assert ch.label == "alpha"
    assert ch.active is True
    assert ch.trader_id == "trader_a"
    assert ch.blacklist == ["#weekly"]


def test_active_channels_filter(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: 1
    label: a
    active: true
    trader_id: null
  - chat_id: 2
    label: b
    active: false
    trader_id: null
""",
    )
    cfg = load_channels_config(str(yaml_path))
    assert cfg.active_chat_ids == {1}
    assert len(cfg.active_channels) == 1


def test_channel_for_returns_correct(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        "channels:\n  - chat_id: 42\n    label: x\n    active: true\n    trader_id: null\n",
    )
    cfg = load_channels_config(str(yaml_path))
    assert cfg.channel_for(42) is not None
    assert cfg.channel_for(99) is None


def test_trader_id_null(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        "channels:\n  - chat_id: 1\n    label: multi\n    active: true\n    trader_id: null\n",
    )
    cfg = load_channels_config(str(yaml_path))
    assert cfg.channels[0].trader_id is None


def test_defaults_when_keys_missing(yaml_path: Path) -> None:
    _write_yaml(yaml_path, "{}")
    cfg = load_channels_config(str(yaml_path))
    assert cfg.recovery_max_hours == 4
    assert cfg.blacklist_global == []
    assert cfg.channels == []


# ---------------------------------------------------------------------------
# ChannelConfigWatcher — hot reload
# ---------------------------------------------------------------------------


def test_watcher_calls_on_reload(yaml_path: Path) -> None:
    _write_yaml(yaml_path, "channels: []\n")
    received: list[ChannelsConfig] = []
    watcher = ChannelConfigWatcher(str(yaml_path), on_reload=received.append)
    watcher.start()
    try:
        time.sleep(0.2)
        _write_yaml(
            yaml_path,
            "channels:\n  - chat_id: 99\n    label: new\n    active: true\n    trader_id: null\n",
        )
        time.sleep(1.0)  # watchdog debounce
    finally:
        watcher.stop()

    assert len(received) >= 1
    last = received[-1]
    assert last.channels[0].chat_id == 99


def test_watcher_logs_parse_error(yaml_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write_yaml(yaml_path, "channels: []\n")
    watcher = ChannelConfigWatcher(str(yaml_path), on_reload=lambda _: None)
    watcher.start()
    try:
        time.sleep(0.2)
        yaml_path.write_text("chat_id: [invalid: yaml: {}", encoding="utf-8")
        time.sleep(1.0)
    finally:
        watcher.stop()
    # Should not raise; error is logged instead


# ---------------------------------------------------------------------------
# topic_id — loading
# ---------------------------------------------------------------------------


def test_load_channel_with_topic_id(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: -1001
    label: forum_topic
    active: true
    trader_id: trader_a
    topic_id: 3
""",
    )
    cfg = load_channels_config(str(yaml_path))
    assert cfg.channels[0].topic_id == 3


def test_load_channel_without_topic_id_defaults_none(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        "channels:\n  - chat_id: 1\n    label: x\n    active: true\n    trader_id: null\n",
    )
    cfg = load_channels_config(str(yaml_path))
    assert cfg.channels[0].topic_id is None


def test_load_channel_topic_id_general(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        "channels:\n  - chat_id: 1\n    label: x\n    active: true\n    trader_id: null\n    topic_id: 1\n",
    )
    cfg = load_channels_config(str(yaml_path))
    assert cfg.channels[0].topic_id == 1


def test_load_invalid_topic_id_zero_raises(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        "channels:\n  - chat_id: 1\n    label: x\n    active: true\n    trader_id: null\n    topic_id: 0\n",
    )
    with pytest.raises(ValueError, match="topic_id"):
        load_channels_config(str(yaml_path))


def test_load_invalid_topic_id_negative_raises(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        "channels:\n  - chat_id: 1\n    label: x\n    active: true\n    trader_id: null\n    topic_id: -5\n",
    )
    with pytest.raises(ValueError, match="topic_id"):
        load_channels_config(str(yaml_path))


def test_load_duplicate_scope_raises(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: 1
    label: a
    active: true
    trader_id: null
    topic_id: 3
  - chat_id: 1
    label: b
    active: true
    trader_id: null
    topic_id: 3
""",
    )
    with pytest.raises(ValueError, match="Duplicate scope"):
        load_channels_config(str(yaml_path))


def test_load_duplicate_forum_wide_scope_raises(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: 1
    label: a
    active: true
    trader_id: null
  - chat_id: 1
    label: b
    active: true
    trader_id: null
""",
    )
    with pytest.raises(ValueError, match="Duplicate scope"):
        load_channels_config(str(yaml_path))


def test_load_same_chat_different_topics_ok(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: -1001
    label: t3
    active: true
    trader_id: trader_a
    topic_id: 3
  - chat_id: -1001
    label: t4
    active: true
    trader_id: trader_b
    topic_id: 4
""",
    )
    cfg = load_channels_config(str(yaml_path))
    assert len(cfg.channels) == 2
    assert cfg.channels[0].topic_id == 3
    assert cfg.channels[1].topic_id == 4


# ---------------------------------------------------------------------------
# entries_for_chat
# ---------------------------------------------------------------------------


def test_entries_for_chat_returns_all_entries(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: -1001
    label: t3
    active: true
    trader_id: trader_a
    topic_id: 3
  - chat_id: -1001
    label: t4
    active: false
    trader_id: trader_b
    topic_id: 4
  - chat_id: -9999
    label: other
    active: true
    trader_id: trader_c
""",
    )
    cfg = load_channels_config(str(yaml_path))
    entries = cfg.entries_for_chat(-1001)
    assert len(entries) == 2
    assert all(e.chat_id == -1001 for e in entries)


def test_entries_for_chat_unknown_chat(yaml_path: Path) -> None:
    _write_yaml(yaml_path, "channels:\n  - chat_id: 1\n    label: x\n    active: true\n    trader_id: null\n")
    cfg = load_channels_config(str(yaml_path))
    assert cfg.entries_for_chat(9999) == []


# ---------------------------------------------------------------------------
# match_entry
# ---------------------------------------------------------------------------


def _cfg_multi_topic(yaml_path: Path) -> ChannelsConfig:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: -1001
    label: wide
    active: true
    trader_id: forum_trader
  - chat_id: -1001
    label: t3
    active: true
    trader_id: trader_a
    topic_id: 3
  - chat_id: -1001
    label: t4
    active: true
    trader_id: trader_b
    topic_id: 4
""",
    )
    return load_channels_config(str(yaml_path))


def test_match_entry_topic_specific_wins(yaml_path: Path) -> None:
    cfg = _cfg_multi_topic(yaml_path)
    entry = cfg.match_entry(-1001, 3)
    assert entry is not None
    assert entry.topic_id == 3
    assert entry.trader_id == "trader_a"


def test_match_entry_fallback_to_forum_wide(yaml_path: Path) -> None:
    cfg = _cfg_multi_topic(yaml_path)
    # topic 99 not in config → falls back to forum-wide (topic_id=None)
    entry = cfg.match_entry(-1001, 99)
    assert entry is not None
    assert entry.topic_id is None
    assert entry.trader_id == "forum_trader"


def test_match_entry_none_topic_returns_forum_wide(yaml_path: Path) -> None:
    cfg = _cfg_multi_topic(yaml_path)
    entry = cfg.match_entry(-1001, None)
    assert entry is not None
    assert entry.topic_id is None


def test_match_entry_no_match(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        "channels:\n  - chat_id: 1\n    label: x\n    active: true\n    trader_id: null\n    topic_id: 3\n",
    )
    cfg = load_channels_config(str(yaml_path))
    # chat_id exists but no forum-wide and topic_id=None → no match
    assert cfg.match_entry(1, None) is None


def test_match_entry_general_topic(yaml_path: Path) -> None:
    _write_yaml(
        yaml_path,
        """
channels:
  - chat_id: -1001
    label: general
    active: true
    trader_id: trader_g
    topic_id: 1
  - chat_id: -1001
    label: wide
    active: true
    trader_id: forum_trader
""",
    )
    cfg = load_channels_config(str(yaml_path))
    entry = cfg.match_entry(-1001, 1)
    assert entry is not None
    assert entry.topic_id == 1
    assert entry.trader_id == "trader_g"


def test_match_entry_unknown_chat(yaml_path: Path) -> None:
    _write_yaml(yaml_path, "channels:\n  - chat_id: 1\n    label: x\n    active: true\n    trader_id: null\n")
    cfg = load_channels_config(str(yaml_path))
    assert cfg.match_entry(9999, None) is None
    assert cfg.match_entry(9999, 3) is None
