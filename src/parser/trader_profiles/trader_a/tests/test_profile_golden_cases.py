from __future__ import annotations

from dataclasses import dataclass
import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    input_text: str
    expected_message_type: str
    expected_intents: tuple[str, ...]
    expected_target_mode: str
    reply_to: int | None = None
    extracted_links: tuple[str, ...] = ()
    expected_warnings_contains: tuple[str, ...] = ()


def _context(case: GoldenCase) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=9000,
        reply_to_message_id=case.reply_to,
        channel_id="-1001",
        raw_text=case.input_text,
        extracted_links=list(case.extracted_links),
        hashtags=[],
    )


def _assert_target_mode(test: unittest.TestCase, case: GoldenCase, target_refs: list[dict[str, object]]) -> None:
    links = [item for item in target_refs if item.get("kind") == "telegram_link"]
    if case.expected_target_mode == "NONE":
        test.assertEqual(target_refs, [], msg=case.case_id)
        return
    if case.expected_target_mode == "MULTI_TARGET_LINKS":
        test.assertGreaterEqual(len(links), 2, msg=case.case_id)
        return
    if case.expected_target_mode == "MULTI_TARGET_PER_LINE":
        test.assertGreaterEqual(len(links), 2, msg=case.case_id)
        link_lines = [line for line in case.input_text.splitlines() if "t.me/" in line]
        test.assertGreaterEqual(len(link_lines), 2, msg=case.case_id)
        return
    if case.expected_target_mode == "GLOBAL":
        test.assertEqual(target_refs, [], msg=case.case_id)
        test.assertIn(case.expected_message_type, ("UPDATE", "INFO_ONLY", "UNCLASSIFIED"), msg=case.case_id)
        return
    raise AssertionError(f"{case.case_id}: unexpected target mode {case.expected_target_mode}")


class TraderAProfileGoldenCasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_golden_cases(self) -> None:
        cases = [
            GoldenCase(
                case_id="g01_new_signal_long_complete",
                input_text="BTCUSDT long entry 62000 sl: 61000 tp1: 63000 tp2: 64000",
                expected_message_type="NEW_SIGNAL",
                expected_intents=("NS_CREATE_SIGNAL",),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g02_new_signal_short_complete",
                input_text=(
                    "#1000PEPEUSDT \U0001f43b \u0428\u043e\u0440\u0442 (\u0432\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445)\n"
                    "SL: 0.003909\nTP1: 0.003229\nTP2: 0.002969\nTP3: 0.002639"
                ),
                expected_message_type="NEW_SIGNAL",
                expected_intents=("NS_CREATE_SIGNAL",),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g03_setup_incomplete_teyki_pozzhe",
                input_text="SOLUSDT LONG entry 120 sl 114 \u0442\u0435\u0439\u043a\u0438 \u043f\u043e\u0437\u0436\u0435",
                expected_message_type="SETUP_INCOMPLETE",
                expected_intents=(),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g04_setup_incomplete_english",
                input_text="ETHUSDT long entry only, sl later",
                expected_message_type="SETUP_INCOMPLETE",
                expected_intents=(),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g05_admin_tag_info_only",
                input_text="# \u0430\u0434\u043c\u0438\u043d\n\u0421\u0442\u0430\u0440\u0442: 18:00\n\u0424\u0438\u043d\u0438\u0448: 21:00",
                expected_message_type="INFO_ONLY",
                expected_intents=(),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g06_admin_text_info_only",
                input_text="\u044d\u0442\u043e \u0430\u0434\u043c\u0438\u043d, \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u043c \u0431\u0435\u0437 \u0441\u0438\u0433\u043d\u0430\u043b\u043e\u0432",
                expected_message_type="INFO_ONLY",
                expected_intents=(),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g07_unclassified_plain_text",
                input_text="good morning everyone",
                expected_message_type="UNCLASSIFIED",
                expected_intents=(),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g08_update_multi_links_close_full_tp2",
                input_text=(
                    "https://t.me/c/100/10\n"
                    "https://t.me/c/100/11\n"
                    "\u043e\u0441\u0442\u0430\u0442\u043e\u043a \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0446\u0435\u043d\u0435, "
                    "\u0434\u043e\u0448\u043b\u0438 \u0434\u043e 2-\u0445 \u0442\u0435\u0439\u043a\u043e\u0432"
                ),
                expected_message_type="UPDATE",
                expected_intents=("U_CLOSE_FULL", "U_TP_HIT"),
                expected_target_mode="MULTI_TARGET_LINKS",
            ),
            GoldenCase(
                case_id="g09_update_per_line_stop_management",
                input_text=(
                    "https://t.me/c/100/21\n"
                    "BTCUSDT \u0441\u0442\u043e\u043f \u0432 \u0431\u0443\n"
                    "https://t.me/c/100/22\n"
                    "ETHUSDT \u0441\u0442\u043e\u043f \u043d\u0430 1 \u0442\u0435\u0439\u043a"
                ),
                expected_message_type="UPDATE",
                expected_intents=("U_MOVE_STOP_TO_BE", "U_MOVE_STOP"),
                expected_target_mode="MULTI_TARGET_PER_LINE",
            ),
            GoldenCase(
                case_id="g10_update_multi_links_cancel_pending",
                input_text=(
                    "remove pending:\n"
                    "https://t.me/c/100/30\n"
                    "https://t.me/c/100/31\n"
                    "\u0443\u0431\u0438\u0440\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438"
                ),
                expected_message_type="UPDATE",
                expected_intents=("U_CANCEL_PENDING_ORDERS",),
                expected_target_mode="MULTI_TARGET_LINKS",
            ),
            GoldenCase(
                case_id="g11_global_close_without_results",
                input_text="\u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c",
                expected_message_type="UPDATE",
                expected_intents=("U_CLOSE_FULL",),
                expected_target_mode="GLOBAL",
            ),
            GoldenCase(
                case_id="g12_global_close_with_results",
                input_text=(
                    "\u0417\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c\n"
                    "bnb - 0.07R\n"
                    "sol - 0.82R\n"
                    "sui - 0.95R"
                ),
                expected_message_type="UPDATE",
                expected_intents=("U_CLOSE_FULL", "U_REPORT_FINAL_RESULT"),
                expected_target_mode="GLOBAL",
            ),
            GoldenCase(
                case_id="g13_move_stop_be_without_target",
                input_text="PYTH \u0442\u043e\u0436\u0435 \u0441\u0442\u043e\u043f \u0432 \u0431\u0443",
                expected_message_type="UNCLASSIFIED",
                expected_intents=("U_MOVE_STOP_TO_BE", "U_MOVE_STOP"),
                expected_target_mode="NONE",
                expected_warnings_contains=("trader_a_ambiguous_update_without_target",),
            ),
            GoldenCase(
                case_id="g14_tp_hit_multi_links",
                input_text="tp1 hit\nhttps://t.me/c/100/41\nhttps://t.me/c/100/42",
                expected_message_type="UPDATE",
                expected_intents=("U_TP_HIT",),
                expected_target_mode="MULTI_TARGET_LINKS",
            ),
            GoldenCase(
                case_id="g15_stop_hit_multi_links",
                input_text="stopped out\nhttps://t.me/c/100/43\nhttps://t.me/c/100/44",
                expected_message_type="UPDATE",
                expected_intents=("U_STOP_HIT",),
                expected_target_mode="MULTI_TARGET_LINKS",
            ),
            GoldenCase(
                case_id="g16_mark_filled_multi_links",
                input_text="entry filled\nhttps://t.me/c/100/45\nhttps://t.me/c/100/46",
                expected_message_type="UPDATE",
                expected_intents=("U_MARK_FILLED",),
                expected_target_mode="MULTI_TARGET_LINKS",
            ),
            GoldenCase(
                case_id="g17_report_final_result_only",
                input_text="Final result BTCUSDT - 1.2R ETHUSDT - -0.3R",
                expected_message_type="INFO_ONLY",
                expected_intents=("U_REPORT_FINAL_RESULT",),
                expected_target_mode="NONE",
            ),
            GoldenCase(
                case_id="g18_close_partial_multi_links",
                input_text="partial close 50%\nhttps://t.me/c/100/47\nhttps://t.me/c/100/48",
                expected_message_type="UPDATE",
                expected_intents=("U_CLOSE_PARTIAL",),
                expected_target_mode="MULTI_TARGET_LINKS",
            ),
            GoldenCase(
                case_id="g19_ambiguous_update_without_target",
                input_text="maybe close maybe move later",
                expected_message_type="UNCLASSIFIED",
                expected_intents=(),
                expected_target_mode="NONE",
                expected_warnings_contains=("trader_a_ambiguous_update_without_target",),
            ),
            GoldenCase(
                case_id="g20_multi_intent_update_multi_links",
                input_text=(
                    "move stop to be and cancel pending orders\n"
                    "https://t.me/c/100/51\n"
                    "https://t.me/c/100/52"
                ),
                expected_message_type="UPDATE",
                expected_intents=("U_MOVE_STOP_TO_BE", "U_MOVE_STOP", "U_CANCEL_PENDING_ORDERS"),
                expected_target_mode="MULTI_TARGET_LINKS",
            ),
        ]

        for case in cases:
            with self.subTest(case_id=case.case_id):
                result = self.parser.parse_message(case.input_text, _context(case))
                self.assertEqual(result.message_type, case.expected_message_type, msg=case.case_id)
                self.assertEqual(result.intents, list(case.expected_intents), msg=case.case_id)
                _assert_target_mode(self, case, result.target_refs)
                for expected_warning in case.expected_warnings_contains:
                    self.assertIn(expected_warning, result.warnings, msg=case.case_id)


if __name__ == "__main__":
    unittest.main()
