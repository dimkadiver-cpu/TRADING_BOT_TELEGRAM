from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


def _context(*, text: str, reply_to: int | None = None, links: list[str] | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=1000,
        reply_to_message_id=reply_to,
        channel_id="-1001",
        raw_text=text,
        extracted_links=links or [],
        hashtags=[],
    )


class TraderAProfilePhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_new_signal_classification(self) -> None:
        text = "BTCUSDT long entry 62000 sl 61000 tp1 63000 tp2 64000"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertIn("NS_CREATE_SIGNAL", result.intents)
        self.assertEqual(result.entities.get("symbol"), "BTCUSDT")
        self.assertEqual(result.entities.get("side"), "LONG")
        self.assertEqual(result.entities.get("entry"), [62000.0])
        self.assertEqual(result.entities.get("stop_loss"), 61000.0)
        self.assertEqual(result.entities.get("take_profits"), [63000.0, 64000.0])

    def test_update_with_reply_classification(self) -> None:
        text = "move stop to breakeven"
        result = self.parser.parse_message(text, _context(text=text, reply_to=555))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertNotIn("NS_CREATE_SIGNAL", result.intents)
        self.assertIn({"kind": "reply", "ref": 555}, result.target_refs)

    def test_update_with_telegram_link_classification(self) -> None:
        text = "close full https://t.me/c/123/456"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn({"kind": "telegram_link", "ref": "https://t.me/c/123/456"}, result.target_refs)
        self.assertIn({"kind": "message_id", "ref": 456}, result.target_refs)

    def test_multiple_targets_are_extracted_stably(self) -> None:
        text = "update refs https://t.me/c/10/101 and https://t.me/c/10/102"
        result = self.parser.parse_message(text, _context(text=text))
        links = [item for item in result.target_refs if item.get("kind") == "telegram_link"]
        self.assertEqual(
            links,
            [
                {"kind": "telegram_link", "ref": "https://t.me/c/10/101"},
                {"kind": "telegram_link", "ref": "https://t.me/c/10/102"},
            ],
        )

    def test_setup_incomplete_classification(self) -> None:
        text = "ETHUSDT long entry only, sl later"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")
        self.assertNotIn("NS_CREATE_SIGNAL", result.intents)

    def test_unclassified_fallback(self) -> None:
        text = "good morning everyone"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UNCLASSIFIED")

    def test_ambiguous_update_without_target_adds_warning(self) -> None:
        text = "move stop and close now"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UNCLASSIFIED")
        self.assertIn("trader_a_ambiguous_update_without_target", result.warnings)


if __name__ == "__main__":
    unittest.main()
