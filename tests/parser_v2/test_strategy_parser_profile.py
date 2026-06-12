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
