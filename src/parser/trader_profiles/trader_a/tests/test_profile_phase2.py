from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=2000,
        reply_to_message_id=reply_to,
        channel_id="-1001",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TraderAProfilePhase2IntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_move_stop_to_be_intent(self) -> None:
        text = "move stop to be now"
        result = self.parser.parse_message(text, _context(text=text, reply_to=501))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)

    def test_cancel_pending_orders_intent(self) -> None:
        text = "cancel pending limits https://t.me/c/77/601"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)
        self.assertTrue(any(t.get("kind") == "telegram_link" for t in result.target_refs))

    def test_close_full_intent(self) -> None:
        text = "close all positions now"
        result = self.parser.parse_message(text, _context(text=text, reply_to=602))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)

    def test_close_partial_intent(self) -> None:
        text = "partial close, close half here"
        result = self.parser.parse_message(text, _context(text=text, reply_to=603))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_PARTIAL", result.intents)

    def test_tp_hit_intent(self) -> None:
        text = "tp1 hit on the trade https://t.me/c/88/604"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)

    def test_stop_hit_intent(self) -> None:
        text = "stopped out, stop hit"
        result = self.parser.parse_message(text, _context(text=text, reply_to=605))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_STOP_HIT", result.intents)

    def test_mark_filled_intent(self) -> None:
        text = "entry filled now"
        result = self.parser.parse_message(text, _context(text=text, reply_to=606))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MARK_FILLED", result.intents)

    def test_report_final_result_intent(self) -> None:
        text = "Final result BTCUSDT - 1.2R ETHUSDT - -0.3R"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)

    def test_multi_intent_update(self) -> None:
        text = "move stop to be and cancel pending orders"
        result = self.parser.parse_message(text, _context(text=text, reply_to=607))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)

    def test_ambiguous_without_target_has_no_aggressive_intents(self) -> None:
        text = "maybe close maybe move later"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UNCLASSIFIED")
        self.assertEqual(result.intents, [])
        self.assertIn("trader_a_ambiguous_update_without_target", result.warnings)


if __name__ == "__main__":
    unittest.main()
