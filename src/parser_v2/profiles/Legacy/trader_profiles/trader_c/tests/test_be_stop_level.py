from __future__ import annotations

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_c.profile import TraderCProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_c",
        message_id=5000,
        reply_to_message_id=reply_to,
        channel_id="-1005",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


def test_move_stop_to_be_sets_entry_stop_level() -> None:
    parser = TraderCProfileParser()
    text = "\u0412 \u0431\u0443 \u043f\u0435\u0440\u0435\u0432\u0435\u043b 89650"

    result = parser.parse_message(text, _context(text=text, reply_to=4))

    assert result.message_type == "UPDATE"
    assert "U_MOVE_STOP_TO_BE" in result.intents
    assert result.entities.get("new_stop_level") == "ENTRY"
    assert result.entities.get("new_stop_price") == 89650.0
