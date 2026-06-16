from __future__ import annotations

import pytest

from src.parser_v2.profiles.registry import (
    canonicalize_trader_v2,
    get_parser_v2_profile,
    list_parser_v2_profiles,
)
from src.parser_v2.profiles.trader_a.profile import TraderAProfile
from src.parser_v2.profiles.trader_b.profile import TraderBProfile
from src.parser_v2.profiles.trader_c.profile import TraderCProfile
from src.parser_v2.profiles.trader_d.profile import TraderDProfile


def test_get_trader_a_by_canonical_name():
    profile = get_parser_v2_profile("trader_a")
    assert profile.trader_code == "trader_a"


def test_get_trader_a_by_alias_ta():
    profile = get_parser_v2_profile("ta")
    assert profile.trader_code == "trader_a"


def test_get_trader_a_by_alias_a():
    profile = get_parser_v2_profile("a")
    assert profile.trader_code == "trader_a"


def test_get_trader_b_by_canonical_name():
    profile = get_parser_v2_profile("trader_b")
    assert profile.trader_code == "trader_b"


def test_get_trader_b_by_alias_tb():
    profile = get_parser_v2_profile("tb")
    assert profile.trader_code == "trader_b"


def test_get_trader_b_by_alias_b():
    profile = get_parser_v2_profile("b")
    assert profile.trader_code == "trader_b"


def test_get_trader_c_by_canonical_name():
    profile = get_parser_v2_profile("trader_c")
    assert profile.trader_code == "trader_c"


def test_get_trader_c_by_alias_tc():
    profile = get_parser_v2_profile("tc")
    assert profile.trader_code == "trader_c"


def test_get_trader_c_by_alias_c():
    profile = get_parser_v2_profile("c")
    assert profile.trader_code == "trader_c"


def test_get_trader_d_by_canonical_name():
    profile = get_parser_v2_profile("trader_d")
    assert profile.trader_code == "trader_d"


def test_get_trader_d_by_alias_td():
    profile = get_parser_v2_profile("td")
    assert profile.trader_code == "trader_d"


def test_get_trader_d_by_alias_d():
    profile = get_parser_v2_profile("d")
    assert profile.trader_code == "trader_d"


def test_runtime_profile_modules_export_expected_classes():
    assert TraderAProfile.trader_code == "trader_a"
    assert TraderBProfile.trader_code == "trader_b"
    assert TraderCProfile.trader_code == "trader_c"
    assert TraderDProfile.trader_code == "trader_d"


def test_get_unknown_trader_raises_key_error():
    with pytest.raises(KeyError, match="unknown_xyz"):
        get_parser_v2_profile("unknown_xyz")


def test_list_profiles_contains_canonical_name():
    profiles = list_parser_v2_profiles()
    assert "trader_a" in profiles
    assert "trader_b" in profiles
    assert "trader_c" in profiles
    assert "trader_d" in profiles


def test_list_profiles_no_aliases():
    profiles = list_parser_v2_profiles()
    assert "ta" not in profiles
    assert "a" not in profiles
    assert "tb" not in profiles
    assert "b" not in profiles
    assert "tc" not in profiles
    assert "c" not in profiles
    assert "td" not in profiles
    assert "d" not in profiles


def test_canonicalize_known_alias():
    assert canonicalize_trader_v2("ta") == "trader_a"
    assert canonicalize_trader_v2("tb") == "trader_b"
    assert canonicalize_trader_v2("tc") == "trader_c"
    assert canonicalize_trader_v2("td") == "trader_d"


def test_canonicalize_canonical_name():
    assert canonicalize_trader_v2("trader_a") == "trader_a"
    assert canonicalize_trader_v2("trader_b") == "trader_b"
    assert canonicalize_trader_v2("trader_c") == "trader_c"
    assert canonicalize_trader_v2("trader_d") == "trader_d"


def test_canonicalize_case_insensitive():
    assert canonicalize_trader_v2("TRADER_A") == "trader_a"
    assert canonicalize_trader_v2("TA") == "trader_a"
    assert canonicalize_trader_v2("TRADER_B") == "trader_b"
    assert canonicalize_trader_v2("TB") == "trader_b"
    assert canonicalize_trader_v2("TRADER_C") == "trader_c"
    assert canonicalize_trader_v2("TC") == "trader_c"
    assert canonicalize_trader_v2("TRADER_D") == "trader_d"
    assert canonicalize_trader_v2("TD") == "trader_d"


def test_canonicalize_unknown_returns_none():
    assert canonicalize_trader_v2("unknown") is None


def test_canonicalize_none_returns_none():
    assert canonicalize_trader_v2(None) is None
