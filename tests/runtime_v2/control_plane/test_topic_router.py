# tests/runtime_v2/control_plane/test_topic_router.py
from __future__ import annotations

import pytest

from src.runtime_v2.control_plane.models import (
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig, TopicsConfig,
)
from src.runtime_v2.control_plane.topic_router import TopicRouter


def _config_supergroup():
    return ControlPlaneConfig(
        token="t", chat_id=-100999,
        delivery_mode="supergroup_topics",
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
    )


def _config_private_bot():
    return ControlPlaneConfig(
        token="t", chat_id=-100999,
        delivery_mode="private_bot",
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=None),
            tech_log=TechLogConfig(thread_id=None),
            clean_log=CleanLogConfig(thread_id=None),
        ),
    )


def test_supergroup_routes_to_thread():
    router = TopicRouter(_config_supergroup())
    assert router.route("CLEAN_LOG") == (-100999, 103)
    assert router.route("TECH_LOG") == (-100999, 102)
    assert router.route("COMMANDS_REPLY") == (-100999, 101)


def test_private_bot_routes_without_thread():
    router = TopicRouter(_config_private_bot())
    assert router.route("CLEAN_LOG") == (-100999, None)
    assert router.route("TECH_LOG") == (-100999, None)
    assert router.route("COMMANDS_REPLY") == (-100999, None)


def test_resolve_unknown_raises():
    router = TopicRouter(_config_supergroup())
    with pytest.raises((ValueError, KeyError)):
        router.route("NOPE")
