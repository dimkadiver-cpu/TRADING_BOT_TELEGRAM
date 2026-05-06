from __future__ import annotations

import pytest

from src.parser_v2.profiles.registry import (
    canonicalize_trader_v2,
    get_parser_v2_profile,
    list_parser_v2_profiles,
)


def test_get_trader_a_by_canonical_name():
    profile = get_parser_v2_profile("trader_a")
    assert profile.trader_code == "trader_a"


def test_get_trader_a_by_alias_ta():
    profile = get_parser_v2_profile("ta")
    assert profile.trader_code == "trader_a"


def test_get_trader_a_by_alias_a():
    profile = get_parser_v2_profile("a")
    assert profile.trader_code == "trader_a"


def test_get_unknown_trader_raises_key_error():
    with pytest.raises(KeyError, match="unknown_xyz"):
        get_parser_v2_profile("unknown_xyz")


def test_list_profiles_contains_canonical_name():
    profiles = list_parser_v2_profiles()
    assert "trader_a" in profiles


def test_list_profiles_no_aliases():
    profiles = list_parser_v2_profiles()
    assert "ta" not in profiles
    assert "a" not in profiles


def test_canonicalize_known_alias():
    assert canonicalize_trader_v2("ta") == "trader_a"


def test_canonicalize_canonical_name():
    assert canonicalize_trader_v2("trader_a") == "trader_a"


def test_canonicalize_case_insensitive():
    assert canonicalize_trader_v2("TRADER_A") == "trader_a"
    assert canonicalize_trader_v2("TA") == "trader_a"


def test_canonicalize_unknown_returns_none():
    assert canonicalize_trader_v2("unknown") is None


def test_canonicalize_none_returns_none():
    assert canonicalize_trader_v2(None) is None
