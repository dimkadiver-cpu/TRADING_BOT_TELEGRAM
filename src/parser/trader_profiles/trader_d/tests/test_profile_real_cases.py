from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.registry import get_profile_parser
from src.parser.trader_profiles.trader_d.profile import TraderDProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_d",
        message_id=6000,
        reply_to_message_id=reply_to,
        channel_id="-1004",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TraderDProfileRealCasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderDProfileParser()

    def test_registry_resolves_trader_d(self) -> None:
        parser = get_profile_parser("trader_d")
        self.assertIsNotNone(parser)
        self.assertIsInstance(parser, TraderDProfileParser)

    def test_new_signal_with_vhod_s_tekushchikh_and_multi_tp(self) -> None:
        text = (
            "Storjusdt SHORT вход с текущих\n"
            "рыночный — риск 0.5% депо\n"
            "Стоп 0.16134\n"
            "Тейки\n"
            "TP1: 0.1445\n"
            "TP2: 0.138"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "STORJUSDT")
        self.assertEqual(result.entities.get("side"), "SHORT")
        self.assertEqual(result.entities.get("risk_percent"), 0.5)
        self.assertEqual(result.entities.get("stop_loss"), 0.16134)
        self.assertEqual(result.entities.get("take_profits"), [0.1445, 0.138])
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")

    def test_new_signal_vhod_s_tekushchikh_without_price_is_market(self) -> None:
        text = (
            "Brev SHORT вход с текущих\n"
            "рыночный — риск 0.5% депо\n"
            "Стоп 0.5358\n"
            "TP1: 0.4283\n"
            "TP2: 0.3485"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry"), [])

    def test_new_signal_rynochnyi_without_numeric_entry(self) -> None:
        text = (
            "Brev SHORT вход с текущих\n"
            "рыночный — риск 0.5% депо\n"
            "Стоп 0.5358\n"
            "TP1: 0.4283\n"
            "TP2: 0.3485"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [])
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry_plan_type"), "SINGLE")
        self.assertEqual(result.entities.get("entry_structure"), "ONE_SHOT")
        self.assertFalse(result.entities.get("has_averaging_plan"))

    def test_new_signal_short_sl_tp_tp2(self) -> None:
        text = (
            "Sent short риск 0,5\n"
            "sl 0.034094\n"
            "tp 0.027789\n"
            "tp2 0.026276"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "SENTUSDT")
        self.assertEqual(result.entities.get("side"), "SHORT")
        self.assertEqual(result.entities.get("risk_percent"), 0.5)
        self.assertEqual(result.entities.get("stop_loss"), 0.034094)
        self.assertEqual(result.entities.get("take_profits"), [0.027789, 0.026276])

    def test_update_perevod_v_bezubyitok(self) -> None:
        text = "Перевод в безубыток"
        result = self.parser.parse_message(text, _context(text=text, reply_to=701))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), "ENTRY")

    def test_update_tp1_plus_and_perevod_v_bu(self) -> None:
        text = "tp1+\nПеревод в бу"
        result = self.parser.parse_message(text, _context(text=text, reply_to=702))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertEqual(result.entities.get("hit_target"), "TP1")

    def test_compact_new_signal_without_explicit_entry_sent(self) -> None:
        text = "Sent short риск 0,5\nsl 0.034094\ntp 0.027789\ntp2 0.026276"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "SENTUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("risk_value_normalized"), 0.5)

    def test_compact_new_signal_enj_russian_tp(self) -> None:
        text = "Enj шорт риск 0,5\nsl 0.2701\nтп1 0.245\nтп2 0.231\nтп3 0.218"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "ENJUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")

    def test_paxg_long_compact_signal(self) -> None:
        text = "PAXG Long\nsl 2280\ntp1 2350\ntp2 2390"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "PAXGUSDT")

    def test_limit_inline_entry_is_supported(self) -> None:
        text = "AXS SHORT вход лимит 1,0573\nsl 1,105\ntp1 1,01\ntp2 0,97"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "LIMIT")
        self.assertEqual(result.entities.get("entry"), [1.0573])

    def test_update_partial_close_with_stop_to_be(self) -> None:
        text = "upd:50% срежем rr 1;1 стоп в бу"
        result = self.parser.parse_message(text, _context(text=text, reply_to=703))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_PARTIAL", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "PARTIAL")
        self.assertEqual(result.entities.get("close_fraction"), 0.5)

    def test_partial_close_with_tp_hit_and_move_stop(self) -> None:
        text = "тп1 фикс 50% +0,35% стоп в бу"
        result = self.parser.parse_message(text, _context(text=text, reply_to=706))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_CLOSE_PARTIAL", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)

    def test_partial_close_r_result_with_update_tp(self) -> None:
        text = "Срежем тут 50% +0,42р Первый тейк убираем"
        result = self.parser.parse_message(text, _context(text=text, reply_to=707))
        self.assertIn("U_CLOSE_PARTIAL", result.intents)
        self.assertIn("U_UPDATE_TAKE_PROFITS", result.intents)
        self.assertEqual(result.entities.get("reported_profit_r"), 0.42)

    def test_compact_tp_variants(self) -> None:
        for text in ("ТП2", "Tp2😑", "tp1+"):
            result = self.parser.parse_message(text, _context(text=text, reply_to=708))
            self.assertEqual(result.message_type, "UPDATE")
            self.assertIn("U_TP_HIT", result.intents)

    def test_tp_with_profit_percent_and_close_full(self) -> None:
        text = "ТП2 позиция закрыта +0,75"
        result = self.parser.parse_message(text, _context(text=text, reply_to=709))
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("reported_profit_percent"), 0.75)

    def test_tp_with_overall_profit_percent(self) -> None:
        text = "Tp3 общий профит +1.18%"
        result = self.parser.parse_message(text, _context(text=text, reply_to=710))
        self.assertIn("U_TP_HIT", result.intents)
        self.assertEqual(result.entities.get("reported_profit_percent"), 1.18)

    def test_update_move_stop_numeric(self) -> None:
        text = "стоп переставляем в + на 1,2453"
        result = self.parser.parse_message(text, _context(text=text, reply_to=704))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), 1.2453)
        self.assertEqual(result.entities.get("new_stop_price"), 1.2453)

    def test_update_close_full_with_r_result(self) -> None:
        text = "JTO закрываем полностью 0,307\n+1р"
        result = self.parser.parse_message(text, _context(text=text, reply_to=705))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "FULL")
        self.assertTrue(result.reported_results)
        self.assertEqual(result.reported_results[0].get("value"), 1.0)
        self.assertEqual(result.reported_results[0].get("unit"), "R")
        self.assertEqual(result.entities.get("symbol"), "JTOUSDT")
        self.assertEqual(result.entities.get("reported_profit_r"), 1.0)

    def test_exit_be_variants(self) -> None:
        first = self.parser.parse_message("позиция ушла в бу", _context(text="позиция ушла в бу", reply_to=711))
        second = self.parser.parse_message("остаток ушел в бу+", _context(text="остаток ушел в бу+", reply_to=712))
        self.assertIn("U_EXIT_BE", first.intents)
        self.assertIn("U_EXIT_BE", second.intents)

    def test_close_full_current_price(self) -> None:
        result = self.parser.parse_message("закрываю по текущим", _context(text="закрываю по текущим", reply_to=713))
        self.assertIn("U_CLOSE_FULL", result.intents)

    def test_tp_close_full_with_r_result(self) -> None:
        text = "Tp2 сделка закрыта Общий профит +1.49р"
        result = self.parser.parse_message(text, _context(text=text, reply_to=714))
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("reported_profit_r"), 1.49)

    def test_warning_on_operational_update_without_target(self) -> None:
        text = "tp1+"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("trader_d_update_missing_target", result.warnings)


if __name__ == "__main__":
    unittest.main()
