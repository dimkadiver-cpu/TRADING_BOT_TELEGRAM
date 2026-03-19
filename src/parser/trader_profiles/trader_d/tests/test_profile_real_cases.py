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


def _u(value: str) -> str:
    try:
        return bytes(value, "ascii").decode("unicode_escape")
    except UnicodeEncodeError:
        return value


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

    def test_new_signal_compact_current_entry_with_tp_block(self) -> None:
        text = (
            "[trader#d]\n\n"
            "Storjusdt SHORT вход с текущих\n\n"
            "рыночный— риск 0.5% депо\n\n"
            "Стоп 0.16134\n\n"
            "Тейки\n"
            "• TP1: 0.1445\n"
            "TP2; 0,138"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "STORJUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("take_profits"), [0.1445, 0.138])

    def test_new_signal_compact_current_entry_with_tp_block_btc(self) -> None:
        text = (
            "[trader#d]\n\n"
            "Btc Long вход с текущих\n\n"
            "рыночный— риск 0.5% депо\n\n"
            "Стоп 90939\n\n"
            "Тейки\n"
            "• TP1: 92265\n"
            "• TP2:93704"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "BTCUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("take_profits"), [92265.0, 93704.0])

    def test_new_signal_compact_current_entry_with_tp_block_zil(self) -> None:
        text = (
            "[trader#d]\n\n"
            "ZIL LONG вход с текущих\n\n"
            "рыночный— риск 0.5% депо\n\n"
            "Стоп 0.005080\n\n"
            "Тейки\n"
            "• TP1:  0.00654"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "ZILUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("take_profits"), [0.00654])

    def test_new_signal_market_default_when_entry_not_specified(self) -> None:
        text = (
            "Trader#d\n"
            "scrt short\n"
            "риск 0,5\n"
            "сл 0,13764\n"
            "тп1 0,12522\n"
            "тп2 0,11886"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "SCRTUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry_plan_entries", [])[0].get("order_type"), "MARKET")
        self.assertIsNone(result.entities.get("entry_plan_entries", [])[0].get("price"))
        self.assertEqual(result.entities.get("take_profits"), [0.12522, 0.11886])

    def test_new_signal_market_default_when_entry_not_specified_with_tp_only(self) -> None:
        text = (
            "Trader#d\n"
            "scrt short\n"
            "риск 0,5\n"
            "сл 0,13764\n"
            "тп1 0,12522\n"
            "тп2 0,11886"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "SCRTUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry_plan_entries", [])[0].get("order_type"), "MARKET")
        self.assertEqual(result.entities.get("take_profits"), [0.12522, 0.11886])

    def test_new_signal_market_default_with_single_tp_and_sl_synonym(self) -> None:
        text = (
            "Trader#d\n"
            "scrt short\n"
            "риск 0,5\n"
            "сл 0,13764\n"
            "тп1 0,12522\n"
            "тп2"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "SCRTUSDT")
        self.assertEqual(result.entities.get("stop_loss"), 0.13764)
        self.assertEqual(result.entities.get("take_profits"), [0.12522])

    def test_new_signal_market_default_when_entry_not_specified_btc(self) -> None:
        text = (
            "trader#d\n"
            "Btc Long  вход с текущих\n"
            "рыночный— риск 0.5% депо\n"
            "Стоп 90939\n"
            "Тейки\n"
            "• TP1: 92265\n"
            "• TP2:93704"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "BTCUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry_plan_entries", [])[0].get("order_type"), "MARKET")
        self.assertEqual(result.entities.get("take_profits"), [92265.0, 93704.0])

    def test_partial_close_fix_seventy_percent(self) -> None:
        text = "Trader#d\nФикс 70%\nСтоп в бу"
        result = self.parser.parse_message(text, _context(text=text, reply_to=842))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_PARTIAL", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "PARTIAL")
        self.assertEqual(result.entities.get("close_fraction"), 0.7)

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
        self.assertEqual(result.entities.get("entry_plan_type"), "SINGLE_MARKET")
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
        self.assertNotIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), "ENTRY")

    def test_update_tp1_plus_and_perevod_v_bu(self) -> None:
        text = "tp1+\nПеревод в бу"
        result = self.parser.parse_message(text, _context(text=text, reply_to=702))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_MOVE_STOP", result.intents)
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
        text = _u(
            r"Tp2 сделка закрыта "
            r"Общий профит +1.49р"
        )
        result = self.parser.parse_message(text, _context(text=text, reply_to=714))
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_CLOSE_FULL", result.intents)
        action_types = {item.get("action") for item in result.actions_structured}
        self.assertIn("MARK_POSITION_CLOSED", action_types)
        self.assertNotIn("TAKE_PROFIT", action_types)
        self.assertNotIn("CLOSE_POSITION", action_types)
        self.assertEqual(result.entities.get("reported_profit_r"), 1.49)

    def test_tp_hit_compact_variants(self) -> None:
        result = self.parser.parse_message("Tp 2😑", _context(text="Tp 2😑", reply_to=715))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)

    def test_stop_shift_with_value_is_update(self) -> None:
        text = "Стоп сдвигаю в + 0.4671"
        result = self.parser.parse_message(text, _context(text=text, reply_to=716))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertEqual(result.entities.get("new_stop_price"), 0.4671)

    def test_stop_loss_hit_short_form(self) -> None:
        text = "Sl -0.5"
        result = self.parser.parse_message(text, _context(text=text, reply_to=717))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_STOP_HIT", result.intents)

    def test_warning_on_operational_update_without_target(self) -> None:
        text = "tp1+"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("trader_d_update_missing_target", result.warnings)


    def test_new_signal_market_numeric_entry_is_captured(self) -> None:
        text = _u(
            r"STGUSDT Short \u0440\u0438\u0441\u043a 0,5\n"
            r"\u0432\u0445\u043e\u0434 \u043f\u043e \u0440\u044b\u043d\u043a\u0443 0.21714\n"
            r"SL : 0.22575\n"
            r"TP1: 0.19023\n"
            r"TP2: 0.18384\n"
            r"TP3: 0.165"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("entry"), [0.21714])
        self.assertEqual(result.entities.get("entry_plan_entries", [])[0].get("price"), 0.21714)

    def test_new_signal_limit_typo_entry_is_captured(self) -> None:
        text = _u(
            r"Parti \u0428\u043e\u0440\u0442\n"
            r"\u043b\u0438\u043c\u0442 0,09287\n"
            r"\u0441\u0442\u043e\u043f 0,09494\n"
            r"\u0442\u043f1 0,08977\n"
            r"\u0442\u043f2 0,08584"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_order_type"), "LIMIT")
        self.assertEqual(result.entities.get("entry"), [0.09287])
        self.assertEqual(result.entities.get("entry_plan_entries", [])[0].get("price"), 0.09287)

    def test_partial_close_with_remaining_position_is_tracked(self) -> None:
        text = _u(
            r"Tp2 \u0441\u0440\u0435\u0437\u0430\u043b \u0435\u0449\u0451 25% +0.36%\n"
            r"\u041e\u0441\u0442\u0430\u0442\u043e\u043a 25% \u043f\u043e\u0437\u0438\u0446\u0438\u0438\n"
            r"\u0421\u0442\u043e\u043f \u043d\u0430\u0445\u043e\u0434\u0438\u0442\u0441\u044f \u0432 \u0431\u0443"
        )
        result = self.parser.parse_message(text, _context(text=text, reply_to=718))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertIn("U_CLOSE_PARTIAL", result.intents)
        self.assertEqual(result.entities.get("close_fraction_percent"), 25.0)
        self.assertEqual(result.entities.get("remaining_position_percent"), 25.0)

    def test_passive_be_outcome_is_info_only(self) -> None:
        text = _u(r"\u041e\u0441\u0442\u0430\u0442\u043e\u043a \u0443\u0448\u0435\u043b \u0432 \u0431\u0443")
        result = self.parser.parse_message(text, _context(text=text, reply_to=719))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.intents, [])

    def test_close_full_remaining_current_price_extracts_close_price(self) -> None:
        text = _u(
            r"\u041e\u0441\u0442\u0430\u0442\u043e\u043a \u043f\u043e\u0437\u0438\u0446\u0438\u0438 "
            r"\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u0435\u043c \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c 31.57"
        )
        result = self.parser.parse_message(text, _context(text=text, reply_to=720))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_price"), 31.57)


if __name__ == "__main__":
    unittest.main()
