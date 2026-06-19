from __future__ import annotations

from src.runtime_v2.control_plane.models import (
    AccountConfig,
    AccountTopicsConfig,
    CleanLogConfig,
    ControlPlaneConfig,
    TechLogConfig,
    TopicConfig,
)
from src.runtime_v2.control_plane.scope_resolver import QueryScope, ScopeResolver


def _config_multi() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="demo_1",
        per_account={
            "demo_1": AccountConfig(
                chat_id=-100999,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=4),
                    tech_log=TechLogConfig(thread_id=5),
                    clean_log=CleanLogConfig(
                        thread_id=6,
                        per_trader={"trader_a": 316, "trader_b": 317},
                    ),
                ),
            ),
            "demo_2": AccountConfig(
                chat_id=-100999,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=42),
                    tech_log=TechLogConfig(thread_id=43),
                    clean_log=CleanLogConfig(thread_id=44),
                ),
            ),
        },
    )


def test_commands_thread_maps_to_full_account_scope():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(4)
    assert scope == QueryScope(account_id="demo_1", trader_ids=None)


def test_commands_thread_second_account():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(42)
    assert scope == QueryScope(account_id="demo_2", trader_ids=None)


def test_clean_log_fallback_thread_maps_to_full_account_scope():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(6)
    assert scope == QueryScope(account_id="demo_1", trader_ids=None)


def test_clean_log_per_trader_thread_maps_to_single_trader_scope():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(316)
    assert scope == QueryScope(account_id="demo_1", trader_ids=["trader_a"])


def test_clean_log_second_per_trader_thread():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(317)
    assert scope == QueryScope(account_id="demo_1", trader_ids=["trader_b"])


def test_tech_log_thread_not_registered_falls_back_to_default():
    # tech_log thread (5) must not be in the map
    r = ScopeResolver(_config_multi())
    scope = r.resolve(5)
    assert scope == QueryScope(account_id="demo_1", trader_ids=None)


def test_unknown_thread_falls_back_to_default_account():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(9999)
    assert scope == QueryScope(account_id="demo_1", trader_ids=None)


def test_none_thread_id_falls_back_to_default_account():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(None)
    assert scope == QueryScope(account_id="demo_1", trader_ids=None)


def test_per_trader_with_none_value_is_skipped():
    cfg = ControlPlaneConfig(
        token="t",
        default_account="main",
        per_account={
            "main": AccountConfig(
                chat_id=-100,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=1),
                    tech_log=TechLogConfig(thread_id=2),
                    clean_log=CleanLogConfig(
                        thread_id=3,
                        per_trader={"trader_x": None},
                    ),
                ),
            )
        },
    )
    r = ScopeResolver(cfg)
    # thread_id=None in per_trader should not be registered; no KeyError
    scope = r.resolve(None)
    assert scope == QueryScope(account_id="main", trader_ids=None)


def test_query_scope_is_frozen():
    scope = QueryScope(account_id="x", trader_ids=None)
    try:
        scope.account_id = "y"  # type: ignore[misc]
        assert False, "should raise"
    except (AttributeError, TypeError):
        pass
