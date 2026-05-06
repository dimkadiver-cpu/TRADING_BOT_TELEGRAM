from __future__ import annotations

from src.parser_v2.core.symbol_normalizer import normalize_symbol


def test_normalize_symbol_strips_perpetual_suffix() -> None:
    assert normalize_symbol("FARTCOINUSDT.P") == "FARTCOINUSDT"


def test_normalize_symbol_keeps_plain_symbol_unchanged() -> None:
    assert normalize_symbol("BTCUSDT") == "BTCUSDT"


def test_normalize_symbol_preserves_none() -> None:
    assert normalize_symbol(None) is None
