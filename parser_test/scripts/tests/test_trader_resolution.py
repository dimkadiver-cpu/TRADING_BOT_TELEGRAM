from __future__ import annotations

from parser_test.scripts.trader_resolution import normalize_trader_id


def test_normalize_none_returns_none():
    assert normalize_trader_id(None) is None


def test_normalize_whitespace_returns_none():
    assert normalize_trader_id("   ") is None


def test_normalize_known_trader_id():
    assert normalize_trader_id("trader_a") == "trader_a"


def test_normalize_known_alias_ta():
    assert normalize_trader_id("ta") == "trader_a"


def test_normalize_unknown_falls_back_to_lowercase():
    assert normalize_trader_id("UNKNOWN_TRADER_XYZ") == "unknown_trader_xyz"


def test_normalize_mixed_case_known():
    assert normalize_trader_id("TRADER_A") == "trader_a"
