from __future__ import annotations

import json
import unittest

from src.parser.pipeline import MinimalParserPipeline, ParserInput


class ParseResultNormalizedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB"})

    def test_new_signal_contains_semantic_and_legacy_fields(self) -> None:
        payload = ParserInput(
            raw_message_id=1,
            raw_text="BTCUSDT long entry 90000-90100 sl 89500 tp1 91000 tp2 92000",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TB",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=77,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)

        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(result.message_type, normalized.get("message_type"))
        self.assertEqual(normalized.get("event_type"), "NEW_SIGNAL")
        self.assertEqual(normalized.get("instrument"), "BTCUSDT")
        self.assertEqual(normalized.get("side"), "BUY")
        self.assertEqual(normalized.get("parser_used"), "regex")
        self.assertEqual(normalized.get("parser_mode"), "regex_only")
        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
        self.assertEqual(normalized.get("symbol"), "BTCUSDT")
        self.assertEqual(normalized.get("direction"), "LONG")
        self.assertEqual(normalized.get("entry_main"), 90000.0)
        self.assertEqual(normalized.get("stop_loss_price"), 89500.0)
        self.assertEqual(normalized.get("take_profit_prices"), [91000.0, 92000.0])

    def test_update_contains_canonical_actions_and_target_refs(self) -> None:
        payload = ParserInput(
            raw_message_id=2,
            raw_text="move sl to breakeven and cancel pending orders https://t.me/c/123/265",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TB",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=88,
            linkage_reference_id=266,
        )
        result = self.pipeline.parse(payload)

        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(result.message_type, normalized.get("message_type"))
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", normalized.get("intents", []))
        self.assertIn("U_CANCEL_PENDING_ORDERS", normalized.get("intents", []))
        self.assertIn("ACT_MOVE_STOP_LOSS", normalized.get("actions", []))
        self.assertIn("ACT_CANCEL_ALL_PENDING_ENTRIES", normalized.get("actions", []))
        self.assertEqual(normalized.get("event_type"), "UPDATE")
        self.assertIn("entities", normalized)
        self.assertEqual(normalized.get("entities", {}).get("new_stop_level"), "ENTRY")
        self.assertEqual(normalized.get("entities", {}).get("cancel_scope"), "ALL_PENDING_ENTRIES")
        self.assertEqual(sorted(normalized.get("target_refs", [])), [265, 266])
        self.assertEqual(normalized.get("validation_warnings", []), [])

    def test_info_only_contains_reported_results(self) -> None:
        payload = ParserInput(
            raw_message_id=3,
            raw_text="weekly update BTC - 1.2R DOGE - -0.4R",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TB",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=99,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)

        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(result.message_type, normalized.get("message_type"))
        self.assertEqual(normalized.get("message_type"), "INFO_ONLY")
        reported = normalized.get("reported_results", [])
        self.assertEqual(len(reported), 2)
        self.assertEqual(reported[0].get("symbol"), "BTC")
        self.assertEqual(reported[0].get("r_multiple"), 1.2)
        self.assertEqual(reported[1].get("symbol"), "DOGE")
        self.assertEqual(reported[1].get("r_multiple"), -0.4)

    def test_trader_a_new_signal_exposes_canonical_entry_plan_and_legacy_fields(self) -> None:
        payload = ParserInput(
            raw_message_id=4,
            raw_text=(
                "#FARTCOINUSDT \u0428\u043e\u0440\u0442 (\u0432\u0445\u043e\u0434 \u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439)\n"
                "\u0412\u0445\u043e\u0434 (2-\u0444\u0430\u0437\u043d\u044b\u0439):\n"
                "\u2014 \u0412\u0445\u043e\u0434 A: 0.1882\n"
                "\u2014 \u0412\u0445\u043e\u0434 B (\u0443\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435/\u0434\u043e\u0431\u043e\u0440): 0.1900\n"
                "SL: 0.1950\n"
                "TP1: 0.1825"
            ),
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=100,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
        entries = normalized.get("entries", [])
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].get("role"), "PRIMARY")
        self.assertEqual(entries[0].get("order_type"), "LIMIT")
        self.assertEqual(entries[1].get("role"), "AVERAGING")
        self.assertEqual(entries[1].get("order_type"), "LIMIT")
        self.assertEqual(normalized.get("entry_plan_type"), "LIMIT_WITH_LIMIT_AVERAGING")
        self.assertEqual(normalized.get("entry_structure"), "TWO_STEP")
        self.assertTrue(normalized.get("has_averaging_plan"))
        self.assertEqual(normalized.get("entry_main"), 0.1882)
        self.assertEqual(normalized.get("average_entry"), 0.19)


if __name__ == "__main__":
    unittest.main()
