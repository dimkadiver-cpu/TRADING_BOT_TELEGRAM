from __future__ import annotations

import json
import unittest

from src.parser.pipeline import MinimalParserPipeline, ParserInput
from src.parser.trader_profiles.ta_profile import classify_ta_message, extract_ta_fields, load_ta_profile_config


class TAProfileRefactorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB"})

    def test_load_profile_options_and_markers_from_rules(self) -> None:
        cfg = load_ta_profile_config(
            rules={
                "profile_options": {"language": "ru", "parser_mode": "hybrid_auto", "allows_multi_intent": True},
                "intent_keywords": {"U_CANCEL_PENDING_ORDERS": ["убрать все лимитные ордера"]},
            }
        )
        self.assertEqual(cfg.profile_options.get("language"), "ru")
        self.assertEqual(cfg.profile_options.get("parser_mode"), "hybrid_auto")
        self.assertIn("убрать все лимитные ордера", cfg.intent_keywords["U_CANCEL_PENDING_ORDERS"])

    def test_multi_intent_update_extraction(self) -> None:
        text = "по этим сетапам стоп нужно обязательно поставить на точку входа и убрать все лимитные ордера"
        normalized = text.lower()
        ta = extract_ta_fields(text=text, normalized=normalized)

        self.assertIn("U_MOVE_STOP_TO_BE", ta.intents)
        self.assertIn("U_CANCEL_PENDING_ORDERS", ta.intents)
        self.assertGreaterEqual(ta.update_hits, 2)

    def test_ta_pipeline_update_close_full_and_market_entry_backward_compat(self) -> None:
        update_payload = ParserInput(
            raw_message_id=501,
            raw_text="зафиксирую все свои позиции по текущим",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=501,
            linkage_reference_id=500,
        )
        update_result = self.pipeline.parse(update_payload)
        update_normalized = json.loads(update_result.parse_result_normalized_json or "{}")

        self.assertEqual(update_normalized.get("message_type"), "UPDATE")
        notes_blob = " ".join(update_normalized.get("notes", []))
        self.assertIn("U_CLOSE_FULL", notes_blob)

        setup_payload = ParserInput(
            raw_message_id=502,
            raw_text="BTCUSDT лонг вход с текущих sl 99900 tp1 100500",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=502,
            linkage_reference_id=None,
        )
        setup_result = self.pipeline.parse(setup_payload)
        setup_normalized = json.loads(setup_result.parse_result_normalized_json or "{}")

        self.assertEqual(setup_normalized.get("message_type"), "NEW_SIGNAL")
        self.assertEqual(setup_normalized.get("entry_main"), None)
        entries = setup_normalized.get("entries", [])
        self.assertTrue(entries)
        self.assertEqual(entries[0].get("raw"), "MARKET_CURRENT")

    def test_result_report_still_detected_as_info_payload(self) -> None:
        payload = ParserInput(
            raw_message_id=503,
            raw_text="PORTALUSDT - 0.2R\nSAGAUSDT - 0.18R",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=503,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "INFO_ONLY")
        self.assertEqual(len(normalized.get("reported_results", [])), 2)


if __name__ == "__main__":
    unittest.main()
