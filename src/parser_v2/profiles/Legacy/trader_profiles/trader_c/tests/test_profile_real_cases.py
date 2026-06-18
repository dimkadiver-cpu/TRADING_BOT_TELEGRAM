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
        self.assertEqual(result.entities.get("entry_structure"), "TWO_STEP")

    def test_new_signal_three_entry_tranches_are_ladder(self) -> None:
        text = (
            "$BTCUSDT - LONG\n"
            "\u0412\u0445\u043e\u0434 \u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439\n"
            "1)87650(1/4)\n2)87150(2/4)\n3)86650(3/4)\n"
            "Stop 86100. 1% \u0434\u0435\u043f\n"
            "T\u0435\u0439\u043a-\u043f\u0440\u043e\u0444\u0438\u0442 \n"
            "1)88200(RR1-1)\n2)88900(RR1-2)\n3)89500(RR1-3)"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_structure"), "LADDER")

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

    def test_activation_updates(self) -> None:
        for text in ("Первая лимитка сработала", "Активировалась"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=1))
            self.assertEqual(result.message_type, "UPDATE")
            self.assertIn("U_ACTIVATION", result.intents)

    def test_tp_hit_updates(self) -> None:
        for text in ("Tp1🥳", "Тейк 1🥳", "Тп2 🥳"):
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
        ):
            result = self.parser.parse_message(text, _context(text=text, reply_to=8))
            self.assertIn("U_CLOSE_FULL", result.intents)

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

    def test_indexed_entry_without_size_hint_keeps_real_price(self) -> None:
        text = (
            "[trader#С]\n\n $LTCUSDT - LONG \n\nВход лимиткой \n\n1)67,25\n2)\n\n"
            "Stop 65,2  0,5% деп\n\nTейк-профит \n\n1)70(RR1-1,5)\n\n2)72(RR1-2,5)\n\n3)74(RR1-3+)"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [67.25])
        self.assertEqual(result.entities.get("entries"), [{"sequence": 1, "price": 67.25}])

    def test_decimal_take_profits_are_extracted(self) -> None:
        text = (
            "[trader#С]\n\n$LDOUSDT - LONG \n\nВход лимиткой \n\n1)0,519(1/3)\n2)0,508(2/3)\n\n"
            "Stop 0,498.  1% деп\n\nTейк-профит \n\n1)0,539(RR1-1)\n\n2)0,56(RR1-2)\n\n3)0,582(RR1-3)"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("take_profits"), [0.539, 0.56, 0.582])

    def test_admin_take_profit_guide_is_info_only(self) -> None:
        text = (
            "Друзья, это снова #админ\n"
            "Если в сигнале 3 тейк-профита (TP1-TP3)\n"
            "TP1 - 30%\nTP2 - 30%\nTP3 - 40%\n"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_short_stop_reply_is_update(self) -> None:
        text = "На байбит стоп 89950!!!!!!\n [trader #C]"
        result = self.parser.parse_message(text, _context(text=text, reply_to=1327))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_UPDATE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_price"), 89950.0)

    def test_be_reply_variants_are_updates(self) -> None:
        for text in ("Остаток закрыт в бу\n[trader#C]", "Увы ушли в бу \n\n[tradet#C]"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=1683))
            self.assertEqual(result.message_type, "UPDATE")
            self.assertIn("U_EXIT_BE", result.intents)

    def test_aktualno_is_info_only(self) -> None:
        text = "Актуально"
        result = self.parser.parse_message(text, _context(text=text, reply_to=2226))
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_limitka_reply_is_update(self) -> None:
        text = "Лимитка на 73400 на этот объем \n0,3% то что скинули\n\n[trader #C]"
        result = self.parser.parse_message(text, _context(text=text, reply_to=2956))
        self.assertEqual(result.message_type, "UPDATE")

    def test_reduce_percent_populates_partial_close(self) -> None:
        text = "Сократил -0,3% по 72800\n\nСредняя 73025\n\n[trader #C]"
        result = self.parser.parse_message(text, _context(text=text, reply_to=2959))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_PARTIAL", result.intents)
        self.assertEqual(result.entities.get("partial_close_percent"), 0.3)
        self.assertEqual(result.entities.get("partial_close_price"), 72800.0)

    def test_tp_hit_close_extracts_close_price(self) -> None:
        text = "90500 Позиция закрыта по тейку. Поздравляю!"
        result = self.parser.parse_message(text, _context(text=text, reply_to=1094))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertEqual(result.entities.get("close_price"), 90500.0)

    def test_symbol_only_update_warns_about_weak_target(self) -> None:
        text = (
            "Btcusdt SHORT\n\nДоливаю часть шорта:87800-900\n"
            "Чтобы средняя оказалась в 88500 стоп в бу \nТп1.  87222\nТп2.  86666\nТп3.  85888"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("trader_c_update_weak_target_only", result.warnings)
        self.assertEqual(result.target_scope, {"kind": "signal", "scope": "unknown"})

    def test_addon_reply_to_existing_setup_is_update_reenter(self) -> None:
        text = (
            "[trader #\u0421]\n\n$BT\u0421USDT - LONG \n\n"
            "\u0412\u0445\u043e\u0434 72100 \u0434\u043e\u043b\u0438\u043b \u043e\u0441\u0442\u0430\u0442\u043e\u043a "
            "\u043f\u043e\u0437\u044b \u043a \u0442\u0435\u043a\u0443\u0449\u0435\u043c\u0443 \u0441\u044d\u0442\u0430\u043f\u0443.\n\n"
            "Stop 71250 \u0432 \u0431\u0443 .  0,2%\u0434\u0435\u043f\n\n"
            "T\u0435\u0439\u043a-\u043f\u0440\u043e\u0444\u0438\u0442 \n\n1) 74500 \u043d\u0430 \u044d\u0442\u043e\u0442 \u043e\u0431\u044a\u0435\u043c"
        )
        result = self.parser.parse_message(text, _context(text=text, reply_to=3162))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_REENTER", result.intents)
        self.assertIn("reply", {ref["kind"] for ref in result.target_refs})


if __name__ == "__main__":
    unittest.main()
