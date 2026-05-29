from __future__ import annotations

from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.models import (
    CleanLogConfig,
    ControlPlaneConfig,
    TechLogConfig,
    TopicConfig,
    TopicsConfig,
)


def _config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[42, 43],
    )


def test_authorized_user_in_commands_topic_ok():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=101, user_id=42)
    assert res.decision == "OK"
    assert res.reason is None


def test_wrong_chat_ignored():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-1, thread_id=101, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_chat"


def test_wrong_topic_ignored():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=999, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"


def test_unauthorized_user_rejected():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=101, user_id=7)
    assert res.decision == "REJECT_UNAUTHORIZED"
    assert res.reason == "unauthorized_user"


def test_missing_thread_id_treated_as_wrong_topic():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=None, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"
