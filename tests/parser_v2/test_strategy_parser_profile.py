from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.profiles.strategy_parser.profile import StrategyParserProfile


def _normalized_text(text: str) -> NormalizedText:
    return NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())


def test_strategy_parser_uses_market_default_for_ambiguous_entries() -> None:
    profile = StrategyParserProfile()
    text = _normalized_text(
        "🔴 Стратегия «RSI(2) Коннора» открыла ШОРТ по DOGE · интрадей (1H)\n\n"
        "Вход 0.09055, стоп 0.09298, цель 0.08433 — риск к прибыли 1 к 2.6"
    )

    signal = profile.extract_signal(
        text,
        ParserContext(raw_context=RawContext(raw_text=text.raw_text)),
        evidence=[],
    )

    assert signal is not None
    assert signal.entries[0].entry_type == "MARKET"


def test_strategy_parser_extracts_numeric_multiplier_symbol_in_signal() -> None:
    profile = StrategyParserProfile()
    text = _normalized_text(
        "🔴 Стратегия «RSI(2) Коннора» открыла ШОРТ по 1000PEPE · интрадей (1H)\n\n"
        "Вход 0.009055, стоп 0.009298, цель 0.008433"
    )

    signal = profile.extract_signal(
        text,
        ParserContext(raw_context=RawContext(raw_text=text.raw_text)),
        evidence=[],
    )

    assert signal is not None
    assert signal.symbol == "1000PEPE"
    assert signal.side == "SHORT"


def _extract_target_hints(profile: StrategyParserProfile, text: str):
    normalized = _normalized_text(text)
    return profile.extract_target_hints(
        normalized,
        ParserContext(raw_context=RawContext(raw_text=normalized.raw_text)),
        profile.load_markers(),
    )


def test_strategy_parser_target_hints_extract_numeric_multiplier_symbol() -> None:
    profile = StrategyParserProfile()
    result = _extract_target_hints(
        profile,
        "⚪ Стратегия «RSI(2) Коннора» закрыла ШОРТ по 1000BONK — вышла по обратному сигналу",
    )
    hints = result.message_target_hints
    assert hints.target_source == "SYMBOL"
    assert hints.symbols == ["1000BONK"]


def test_strategy_parser_target_hints_extract_side_when_present() -> None:
    profile = StrategyParserProfile()
    result = _extract_target_hints(
        profile,
        "⚪ Стратегия «RSI(2) Коннора» закрыла ЛОНГ по WLD — вышла по обратному сигналу",
    )
    hints = result.message_target_hints
    assert hints.target_source == "SYMBOL"
    assert hints.symbols == ["WLD"]
    assert hints.side == "LONG"


def test_strategy_parser_target_hints_side_none_when_absent() -> None:
    profile = StrategyParserProfile()
    result = _extract_target_hints(
        profile,
        "⚪ Стратегия «RSI(2) Коннора» обновление по SUI",
    )
    hints = result.message_target_hints
    assert hints.target_source == "SYMBOL"
    assert hints.symbols == ["SUI"]
    assert hints.side is None
