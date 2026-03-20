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
                "intent_keywords": {"U_CANCEL_PENDING_ORDERS": ["cancel pending orders"]},
            }
        )
        self.assertEqual(cfg.profile_options.get("language"), "ru")
        self.assertEqual(cfg.profile_options.get("parser_mode"), "hybrid_auto")
        self.assertIn("cancel pending orders", cfg.intent_keywords["U_CANCEL_PENDING_ORDERS"])

    def test_multi_intent_update_extraction(self) -> None:
        text = "move stop to breakeven and cancel pending orders"
        normalized = text.lower()
        ta = extract_ta_fields(text=text, normalized=normalized)

        self.assertIn("U_MOVE_STOP_TO_BE", ta.intents)
        self.assertIn("U_MOVE_STOP", ta.intents)
        self.assertIn("U_CANCEL_PENDING_ORDERS", ta.intents)
        self.assertGreaterEqual(ta.update_hits, 2)

    def test_pipeline_multi_intent_update_maps_to_canonical_actions(self) -> None:
        payload = ParserInput(
            raw_message_id=510,
            raw_text="move stop to breakeven and cancel pending orders",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=510,
            linkage_reference_id=500,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", normalized.get("intents", []))
        self.assertIn("U_CANCEL_PENDING_ORDERS", normalized.get("intents", []))
        self.assertIn("ACT_MOVE_STOP_LOSS", normalized.get("actions", []))
        self.assertIn("ACT_CANCEL_ALL_PENDING_ENTRIES", normalized.get("actions", []))
        self.assertEqual(normalized.get("entities", {}).get("new_stop_level"), "ENTRY")
        self.assertEqual(normalized.get("entities", {}).get("cancel_scope"), "TARGETED")

    def test_pipeline_update_close_full_maps_to_actions(self) -> None:
        payload = ParserInput(
            raw_message_id=511,
            raw_text="close all positions at current market price",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=511,
            linkage_reference_id=500,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_CLOSE_FULL", normalized.get("intents", []))
        self.assertIn("ACT_CLOSE_FULL", normalized.get("actions", []))
        self.assertEqual(normalized.get("entities", {}).get("close_scope"), "FULL")

    def test_pipeline_update_close_partial_entities(self) -> None:
        payload = ParserInput(
            raw_message_id=514,
            raw_text="partial close 50%",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=514,
            linkage_reference_id=500,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_CLOSE_PARTIAL", normalized.get("intents", []))
        self.assertEqual(normalized.get("entities", {}).get("close_scope"), "PARTIAL")
        self.assertEqual(normalized.get("entities", {}).get("close_fraction"), 0.5)

    def test_pipeline_update_tp_and_stop_hit(self) -> None:
        tp_payload = ParserInput(
            raw_message_id=512,
            raw_text="tp1 tp hit on this setup",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=512,
            linkage_reference_id=500,
        )
        tp_result = self.pipeline.parse(tp_payload)
        tp_normalized = json.loads(tp_result.parse_result_normalized_json or "{}")
        self.assertIn("U_TP_HIT", tp_normalized.get("intents", []))
        self.assertEqual(tp_normalized.get("actions", []), [])
        self.assertEqual(tp_normalized.get("entities", {}).get("hit_target"), "TP1")

        stop_payload = ParserInput(
            raw_message_id=513,
            raw_text="stop hit",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=513,
            linkage_reference_id=500,
        )
        stop_result = self.pipeline.parse(stop_payload)
        stop_normalized = json.loads(stop_result.parse_result_normalized_json or "{}")
        self.assertIn("U_STOP_HIT", stop_normalized.get("intents", []))
        self.assertEqual(stop_normalized.get("actions", []), [])
        self.assertEqual(stop_normalized.get("entities", {}).get("hit_target"), "STOP")

    def test_pipeline_update_mark_filled_entities(self) -> None:
        payload = ParserInput(
            raw_message_id=515,
            raw_text="order filled at entry",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=515,
            linkage_reference_id=500,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertIn("U_MARK_FILLED", normalized.get("intents", []))
        self.assertEqual(normalized.get("entities", {}).get("fill_state"), "FILLED")

    def test_ta_pipeline_update_close_full_and_market_entry_backward_compat(self) -> None:
        update_payload = ParserInput(
            raw_message_id=501,
            raw_text="close all positions at current market price",
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
        self.assertIn("U_CLOSE_FULL", update_normalized.get("intents", []))
        self.assertIn("ACT_CLOSE_FULL", update_normalized.get("actions", []))

        setup_payload = ParserInput(
            raw_message_id=502,
            raw_text="BTCUSDT long entry 100000 sl 99900 tp1 100500",
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
        entries = setup_normalized.get("entries", [])
        self.assertTrue(entries)
        self.assertIn("NS_CREATE_SIGNAL", setup_normalized.get("intents", []))
        self.assertEqual((setup_normalized.get("entities") or {}).get("symbol"), "BTCUSDT")
        self.assertEqual((setup_normalized.get("entities") or {}).get("side"), "LONG")

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
        self.assertIn("U_REPORT_FINAL_RESULT", normalized.get("intents", []))
        self.assertEqual(normalized.get("actions", []), [])
        self.assertEqual(normalized.get("entities", {}).get("result_mode"), "R_MULTIPLE")


    def test_new_signal_ab_entry_bulleted_lines(self) -> None:
        payload = ParserInput(
            raw_message_id=574,
            raw_text=(
                "#FARTCOINUSDT \U0001f43b \u0428\u043e\u0440\u0442 (\u0432\u0445\u043e\u0434 \u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439)\n\n"
                "\u0412\u0445\u043e\u0434 (2-\u0444\u0430\u0437\u043d\u044b\u0439):\n"
                "\u2014 \u0412\u0445\u043e\u0434 A: 0.1882\n"
                "\u2014 \u0412\u0445\u043e\u0434 B (\u0443\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435/\u0434\u043e\u0431\u043e\u0440): 0.1900\n\n"
                "SL: 0.1950\n"
                "TP1: 0.1825\n"
            ),
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=574,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
        self.assertIn("NS_CREATE_SIGNAL", normalized.get("intents", []))
        self.assertEqual(normalized.get("symbol"), "FARTCOINUSDT")
        self.assertEqual(normalized.get("entry_main"), 0.1882)
        self.assertEqual(normalized.get("average_entry"), 0.19)

    def test_bare_hashtag_symbol_complete_setup_defaults_to_usdt(self) -> None:
        payload = ParserInput(
            raw_message_id=657,
            raw_text="#LINK long entry 9.05 sl 9.25 tp1 8.94",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=657,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
        self.assertEqual(normalized.get("symbol"), "LINKUSDT")
        self.assertIn("NS_CREATE_SIGNAL", normalized.get("intents", []))

    def test_cancel_pending_with_links_is_update(self) -> None:
        payload = ParserInput(
            raw_message_id=587,
            raw_text=(
                "https://t.me/c/3171748254/2243\n"
                "https://t.me/c/3171748254/2242\n"
                "\u0441\u043d\u0438\u043c\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438"
            ),
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=587,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_CANCEL_PENDING_ORDERS", normalized.get("intents", []))


if __name__ == "__main__":
    unittest.main()

