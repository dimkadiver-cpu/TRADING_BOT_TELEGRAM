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
        self.assertIsNone(result.entities.get("average_entry"))
        self.assertEqual(result.entities.get("entry_plan_type"), "SINGLE")
        self.assertEqual(result.entities.get("entry_structure"), "ONE_SHOT")
        self.assertFalse(result.entities.get("has_averaging_plan"))

    def test_new_signal_vhod_s_tekushchikh_is_market(self) -> None:
        text = (
            "$FARTCOINUSDT.P - Шорт (вход с текущих)\n"
            "Вход с текущих: 0.3053\n"
            "Тейк профит: 0.2737\n"
            "Стоп лосс: 0.3307"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry"), [0.3053])
        self.assertEqual(result.entities.get("entry_plan_entries")[0]["role"], "PRIMARY")
        self.assertEqual(result.entities.get("entry_plan_entries")[0]["order_type"], "MARKET")

    def test_new_signal_vhod_s_tekushchikh_without_price_keeps_market_context(self) -> None:
        text = (
            "$COAIUSDT - Шорт (вход с текущих)\n"
            "Вход с текущих\n"
            "Тейк профит: 0.8627\n"
            "Стоп лосс: 1.2769"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry"), [])
        self.assertEqual(result.entities.get("entry_plan_entries")[0]["role"], "PRIMARY")
        self.assertEqual(result.entities.get("entry_plan_entries")[0]["order_type"], "MARKET")
        self.assertIsNone(result.entities.get("entry_plan_entries")[0]["price"])

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
        self.assertIsNone(result.entities.get("average_entry"))

    def test_move_stop_to_be(self) -> None:
        text = "Все пока идет по плану, стоп лосс переносим в БУ"
        result = self.parser.parse_message(text, _context(text=text, reply_to=501))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), "ENTRY")
        self.assertIn({"kind": "reply", "ref": 501}, result.target_refs)

    def test_move_stop_numeric(self) -> None:
        text = "Стоп лосс переносим на 1.553"
        result = self.parser.parse_message(text, _context(text=text, reply_to=502))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertNotIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), 1.553)

    def test_move_stop_to_be_with_explicit_level(self) -> None:
        text = "Стоп лосс переносим в БУ на уровень 2941"
        result = self.parser.parse_message(text, _context(text=text, reply_to=503))
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), 2941.0)

    def test_move_stop_structural_reference(self) -> None:
        text = "Переносим за указанный минимум"
        result = self.parser.parse_message(text, _context(text=text, reply_to=5031))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("stop_reference_text"), "за указанный минимум")
        self.assertNotIn("U_STOP_HIT", result.intents)

    def test_close_full_and_result_percent(self) -> None:
        text = "Сделка полностью закрыта в +3.6% к депозиту"
        result = self.parser.parse_message(text, _context(text=text, reply_to=504))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "FULL")
        self.assertEqual(result.entities.get("result_percent"), 3.6)

    def test_stop_hit(self) -> None:
        text = "Закрылись по стопу"
        result = self.parser.parse_message(text, _context(text=text, reply_to=505))
        self.assertIn("U_STOP_HIT", result.intents)
        self.assertEqual(result.entities.get("hit_target"), "STOP")

    def test_tp_hit_and_result_report(self) -> None:
        text = "Сделка полностью реализована в +2% по текущим, поздравляю с профитом"
        result = self.parser.parse_message(text, _context(text=text, reply_to=5051))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)
        self.assertEqual(result.entities.get("result_percent"), 2.0)

    def test_stop_hit_variant_is_info_only(self) -> None:
        message = "Очень обидный стоп, идея в целом отработала"
        result = self.parser.parse_message(message, _context(text=message, reply_to=507))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_passive_close_in_be_is_info_only(self) -> None:
        message = "Закрыта в БУ"
        result = self.parser.parse_message(message, _context(text=message, reply_to=510))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_passive_close_with_stop_loss_is_info_only(self) -> None:
        message = "Сделка закрыта по стоп лоссу -1%"
        result = self.parser.parse_message(message, _context(text=message, reply_to=511))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_passive_closed_by_stop_loss_is_info_only(self) -> None:
        text = "Тут к сожалению закрылись по стоп лоссу, -1%"
        result = self.parser.parse_message(text, _context(text=text, reply_to=516))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_passive_obidny_stop_is_info_only(self) -> None:
        message = "Очень обидный стоп, идея в целом отработала"
        result = self.parser.parse_message(message, _context(text=message, reply_to=512))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_passive_stop_loss_is_info_only(self) -> None:
        message = "К сожалению стоп лосс, рынок прям медвежий"
        result = self.parser.parse_message(message, _context(text=message, reply_to=513))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_short_commentary_is_info_only(self) -> None:
        text = "Небольшие изменения!"
        result = self.parser.parse_message(text, _context(text=text, reply_to=514))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_market_commentary_is_info_only(self) -> None:
        text = "Как и ожидалось по btc, мы сняли 98k$, сейчас рассматриваю для вас новые возможности для открытия позиций, следующий пост"
        result = self.parser.parse_message(text, _context(text=text, reply_to=515))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_close_full_by_current_and_profit_report(self) -> None:
        text = "Закрываем сделку по текущим и спасибо за профит"
        result = self.parser.parse_message(text, _context(text=text, reply_to=508))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "FULL")

    def test_move_stop_to_be_from_bu_only(self) -> None:
        message = "БУ"
        result = self.parser.parse_message(message, _context(text=message, reply_to=509))
        self.assertEqual(result.message_type, "UNCLASSIFIED")
        self.assertNotIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_MOVE_STOP", result.intents)

    def test_close_full_priority_over_be_commentary(self) -> None:
        text = "Закрываю сделки, sol уходит в БУ , eth в -0.4%"
        result = self.parser.parse_message(text, _context(text=text, reply_to=517))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertNotIn("U_MOVE_STOP_TO_BE", result.intents)

    def test_cancel_pending(self) -> None:
        text = "Тут не актуально, лонгов открытых нет"
        result = self.parser.parse_message(text, _context(text=text, reply_to=506))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)
        self.assertEqual(result.intents, ["U_CANCEL_PENDING_ORDERS"])
        self.assertEqual(result.entities.get("cancel_scope"), "TARGETED")

    def test_cancel_pending_price_moved_too_far(self) -> None:
        text = "Пока не актуально, цена ушла высоко, будем искать твх повторно"
        result = self.parser.parse_message(text, _context(text=text, reply_to=5061))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.intents, ["U_CANCEL_PENDING_ORDERS"])
        self.assertEqual(result.entities.get("cancel_scope"), "TARGETED")

    def test_close_full_global_scope(self) -> None:
        text = "Закрыть все позиции по текущим"
        result = self.parser.parse_message(text, _context(text=text, reply_to=5062))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.intents, ["U_CLOSE_FULL"])
        self.assertEqual(result.entities.get("close_scope"), "ALL_ALL")
        self.assertEqual(result.target_scope.get("kind"), "portfolio_side")
        self.assertEqual(result.target_scope.get("scope"), "ALL_ALL")

    def test_multi_link_move_stop_to_be_extracts_all_targets(self) -> None:
        text = (
            "https://t.me/c/3171748254/1001 "
            "https://t.me/c/3171748254/1002 "
            "По обоим сделкам - стопы переносим в БУ"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertGreaterEqual(len([ref for ref in result.target_refs if ref.get("kind") == "telegram_link"]), 2)
        self.assertEqual(result.entities.get("new_stop_level"), "ENTRY")


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


if __name__ == "__main__":
    unittest.main()
