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

    def test_missing_entry_does_not_classify_as_new_signal(self) -> None:
        text = (
            "Trader#d\n"
            "scrt short\n"
            "риск 0,5\n"
            "сл 0,13764\n"
            "тп1 0,12522\n"
            "тп2 0,11886"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")

    def test_missing_entry_with_multiple_tp_does_not_classify_as_new_signal(self) -> None:
        text = (
            "Trader#d\n"
            "scrt short\n"
            "риск 0,5\n"
            "сл 0,13764\n"
            "тп1 0,12522\n"
            "тп2 0,11886"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")

    def test_missing_entry_with_single_tp_does_not_classify_as_new_signal(self) -> None:
        text = (
            "Trader#d\n"
            "scrt short\n"
            "риск 0,5\n"
            "сл 0,13764\n"
            "тп1 0,12522\n"
            "тп2"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")

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
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")
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
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")
        self.assertEqual(result.entities.get("symbol"), "SENTUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")
        self.assertEqual(result.entities.get("risk_value_normalized"), 0.5)

    def test_compact_new_signal_enj_russian_tp(self) -> None:
        text = "Enj шорт риск 0,5\nsl 0.2701\nтп1 0.245\nтп2 0.231\nтп3 0.218"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")
        self.assertEqual(result.entities.get("symbol"), "ENJUSDT")
        self.assertEqual(result.entities.get("entry_order_type"), "MARKET")

    def test_paxg_long_compact_signal(self) -> None:
        text = "PAXG Long\nsl 2280\ntp1 2350\ntp2 2390"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")
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

    def test_stop_loss_hit_short_form(self) -> None:
        text = "Sl -0.5"
        result = self.parser.parse_message(text, _context(text=text, reply_to=717))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "FULL")
        self.assertTrue(result.reported_results)
        self.assertEqual(result.reported_results[0].get("value"), 1.0)
        self.assertEqual(result.reported_results[0].get("unit"), "R")
        # symbol cannot be derived from "Sl -0.5" without DB context — not asserted here
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

    def test_passive_be_outcome_with_target_is_update_exit_be(self) -> None:
        text = _u(r"\u041e\u0441\u0442\u0430\u0442\u043e\u043a \u0443\u0448\u0435\u043b \u0432 \u0431\u0443")
        result = self.parser.parse_message(text, _context(text=text, reply_to=719))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_EXIT_BE", result.intents)

    def test_passive_be_outcome_without_target_is_still_treated_as_update(self) -> None:
        text = _u(r"\u041e\u0441\u0442\u0430\u0442\u043e\u043a \u0443\u0448\u0435\u043b \u0432 \u0431\u0443")
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_EXIT_BE", result.intents)

    def test_close_full_remaining_current_price_extracts_close_price(self) -> None:
        text = _u(
            r"\u041e\u0441\u0442\u0430\u0442\u043e\u043a \u043f\u043e\u0437\u0438\u0446\u0438\u0438 "
            r"\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u0435\u043c \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c 31.57"
        )
        result = self.parser.parse_message(text, _context(text=text, reply_to=720))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_price"), 31.57)

    def test_new_signal_reclassification_drops_update_leakage(self) -> None:
        text = _u(
            r"[trader#d]" "\n\n"
            r"Brev SHORT \u0432\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445" "\n\n"
            r"\u0440\u044b\u043d\u043e\u0447\u043d\u044b\u0439 \u2014 \u0440\u0438\u0441\u043a 0.5% \u0434\u0435\u043f\u043e" "\n\n"
            r"\u0421\u0442\u043e\u043f 0.5358" "\n\n"
            r"\u0422\u0435\u0439\u043a\u0438" "\n"
            r"TP1: 0.4283" "\n"
            r"TP2:0.3485"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.intents, ["NS_CREATE_SIGNAL"])
        self.assertNotIn("close_scope", result.entities)
        self.assertNotIn("hit_target", result.entities)
        self.assertNotIn("update_tense", result.entities)
        self.assertEqual(result.warnings, [])

    def test_link_prefix_does_not_become_symbol(self) -> None:
        text = _u(
            r"https://t.me/c/3171748254/1408" "\n"
            r"[trader#d] \u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0443\u0448\u043b\u0430 \u0432 \u0431\u0443"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_EXIT_BE", result.intents)
        self.assertNotIn("symbol", result.entities)
        self.assertNotIn("symbol_raw", result.entities)

    def test_in_profit_stop_move_is_not_stop_hit(self) -> None:
        text = _u(r"\u0421\u0442\u043e\u043f \u0441\u0434\u0432\u0438\u0433\u0430\u044e \u0432 +" "\n" r"0.4671")
        result = self.parser.parse_message(text, _context(text=text, reply_to=1439))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertNotIn("U_STOP_HIT", result.intents)
        self.assertNotIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), 0.4671)

    def test_take_profit_formats_with_spaced_index_preserve_value(self) -> None:
        first = _u(
            r"Kiteusdt Short  \u0420\u0418\u0421\u041a 0,5" "\n"
            r"\u0412\u0445\u043e\u0434 \u043f\u043e \u0440\u044b\u043d\u043a\u0443" "\n"
            r"Sl: 0.28281" "\n"
            r"Tp1:0.26261" "\n"
            r"Tp 2:0.2242" "\n"
            r"Tp3: 0.18147"
        )
        second = _u(
            r"CVXUSDT Short  \u0420\u0418\u0421\u041a 0,5" "\n"
            r"\u0412\u0445\u043e\u0434 \u043f\u043e \u0440\u044b\u043d\u043a\u0443" "\n"
            r"Sl: 2.099" "\n"
            r"Tp1: 2.023" "\n"
            r"Tp 2: 1.988"
        )
        third = _u(
            r"Enj \u0448\u043e\u0440\u0442 \u0440\u0438\u0441\u043a 0,5" "\n"
            r"sl 0.02832" "\n"
            r"\u0442\u043f1 0,02751" "\n"
            r"\u0442\u043f 2 0,0270" "\n"
            r"\u0442\u043f3 0,02669"
        )
        self.assertEqual(self.parser.parse_message(first, _context(text=first)).entities.get("take_profits"), [0.26261, 0.2242, 0.18147])
        self.assertEqual(self.parser.parse_message(second, _context(text=second)).entities.get("take_profits"), [2.023, 1.988])
        self.assertEqual(self.parser.parse_message(third, _context(text=third)).entities.get("take_profits"), [0.02751, 0.027, 0.02669])

    def test_brief_bu_reply_is_operational_update(self) -> None:
        text = _u(r"\u0411\u0443\ud83e\udd1d")
        result = self.parser.parse_message(text, _context(text=text, reply_to=2993))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)

    def test_short_sl_variants_are_updates(self) -> None:
        first = self.parser.parse_message("Sl.-0,5%", _context(text="Sl.-0,5%", reply_to=1595))
        second = self.parser.parse_message("Upd: sl- 0.5", _context(text="Upd: sl- 0.5", reply_to=1615))
        self.assertEqual(first.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", first.intents)
        self.assertEqual(second.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", second.intents)

    def test_tp_hit_with_emoji_suffix_is_update(self) -> None:
        text = _u(r"Tp 2\ud83d\ude11")
        result = self.parser.parse_message(text, _context(text=text, reply_to=2475))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)
        self.assertEqual(result.entities.get("hit_target"), "TP2")

    def test_targeted_remaining_position_to_be_with_profit_is_update(self) -> None:
        text = _u(
            r"Trader#d" "\n"
            r"Gmt \u043e\u0441\u0442\u0430\u0442\u043e\u043a \u0443\u0448\u0435\u043b \u0432 \u0431\u0443" "\n"
            r"+0.15%"
        )
        result = self.parser.parse_message(text, _context(text=text, reply_to=1496))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_EXIT_BE", result.intents)
        self.assertEqual(result.entities.get("reported_profit_percent"), 0.15)


if __name__ == "__main__":
    unittest.main()
