from __future__ import annotations

from parser_test.scripts.replay_parser_v2 import _resolve_trader


def test_resolve_from_explicit_arg():
    assert _resolve_trader(explicit="trader_a", source_trader_id=None) == "trader_a"


def test_resolve_explicit_overrides_source():
    assert _resolve_trader(explicit="trader_a", source_trader_id="trader_b") == "trader_a"


def test_resolve_from_source_trader_id():
    assert _resolve_trader(explicit=None, source_trader_id="ta") == "trader_a"


def test_resolve_unknown_explicit_returns_none():
    assert _resolve_trader(explicit="unknown_xyz", source_trader_id=None) is None


def test_resolve_both_none_returns_none():
    assert _resolve_trader(explicit=None, source_trader_id=None) is None
