from __future__ import annotations

import pytest

from src.runtime_v2.control_plane.config import (
    ControlPlaneConfigError,
    load_control_plane_config,
)


_VALID_YAML = """
enabled: true
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
topics:
  commands: {thread_id: 101}
  tech_log: {thread_id: 102}
  clean_log: {thread_id: 103}
authorized_users:
  - "${CP_USER}"
startup:
  mode: standby
  restore_max_age_seconds: 600
"""


def _write(tmp_path, text):
    path = tmp_path / "telegram_control.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_valid_config_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")

    cfg = load_control_plane_config(_write(tmp_path, _VALID_YAML))

    assert cfg.token == "999:XYZ"
    assert cfg.chat_id == -1009999
    assert cfg.authorized_users == [42]
    assert cfg.startup.mode == "standby"
    assert cfg.startup.restore_max_age_seconds == 600
    assert cfg.topics.commands.thread_id == 101


def test_missing_token_env_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("CP_TOKEN", raising=False)
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")

    with pytest.raises(ControlPlaneConfigError) as exc:
        load_control_plane_config(_write(tmp_path, _VALID_YAML))

    assert "CP_TOKEN" in str(exc.value)


def test_unresolved_env_placeholder_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.delenv("CP_CHAT", raising=False)
    monkeypatch.setenv("CP_USER", "42")

    with pytest.raises(ControlPlaneConfigError) as exc:
        load_control_plane_config(_write(tmp_path, _VALID_YAML))

    assert "CP_CHAT" in str(exc.value)


def test_missing_required_field_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")

    bad = """
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
topics:
  commands: {thread_id: 101}
"""

    with pytest.raises(ControlPlaneConfigError):
        load_control_plane_config(_write(tmp_path, bad))


def test_top_level_yaml_list_raises(tmp_path):
    bad = """
- not
- a
- mapping
"""

    with pytest.raises(ControlPlaneConfigError) as exc:
        load_control_plane_config(_write(tmp_path, bad))

    assert "top-level YAML must be a mapping" in str(exc.value)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ControlPlaneConfigError):
        load_control_plane_config(str(tmp_path / "nope.yaml"))


_PRIVATE_BOT_YAML = """
delivery_mode: private_bot
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
authorized_users:
  - "${CP_USER}"
"""


def test_private_bot_config_without_topics(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    cfg = load_control_plane_config(_write(tmp_path, _PRIVATE_BOT_YAML))
    assert cfg.delivery_mode == "private_bot"
    assert cfg.topics.commands.thread_id is None
    assert cfg.topics.tech_log.thread_id is None
    assert cfg.topics.clean_log.thread_id is None


def test_supergroup_without_topics_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    bad = """
delivery_mode: supergroup_topics
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
authorized_users:
  - "${CP_USER}"
"""
    with pytest.raises(ControlPlaneConfigError):
        load_control_plane_config(_write(tmp_path, bad))
