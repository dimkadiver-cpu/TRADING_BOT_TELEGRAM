from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.parser.dispatcher import ParserDispatcher
from src.parser.llm_adapter import LLMAdapter
from src.parser.normalization import build_parse_result_normalized
from src.parser.pipeline import MinimalParserPipeline, ParserInput
from src.parser.trader_profiles.registry import canonicalize_trader_code, get_profile_parser


class PipelineSemanticConsistencyTests(unittest.TestCase):
    def test_warning_merge_keeps_upstream_and_validation(self) -> None:
        result = build_parse_result_normalized(
            message_type="UPDATE",
            normalized_text="update",
            trader_id="TB",
            source_chat_id="-1001",
            source_message_id=1,
            raw_text="update",
            parser_used="regex",
            parser_mode="regex_only",
            parse_status="PARSED",
            instrument=None,
            side=None,
            entry_raw=None,
            stop_raw=None,
            targets=[],
            root_ref=None,
            existing_warnings=["upstream_warning"],
            notes=[],
            intents=[],
            actions=[],
            entities=None,
        )
        warnings = result.validation_warnings
        self.assertTrue(warnings)
        self.assertEqual(warnings[0], "upstream_warning")
        self.assertIn("normalized_update_missing_intents", warnings)
        self.assertIn("normalized_update_missing_actions", warnings)
        self.assertIn("normalized_update_missing_target_ref", warnings)
        self.assertEqual(result.status, "PARSED_WITH_WARNINGS")

    def test_parse_status_and_selection_metadata_handling(self) -> None:
        pipeline = MinimalParserPipeline(
            trader_aliases={"TA": "TA", "TB": "TB"},
            global_parser_mode="llm_only",
            dispatcher=ParserDispatcher(llm_adapter=LLMAdapter(enabled=False)),
        )
        result = pipeline.parse(
            ParserInput(
                raw_message_id=801,
                raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=801,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(result.parse_status, normalized.get("status"))
        self.assertEqual(result.parse_status, "PARSED_WITH_WARNINGS")
        self.assertNotIn("selection_reason=", result.warning_text or "")
        self.assertTrue((normalized.get("selection_metadata") or {}).get("selection_reason"))

    def test_completeness_is_semantically_coherent(self) -> None:
        pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB"})
        update = pipeline.parse(
            ParserInput(
                raw_message_id=802,
                raw_text="move sl to breakeven https://t.me/c/123/456",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method="direct_reply",
                source_chat_id="-1001",
                source_message_id=802,
                linkage_reference_id=999,
            )
        )
        info_only = pipeline.parse(
            ParserInput(
                raw_message_id=803,
                raw_text="weekly stats",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method="direct_reply",
                source_chat_id="-1001",
                source_message_id=803,
                linkage_reference_id=1000,
            )
        )
        incomplete = pipeline.parse(
            ParserInput(
                raw_message_id=804,
                raw_text="BTCUSDT long entry later tp later",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=804,
            )
        )
        self.assertEqual(update.completeness, "COMPLETE")
        self.assertEqual(info_only.completeness, "COMPLETE")
        self.assertEqual(incomplete.completeness, "INCOMPLETE")
        self.assertEqual(info_only.linkage_status, "LINKED")

    def test_base_classification_avoids_premature_new_signal(self) -> None:
        pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB"})
        result = pipeline.parse(
            ParserInput(
                raw_message_id=805,
                raw_text="BTCUSDT long",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=805,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "SETUP_INCOMPLETE")


    def test_ta_and_trader_a_new_signal_semantic_alignment(self) -> None:
        raw_text = "BTCUSDT long entry 90000 sl 89500 tp1 91000"
        pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB", "A": "A", "trader_a": "trader_a"})

        ta_result = pipeline.parse(
            ParserInput(
                raw_message_id=806,
                raw_text=raw_text,
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TA",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=806,
            )
        )
        a_result = pipeline.parse(
            ParserInput(
                raw_message_id=807,
                raw_text=raw_text,
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="A",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=807,
            )
        )
        ta_n = json.loads(ta_result.parse_result_normalized_json or "{}")
        a_n = json.loads(a_result.parse_result_normalized_json or "{}")

        self.assertEqual(ta_n.get("message_type"), "NEW_SIGNAL")
        self.assertEqual(a_n.get("message_type"), "NEW_SIGNAL")
        self.assertIn("NS_CREATE_SIGNAL", ta_n.get("intents", []))
        self.assertIn("NS_CREATE_SIGNAL", a_n.get("intents", []))

        for normalized in (ta_n, a_n):
            entities = normalized.get("entities") or {}
            self.assertEqual(entities.get("symbol"), "BTCUSDT")
            self.assertEqual(entities.get("side"), "LONG")
            self.assertTrue(entities.get("entry"))
            self.assertIsNotNone(entities.get("stop_loss"))
            self.assertTrue(entities.get("take_profits"))


    def test_trader_a_real_cases_matrix(self) -> None:
        pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB", "A": "A", "trader_a": "trader_a"})

        new_signal = pipeline.parse(
            ParserInput(
                raw_message_id=808,
                raw_text="ARBUSDT short entry 0.10380 sl 0.10612 tp1 0.1016",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="A",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=808,
            )
        )
        ns = json.loads(new_signal.parse_result_normalized_json or "{}")
        self.assertEqual(ns.get("message_type"), "NEW_SIGNAL")
        self.assertIn("NS_CREATE_SIGNAL", ns.get("intents", []))

        update_with_target = pipeline.parse(
            ParserInput(
                raw_message_id=809,
                raw_text=("https://t.me/c/3171748254/2571\n" "move stop to be"),
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="A",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=809,
            )
        )
        upd = json.loads(update_with_target.parse_result_normalized_json or "{}")
        self.assertEqual(upd.get("message_type"), "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", upd.get("intents", []))
        self.assertIn(2571, upd.get("target_refs", []))

        cancel_pending = pipeline.parse(
            ParserInput(
                raw_message_id=810,
                raw_text=("https://t.me/c/3171748254/2243\n" "cancel pending orders"),
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="A",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=810,
            )
        )
        can = json.loads(cancel_pending.parse_result_normalized_json or "{}")
        self.assertEqual(can.get("message_type"), "UPDATE")
        self.assertIn("U_CANCEL_PENDING_ORDERS", can.get("intents", []))

        report = pipeline.parse(
            ParserInput(
                raw_message_id=811,
                raw_text=("BTCUSDT - +1.2RR\n" "ETHUSDT - -0.4R"),
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="A",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=811,
            )
        )
        rep = json.loads(report.parse_result_normalized_json or "{}")
        self.assertIn("U_REPORT_FINAL_RESULT", rep.get("intents", []))
        self.assertTrue(rep.get("reported_results"))


    def test_aliases_ta_a_trader_a_are_semantically_equivalent(self) -> None:
        pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB", "A": "A", "trader_a": "trader_a"})
        aliases = ["TA", "A", "trader_a"]

        cases = [
            (901, "BTCUSDT long entry 90000 sl 89500 tp1 91000"),
            (902, "BTCUSDT long entry later"),
            (903, "https://t.me/c/3171748254/2571\nmove stop to be"),
            (904, "BTCUSDT - +1.2RR\nETHUSDT - -0.4R"),
            (905, "https://t.me/c/3171748254/2243\ncancel pending orders"),
            (906, "https://t.me/c/3171748254/2571\nstop in be"),
        ]

        for base_id, raw_text in cases:
            outputs = []
            for i, alias in enumerate(aliases):
                result = pipeline.parse(
                    ParserInput(
                        raw_message_id=base_id * 10 + i,
                        raw_text=raw_text,
                        eligibility_status="ACQUIRED_ELIGIBLE",
                        eligibility_reason="eligible",
                        resolved_trader_id=alias,
                        trader_resolution_method="tag",
                        linkage_method=None,
                        source_chat_id="-1001",
                        source_message_id=base_id * 10 + i,
                    )
                )
                normalized = json.loads(result.parse_result_normalized_json or "{}")
                outputs.append(
                    {
                        "resolved_trader_id": result.resolved_trader_id,
                        "parse_status": result.parse_status,
                        "message_type": normalized.get("message_type"),
                        "intents": normalized.get("intents", []),
                        "actions": normalized.get("actions", []),
                        "target_refs": normalized.get("target_refs", []),
                        "validation_warnings": normalized.get("validation_warnings", []),
                        "entities": {
                            "symbol": (normalized.get("entities") or {}).get("symbol"),
                            "side": (normalized.get("entities") or {}).get("side"),
                            "entry": (normalized.get("entities") or {}).get("entry"),
                            "stop_loss": (normalized.get("entities") or {}).get("stop_loss"),
                            "take_profits": (normalized.get("entities") or {}).get("take_profits"),
                            "new_stop_level": (normalized.get("entities") or {}).get("new_stop_level"),
                            "cancel_scope": (normalized.get("entities") or {}).get("cancel_scope"),
                            "result_mode": (normalized.get("entities") or {}).get("result_mode"),
                        },
                    }
                )

            first = outputs[0]
            for output in outputs[1:]:
                self.assertEqual(output["resolved_trader_id"], "trader_a")
                self.assertEqual(output["parse_status"], first["parse_status"])
                self.assertEqual(output["message_type"], first["message_type"])
                self.assertEqual(output["intents"], first["intents"])
                self.assertEqual(output["actions"], first["actions"])
                self.assertEqual(output["target_refs"], first["target_refs"])
                self.assertEqual(output["validation_warnings"], first["validation_warnings"])
                self.assertEqual(output["entities"], first["entities"])



    def test_canonicalize_trader_code_hardening(self) -> None:
        self.assertEqual(canonicalize_trader_code("TA"), "trader_a")
        self.assertEqual(canonicalize_trader_code("  a "), "trader_a")
        self.assertEqual(canonicalize_trader_code("TrAdEr_A"), "trader_a")
        self.assertEqual(canonicalize_trader_code("TB"), "tb")
        self.assertIsNone(canonicalize_trader_code(None))
        self.assertIsNone(canonicalize_trader_code("   "))

    def test_source_label_preserved_and_resolved_is_canonical(self) -> None:
        pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB", "A": "A", "trader_a": "trader_a"})
        result = pipeline.parse(
            ParserInput(
                raw_message_id=990,
                raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id=" TA ",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=990,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        metadata = normalized.get("selection_metadata") or {}
        self.assertEqual(result.resolved_trader_id, "trader_a")
        self.assertEqual(metadata.get("canonical_trader_id"), "trader_a")
        self.assertEqual(metadata.get("source_trader_label"), " TA ")

    def test_runtime_does_not_depend_on_ta_profile_branch(self) -> None:
        pipeline = MinimalParserPipeline(trader_aliases={"TA": "TA", "TB": "TB", "A": "A", "trader_a": "trader_a"})
        with patch("src.parser.trader_profiles.ta_profile.extract_ta_fields", side_effect=AssertionError("legacy TA branch should not be used")):
            result = pipeline.parse(
                ParserInput(
                    raw_message_id=991,
                    raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                    eligibility_status="ACQUIRED_ELIGIBLE",
                    eligibility_reason="eligible",
                    resolved_trader_id="TA",
                    trader_resolution_method="tag",
                    linkage_method=None,
                    source_chat_id="-1001",
                    source_message_id=991,
                )
            )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")
    def test_registry_aliases_are_importable(self) -> None:
        self.assertIsNotNone(get_profile_parser("trader_a"))
        self.assertIsNotNone(get_profile_parser("a"))
        self.assertIsNotNone(get_profile_parser("TA"))


if __name__ == "__main__":
    unittest.main()
