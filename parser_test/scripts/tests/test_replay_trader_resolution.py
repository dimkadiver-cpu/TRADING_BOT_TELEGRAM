from __future__ import annotations

from parser_test.scripts.replay_parser_v2 import _resolve_trader


def test_resolve_from_explicit_arg():
    assert _resolve_trader(explicit="trader_a", source_trader_id=None, inferred_trader_id=None) == "trader_a"


def test_resolve_source_overrides_explicit():
    assert _resolve_trader(explicit="trader_a", source_trader_id="trader_b", inferred_trader_id=None) == "trader_b"


def test_resolve_inferred_overrides_explicit():
    assert _resolve_trader(explicit="trader_a", source_trader_id=None, inferred_trader_id="trader_b") == "trader_b"


def test_resolve_from_source_trader_id():
    assert _resolve_trader(explicit=None, source_trader_id="ta", inferred_trader_id=None) == "trader_a"


def test_resolve_unknown_explicit_returns_none():
    assert _resolve_trader(explicit="unknown_xyz", source_trader_id=None, inferred_trader_id=None) is None


def test_resolve_both_none_returns_none():
    assert _resolve_trader(explicit=None, source_trader_id=None, inferred_trader_id=None) is None
