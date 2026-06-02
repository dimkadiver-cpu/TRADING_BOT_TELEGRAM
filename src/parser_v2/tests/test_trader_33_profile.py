from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.trader_3.profile import Trader33Profile


def _parse(text: str):
    context = ParserContext(raw_context=RawContext(raw_text=text))
    return UniversalParserRuntime().parse(text, context, Trader33Profile())


def test_trader_33_parses_structured_signal() -> None:
    text = (
        "[trader#3] SIGNAL ID: #1997\n"
        "COIN: $BTC/USDT (3-5x)\n"
        "Direction: LONG\n"
        "ENTRY: 105200 - 107878\n"
        "TARGETS: 109600 - 111500 - 114000 - 116000 - 120000 - 126000\n"
        "STOP LOSS: 104103\n"
    )

    parsed = _parse(text)

    assert parsed.primary_class == "SIGNAL"
    assert parsed.parse_status == "PARSED"
    assert parsed.signal is not None
    assert parsed.signal.symbol == "BTCUSDT"
    assert parsed.signal.side == "LONG"
    assert parsed.signal.entry_structure == "RANGE"
    assert len(parsed.signal.entries) == 2
    assert parsed.signal.entries[0].price.value == 105200.0
    assert parsed.signal.entries[1].price.value == 107878.0
    assert parsed.signal.stop_loss is not None
    assert parsed.signal.stop_loss.price.value == 104103.0
    assert [tp.price.value for tp in parsed.signal.take_profits] == [
        109600.0,
        111500.0,
        114000.0,
        116000.0,
        120000.0,
        126000.0,
    ]


def test_trader_33_parses_target_hit_report() -> None:
    text = (
        "[trader#3] SIGNAL ID: #1997\n"
        "COIN: $BTC/USDT (3-5x)\n"
        "Direction: LONG\n"
        "Target 1: 109600✅\n\n"
        "15% Profit (5x)\n"
    )

    parsed = _parse(text)

    assert parsed.primary_class == "REPORT"
    assert parsed.parse_status == "PARSED"
    assert parsed.report is not None
    assert len(parsed.report.events) == 1
    assert parsed.report.events[0].event_type == "TP_HIT"
    assert parsed.report.events[0].level == 1
    assert parsed.report.events[0].price is not None
    assert parsed.report.events[0].price.value == 109600.0
    assert parsed.report.result is not None


def test_trader_33_parses_stop_loss_report() -> None:
    text = (
        "[trader#3] SIGNAL ID: #2001\n"
        "COIN: $SOL/USDT (2-5x)\n"
        "Direction: LONG\n"
        "STOP LOSS: 170.23\n\n"
        "4.55% Loss (2x)\n"
    )

    parsed = _parse(text)

    assert parsed.primary_class == "REPORT"
    assert parsed.parse_status == "PARSED"
    assert parsed.report is not None
    assert any(event.event_type == "SL_HIT" for event in parsed.report.events)
    sl_event = next(event for event in parsed.report.events if event.event_type == "SL_HIT")
    assert sl_event.price is not None
    assert sl_event.price.value == 170.23
    assert parsed.report.result is not None


def test_trader_33_treats_market_update_as_info() -> None:
    text = (
        "[trader#3] VIP MARKET UPDATE: $BTC\n"
        "BTC pulled back and then perfectly bounced from the 108K level as predicted.\n"
    )

    parsed = _parse(text)

    assert parsed.primary_class == "INFO"
    assert parsed.parse_status == "PARSED"
    assert parsed.signal is None
    assert parsed.intents == []
    assert parsed.info is not None


def test_trader_33_parses_manual_close_as_close_full_update() -> None:
    text = (
        "[trader#3] SIGNAL ID: #2011\n"
        "COIN: $LINK/USDT (2-4x)\n"
        "Direction: LONG\n\n"
        "Closed Manually\n\n"
        "12% Loss (2x)\n"
    )

    parsed = _parse(text)

    assert parsed.primary_class == "UPDATE"
    assert parsed.parse_status == "PARSED"
    assert parsed.primary_intent == "CLOSE_FULL"
    assert len(parsed.target_action_groups) == 1
    assert len(parsed.target_action_groups[0].actions) == 1
    action = parsed.target_action_groups[0].actions[0]
    assert action.action_type == "CLOSE"
    assert action.close is not None
    assert action.close.close_scope == "FULL"
