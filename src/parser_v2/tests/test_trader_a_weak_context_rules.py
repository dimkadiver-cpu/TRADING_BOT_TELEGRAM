from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.trader_a.profile import TraderAProfile


def _parse(text: str):
    context = ParserContext(raw_context=RawContext(raw_text=text))
    return UniversalParserRuntime().parse(text, context, TraderAProfile())


def test_trader_a_after_n_tp_context_does_not_emit_tp_hit():
    result = _parse("Закрылась в бу, после 1 тейка, конечно же")

    assert "EXIT_BE" in result.intents
    assert "TP_HIT" not in result.intents


def test_trader_a_active_tp_hit_after_historical_context_still_emits_tp_hit():
    result = _parse("после 1 тейка второй тейк взят")

    assert "TP_HIT" in result.intents
