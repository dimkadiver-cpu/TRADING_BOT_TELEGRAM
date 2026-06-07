from __future__ import annotations

from src.runtime_v2.symbols import to_raw_symbol


def test_to_raw_symbol_keeps_raw_symbol():
    assert to_raw_symbol("FIDAUSDT") == "FIDAUSDT"


def test_to_raw_symbol_converts_slash_symbol():
    assert to_raw_symbol("FIDA/USDT") == "FIDAUSDT"


def test_to_raw_symbol_converts_ccxt_style_symbol():
    assert to_raw_symbol("FIDA/USDT:USDT") == "FIDAUSDT"


def test_to_raw_symbol_is_case_and_whitespace_insensitive():
    assert to_raw_symbol("  fida/usdt  ") == "FIDAUSDT"


def test_to_raw_symbol_preserves_none():
    assert to_raw_symbol(None) is None


def test_to_raw_symbol_returns_none_for_empty_string():
    assert to_raw_symbol("") is None


def test_to_raw_symbol_returns_none_for_whitespace_only_string():
    assert to_raw_symbol("   ") is None


def test_to_raw_symbol_returns_none_for_slash_only_input():
    assert to_raw_symbol("/") is None


def test_to_raw_symbol_returns_none_for_spaced_slash_only_input():
    assert to_raw_symbol(" / ") is None


def test_to_raw_symbol_returns_none_for_ccxt_suffix_only_input():
    assert to_raw_symbol(":USDT") is None
