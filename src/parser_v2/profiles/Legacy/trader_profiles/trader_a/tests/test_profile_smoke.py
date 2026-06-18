from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.registry import get_profile_parser
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


class TraderAProfileSmokeTests(unittest.TestCase):
    def test_parse_message_returns_expected_shape(self) -> None:
        parser = TraderAProfileParser()
        context = ParserContext(
            trader_code="trader_a",
            message_id=101,
            reply_to_message_id=100,
            channel_id="-1001",
            raw_text="sample text",
            extracted_links=[],
            hashtags=[],
        )
        result = parser.parse_message("sample text", context)

        self.assertIsInstance(result, TraderParseResult)
        self.assertTrue(isinstance(result.message_type, str) and result.message_type)
        self.assertIsInstance(result.intents, list)
        self.assertIsInstance(result.entities, dict)
        self.assertIsInstance(result.target_refs, list)
        self.assertIsInstance(result.reported_results, list)
        self.assertIsInstance(result.warnings, list)
        self.assertIsInstance(result.confidence, float)

    def test_parse_message_never_crashes_on_empty_text(self) -> None:
        parser = TraderAProfileParser()
        context = ParserContext(
            trader_code="trader_a",
            message_id=102,
            reply_to_message_id=None,
            channel_id="-1001",
            raw_text="",
            extracted_links=[],
            hashtags=[],
        )
        result = parser.parse_message("", context)
        self.assertEqual(result.message_type, "UNCLASSIFIED")

    def test_registry_resolves_trader_a(self) -> None:
        parser = get_profile_parser("trader_a")
        self.assertIsNotNone(parser)
        self.assertIsInstance(parser, TraderAProfileParser)


if __name__ == "__main__":
    unittest.main()
