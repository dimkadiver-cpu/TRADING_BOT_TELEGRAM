from __future__ import annotations

from src.runtime_v2.execution_gateway import client_order_id as coid_mod


def test_exit_partial_role_parses():
    coid_str = "tsb:10:5:exit_partial:1"
    parsed = coid_mod.parse(coid_str)
    assert parsed.role == "exit_partial"


def test_exit_full_role_parses():
    coid_str = "tsb:10:6:exit_full:1"
    parsed = coid_mod.parse(coid_str)
    assert parsed.role == "exit_full"


def test_sync_role_parses():
    coid_str = "tsb:10:7:sync:1"
    parsed = coid_mod.parse(coid_str)
    assert parsed.role == "sync"
