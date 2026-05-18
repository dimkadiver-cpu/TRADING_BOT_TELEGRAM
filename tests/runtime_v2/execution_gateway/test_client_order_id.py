# tests/runtime_v2/execution_gateway/test_client_order_id.py
from __future__ import annotations

import pytest


def test_build_entry():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    coid = build(trade_chain_id=42, command_id=1001, role="entry", sequence=1)
    assert coid == "tsb:42:1001:entry:1"
    parsed = parse(coid)
    assert parsed.trade_chain_id == 42
    assert parsed.command_id == 1001
    assert parsed.role == "entry"
    assert parsed.sequence == 1


def test_build_tp():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    coid = build(trade_chain_id=42, command_id=1004, role="tp", sequence=3)
    assert coid == "tsb:42:1004:tp:3"
    parsed = parse(coid)
    assert parsed.role == "tp"
    assert parsed.sequence == 3


def test_roundtrip():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    for role in ("entry", "sl", "tp"):
        coid = build(1, 2, role, 1)
        parsed = parse(coid)
        assert build(parsed.trade_chain_id, parsed.command_id, parsed.role, parsed.sequence) == coid


def test_parse_invalid_raises():
    from src.runtime_v2.execution_gateway.client_order_id import parse
    with pytest.raises(ValueError):
        parse("not-a-tsb-id")


def test_parse_wrong_prefix_raises():
    from src.runtime_v2.execution_gateway.client_order_id import parse
    with pytest.raises(ValueError):
        parse("other:42:1001:entry:1")


def test_exit_partial_role_is_valid():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    coid = build(trade_chain_id=10, command_id=5, role="exit_partial", sequence=1)
    assert coid == "tsb:10:5:exit_partial:1"


def test_exit_full_role_is_valid():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    coid = build(trade_chain_id=10, command_id=6, role="exit_full", sequence=1)
    assert coid == "tsb:10:6:exit_full:1"


def test_sync_role_is_valid():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    coid = build(trade_chain_id=10, command_id=7, role="sync", sequence=1)
    assert coid == "tsb:10:7:sync:1"


def test_invalid_role_raises():
    from src.runtime_v2.execution_gateway.client_order_id import build
    with pytest.raises(ValueError, match="Invalid role"):
        build(trade_chain_id=10, command_id=8, role="entry_old", sequence=1)
