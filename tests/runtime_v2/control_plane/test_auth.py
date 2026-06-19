from __future__ import annotations

from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.models import (
    AccountConfig,
    AccountTopicsConfig,
    CleanLogConfig,
    ControlPlaneConfig,
    TechLogConfig,
    TopicConfig,
)


def _config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={
            "main": AccountConfig(
                chat_id=-100999,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=101),
                    tech_log=TechLogConfig(thread_id=102),
                    clean_log=CleanLogConfig(thread_id=103),
                ),
            )
        },
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


def _config_private_bot() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        delivery_mode="private_bot",
        per_account={
            "main": AccountConfig(
                chat_id=-100999,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=None),
                    tech_log=TechLogConfig(thread_id=None),
                    clean_log=CleanLogConfig(thread_id=None),
                ),
            )
        },
        authorized_users=[42, 43],
    )


def test_private_bot_authorized_no_thread():
    v = AuthValidator(_config_private_bot())
    res = v.validate(chat_id=-100999, thread_id=None, user_id=42)
    assert res.decision == "OK"


def test_private_bot_wrong_chat():
    v = AuthValidator(_config_private_bot())
    res = v.validate(chat_id=-1, thread_id=None, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_chat"


def test_private_bot_unauthorized_user():
    v = AuthValidator(_config_private_bot())
    res = v.validate(chat_id=-100999, thread_id=None, user_id=99)
    assert res.decision == "REJECT_UNAUTHORIZED"
    assert res.reason == "unauthorized_user"


# --- /dashboard from clean_log topics ---


def _config_with_per_trader() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={
            "main": AccountConfig(
                chat_id=-100999,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=101),
                    tech_log=TechLogConfig(thread_id=102),
                    clean_log=CleanLogConfig(
                        thread_id=103,
                        per_trader={"trader_a": 200, "trader_b": 201},
                    ),
                ),
            )
        },
        authorized_users=[42],
    )


def test_dashboard_allowed_from_clean_log_fallback_thread():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(chat_id=-100999, thread_id=103, user_id=42, command_name="dashboard")
    assert res.decision == "OK"


def test_dashboard_allowed_from_clean_log_per_trader_thread():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(chat_id=-100999, thread_id=200, user_id=42, command_name="dashboard")
    assert res.decision == "OK"


def test_dashboard_allowed_from_clean_log_second_per_trader_thread():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(chat_id=-100999, thread_id=201, user_id=42, command_name="dashboard")
    assert res.decision == "OK"


def test_dashboard_from_clean_log_unauthorized_user_rejected():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(chat_id=-100999, thread_id=103, user_id=99, command_name="dashboard")
    assert res.decision == "REJECT_UNAUTHORIZED"
    assert res.reason == "unauthorized_user"


def test_dashboard_not_allowed_from_tech_log_thread():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(chat_id=-100999, thread_id=102, user_id=42, command_name="dashboard")
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"


def test_other_command_not_allowed_from_clean_log_thread():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(chat_id=-100999, thread_id=103, user_id=42, command_name="status")
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"


def test_command_name_none_in_clean_log_thread_is_ignored():
    # No command_name supplied (default None) — should not grant access from clean_log
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(chat_id=-100999, thread_id=103, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"


def test_private_bot_dashboard_always_ok_from_clean_log():
    # private_bot mode: thread checks are skipped entirely
    v = AuthValidator(_config_private_bot())
    res = v.validate(chat_id=-100999, thread_id=None, user_id=42, command_name="dashboard")
    assert res.decision == "OK"
