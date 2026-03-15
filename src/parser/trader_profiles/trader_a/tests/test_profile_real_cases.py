from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=4000,
        reply_to_message_id=reply_to,
        channel_id="-1001",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TraderAProfileRealCasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_new_signal_complete_with_averaging_dash_is_new_signal(self) -> None:
        text = (
            "BTCUSDT LONG\n"
            "\u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445\n"
            "SL: 61200\n"
            "TP1: 63000\n"
            "\u0423\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435: \u2014"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_admin_message_is_info_only_with_no_operational_intents(self) -> None:
        text = "# \u0430\u0434\u043c\u0438\u043d\n\u0421\u0442\u0430\u0440\u0442: 18:00\n\u0424\u0438\u043d\u0438\u0448: 21:00"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn(result.message_type, ("INFO_ONLY", "UNCLASSIFIED"))
        self.assertEqual(result.intents, [])

    def test_new_signal_with_reply_still_new_signal(self) -> None:
        text = (
            "ETHUSDT short\n"
            "entry 3450\n"
            "sl: 3520\n"
            "tp1: 3380"
        )
        result = self.parser.parse_message(text, _context(text=text, reply_to=777))
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_multi_target_close_full_is_update(self) -> None:
        text = (
            "https://t.me/c/100/10\n"
            "https://t.me/c/100/11\n"
            "\u043e\u0441\u0442\u0430\u0442\u043e\u043a \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0446\u0435\u043d\u0435, "
            "\u0434\u043e\u0448\u043b\u0438 \u0434\u043e 2-\u0445 \u0442\u0435\u0439\u043a\u043e\u0432"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertIn("U_TP_HIT", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "FULL")
        self.assertEqual(result.entities.get("hit_target"), "TP2")

    def test_multiline_stop_updates_no_tp_or_stop_hit_false_positive(self) -> None:
        text = (
            "https://t.me/c/100/21\n"
            "BTCUSDT \u0441\u0442\u043e\u043f \u0432 \u0431\u0443\n"
            "https://t.me/c/100/22\n"
            "ETHUSDT \u0441\u0442\u043e\u043f \u043d\u0430 1 \u0442\u0435\u0439\u043a"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_TP_HIT", result.intents)
        self.assertNotIn("U_STOP_HIT", result.intents)
        self.assertIn(result.entities.get("new_stop_level"), ("ENTRY", "TP1"))

    def test_setup_incomplete_with_take_profits_later_stays_incomplete(self) -> None:
        text = "SOLUSDT LONG entry 120 sl 114 \u0442\u0435\u0439\u043a\u0438 \u043f\u043e\u0437\u0436\u0435"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "SETUP_INCOMPLETE")

    def test_short_complete_setup_is_new_signal(self) -> None:
        text = (
            "#1000PEPEUSDT \U0001f43b \u0428\u043e\u0440\u0442 (\u0432\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445)\n"
            "\u2014 \u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445: 0.003484\n"
            "\u2014 \u0423\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435: 0.003631\n"
            "\u2014 SL: 0.003909\n"
            "\u2014 TP1: 0.003229\n"
            "\u2014 TP2: 0.002969\n"
            "\u2014 TP3: 0.002639"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [0.003484])

    def test_new_signal_limit_entry_with_comma_populates_entry(self) -> None:
        text = (
            "#ARBUSDT 🐻 Шорт (вход лимиткой)\n"
            "— Вход лимиткой: 0,10380\n"
            "— Усреднение: нет\n"
            "— SL: 0,10612\n"
            "— TP1: 0,1016\n"
            "— TP2: 0,1005\n"
            "— TP3: 0,0991"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.intents, ["NS_CREATE_SIGNAL"])
        self.assertEqual(result.entities.get("symbol"), "ARBUSDT")
        self.assertEqual(result.entities.get("side"), "SHORT")
        self.assertEqual(result.entities.get("entry"), [0.1038])
        self.assertEqual(result.entities.get("stop_loss"), 0.10612)
        self.assertEqual(result.entities.get("take_profits"), [0.1016, 0.1005, 0.0991])

    def test_new_signal_entry_current_price_with_spaces_is_parsed(self) -> None:
        text = (
            "BTCUSDT LONG\n"
            "Вход с текущих: 64 012.30\n"
            "SL: 63 000.00\n"
            "TP1: 65 000.00"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [64012.3])

    def test_averaging_net_does_not_create_extra_entry(self) -> None:
        text = (
            "ETHUSDT LONG\n"
            "Вход с текущих: 2000.50\n"
            "Усреднение: нет\n"
            "SL: 1988.00\n"
            "TP1: 2030.00"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [2000.5])

    def test_new_signal_thousand_space_numbers_are_parsed_correctly(self) -> None:
        text = (
            "ETHUSDT SHORT\n"
            "Вход с текущих: 1 977.63\n"
            "Усреднение: 2 030.65\n"
            "SL: 2 158.82\n"
            "TP1: 1 807.28\n"
            "TP2: 1 615.96\n"
            "TP3: 1 370.28"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [1977.63])
        self.assertEqual(result.entities.get("averaging"), 2030.65)
        self.assertEqual(result.entities.get("stop_loss"), 2158.82)
        self.assertEqual(result.entities.get("take_profits"), [1807.28, 1615.96, 1370.28])

    def test_percent_size_line_does_not_create_extra_entry(self) -> None:
        text = (
            "BNBUSDT LONG\n"
            "Вход с текущих: 591.59\n"
            "вход 1%\n"
            "SL: 585.10\n"
            "TP1: 602.00"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [591.59])

    def test_percent_risk_phrase_does_not_create_extra_entry(self) -> None:
        text = (
            "BNBUSDT LONG\n"
            "Вход с текущих: 591.59\n"
            "вход не более 1% от депозита\n"
            "SL: 585.10\n"
            "TP1: 602.00"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [591.59])

    def test_global_close_with_r_results_is_update_and_extracts_intents(self) -> None:
        text = (
            "\u0417\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c, \u0447\u0442\u043e\u0431\u044b \u0437\u0430\u043a\u0440\u044b\u0442\u044c \u043c\u0435\u0441\u044f\u0446\n"
            "hype - \u0432 \u0431\u0443\n"
            "bnb - 0.07R\n"
            "sol - 0.82R\n"
            "sui - 0.95R"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)
        self.assertNotIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_MOVE_STOP", result.intents)
        self.assertTrue(result.reported_results)

    def test_stop_to_be_without_target_keeps_strong_intents(self) -> None:
        text = "PYTH \u0442\u043e\u0436\u0435 \u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertIn(result.message_type, ("UNCLASSIFIED", "UPDATE"))
        self.assertIn("trader_a_ambiguous_update_without_target", result.warnings)

    def test_global_shorts_stop_to_be_is_update_without_ambiguous_warning(self) -> None:
        text = "\u041f\u043e \u0432\u0441\u0435\u043c \u043c\u043e\u0438\u043c \u043e\u0441\u0442\u0430\u0432\u0448\u0438\u043c\u0441\u044f \u0448\u043e\u0440\u0442\u0430\u043c \u043d\u0443\u0436\u043d\u043e \u043f\u0435\u0440\u0435\u0432\u0435\u0441\u0442\u0438 \u0441\u0442\u043e\u043f \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043e\u043a, \u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertNotIn("trader_a_ambiguous_update_without_target", result.warnings)

    def test_stop_to_be_plural_without_target_has_consistent_warning(self) -> None:
        text = "WOO \u0438 ATP \u0441\u0442\u043e\u043f\u044b \u0432 \u0431\u0443"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertIn("trader_a_ambiguous_update_without_target", result.warnings)

    def test_cancel_pending_recommendation_without_target_extracts_intent(self) -> None:
        text = "\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0443\u044e \u0441\u043d\u044f\u0442\u044c \u0432\u0441\u0435 \u043b\u0438\u043c\u0438\u0442\u043d\u044b\u0435 \u043e\u0440\u0434\u0435\u0440\u0430"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)

    def test_reporting_summary_with_r_results_is_not_unclassified(self) -> None:
        text = "\u0410\u043f\u0434\u0435\u0439\u0442 \u043f\u043e \u043c\u0430\u0440\u0430\u0444\u043e\u043d\u043e\u0432\u0441\u043a\u0438\u043c \u0441\u0434\u0435\u043b\u043a\u0430\u043c\nBTCUSDT - 0.4R\nETHUSDT - -0.2R"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)

    def test_new_signal_with_ab_entries_is_classified_and_extracted(self) -> None:
        text = (
            "TUSDT \u0428\u043e\u0440\u0442\n"
            "A (\u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445): 617.77\n"
            "B (\u043b\u0438\u043c\u0438\u0442): 602.25\n"
            "SL: 640.00\n"
            "TP1: 590.10\n"
            "TP2: 560.00\n"
            "\u0422\u0412\u0425 \u0438\u0437\u043c\u0435\u043d\u0438\u043b \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0443\u044e"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [617.77, 602.25])

    def test_new_signal_with_vhod_a_b_keeps_averaging_separate(self) -> None:
        text = (
            "HUSDT \u041b\u043e\u043d\u0433\n"
            "\u0412\u0445\u043e\u0434 A: 0.1882\n"
            "\u0412\u0445\u043e\u0434 B (\u0443\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435/\u0434\u043e\u0431\u043e\u0440): 0.1900\n"
            "SL: 0.1810\n"
            "TP1: 0.1960"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [0.1882])
        self.assertEqual(result.entities.get("averaging"), 0.19)

    def test_new_signal_does_not_emit_move_stop_from_narrative(self) -> None:
        text = (
            "ETHUSDT \u0428\u043e\u0440\u0442\n"
            "\u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445: 1977.63\n"
            "SL: 2158.82\n"
            "TP1: 1807.28\n"
            "\u0432 \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0435 \u0432\u0445\u043e\u0434 \u0431\u0443\u0434\u0435\u0442 \u043e\u0442\u0434\u0435\u043b\u044c\u043d\u043e, \u043f\u043e\u0441\u043c\u043e\u0442\u0440\u0438\u043c..."
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertNotIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_MOVE_STOP", result.intents)

    def test_narrative_without_operational_phrase_has_no_move_stop(self) -> None:
        text = "\u0414\u0430\u0432\u0430\u0439\u0442\u0435 \u043f\u043e\u043f\u0440\u043e\u0431\u0443\u0435\u043c, \u043e\u0436\u0438\u0434\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438 \u0438 \u0442\u0435\u0439\u043a\u0438, \u0447\u0442\u043e \u0431\u0443\u0434\u0435\u0442 \u0441 TON?"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertNotIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_MOVE_STOP", result.intents)

    def test_multi_link_remove_limits_is_update_with_cancel_intent(self) -> None:
        text = (
            "https://t.me/c/100/71\n"
            "https://t.me/c/100/72\n"
            "\u043f\u043e \u044d\u0442\u0438\u043c \u0441\u0438\u0433\u043d\u0430\u043b\u0430\u043c \u043f\u043e\u043a\u0430 \u0443\u0431\u0435\u0440\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)

    def test_global_summary_remove_limits_extracts_cancel_intent(self) -> None:
        text = "\u0412\u0441\u0435 \u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u043b\u0438\u043c\u0438\u0442\u043a\u0438 \u0443\u0431\u0438\u0440\u0430\u0435\u043c"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)
        self.assertNotIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertNotIn("U_MOVE_STOP", result.intents)

    def test_new_signal_with_parenthesized_vhod_ab_and_single_tp(self) -> None:
        text = (
            "ETHUSDT \u0428\u043e\u0440\u0442\n"
            "\u0412\u0445\u043e\u0434 (A): 1977.63\n"
            "\u0412\u0445\u043e\u0434 (B): 2030.65\n"
            "SL: 2158.82\n"
            "TP: 1807.28"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [1977.63, 2030.65])
        self.assertEqual(result.entities.get("take_profits"), [1807.28])

    def test_snimayem_limitki_with_multi_links_is_update_cancel(self) -> None:
        text = (
            "\u0441\u043d\u0438\u043c\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438\n"
            "https://t.me/c/100/81\n"
            "https://t.me/c/100/82"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)

    def test_stopi_v_bu_with_multi_links_is_update(self) -> None:
        text = (
            "\u0421\u0442\u043e\u043f\u044b \u0432 \u0431\u0443\n"
            "https://t.me/c/100/91\n"
            "https://t.me/c/100/92"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)
        self.assertNotIn("trader_a_ambiguous_update_without_target", result.warnings)

    def test_global_close_all_longs_is_update_close_full(self) -> None:
        text = "\u0412\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "ALL_LONGS")

    def test_global_close_all_longs_na_tekushchikh_otmetkakh_is_update_close_full(self) -> None:
        text = "\u0412\u0441\u0435 \u043b\u043e\u043d\u0433\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u0430 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 \u043e\u0442\u043c\u0435\u0442\u043a\u0430\u0445, \u043d\u0435 \u043d\u0440\u0430\u0432\u0438\u0442\u0441\u044f \u0440\u044b\u043d\u043e\u043a."
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "ALL_LONGS")

    def test_global_close_all_shorts_is_update_close_full(self) -> None:
        text = "\u043f\u0440\u0438\u043d\u0438\u043c\u0430\u044e \u0440\u0435\u0448\u0435\u043d\u0438\u0435 \u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertEqual(result.entities.get("close_scope"), "ALL_SHORTS")

    def test_rr_results_are_parsed_as_reported_results(self) -> None:
        text = (
            "\u0417\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c\n"
            "BTCUSDT - +1.2RR\n"
            "ETHUSDT - -0.4RR"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)
        self.assertEqual(result.reported_results, [{"symbol": "BTCUSDT", "value": 1.2, "unit": "R"}, {"symbol": "ETHUSDT", "value": -0.4, "unit": "R"}])

    def test_new_signal_limit_order_phrase_extracts_entry(self) -> None:
        text = (
            "ETHUSDT \u0428\u043e\u0440\u0442\n"
            "\u0412\u0445\u043e\u0434 \u043b\u0438\u043c\u0438\u0442\u043d\u044b\u043c \u043e\u0440\u0434\u0435\u0440\u043e\u043c: 1977.63\n"
            "SL: 2158.82\n"
            "TP: 1807.28"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry"), [1977.63])

    def test_entry_plan_single_market_is_canonical(self) -> None:
        text = (
            "BTCUSDT \u041b\u043e\u043d\u0433\n"
            "\u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445: 1.2345\n"
            "SL: 1.2000\n"
            "TP1: 1.2600"
        )
        result = self.parser.parse_message(text, _context(text=text))
        entries = result.entities.get("entry_plan_entries", [])
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["role"], "PRIMARY")
        self.assertEqual(entries[0]["order_type"], "MARKET")
        self.assertEqual(result.entities.get("entry_plan_type"), "SINGLE_MARKET")
        self.assertEqual(result.entities.get("entry_structure"), "SINGLE")
        self.assertFalse(result.entities.get("has_averaging_plan"))

    def test_entry_plan_single_limit_is_canonical(self) -> None:
        text = (
            "BTCUSDT \u0428\u043e\u0440\u0442\n"
            "\u0412\u0445\u043e\u0434 \u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439: 1.2345\n"
            "SL: 1.2600\n"
            "TP1: 1.2000"
        )
        result = self.parser.parse_message(text, _context(text=text))
        entries = result.entities.get("entry_plan_entries", [])
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["role"], "PRIMARY")
        self.assertEqual(entries[0]["order_type"], "LIMIT")
        self.assertEqual(result.entities.get("entry_plan_type"), "SINGLE_LIMIT")
        self.assertEqual(result.entities.get("entry_structure"), "SINGLE")
        self.assertFalse(result.entities.get("has_averaging_plan"))

    def test_entry_plan_ab_limit_converges_to_two_step(self) -> None:
        text = (
            "ETHUSDT \u0428\u043e\u0440\u0442\n"
            "A: 1.2345\n"
            "B: 1.2100\n"
            "SL: 1.2600\n"
            "TP1: 1.1800"
        )
        result = self.parser.parse_message(text, _context(text=text))
        entries = result.entities.get("entry_plan_entries", [])
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["role"], "PRIMARY")
        self.assertEqual(entries[0]["order_type"], "LIMIT")
        self.assertEqual(entries[1]["role"], "AVERAGING")
        self.assertEqual(entries[1]["order_type"], "LIMIT")
        self.assertEqual(result.entities.get("entry_structure"), "TWO_STEP")
        self.assertEqual(result.entities.get("entry_plan_type"), "LIMIT_WITH_LIMIT_AVERAGING")
        self.assertTrue(result.entities.get("has_averaging_plan"))

    def test_entry_plan_market_plus_averaging_converges_to_two_step(self) -> None:
        text = (
            "ETHUSDT \u041b\u043e\u043d\u0433\n"
            "\u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445: 1.2345\n"
            "\u0423\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435: 1.2100\n"
            "SL: 1.1900\n"
            "TP1: 1.2600"
        )
        result = self.parser.parse_message(text, _context(text=text))
        entries = result.entities.get("entry_plan_entries", [])
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["role"], "PRIMARY")
        self.assertEqual(entries[0]["order_type"], "MARKET")
        self.assertEqual(entries[1]["role"], "AVERAGING")
        self.assertEqual(entries[1]["order_type"], "LIMIT")
        self.assertEqual(result.entities.get("entry_plan_type"), "MARKET_WITH_LIMIT_AVERAGING")
        self.assertTrue(result.entities.get("has_averaging_plan"))

    def test_entry_plan_standard_entry_plus_averaging_uses_prudent_limit(self) -> None:
        text = (
            "ETHUSDT \u041b\u043e\u043d\u0433\n"
            "\u0412\u0445\u043e\u0434: 1.2345\n"
            "\u0423\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435: 1.2100\n"
            "SL: 1.1900\n"
            "TP1: 1.2600"
        )
        result = self.parser.parse_message(text, _context(text=text))
        entries = result.entities.get("entry_plan_entries", [])
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["order_type"], "LIMIT")
        self.assertEqual(entries[1]["order_type"], "LIMIT")
        self.assertEqual(result.entities.get("entry_plan_type"), "LIMIT_WITH_LIMIT_AVERAGING")

    def test_targeted_close_with_rr_results_is_update_not_info_only(self) -> None:
        text = (
            "https://t.me/c/100/401\n"
            "\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c\n"
            "BTCUSDT - +1.2RR"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)

    def test_targeted_fix_positions_with_results_is_update(self) -> None:
        text = (
            "https://t.me/c/100/402\n"
            "\u043f\u0440\u0438\u043d\u0438\u043c\u0430\u044e \u0440\u0435\u0448\u0435\u043d\u0438\u0435 \u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0441\u0435 \u0448\u043e\u0440\u0442\u044b\n"
            "ETHUSDT - -0.4R"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)

    def test_targeted_prikroem_phrase_is_update_close_full(self) -> None:
        text = (
            "XRP - https://t.me/c/100/1015\n"
            "ADA - https://t.me/c/100/1017\n\n"
            "\u0410 \u0434\u0430\u0432\u0430\u0439\u0442\u0435 \u0438\u0445 \u043f\u0440\u0438\u043a\u0440\u043e\u0435\u043c, \u043f\u043e\u043a\u0430 \u043e\u043d\u0438 \u0440\u044f\u0434\u043e\u043c \u0441 \u0422\u0412\u0425"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_CLOSE_FULL", result.intents)

    def test_targeted_stop_should_stay_in_be_is_update(self) -> None:
        text = (
            "https://t.me/c/100/379\n"
            "https://t.me/c/100/290\n"
            "\u0412\u043e\u0442 \u043f\u043e \u044d\u0442\u0438\u043c \u0448\u043e\u0440\u0442\u0430\u043c \u0441\u0442\u043e\u043f \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u0442\u043e\u044f\u0442\u044c \u0432 \u0431\u0435\u0437\u0443\u0431\u044b\u0442\u043a\u0435"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertIn("U_MOVE_STOP", result.intents)

    def test_targeted_tut_teyk_is_update_with_tp_hit(self) -> None:
        text = (
            "https://t.me/c/100/417\n"
            "\u0422\u0430\u043a, \u0442\u0443\u0442 \u0442\u0435\u0439\u043a. 8.8%\n"
            "\u0421\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_TP_HIT", result.intents)

    def test_bare_symbol_hashtag_complete_setup_gets_usdt_and_new_signal(self) -> None:
        text = (
            "#LINK \u0428\u043e\u0440\u0442\n"
            "\u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445: 10.50\n"
            "SL: 11.20\n"
            "TP1: 9.80"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertIn("NS_CREATE_SIGNAL", result.intents)
        self.assertEqual(result.entities.get("symbol"), "LINKUSDT")

    def test_ordiusdt_p_with_teiki_section_is_new_signal(self) -> None:
        text = (
            "ORDIUSDT.P — \u041b\u041e\u041d\u0413 (\u0432\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445)\n"
            "\u2022 \u0412\u0445\u043e\u0434: 5.0113\n"
            "\u2022 \u0423\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435: 4.7291\n"
            "\u2022 \u0421\u0442\u043e\u043f: 4.4913\n"
            "\u2022 \u0422\u0435\u0439\u043a\u0438:\n"
            "\u2014 5.8613\n"
            "\u2014 6.3269\n"
            "\u2014 7.2469"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("symbol"), "ORDIUSDT.P")
        self.assertEqual(result.entities.get("take_profits"), [5.8613, 6.3269, 7.2469])


if __name__ == "__main__":
    unittest.main()
