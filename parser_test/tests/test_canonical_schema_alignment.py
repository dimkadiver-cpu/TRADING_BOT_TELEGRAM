from __future__ import annotations

import json
import unittest

from src.parser.canonical_schema import canonical_action_for_intent, load_canonical_intent_schema, normalize_intents, trader_intent_support
from src.parser.normalization import build_parse_result_normalized
from src.parser.pipeline import MinimalParserPipeline, ParserInput


class CanonicalSchemaAlignmentTests(unittest.TestCase):
    def test_csv_is_loaded_as_authoritative_schema(self) -> None:
        schema = load_canonical_intent_schema()
        self.assertIn("NS_CREATE_SIGNAL", schema)
        self.assertEqual(canonical_action_for_intent("U_MOVE_STOP_TO_BE"), "ACT_MOVE_STOP_LOSS_TO_BE")

    def test_intent_aliases_are_normalized(self) -> None:
        normalized = normalize_intents(["U_UPDATE_STOP", "U_TP_HIT_EXPLICIT", "U_TP_HIT"])
        self.assertEqual(normalized, ["U_MOVE_STOP", "U_TP_HIT"])

    def test_validation_depends_on_intent_required_entities(self) -> None:
        result = build_parse_result_normalized(
            message_type="UPDATE",
            normalized_text="close all",
            trader_id="TB",
            source_chat_id="-1001",
            source_message_id=101,
            raw_text="close all",
            parser_used="regex",
            parser_mode="regex_only",
            parse_status="PARSED",
            instrument=None,
            side=None,
            entry_raw=None,
            stop_raw=None,
            targets=[],
            root_ref=None,
            existing_warnings=[],
            notes=[],
            intents=["U_CLOSE_FULL"],
            actions=["ACT_CLOSE_FULL_AND_MARK_CLOSED"],
            entities={},
        )
        self.assertIn("normalized_intent_missing_required:U_CLOSE_FULL:symbol", result.validation_warnings)

    def test_cancel_pending_without_symbol_is_allowed_when_targeted(self) -> None:
        result = build_parse_result_normalized(
            message_type="UPDATE",
            normalized_text="cancel pending https://t.me/c/1/999",
            trader_id="TB",
            source_chat_id="-1001",
            source_message_id=102,
            raw_text="cancel pending https://t.me/c/1/999",
            parser_used="regex",
            parser_mode="regex_only",
            parse_status="PARSED",
            instrument=None,
            side=None,
            entry_raw=None,
            stop_raw=None,
            targets=[],
            root_ref=None,
            existing_warnings=[],
            notes=[],
            intents=["U_CANCEL_PENDING_ORDERS"],
            actions=["ACT_CANCEL_ALL_PENDING_ENTRIES"],
            entities={},
        )
        self.assertFalse(any("U_CANCEL_PENDING_ORDERS:symbol" in w for w in result.validation_warnings))

    def test_setup_incomplete_keeps_optional_entities_null(self) -> None:
        pipeline = MinimalParserPipeline(trader_aliases={"TB": "TB"})
        parsed = pipeline.parse(
            ParserInput(
                raw_message_id=501,
                raw_text="BTCUSDT long entry soon",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=501,
            )
        )
        normalized = json.loads(parsed.parse_result_normalized_json or "{}")
        self.assertIn(normalized.get("message_type"), {"SETUP_INCOMPLETE", "UNCLASSIFIED"})
        self.assertIsNone(normalized.get("stop_loss_price"))
        self.assertEqual(normalized.get("take_profit_prices"), [])

    def test_trader_support_matrix_is_exposed(self) -> None:
        support = trader_intent_support("TA")
        self.assertIn("NS_CREATE_SIGNAL", support["supported"])
        self.assertIn("U_REVERSE_SIGNAL", support["unsupported"])


if __name__ == "__main__":
    unittest.main()
