from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.registry import get_profile_parser
from src.parser.trader_profiles.trader_d.profile import TraderDProfileParser


def _context(raw_text: str, reply_to_message_id: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_d",
        message_id=100,
        reply_to_message_id=reply_to_message_id,
        channel_id="-1001",
        raw_text=raw_text,
    )


def test_registry_resolves_trader_d():
    parser = get_profile_parser("td")
    assert isinstance(parser, TraderDProfileParser)


def test_new_signal_sets_v2_semantic_envelope():
    parser = TraderDProfileParser()
    text = "BTCUSDT long\nВход с текущих: 65000\nSL: 63000\nTP1: 66000"

    result = parser.parse_message(text=text, context=_context(text))

    assert result.message_type == "NEW_SIGNAL"
    assert result.primary_intent == "NS_CREATE_SIGNAL"
    assert result.actions_structured
    assert result.actions_structured[0]["action"] == "CREATE_SIGNAL"
    assert result.linking["strategy"] == "unresolved"


def test_update_reply_sets_linking_targeted():
    parser = TraderDProfileParser()
    text = "Переносим стоп в бу"

    result = parser.parse_message(text=text, context=_context(text, reply_to_message_id=77))

    assert result.message_type == "UPDATE"
    assert result.linking["targeted"] is True
    assert result.primary_intent in {"U_MOVE_STOP_TO_BE", "U_MOVE_STOP"}
