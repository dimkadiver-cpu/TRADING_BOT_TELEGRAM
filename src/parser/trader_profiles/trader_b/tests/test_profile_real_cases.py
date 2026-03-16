from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.registry import get_profile_parser
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_b",
        message_id=2000,
        reply_to_message_id=reply_to,
        channel_id="-1002",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TraderBProfileRealCasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_registry_resolves_trader_b(self) -> None:
        parser = get_profile_parser("trader_b")
        self.assertIsNotNone(parser)
        self.assertIsInstance(parser, TraderBProfileParser)

    def test_new_signal_spot_and_market_entry(self) -> None:
        text = (
            "$ARBUSDT - Лонг (Сделка на споте)\n"
            "Вход: 1.25 (+- по текущим)\n"
            "Тейк профит: 1.40\n"
            "Стоп лосс: 1.10\n"
            "Риск на сделку 2%"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "ARBUSDT")
        self.assertEqual(result.entities.get("side"), "LONG")
        self.assertEqual(result.entities.get("entry"), [1.25])
        self.assertEqual(result.entities.get("stop_loss"), 1.1)
        self.assertEqual(result.entities.get("take_profits"), [1.4])
        self.assertEqual(result.entities.get("market_context"), "SPOT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry_plan_type"), "SINGLE")
        self.assertEqual(result.entities.get("entry_structure"), "ONE_SHOT")
        self.assertFalse(result.entities.get("has_averaging_plan"))

    def test_new_signal_limit_default_and_tp_list(self) -> None:
        text = (
            "$SOLUSDT - Лонг\n"
            "Вход: 125\n"
            "Тейк профит: 130\n"
            "ТП2: 140\n"
            "Стоп лосс: 119"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "LIMIT")
        self.assertEqual(result.entities.get("take_profits"), [130.0, 140.0])
    def test_move_stop_to_be(self) -> None:
        text = "Move stop to BE"
        result = self.parser.parse_message(text, _context(text=text, reply_to=501))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), "ENTRY")
    def test_move_stop_numeric(self) -> None:
        text = "move stop to 1.553"
        result = self.parser.parse_message(text, _context(text=text, reply_to=502))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), 1.553)
    def test_move_stop_to_be_with_explicit_level(self) -> None:
        text = "move stop to BE at level 2941"
        result = self.parser.parse_message(text, _context(text=text, reply_to=503))
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), 2941.0)


    def test_close_full_and_result_percent(self) -> None:
        text = "Сделка полностью закрыта в +3.6% к депозиту"
        result = self.parser.parse_message(text, _context(text=text, reply_to=504))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "FULL")
        self.assertEqual(result.entities.get("result_percent"), 3.6)
    def test_stop_hit(self) -> None:
        text = "stopped out"
        result = self.parser.parse_message(text, _context(text=text, reply_to=505))
        self.assertIn("U_STOP_HIT", result.intents)
        self.assertEqual(result.entities.get("hit_target"), "STOP")
    def test_cancel_pending(self) -> None:
        text = "cancel pending orders"
        result = self.parser.parse_message(text, _context(text=text, reply_to=506))
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)
        self.assertEqual(result.entities.get("cancel_scope"), "TARGETED")

    def test_cancel_pending_global_scopes(self) -> None:
        all_result = self.parser.parse_message("cancel pending all", _context(text="cancel pending all"))
        self.assertEqual(all_result.entities.get("cancel_scope"), "ALL_ALL")

        long_result = self.parser.parse_message("cancel pending all longs", _context(text="cancel pending all longs"))
        self.assertEqual(long_result.entities.get("cancel_scope"), "ALL_LONG")

        short_result = self.parser.parse_message("cancel pending all shorts", _context(text="cancel pending all shorts"))
        self.assertEqual(short_result.entities.get("cancel_scope"), "ALL_SHORT")



    def test_update_with_telegram_link_extracts_targets(self) -> None:
        text = "Закрыта вручную https://t.me/c/123/456"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn({"kind": "telegram_link", "ref": "https://t.me/c/123/456"}, result.target_refs)
        self.assertIn({"kind": "message_id", "ref": 456}, result.target_refs)
        self.assertNotIn("trader_b_update_missing_target", result.warnings)

    def test_missing_target_warning_when_update_without_reply_link_symbol(self) -> None:
        text = "Закрыта вручную"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertIn("trader_b_update_missing_target", result.warnings)
    def test_new_signal_market_entry_without_numeric_price(self) -> None:
        text = (
            "$ETHUSDT - Лонг (сделка на споте⭐️)\n"
            "Вход: по текущим\n"
            "Тейк профит: 2160\n"
            "Стоп лосс: 1972\n"
            "Риск на сделку 1%"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "ETHUSDT")
        self.assertEqual(result.entities.get("side"), "LONG")
        self.assertEqual(result.entities.get("entry"), [])
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("stop_loss"), 1972.0)
        self.assertEqual(result.entities.get("take_profits"), [2160.0])
        self.assertEqual(result.entities.get("market_context"), "SPOT")


if __name__ == "__main__":
    unittest.main()
