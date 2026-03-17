from __future__ import annotations

import json
import unittest

from src.parser.pipeline import MinimalParserPipeline, ParserInput


class TraderAIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB", "A": "A", "trader_a": "trader_a"})

    def test_trader_a_dispatch_uses_profile_and_common_mapping(self) -> None:
        payload = ParserInput(
            raw_message_id=9001,
            raw_text="move stop to be and cancel pending orders https://t.me/c/123/265",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="A",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=901,
            linkage_reference_id=266,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", normalized.get("intents", []))
        self.assertIn("U_CANCEL_PENDING_ORDERS", normalized.get("intents", []))
        self.assertIn("ACT_MOVE_STOP_LOSS", normalized.get("actions", []))
        self.assertIn("ACT_CANCEL_ALL_PENDING_ENTRIES", normalized.get("actions", []))
        self.assertEqual(normalized.get("entities", {}).get("new_stop_level"), "ENTRY")
        self.assertEqual(normalized.get("entities", {}).get("cancel_scope"), "ALL_PENDING_ENTRIES")
        self.assertEqual(sorted(normalized.get("target_refs", [])), [265, 266])
        # v2 semantic assertions (additive to legacy checks)
        self.assertEqual(normalized.get("primary_intent"), "U_MOVE_STOP_TO_BE")
        self.assertTrue(normalized.get("actions_structured"))
        self.assertEqual(normalized.get("target_scope", {}).get("kind"), "signal")
        self.assertTrue(normalized.get("linking", {}).get("targeted"))
        self.assertEqual(normalized.get("linking", {}).get("strategy"), "reply_or_link")
        notes = normalized.get("notes", [])
        self.assertTrue(any("profile_parser=trader_a" in note for note in notes))

    def test_trader_a_reported_results_are_translated_to_common_shape(self) -> None:
        payload = ParserInput(
            raw_message_id=9002,
            raw_text="Final result BTCUSDT - 1.2R ETHUSDT - -0.3R",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_a",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=902,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertIn("U_REPORT_FINAL_RESULT", normalized.get("intents", []))
        self.assertEqual(
            normalized.get("reported_results", []),
            [
                {"symbol": "BTCUSDT", "r_multiple": 1.2},
                {"symbol": "ETHUSDT", "r_multiple": -0.3},
            ],
        )
        self.assertEqual(normalized.get("primary_intent"), "U_REPORT_FINAL_RESULT")
        self.assertTrue(normalized.get("results_v2"))
        self.assertIn("result_type", normalized.get("results_v2", [])[0])
        self.assertIsInstance(normalized.get("entry_plan"), dict)
        self.assertIsInstance(normalized.get("risk_plan"), dict)

    def test_unknown_trader_keeps_fallback_flow(self) -> None:
        payload = ParserInput(
            raw_message_id=9003,
            raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="UNKNOWN",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=903,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("parser_used"), "regex")
        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
        notes = normalized.get("notes", [])
        self.assertFalse(any("profile_parser=" in note for note in notes))

    def test_ta_branch_still_works(self) -> None:
        payload = ParserInput(
            raw_message_id=9004,
            raw_text="close all positions at current market price",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=904,
            linkage_reference_id=500,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_CLOSE_FULL", normalized.get("intents", []))
        self.assertIn("ACT_CLOSE_FULL", normalized.get("actions", []))
        self.assertEqual(normalized.get("primary_intent"), "U_CLOSE_FULL")
        self.assertTrue(normalized.get("actions_structured"))
        self.assertEqual(normalized.get("actions_structured", [])[0].get("action"), "CLOSE_POSITION")

    def test_trader_a_update_with_targets_and_cancel_pending_ru(self) -> None:
        payload = ParserInput(
            raw_message_id=9005,
            raw_text=(
                "https://t.me/c/3171748254/2243\n"
                "https://t.me/c/3171748254/2242\n"
                "снимаем лимитки"
            ),
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_a",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=905,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_CANCEL_PENDING_ORDERS", normalized.get("intents", []))
        self.assertIn(2243, normalized.get("target_refs", []))
        self.assertIn(2242, normalized.get("target_refs", []))
        self.assertNotIn("trader_a_update_missing_target", normalized.get("validation_warnings", []))
        self.assertEqual(normalized.get("target_scope", {}).get("kind"), "signal")
        self.assertEqual(normalized.get("linking", {}).get("strategy"), "reply_or_link")

    def test_trader_a_update_close_with_report_and_targets_ru(self) -> None:
        payload = ParserInput(
            raw_message_id=9006,
            raw_text=(
                "https://t.me/c/3171748254/1685\n"
                "закрываю по текущим\n"
                "BTCUSDT - 1.2RR\n"
                "ETHUSDT - -0.4R"
            ),
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_a",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=906,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_CLOSE_FULL", normalized.get("intents", []))
        self.assertIn("U_REPORT_FINAL_RESULT", normalized.get("intents", []))
        self.assertTrue(normalized.get("reported_results"))
        self.assertIn(1685, normalized.get("target_refs", []))

    def test_trader_a_new_signal_includes_v2_signal_semantics(self) -> None:
        payload = ParserInput(
            raw_message_id=9007,
            raw_text="BTCUSDT long entry: 100000 sl: 99000 tp1: 101000",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_a",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=907,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
        self.assertEqual(normalized.get("primary_intent"), "NS_CREATE_SIGNAL")
        self.assertTrue(normalized.get("actions_structured"))
        self.assertEqual(normalized.get("actions_structured", [])[0].get("action"), "CREATE_SIGNAL")
        self.assertEqual(normalized.get("target_scope", {}).get("scope"), "single")
        self.assertTrue(normalized.get("entry_plan", {}).get("entries"))
        self.assertIsInstance(normalized.get("risk_plan"), dict)

    def test_trader_a_update_tp_hit_and_move_stop_to_be_has_v2_actions(self) -> None:
        payload = ParserInput(
            raw_message_id=9008,
            raw_text="tp1 hit, move stop to be",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_a",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=908,
            linkage_reference_id=500,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_TP_HIT", normalized.get("intents", []))
        self.assertIn("U_MOVE_STOP_TO_BE", normalized.get("intents", []))
        actions_structured = normalized.get("actions_structured", [])
        self.assertTrue(any(item.get("action") == "MOVE_STOP" for item in actions_structured))
        self.assertTrue(any(item.get("action") == "TAKE_PROFIT" for item in actions_structured))
        self.assertEqual(normalized.get("primary_intent"), "U_MOVE_STOP_TO_BE")

    def test_trader_a_partial_close_has_v2_primary_and_actions(self) -> None:
        payload = ParserInput(
            raw_message_id=9009,
            raw_text="close 50% now",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_a",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=909,
            linkage_reference_id=500,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_CLOSE_PARTIAL", normalized.get("intents", []))
        self.assertEqual(normalized.get("primary_intent"), "U_CLOSE_PARTIAL")
        self.assertTrue(normalized.get("actions_structured"))

    def test_trader_a_global_target_scope_in_v2_from_global_markers(self) -> None:
        payload = ParserInput(
            raw_message_id=9010,
            raw_text="все шорты закрываю на текущих",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_a",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=910,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertEqual(normalized.get("target_scope", {}).get("kind"), "portfolio_side")
        self.assertIn(normalized.get("target_scope", {}).get("scope"), {"ALL_SHORTS", "GLOBAL"})
        self.assertTrue(normalized.get("linking", {}).get("targeted"))
        self.assertIn(normalized.get("linking", {}).get("strategy"), {"global_scope", "reply_or_link"})



if __name__ == "__main__":
    unittest.main()
