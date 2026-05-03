from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.parser.rules_engine import RulesEngine
from src.parser.shared.context_resolution_schema import ContextResolutionRulesBlock
from src.parser.shared.disambiguation_rules_schema import DisambiguationRulesBlock
from src.parser.shared.intent_compatibility_schema import IntentCompatibilityBlock
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


_PROFILE_DIR = Path(__file__).resolve().parents[1]
_SEMANTIC_MARKERS_PATH = _PROFILE_DIR / "semantic_markers.json"
_RULES_PATH = _PROFILE_DIR / "rules.json"


def _load_merged_rules() -> dict:
    semantic_markers = json.loads(_SEMANTIC_MARKERS_PATH.read_text(encoding="utf-8"))
    rules = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return {**semantic_markers, **rules}


def _context(text: str) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=1,
        reply_to_message_id=None,
        channel_id="-1001",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TraderAParsingRulesIntegrityTests(unittest.TestCase):
    def test_rules_file_is_utf8_and_contains_restored_markers(self) -> None:
        payload = _load_merged_rules()

        new_signal = payload["classification_markers"]["new_signal"]["strong"]
        self.assertIn("entry", new_signal)
        self.assertIn("sl:", new_signal)
        self.assertIn("tp1:", new_signal)

        self.assertNotIn("U_CANCEL_PENDING_ORDERS", payload["intent_markers"])
        cancel_markers = payload["intent_markers"]["CANCEL_PENDING_ORDERS"]["strong"]
        self.assertGreaterEqual(len(cancel_markers), 6)

        self.assertGreaterEqual(len(payload["global_target_markers"]["ALL_LONGS"]), 1)
        self.assertGreaterEqual(len(payload["global_target_markers"]["ALL_SHORTS"]), 1)

    def test_human_markers_do_not_contain_question_placeholders(self) -> None:
        payload = _load_merged_rules()
        buckets: list[str] = []
        buckets.extend(payload["classification_markers"]["new_signal"]["strong"])
        buckets.extend(payload["classification_markers"]["update"]["strong"])
        buckets.extend(payload["classification_markers"]["new_signal"]["weak"])
        for item in payload["intent_markers"].values():
            if isinstance(item, list):
                buckets.extend(item)
            elif isinstance(item, dict):
                buckets.extend(item.get("strong", []))
                buckets.extend(item.get("weak", []))
        for item in payload["global_target_markers"].values():
            if isinstance(item, list):
                buckets.extend(item)

        broken = [marker for marker in buckets if isinstance(marker, str) and "???" in marker]
        self.assertEqual(broken, [])

    def test_restored_cancel_marker_is_effective_in_parser(self) -> None:
        parser = TraderAProfileParser()
        text = "cancel pending"
        result = parser.parse_message(text, _context(text))
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)


    def test_semantic_resolution_blocks_are_present_and_schema_valid(self) -> None:
        payload = _load_merged_rules()

        compatibility = IntentCompatibilityBlock.model_validate(
            payload["intent_compatibility"]
        )
        disambiguation = DisambiguationRulesBlock.model_validate(
            payload["disambiguation_rules"]
        )
        context_resolution = ContextResolutionRulesBlock.model_validate(
            payload["context_resolution_rules"]
        )

        self.assertGreaterEqual(len(compatibility.pairs), 4)
        self.assertGreaterEqual(len(disambiguation.rules), 3)
        self.assertGreaterEqual(len(context_resolution.rules), 2)

    def test_rules_engine_loads_profile_with_semantic_resolution_blocks(self) -> None:
        engine = RulesEngine.from_dict(_load_merged_rules())
        self.assertIn("intent_compatibility", engine.raw_rules)
        self.assertIn("disambiguation_rules", engine.raw_rules)
        self.assertIn("context_resolution_rules", engine.raw_rules)


if __name__ == "__main__":
    unittest.main()
