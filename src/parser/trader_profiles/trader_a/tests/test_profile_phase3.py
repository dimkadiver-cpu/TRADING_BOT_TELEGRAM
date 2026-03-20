from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=3000,
        reply_to_message_id=reply_to,
        channel_id="-1001",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TraderAProfilePhase3EntitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_move_stop_to_be_entities(self) -> None:
        text = "move stop to be"
        result = self.parser.parse_message(text, _context(text=text, reply_to=701))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", result.intents)
        self.assertEqual(result.entities.get("new_stop_level"), "ENTRY")

    def test_close_full_entities(self) -> None:
        text = "close all now"
        result = self.parser.parse_message(text, _context(text=text, reply_to=702))
        self.assertEqual(result.entities.get("close_scope"), "FULL")

    def test_close_partial_entities_with_fraction(self) -> None:
        text = "partial close 50%"
        result = self.parser.parse_message(text, _context(text=text, reply_to=703))
        self.assertEqual(result.entities.get("close_scope"), "PARTIAL")
        self.assertEqual(result.entities.get("close_fraction"), 0.5)

    def test_tp_and_stop_hit_entities(self) -> None:
        tp_text = "tp1 hit https://t.me/c/9/704"
        tp_result = self.parser.parse_message(tp_text, _context(text=tp_text))
        self.assertEqual(tp_result.message_type, "UPDATE")
        self.assertTrue(any(item.get("kind") == "telegram_link" for item in tp_result.target_refs))
        self.assertEqual(tp_result.entities.get("hit_target"), "TP1")

        stop_text = "stopped out"
        stop_result = self.parser.parse_message(stop_text, _context(text=stop_text, reply_to=705))
        self.assertEqual(stop_result.entities.get("hit_target"), "STOP")

    def test_mark_filled_entities(self) -> None:
        text = "entry filled"
        result = self.parser.parse_message(text, _context(text=text, reply_to=706))
        self.assertEqual(result.entities.get("fill_state"), "FILLED")

    def test_cancel_pending_entities(self) -> None:
        text = "cancel pending orders"
        result = self.parser.parse_message(text, _context(text=text, reply_to=707))
        self.assertEqual(result.entities.get("cancel_scope"), "TARGETED")

    def test_cancel_pending_global_scopes(self) -> None:
        all_text = "cancel pending all limit orders"
        all_result = self.parser.parse_message(all_text, _context(text=all_text))
        self.assertEqual(all_result.entities.get("cancel_scope"), "ALL_ALL")

        long_text = "cancel pending all longs"
        long_result = self.parser.parse_message(long_text, _context(text=long_text))
        self.assertEqual(long_result.entities.get("cancel_scope"), "ALL_LONG")

        short_text = "cancel pending all shorts"
        short_result = self.parser.parse_message(short_text, _context(text=short_text))
        self.assertEqual(short_result.entities.get("cancel_scope"), "ALL_SHORT")

    def test_global_target_scopes_are_recognized(self) -> None:
        short_text = "По всем моим оставшимся шортам нужно перевести стоп в безубыток, обязательно."
        short_result = self.parser.parse_message(short_text, _context(text=short_text))
        self.assertEqual(short_result.entities.get("close_scope"), None)
        self.assertEqual(short_result.target_scope.get("scope"), "ALL_OPEN_SHORTS")
        self.assertEqual(short_result.target_scope.get("kind"), "portfolio_side")
        self.assertTrue(short_result.target_scope.get("applies_to_all"))
        self.assertEqual(short_result.target_scope.get("position_side_filter"), "SHORT")
        self.assertEqual(short_result.target_scope.get("position_status_filter"), "OPEN")

        reply_short_text = "1 тейк. поздравляю\n\nхоть немного минуса прикрыли\n\nпо шортам стоп на точку входа"
        reply_short_result = self.parser.parse_message(reply_short_text, _context(text=reply_short_text, reply_to=485))
        self.assertEqual(reply_short_result.target_scope.get("scope"), "ALL_SHORTS")
        self.assertTrue(any(item.get("ref") == 485 for item in reply_short_result.target_refs))

        all_positions_text = "Закрываю все позиции, результаты по каждой обновлю в этом посте, нужно подождать, посмотреть на рынок"
        all_positions_result = self.parser.parse_message(all_positions_text, _context(text=all_positions_text))
        self.assertEqual(all_positions_result.target_scope.get("scope"), "ALL_ALL")
        self.assertEqual(all_positions_result.entities.get("close_scope"), "ALL_ALL")

        my_positions_text = "зафиксирую все свои позиции по текущим, не будет возможности контролировать сделки"
        my_positions_result = self.parser.parse_message(my_positions_text, _context(text=my_positions_text))
        self.assertEqual(my_positions_result.target_scope.get("scope"), "ALL_ALL")
        self.assertEqual(my_positions_result.entities.get("close_scope"), "ALL_ALL")

    def test_reported_results_and_result_mode(self) -> None:
        text = "Final result BTCUSDT - 1.2R ETHUSDT - -0.3R"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)
        self.assertEqual(result.entities.get("result_mode"), "R_MULTIPLE")
        self.assertEqual(
            result.reported_results,
            [
                {"symbol": "BTCUSDT", "value": 1.2, "unit": "R"},
                {"symbol": "ETHUSDT", "value": -0.3, "unit": "R"},
            ],
        )

    def test_report_final_result_natural_language_take_summary(self) -> None:
        text = "2 тейк 29% чистыми поздравляю"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertNotIn("U_REPORT_FINAL_RESULT", result.intents)
        self.assertEqual(result.entities.get("result_percent"), 29.0)

    def test_report_mode_text_summary_when_no_structured_r(self) -> None:
        text = "final result summary for this week"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertIn("U_REPORT_FINAL_RESULT", result.intents)
        self.assertEqual(result.reported_results, [])
        self.assertEqual(result.entities.get("result_mode"), "TEXT_SUMMARY")

    def test_actions_structured_supports_explicit_targets_for_multiline_stop_updates(self) -> None:
        text = (
            "LINK - https://t.me/c/3171748254/978 - стоп в бу\n"
            "ALGO - https://t.me/c/3171748254/1002 стоп в бу\n"
            "ARKM - https://t.me/c/3171748254/1003 стоп в бу\n"
            "FART - https://t.me/c/3171748254/1005 стоп на 1 тейк\n"
            "UNI - https://t.me/c/3171748254/1018 стоп в бу"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(
            result.actions_structured,
            [
                {"action": "MOVE_STOP", "new_stop_level": "ENTRY", "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [978]}},
                {"action": "MOVE_STOP", "new_stop_level": "ENTRY", "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [1002]}},
                {"action": "MOVE_STOP", "new_stop_level": "ENTRY", "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [1003]}},
                {"action": "MOVE_STOP", "new_stop_level": "TP1", "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [1005]}},
                {"action": "MOVE_STOP", "new_stop_level": "ENTRY", "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [1018]}},
            ],
        )

    def test_actions_structured_supports_target_group_for_shared_close(self) -> None:
        text = (
            "XRP - https://t.me/c/3171748254/1015\n"
            "ADA - https://t.me/c/3171748254/1017\n\n"
            "А давайте их прикроем, пока они рядом с ТВХ"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(
            result.actions_structured,
            [
                {
                    "action": "CLOSE_POSITION",
                    "scope": "FULL",
                    "targeting": {"mode": "TARGET_GROUP", "targets": [1015, 1017]},
                }
            ],
        )

    def test_actions_structured_supports_selector_for_global_short_close(self) -> None:
        text = "принимаю решение зафиксировать все шорты. собрали в целом не плохой профит. хочу закрыть январь."
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(
            result.actions_structured,
            [
                {
                    "action": "CLOSE_POSITION",
                    "scope": "ALL_SHORTS",
                    "targeting": {"mode": "SELECTOR", "selector": {"side": "SHORT", "status": "OPEN"}},
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
