from __future__ import annotations

import json
import unittest

from src.parser.intent_action_map import infer_update_intents_from_text
from src.parser.pipeline import MinimalParserPipeline, ParserInput
from src.parser.normalization import build_parse_result_normalized
from src.parser.text_utils import normalize_text


class ParseResultNormalizedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB"})

    def test_new_signal_contains_semantic_and_legacy_fields(self) -> None:
        payload = ParserInput(
            raw_message_id=1,
            raw_text="BTCUSDT long entry 90000-90100 sl 89500 tp1 91000 tp2 92000",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
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
            resolved_trader_id="TA",
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
        self.assertEqual(normalized.get("entities", {}).get("cancel_scope"), "TARGETED")
        self.assertEqual(sorted(normalized.get("target_refs", [])), [265, 266])
        self.assertEqual(normalized.get("validation_warnings", []), [])

    def test_stop_move_intents_are_distinct(self) -> None:
        self.assertEqual(infer_update_intents_from_text("stop to breakeven"), ["U_MOVE_STOP_TO_BE"])
        self.assertEqual(infer_update_intents_from_text("move stop to tp1"), ["U_MOVE_STOP"])
        self.assertEqual(infer_update_intents_from_text("стоп на 1 тейк"), ["U_MOVE_STOP"])

    def test_reenter_update_inherits_levels_from_reply_message(self) -> None:
        payload = ParserInput(
            raw_message_id=4,
            raw_text="SIGNAL ID: #2011\nRe-Enter.\nSame Entry level ,Targets & SL",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="trader_3",
            trader_resolution_method="tag",
            linkage_method="direct_reply",
            source_chat_id="-100123",
            source_message_id=466,
            linkage_reference_id=465,
            reply_raw_text=(
                "SIGNAL ID: #2011\n"
                "COIN: $LINK/USDT (2-5x)\n"
                "Direction: LONG\n"
                "ENTRY: 14.10 - 14.35\n"
                "TARGETS: 14.80, 15.20\n"
                "STOP LOSS: 13.70"
            ),
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertIn("U_REENTER", normalized.get("intents", []))
        self.assertEqual(normalized.get("entry_main"), 14.1)
        self.assertEqual(normalized.get("average_entry"), 14.35)
        self.assertEqual(normalized.get("stop_loss_price"), 13.7)
        self.assertEqual(normalized.get("take_profit_prices"), [14.8, 15.2])

    def test_info_only_contains_reported_results(self) -> None:
        payload = ParserInput(
            raw_message_id=3,
            raw_text="weekly update BTC - 1.2R DOGE - -0.4R",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
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

    def test_market_entry_without_price_keeps_primary_market_entry(self) -> None:
        payload = ParserInput(
            raw_message_id=5,
            raw_text=(
                "BTCUSDT SHORT\n"
                "вход с текущих\n"
                "SL: 101000\n"
                "TP1: 98000"
            ),
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=101,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
        self.assertEqual(normalized.get("entry_mode"), "MARKET")
        self.assertIsNone(normalized.get("entry_main"))
        self.assertEqual(normalized.get("average_entry"), None)
        self.assertEqual(normalized.get("entry_plan_type"), "SINGLE_MARKET")
        self.assertEqual(normalized.get("entry_structure"), "SINGLE")
        self.assertEqual(normalized.get("entries")[0].get("order_type"), "MARKET")
        self.assertIsNone(normalized.get("entries")[0].get("price"))

    def test_single_entry_does_not_duplicate_average_entry(self) -> None:
        payload = ParserInput(
            raw_message_id=6,
            raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
            eligibility_status="ACQUIRED_ELIGIBLE",
            eligibility_reason="eligible",
            resolved_trader_id="TA",
            trader_resolution_method="tag",
            linkage_method=None,
            source_chat_id="-100123",
            source_message_id=102,
            linkage_reference_id=None,
        )
        result = self.pipeline.parse(payload)
        normalized = json.loads(result.parse_result_normalized_json or "{}")

        self.assertEqual(normalized.get("entry_main"), 90000.0)
        self.assertIsNone(normalized.get("average_entry"))
        self.assertEqual(normalized.get("entry_plan_type"), "SINGLE_LIMIT")

    def test_trader_specific_default_entry_order_type_inference(self) -> None:
        trader_3_result = build_parse_result_normalized(
            message_type="NEW_SIGNAL",
            normalized_text="signal id 1 coin btcusdt direction long entry 105200 107878 targets 109600 stop loss 102450",
            trader_id="trader_3",
            source_chat_id="-100123",
            source_message_id=1,
            raw_text="SIGNAL ID: #1\nCOIN: $BTC/USDT\nDirection: LONG\nENTRY: 105200 - 107878\nTARGETS: 109600\nSTOP LOSS: 102450",
            parser_used="regex",
            parser_mode="regex_only",
            parse_status="PARSED",
            instrument="BTCUSDT",
            side="LONG",
            entry_raw="105200 - 107878",
            stop_raw="102450",
            targets=["109600"],
            root_ref=None,
            existing_warnings=[],
            notes=[],
            intents=["NS_CREATE_SIGNAL"],
            actions=[],
            entities={},
        )
        self.assertEqual(trader_3_result.entries[0].get("order_type"), "LIMIT")
        self.assertEqual(trader_3_result.entries[1].get("order_type"), "LIMIT")
        self.assertEqual(trader_3_result.entry_plan_type, "LIMIT_WITH_LIMIT_AVERAGING")
        self.assertTrue(trader_3_result.has_averaging_plan)

        trader_d_result = build_parse_result_normalized(
            message_type="NEW_SIGNAL",
            normalized_text="traderd short risk 0.5 sl 0.13764 tp1 0.12522",
            trader_id="trader_d",
            source_chat_id="-100123",
            source_message_id=2,
            raw_text="Trader#d\nscrt short\nриск 0,5\nсл 0,13764\nтп1 0,12522",
            parser_used="regex",
            parser_mode="regex_only",
            parse_status="PARSED",
            instrument="SCRTUSDT",
            side="SHORT",
            entry_raw=None,
            stop_raw="0,13764",
            targets=["0,12522"],
            root_ref=None,
            existing_warnings=[],
            notes=[],
            intents=["NS_CREATE_SIGNAL"],
            actions=[],
            entities={
                "entry_plan_entries": [
                    {
                        "sequence": 1,
                        "role": "PRIMARY",
                        "price": None,
                        "raw_label": "ENTRY",
                        "source_style": "MARKET",
                        "is_optional": False,
                    }
                ]
            },
        )
        self.assertEqual(trader_d_result.entries[0].get("order_type"), "MARKET")
        self.assertIsNone(trader_d_result.entries[0].get("price"))
        self.assertEqual(trader_d_result.entry_plan_type, "SINGLE_MARKET")
        self.assertFalse(trader_d_result.has_averaging_plan)

    def test_normalize_text_collapses_inner_whitespace(self) -> None:
        self.assertEqual(normalize_text("  Вход   с \n текущих \t:  1.23 "), "вход с текущих : 1.23")

if __name__ == "__main__":
    unittest.main()









