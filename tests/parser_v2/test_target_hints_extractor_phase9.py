from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext, TargetHints
from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor
from src.parser_v2.core.text_normalizer import TextNormalizer


def test_extracts_reply_to_message_id_from_context() -> None:
    hints = TargetHintsExtractor().extract(
        normalized=TextNormalizer().normalize("stop to be"),
        context=ParserContext(reply_to_message_id=123),
        markers=SemanticMarkers(),
    )

    assert hints.reply_to_message_id == 123


def test_falls_back_to_raw_context_reply_to_message_id() -> None:
    hints = TargetHintsExtractor().extract(
        normalized=TextNormalizer().normalize("stop to be"),
        context=ParserContext(
            raw_context=RawContext(raw_text="stop to be", reply_to_message_id=456)
        ),
        markers=SemanticMarkers(),
    )

    assert hints.reply_to_message_id == 456


def test_extracts_and_deduplicates_telegram_links_and_message_ids() -> None:
    text = (
        "https://t.me/c/777000/123, t.me/trader_channel/456 "
        "and duplicate https://t.me/c/777000/123"
    )

    hints = TargetHintsExtractor().extract(
        normalized=TextNormalizer().normalize(text),
        context=ParserContext(),
        markers=SemanticMarkers(),
    )

    assert hints.telegram_links == [
        "https://t.me/c/777000/123",
        "t.me/trader_channel/456",
    ]
    assert hints.telegram_message_ids == [123, 456]


def test_extracts_explicit_ids_from_english_and_russian_forms() -> None:
    text = "signal id 123, id \u0441\u0438\u0433\u043d\u0430\u043b\u0430 456, signal id 123"

    hints = TargetHintsExtractor().extract(
        normalized=TextNormalizer().normalize(text),
        context=ParserContext(),
        markers=SemanticMarkers(),
    )

    assert hints.explicit_ids == ["123", "456"]


def test_extracts_symbols_matching_symbol_target_hint_marker() -> None:
    text = "move stop on BTCUSDT and #ETHUSDT"
    markers = SemanticMarkers(
        target_hint_markers={"symbol": MarkerSet(strong=["usdt"])}
    )

    hints = TargetHintsExtractor().extract(
        normalized=TextNormalizer().normalize(text),
        context=ParserContext(),
        markers=markers,
    )

    assert hints.symbols == ["BTCUSDT", "ETHUSDT"]


def test_extracts_scope_hint_from_target_hint_markers() -> None:
    text = "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b"
    markers = SemanticMarkers(
        target_hint_markers={
            "ALL_SHORT": MarkerSet(
                strong=["\u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b"]
            )
        }
    )

    hints = TargetHintsExtractor().extract(
        normalized=TextNormalizer().normalize(text),
        context=ParserContext(),
        markers=markers,
    )

    assert hints.scope_hint == "ALL_SHORT"


def test_empty_input_returns_default_optional_fields() -> None:
    hints = TargetHintsExtractor().extract(
        normalized=TextNormalizer().normalize(""),
        context=ParserContext(),
        markers=SemanticMarkers(),
    )

    assert hints == TargetHints()
