from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.registry import get_parser_v2_profile
from src.parser_v2.profiles.trader_3_1.profile import Trader31Profile


def _parse(text: str, *, reply_to_message_id: int | None = None):
    context = ParserContext(
        raw_context=RawContext(raw_text=text, reply_to_message_id=reply_to_message_id),
        reply_to_message_id=reply_to_message_id,
    )
    return UniversalParserRuntime().parse(text, context, Trader31Profile())


def test_trader_3_1_registry_aliases_resolve() -> None:
    assert isinstance(get_parser_v2_profile("trader_3_1"), Trader31Profile)
    assert isinstance(get_parser_v2_profile("trader_31"), Trader31Profile)
    assert isinstance(get_parser_v2_profile("3_1"), Trader31Profile)
    assert isinstance(get_parser_v2_profile("31"), Trader31Profile)


def test_trader_3_1_parses_structured_signal() -> None:
    text = (
        "📍Coin : #HOME/USDT\n\n"
        "🔴 SHORT \n\n"
        "👉 Entry: 0.03190 - 0.03327\n\n"
        "🌐 Leverage: 20x\n\n"
        "🎯 Target 1: 0.03159\n"
        "🎯 Target 2: 0.03128\n"
        "🎯 Target 3: 0.03097\n"
        "🎯 Target 4: 0.03056\n"
        "🎯 Target 5: 0.03026\n"
        "🎯 Target 6: 0.02995\n\n"
        "❌ StopLoss: 0.03411"
    )

    parsed = _parse(text)

    assert parsed.primary_class == "SIGNAL"
    assert parsed.parse_status == "PARSED"
    assert parsed.signal is not None
    assert parsed.signal.symbol == "HOMEUSDT"
    assert parsed.signal.side == "SHORT"
    assert parsed.signal.entry_structure == "RANGE"
    assert len(parsed.signal.entries) == 2
    assert parsed.signal.entries[0].price.value == 0.0319
    assert parsed.signal.entries[1].price.value == 0.03327
    assert parsed.signal.stop_loss is not None
    assert parsed.signal.stop_loss.price.value == 0.03411
    assert parsed.signal.leverage_hint == 20.0
    assert [tp.price.value for tp in parsed.signal.take_profits] == [
        0.03159,
        0.03128,
        0.03097,
        0.03056,
        0.03026,
        0.02995,
    ]


def test_trader_3_1_parses_tp_hit_report() -> None:
    text = (
        "🌐 #HOME        #Signal🟠\n\n"
        "📍Quick 118 % Profit in 1 hr 20 mins🥂\n\n"
        "🎯 Targets 1, 2, 3, 4, 5 Done ✅"
    )

    parsed = _parse(text, reply_to_message_id=2652)

    assert parsed.primary_class == "REPORT"
    assert parsed.parse_status == "PARSED"
    assert parsed.report is not None
    assert len(parsed.report.events) == 1
    assert parsed.report.events[0].event_type == "TP_HIT"
    assert parsed.report.events[0].level == 5
    assert parsed.report.result is not None


def test_trader_3_1_parses_full_tp_report() -> None:
    text = (
        "🌐 #HOME        #Signal🔸\n\n"
        "📌Quick 140 % Profit in 1 hr 24 mins🥂\n\n"
        "🎯 Targets 1, 2, 3, 4, 5, 6 Done ✅\n\n"
        "ALL TRAGETS DONE 🏆🏆🏆🏆🏆🏆"
    )

    parsed = _parse(text, reply_to_message_id=2652)

    assert parsed.primary_class == "REPORT"
    assert parsed.parse_status == "PARSED"
    assert parsed.report is not None
    assert len(parsed.report.events) == 1
    assert parsed.report.events[0].event_type == "TP_HIT"
    assert parsed.report.events[0].level == 6
    assert parsed.report.result is not None


def test_trader_3_1_treats_membership_post_as_info() -> None:
    text = (
        "PREMIUM PLANS 💥💥💥\n\n"
        "$99 - 1 MONTH MEMBERSHIP 🟠\n\n"
        "$399 - ONE YEAR'S MEMBERSHIP 🤩\n\n"
        "[ LIMITED TIME FEE ] 💥💥💥\n\n"
        "📌 Payment Method - USDT BTC ETH etc."
    )

    parsed = _parse(text)

    assert parsed.primary_class == "INFO"
    assert parsed.parse_status == "PARSED"
    assert parsed.signal is None
    assert parsed.report is None
    assert parsed.info is not None


def test_trader_3_1_treats_market_comment_as_info() -> None:
    text = (
        "#TAO\n\n"
        "Bittensor is consolidating within the descending channel formation on the 3D chart\n\n"
        "Market participants appear to be positioning for the next major move higher\n\n"
        "A successful break above the channel resistance could trigger a powerful upward surge toward $700"
    )

    parsed = _parse(text)

    assert parsed.primary_class == "INFO"
    assert parsed.parse_status == "PARSED"
    assert parsed.signal is None
    assert parsed.report is None
    assert parsed.info is not None


def test_trader_3_1_parses_closed_at_sl_report() -> None:
    text = "❌ #BABY Closed at SL"

    parsed = _parse(text, reply_to_message_id=2000)

    assert parsed.primary_class == "REPORT"
    assert parsed.parse_status == "PARSED"
    assert parsed.report is not None
    assert len(parsed.report.events) == 1
    assert parsed.report.events[0].event_type == "SL_HIT"
