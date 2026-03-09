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
        # Legacy compatibility fields
        self.assertEqual(normalized.get("event_type"), "NEW_SIGNAL")
        self.assertEqual(normalized.get("instrument"), "BTCUSDT")
        self.assertEqual(normalized.get("side"), "BUY")
        # Semantic parser contract fields
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
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("MOVE_SL_TO_ENTRY", normalized.get("actions", []))
        self.assertIn("CANCEL_PENDING_ORDERS", normalized.get("actions", []))
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
        self.assertEqual(normalized.get("message_type"), "INFO_ONLY")
        reported = normalized.get("reported_results", [])
        self.assertEqual(len(reported), 2)
        self.assertEqual(reported[0].get("symbol"), "BTC")
        self.assertEqual(reported[0].get("r_multiple"), 1.2)
        self.assertEqual(reported[1].get("symbol"), "DOGE")
        self.assertEqual(reported[1].get("r_multiple"), -0.4)


if __name__ == "__main__":
    unittest.main()
