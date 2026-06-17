# tests/runtime_v2/control_plane/test_topic_router.py
from __future__ import annotations

import logging

import pytest

from src.runtime_v2.control_plane.models import (
    AccountConfig, AccountTopicsConfig,
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig,
)
from src.runtime_v2.control_plane.topic_router import TopicRouter


def _make_account(chat_id: int, per_trader: dict | None = None, thread_clean=103, thread_tech=102, thread_cmd=101):
    return AccountConfig(
        chat_id=chat_id,
        topics=AccountTopicsConfig(
            commands=TopicConfig(thread_id=thread_cmd),
            tech_log=TechLogConfig(thread_id=thread_tech),
            clean_log=CleanLogConfig(thread_id=thread_clean, per_trader=per_trader or {}),
        ),
    )


def _config_supergroup(per_trader: dict | None = None):
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        delivery_mode="supergroup_topics",
        per_account={"main": _make_account(-100999, per_trader=per_trader)},
    )


def _config_private_bot():
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


# --- per_account routing ---

def test_routes_to_correct_account():
    cfg = ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={
            "main": _make_account(-100111, thread_clean=11, thread_tech=3, thread_cmd=13),
            "account_nuovo": _make_account(-100222, thread_clean=11, thread_tech=3, thread_cmd=123),
        },
    )
    router = TopicRouter(cfg)
    assert router.route("CLEAN_LOG", account_id="main") == (-100111, 11)
    assert router.route("CLEAN_LOG", account_id="account_nuovo") == (-100222, 11)
    assert router.route("TECH_LOG", account_id="account_nuovo") == (-100222, 3)
    assert router.route("COMMANDS_REPLY", account_id="account_nuovo") == (-100222, 123)


def test_unknown_account_falls_back_to_default():
    cfg = ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={"main": _make_account(-100111, thread_clean=11)},
    )
    router = TopicRouter(cfg)
    assert router.route("CLEAN_LOG", account_id="sconosciuto") == (-100111, 11)
    assert router.route("CLEAN_LOG", account_id=None) == (-100111, 11)


# --- per_trader routing ---

def test_per_trader_routes_to_dedicated_thread():
    router = TopicRouter(_config_supergroup(per_trader={"trader_a": 42, "trader_3": 57}))
    assert router.route("CLEAN_LOG", trader_id="trader_a") == (-100999, 42)
    assert router.route("CLEAN_LOG", trader_id="trader_3") == (-100999, 57)


def test_per_trader_missing_falls_back_to_global():
    router = TopicRouter(_config_supergroup(per_trader={"trader_a": 42}))
    assert router.route("CLEAN_LOG", trader_id="trader_b") == (-100999, 103)
    assert router.route("CLEAN_LOG", trader_id="trader_c") == (-100999, 103)


def test_no_trader_id_falls_back_to_global():
    router = TopicRouter(_config_supergroup(per_trader={"trader_a": 42}))
    assert router.route("CLEAN_LOG") == (-100999, 103)
    assert router.route("CLEAN_LOG", trader_id=None) == (-100999, 103)


def test_per_trader_null_thread_routes_to_none():
    """Explicit null in per_trader → no thread (private-bot-style for that trader)."""
    router = TopicRouter(_config_supergroup(per_trader={"trader_prova": None}))
    assert router.route("CLEAN_LOG", trader_id="trader_prova") == (-100999, None)


def test_per_trader_ignored_for_non_clean_log():
    """trader_id never influences TECH_LOG or COMMANDS_REPLY routing."""
    router = TopicRouter(_config_supergroup(per_trader={"trader_a": 42}))
    assert router.route("TECH_LOG", trader_id="trader_a") == (-100999, 102)
    assert router.route("COMMANDS_REPLY", trader_id="trader_a") == (-100999, 101)


def test_per_trader_ignored_in_private_bot_mode():
    """In private_bot mode per_trader is irrelevant — always (chat_id, None)."""
    router = TopicRouter(_config_private_bot())
    assert router.route("CLEAN_LOG", trader_id="trader_a") == (-100999, None)


def test_stale_per_trader_key_logs_warning(caplog):
    known = {"trader_a", "trader_b"}
    with caplog.at_level(logging.WARNING, logger="src.runtime_v2.control_plane.topic_router"):
        TopicRouter(
            _config_supergroup(per_trader={"trader_vecchio": 99}),
            known_trader_ids=known,
        )
    assert "trader_vecchio" in caplog.text


def test_valid_per_trader_key_no_warning(caplog):
    known = {"trader_a", "trader_b", "trader_3"}
    with caplog.at_level(logging.WARNING, logger="src.runtime_v2.control_plane.topic_router"):
        TopicRouter(
            _config_supergroup(per_trader={"trader_a": 42, "trader_3": 57}),
            known_trader_ids=known,
        )
    assert "trader_a" not in caplog.text
    assert "trader_3" not in caplog.text


def test_no_known_trader_ids_skips_validation(caplog):
    """known_trader_ids=None disables validation entirely — no warnings."""
    with caplog.at_level(logging.WARNING, logger="src.runtime_v2.control_plane.topic_router"):
        TopicRouter(
            _config_supergroup(per_trader={"qualsiasi": 10}),
            known_trader_ids=None,
        )
    assert "qualsiasi" not in caplog.text
