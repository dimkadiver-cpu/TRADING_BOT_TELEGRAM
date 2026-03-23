from __future__ import annotations

from src.core.trader_tags import find_normalized_trader_tags, normalize_trader_tag


def test_normalize_trader_tag_with_bracket_after_word() -> None:
    assert normalize_trader_tag("Trader [ #D]") == "trader#d"


def test_find_normalized_trader_tags_with_bracket_after_word() -> None:
    text = "Update for Trader [ #D ] right now"
    assert find_normalized_trader_tags(text) == ["trader#d"]


def test_find_normalized_trader_tags_keeps_existing_format() -> None:
    text = "[trader #A] and [trader#3]"
    assert find_normalized_trader_tags(text) == ["trader#a", "trader#3"]


def test_normalize_trader_tag_with_tradet_typo() -> None:
    assert normalize_trader_tag("[tradet#C]") == "trader#c"


def test_find_normalized_trader_tags_with_tradet_typo() -> None:
    text = "Signal [tradet#C] live"
    assert find_normalized_trader_tags(text) == ["trader#c"]
