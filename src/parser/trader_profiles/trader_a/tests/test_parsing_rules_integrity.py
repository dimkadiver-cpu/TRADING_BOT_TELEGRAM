from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


_RULES_PATH = Path(__file__).resolve().parents[1] / "parsing_rules.json"


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
        payload = json.loads(_RULES_PATH.read_text(encoding="utf-8"))

        new_signal = payload["classification_markers"]["new_signal"]["strong"]
        self.assertIn("a (с текущих)", new_signal)
        self.assertIn("a (лимит)", new_signal)
        self.assertIn("b (усреднение)", new_signal)
        self.assertIn("вход (a)", new_signal)
        self.assertIn("вход (b)", new_signal)

        cancel_markers = payload["intent_markers"]["U_CANCEL_PENDING_ORDERS"]
        self.assertIn("уберем лимитки", cancel_markers)
        self.assertIn("убираем лимитки", cancel_markers)
        self.assertIn("отменяем лимитки", cancel_markers)
        self.assertIn("снять все лимитные ордера", cancel_markers)
        self.assertIn("снять лимитки", cancel_markers)
        self.assertIn("снимаем лимитные ордера", cancel_markers)

        self.assertIn("все лонги", payload["global_target_markers"]["ALL_LONGS"])
        self.assertIn("все шорты", payload["global_target_markers"]["ALL_SHORTS"])

    def test_human_markers_do_not_contain_question_placeholders(self) -> None:
        payload = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
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
        text = "рекомендую снимаем лимитные ордера"
        result = parser.parse_message(text, _context(text))
        self.assertIn("U_CANCEL_PENDING_ORDERS", result.intents)


if __name__ == "__main__":
    unittest.main()
