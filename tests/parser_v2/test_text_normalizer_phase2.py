from __future__ import annotations

from src.parser_v2.core.text_normalizer import TextNormalizer
from src.parser_v2.contracts.markers import NormalizedText


def test_lowercase_cyrillic_and_preserve_raw_text() -> None:
    raw_text = "Стоп в БУ"

    normalized = TextNormalizer().normalize(raw_text)

    assert isinstance(normalized, NormalizedText)
    assert normalized.raw_text == raw_text
    assert normalized.normalized_text == "стоп в бу"
    assert normalized.lines == ["стоп в бу"]


def test_replaces_yo_and_normalizes_dash_variants() -> None:
    normalized = TextNormalizer().normalize("Ёлка – тест — цена − стоп")

    assert normalized.normalized_text == "елка - тест - цена - стоп"
    assert normalized.lines == ["елка - тест - цена - стоп"]


def test_collapses_spaces_without_losing_non_empty_lines() -> None:
    normalized = TextNormalizer().normalize("  line1\t  value  \n\n  line2   value  ")

    assert normalized.normalized_text == "line1 value\nline2 value"
    assert normalized.lines == ["line1 value", "line2 value"]


def test_empty_text_returns_empty_normalized_text_and_no_lines() -> None:
    normalized = TextNormalizer().normalize("")

    assert normalized.raw_text == ""
    assert normalized.normalized_text == ""
    assert normalized.lines == []
