from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.registry import get_profile_parser
from src.parser.trader_profiles.trader_c.profile import TraderCProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_c",
        message_id=4000,
        reply_to_message_id=reply_to,
        channel_id="-1005",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TraderCProfileRealCasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderCProfileParser()

    def test_registry_resolves_trader_c(self) -> None:
        parser = get_profile_parser("trader_c")
        self.assertIsInstance(parser, TraderCProfileParser)

    def test_new_signal_structured_range(self) -> None:
        text = "$BTCUSDT - SHORT\nВход с текущих (88000-87900)\nStop 88450. 1% деп\nTейк-профит 87500 87000"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "BTCUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "RANGE")
        self.assertEqual(result.entities.get("risk_value_normalized"), 1.0)

    def test_new_signal_multientry_tranches(self) -> None:
        text = "$BTCUSDT - LONG\nВход лимиткой\n1)87650(1/3)\n2)87150(2/3)\nStop 86700. 1% деп\nTейк-профит 88200 88900"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(len(result.entities.get("entries", [])), 2)
        self.assertEqual(result.entities.get("entry_plan_type"), "MULTI")

    def test_new_signal_multiline_tp_and_cyrillic_symbol(self) -> None:
        text = (
            "[trader #С]\n\n"
            "$BTСUSDT - LONG\n\n"
            "Вход лимитка\n\n"
            "68400\n67300\n"
            "Stop 66000. 0,6%деп\n\n"
            "Tейк-профит\n\n"
            "1) 69500(RR1-1)\n\n"
            "2) 71000 (RR1-3)\n\n"
            "3) 73000\n\n"
            "4) 75000\n\n"
            "Setup completo"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "BTCUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "LIMIT")
        self.assertGreaterEqual(len(result.entities.get("take_profits", [])), 4)
        self.assertEqual(result.entities.get("risk_value_normalized"), 0.6)

    def test_new_signal_range_plus_limit_style_kept_as_signal(self) -> None:
        text = (
            "[trader #С]\n\n"
            "$SOLUSDT - LONG\n\n"
            "Вход лимитка\n\n"
            "- 82,15 0,3%\n"
            "- 79,7 0,3%\n\n"
            "Stop . 76,6 0,6%деп\n\n"
            "Tейк-профит\n\n"
            "1) 87,9(RR1-1)\n\n"
            "2) 93,6 (RR1-2)\n\n"
            "3)\n\n"
            "4)\n\n"
            "Setup completo"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "SOLUSDT")
        self.assertEqual(result.entities.get("stop_loss"), 76.6)
        self.assertEqual(result.entities.get("risk_value_normalized"), 0.6)

    def test_new_signal_limit_single(self) -> None:
        text = "$BTCUSDT - LONG\nВход лимитка 92550\nStop 91800\nTейк-профит 93200"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "LIMIT")

    def test_new_signal_market(self) -> None:
        text = "$BTCUSDT - LONG\nВход по рынку\nStop 91800\nTейк-профит 93200"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")

    def test_new_signal_entry_block_without_markers_is_parsed(self) -> None:
        text = (
            "[trader #С]\n\n"
            "$BTCUSDT - LONG\n\n"
            "Вход лимитка\n\n"
            "68400\n67300\n"
            "Stop 66000. 0,6%деп\n\n"
            "Tейк-профит\n\n"
            "1) 69500(RR1-1)\n\n"
            "2) 71000 (RR1-3)\n\n"
            "3) 73000\n\n"
            "4) 75000"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual([entry.get("price") for entry in result.entities.get("entries", [])], [68400.0, 67300.0])
        self.assertEqual(result.entities.get("take_profits"), [69500.0, 71000.0, 73000.0, 75000.0])

    def test_new_signal_market_header_without_prices_keeps_placeholder_entry(self) -> None:
        text = (
            "[trader#C]\n\n"
            "$BTCUSDT - SHORT\n\n"
            "Вход\n\n"
            "Stop 65300. 0,5%деп\n\n"
            "Tейк-профит\n\n"
            "1) 67500(RR1-1)\n\n"
            "2) 68500 (RR1-2)\n\n"
            "3) 70000\n\n"
            "4) 72000"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(len(result.entities.get("entries", [])), 1)
        self.assertIsNone(result.entities.get("entries", [])[0].get("price"))
        self.assertEqual(result.entities.get("take_profits"), [67500.0, 68500.0, 70000.0, 72000.0])

    def test_new_signal_single_limit_without_multiline_marker(self) -> None:
        text = (
            "[trader#C]\n\n"
            "$KAS USDT - LONG\n\n"
            "Вход лимиткой\n\n"
            "1)0,031\n"
            "2)\n\n"
            "Stop 0,0295 0,5% деп\n\n"
            "Tейк-профит\n\n"
            "1) 0,0331(RR1-1)\n\n"
            "2) 0,035 (RR1-2)\n\n"
            "3) 0,04 (RR1-4)"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual([entry.get("price") for entry in result.entities.get("entries", [])], [0.031])
        self.assertEqual(result.entities.get("take_profits"), [0.0331, 0.035, 0.04])

    def test_new_signal_fractioned_limit_entries_keep_tp(self) -> None:
        text = (
            "[trader#C]\n\n"
            "$LDOUSDT - LONG\n\n"
            "Вход лимиткой\n\n"
            "1)0,592(1/3)\n"
            "2)0,578(2/3)\n\n"
            "Stop 0,565. 1% деп\n\n"
            "Tейк-профит\n\n"
            "1)0,6255(RR1-1,5)\n\n"
            "2)0,65(RR1-2,5)\n\n"
            "3)0,682(RR1-4)"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual([entry.get("price") for entry in result.entities.get("entries", [])], [0.592, 0.578])
        self.assertEqual(result.entities.get("take_profits"), [0.6255, 0.65, 0.682])

    def test_activation_updates(self) -> None:
        for text in ("Первая лимитка сработала", "Активировалась"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=1))
            self.assertEqual(result.message_type, "UPDATE")
            self.assertIn("U_ACTIVATION", result.intents)

    def test_tp_hit_updates(self) -> None:
        for text in ("Tp1🥳", "Тейк 1🥳", "Тп2 🥳", "Тп 1🥳", "Тп1🥳", "Tp1 🥳", "Tp4🥳"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=2))
            self.assertEqual(result.message_type, "UPDATE")
            self.assertIn("U_TP_HIT", result.intents)

    def test_tp_hit_with_close(self) -> None:
        text = "Позиция закрыта по тейку. Поздравляю!"
        result = self.parser.parse_message(text, _context(text=text, reply_to=3))
        self.assertIn("U_TP_HIT", result.intents)

    def test_move_stop_to_be(self) -> None:
        for text in ("В бу перевел", "Стоп в бу на точку входа 92200", "В бу перевел 89650"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=4))
            self.assertEqual(result.message_type, "UPDATE")
            self.assertIn("U_MOVE_STOP_TO_BE", result.intents)

    def test_exit_be(self) -> None:
        for text in ("Ушли в б/у", "Закрыто в бу", "Остаток ушел в бу", "Закрыт остаток в бу"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=5))
            self.assertIn("U_EXIT_BE", result.intents)

    def test_exit_be_variants(self) -> None:
        for text in ("Ушли в бу", "Сэтап закрыт в 0", "Остаток в бу закрыт"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=5))
            self.assertIn("U_EXIT_BE", result.intents)

    def test_close_partial(self) -> None:
        for text in ("Скинул часть по текущим 0,0765 (30%)", "Закрыл часть по 70950"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=6))
            self.assertIn("U_CLOSE_PARTIAL", result.intents)

    def test_multi_intent_partial_be_remove_pending(self) -> None:
        text = "Скинул часть по текущим 0,0765 (30%) / В бу перевел, доливку убрал"
        result = self.parser.parse_message(text, _context(text=text, reply_to=7))
        self.assertIn("U_CLOSE_PARTIAL", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_REMOVE_PENDING_ENTRY", result.intents)

    def test_close_full(self) -> None:
        for text in (
            "Закрываю по рынку",
            "Закрыл по рынку",
            "Закрываю на точке входа, нет реакции",
            "Закрыл в бу 68950",
            "Закрыл -0,11 RR",
            "Закрыл по текущим на комиссию",
            "Закрыл по 68950",
            "Закрыл в минус 67800,лимитку убрал слабая реакция ,на ночь не хочу оставилять.",
        ):
            result = self.parser.parse_message(text, _context(text=text, reply_to=8))
            self.assertIn("U_CLOSE_FULL", result.intents)

    def test_profit_closure_variants(self) -> None:
        text = "С профитом 🥳🥳🥳 + RR 0,45"
        result = self.parser.parse_message(text, _context(text=text, reply_to=8))
        self.assertIn("U_TP_HIT", result.intents)

    def test_cancel_pending(self) -> None:
        for text in (
            "Не актуально",
            "Не актуально не дошел до лимитки",
            "Не актуально ушел без нас",
            "Не актуально пока писал улетели",
        ):
            result = self.parser.parse_message(text, _context(text=text, reply_to=9))
            self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)

    def test_remove_pending_entry(self) -> None:
        for text in ("Доливку убрал", "Добор убрал", "Лимитку с 63750 убираем"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=10))
            self.assertIn("U_REMOVE_PENDING_ENTRY", result.intents)

    def test_multi_intent_be_and_remove_pending(self) -> None:
        text = "В бу перевел / Доливку убрал"
        result = self.parser.parse_message(text, _context(text=text, reply_to=11))
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_REMOVE_PENDING_ENTRY", result.intents)

    def test_tp_hit_be_and_remove_pending(self) -> None:
        text = "Tp1 🥳 В бу перевел лимитки убрал"
        result = self.parser.parse_message(text, _context(text=text, reply_to=12))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_REMOVE_PENDING_ENTRY", result.intents)
        self.assertEqual(len(result.intents), 3)

    def test_close_full_in_minus_and_remove_pending(self) -> None:
        text = "Закрыл в минус 67800,лимитку убрал слабая реакция ,на ночь не хочу оставилять."
        result = self.parser.parse_message(text, _context(text=text, reply_to=13))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertIn("U_REMOVE_PENDING_ENTRY", result.intents)

    def test_exit_be_closed_rest(self) -> None:
        text = "Остаток в бу закрыт"
        result = self.parser.parse_message(text, _context(text=text, reply_to=14))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_EXIT_BE", result.intents)

    def test_new_pending_limitka_is_update(self) -> None:
        text = "Новая лимитка на 73700 +0,3%"
        result = self.parser.parse_message(text, _context(text=text, reply_to=2965))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_UPDATE_PENDING_ENTRY", result.intents)
        self.assertEqual(result.entities.get("pending_entry_price"), 73700.0)

    def test_admin_announcement_is_info_only(self) -> None:
        text = (
            "Друзья, привет! Админ на связи 👋\n"
            "У нас хорошие новости - пополнение в команде!\n\n"
            "#админ"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_tp_hit_tr_variant(self) -> None:
        text = "Тр 3🥳"
        result = self.parser.parse_message(text, _context(text=text, reply_to=1526))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)

    def test_update_take_profits(self) -> None:
        for text in ("Изменения 89900 тп 2", "Изменения - Тп2 88150", "Тп 2 89000", "ТП дополнительный тот же"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=12))
            self.assertIn("U_UPDATE_TAKE_PROFITS", result.intents)

    def test_update_stop(self) -> None:
        result = self.parser.parse_message("Стоп переносим на 88650 в -0,5 RR", _context(text="Стоп переносим на 88650 в -0,5 RR", reply_to=13))
        self.assertIn("U_UPDATE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_price"), 88650.0)

    def test_update_stop_hit_style(self) -> None:
        result = self.parser.parse_message("Стоп -0,5", _context(text="Стоп -0,5", reply_to=14))
        self.assertIn("U_STOP_HIT", result.intents)

    def test_operational_update_missing_target_warns(self) -> None:
        result = self.parser.parse_message("Закрываю по рынку", _context(text="Закрываю по рынку"))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("trader_c_update_missing_target", result.warnings)


if __name__ == "__main__":
    unittest.main()
