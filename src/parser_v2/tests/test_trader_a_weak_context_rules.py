from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.Legacy.trader_a_legacy.profile import TraderAProfile


def _parse(text: str):
    context = ParserContext(raw_context=RawContext(raw_text=text))
    return UniversalParserRuntime().parse(text, context, TraderAProfile())


def test_trader_a_after_n_tp_context_does_not_emit_tp_hit():
    result = _parse(
        "\u0417\u0430\u043a\u0440\u044b\u043b\u0430\u0441\u044c \u0432 \u0431\u0443, "
        "\u043f\u043e\u0441\u043b\u0435 1 \u0442\u0435\u0439\u043a\u0430, "
        "\u043a\u043e\u043d\u0435\u0447\u043d\u043e \u0436\u0435"
    )

    assert "EXIT_BE" in result.intents
    assert "TP_HIT" not in result.intents


def test_trader_a_active_tp_hit_after_historical_context_still_emits_tp_hit():
    result = _parse(
        "\u043f\u043e\u0441\u043b\u0435 1 \u0442\u0435\u0439\u043a\u0430 "
        "\u0432\u0442\u043e\u0440\u043e\u0439 \u0442\u0435\u0439\u043a "
        "\u0432\u0437\u044f\u0442"
    )

    assert "TP_HIT" in result.intents


def test_trader_a_historical_context_uses_normalized_text_for_exclusions():
    result = _parse(
        "[trader#A]\n\n"
        "\u041f\u043e\u0441\u043b\u0435 \u043f\u0435\u0440\u0432\u043e\u0433\u043e "
        "\u0442\u0435\u0439\u043a\u0430, \u0446\u0435\u043d\u0430 \u0432\u0435\u0440\u043d\u0443\u043b\u0430\u0441\u044c "
        "\u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430 "
        "\u0438 \u0437\u0430\u043a\u0440\u044b\u043b\u0430\u0441\u044c \u0432 "
        "\u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a. "
        "\u0421\u0435\u0442\u0430\u043f \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e "
        "\u0437\u0430\u043a\u0440\u044b\u0442"
    )

    assert "EXIT_BE" in result.intents
    assert "TP_HIT" not in result.intents
