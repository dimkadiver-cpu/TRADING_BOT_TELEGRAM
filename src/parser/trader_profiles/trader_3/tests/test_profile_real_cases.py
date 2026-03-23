from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.registry import get_profile_parser
from src.parser.trader_profiles.trader_3.profile import Trader3ProfileParser


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_3",
        message_id=3000,
        reply_to_message_id=reply_to,
        channel_id="-1003",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class Trader3ProfileRealCasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Trader3ProfileParser()

    def test_registry_resolves_trader_3(self) -> None:
        parser = get_profile_parser("trader_3")
        self.assertIsNotNone(parser)
        self.assertIsInstance(parser, Trader3ProfileParser)

    def test_info_vip_market_update(self) -> None:
        text = "VIP MARKET UPDATE: $BTC"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.primary_intent, "MARKET_COMMENTARY")

    def test_info_market_analysis(self) -> None:
        text = "MARKET ANALYSIS: BTC likely consolidating before breakout"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "INFO_ONLY")
        self.assertEqual(result.primary_intent, "MARKET_COMMENTARY")

    def test_new_signal_classic_pattern(self) -> None:
        text = (
            "SIGNAL ID: #1997\n"
            "COIN: $BTC/USDT\n"
            "Direction: LONG\n"
            "ENTRY: 105200 - 107878\n"
            "TARGETS: 109600, 112300, 115000\n"
            "STOP LOSS: 102450"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.primary_intent, "OPEN_POSITION")
        self.assertEqual(result.entities.get("signal_id"), 1997)
        self.assertEqual(result.entities.get("symbol"), "BTCUSDT")
        self.assertEqual(result.entities.get("entry_range_low"), 105200.0)
        self.assertEqual(result.entities.get("entry_range_high"), 107878.0)
        self.assertEqual(result.entities.get("stop_loss"), 102450.0)
        self.assertEqual(result.entities.get("take_profits"), [109600.0, 112300.0, 115000.0])
        self.assertIn({"kind": "signal_id", "ref": 1997}, result.target_refs)

    def test_new_signal_short_side(self) -> None:
        text = (
            "SIGNAL ID: #2002\n"
            "COIN: $ETH/USDT\n"
            "DIRECTION: SHORT\n"
            "ENTRY: 3,840 – 3,870\n"
            "TARGETS: 3,700, 3,610\n"
            "SL: 3,955"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("side"), "SHORT")
        self.assertEqual(result.entities.get("entry_range_low"), 3840.0)
        self.assertEqual(result.entities.get("entry_range_high"), 3870.0)

    def test_new_signal_entry_decimal_space_typo(self) -> None:
        text = (
            "SIGNAL ID: #2078\n"
            "COIN: $CRO/USDT (2-5x)\n"
            "DIRECTION: LONG\n"
            "ENTRY: 0. 0745 - 0.0750\n"
            "TARGETS: 0.0775 - 0.0800 - 0.0840\n"
            "STOP LOSS: 0.0700"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertEqual(result.entities.get("entry_range_low"), 0.0745)
        self.assertEqual(result.entities.get("entry_range_high"), 0.0750)

    def test_tp_hit_single_target(self) -> None:
        text = (
            "SIGNAL ID: #1997\n"
            "Target 1: 109600✅\n"
            "🔥15% Profit (5x)🔥"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.primary_intent, "REPORT_PROFIT")
        self.assertEqual(result.entities.get("hit_targets"), [1])
        self.assertEqual(result.entities.get("reported_profit_percent"), 15.0)
        self.assertEqual(result.entities.get("reported_leverage_hint"), 5.0)

    def test_tp_hit_multi_targets(self) -> None:
        text = (
            "SIGNAL ID: #1998\n"
            "Target 1: 109600✅\n"
            "Target 2: 112300✅\n"
            "Target 3: 115000✅\n"
            "🔥38.8% Profit (4x)🔥"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.entities.get("hit_targets"), [1, 2, 3])
        self.assertEqual(result.entities.get("max_target_hit"), 3)

    def test_loss_update_313(self) -> None:
        text = (
            "SIGNAL ID: #1999\n"
            "Unfortunately, it broke down\n"
            "STOP LOSS: 3298\n"
            "🚫3.13% Loss (2x)🚫"
        )
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.primary_intent, "REPORT_LOSS")
        self.assertEqual(result.entities.get("reported_loss_percent"), 3.13)
        self.assertEqual(result.entities.get("reported_leverage_hint"), 2.0)
        self.assertEqual(result.entities.get("stop_price"), 3298.0)
        self.assertIn("U_STOP_HIT", result.intents)

    def test_loss_update_385(self) -> None:
        text = "SIGNAL ID: #2000\n🚫3.85% Loss (2x)🚫"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.entities.get("reported_loss_percent"), 3.85)
        self.assertEqual(result.primary_intent, "REPORT_LOSS")
        self.assertNotIn("U_STOP_HIT", result.intents)

    def test_closed_manually(self) -> None:
        text = "SIGNAL ID: #2001\nClosed Manually\n🚫12% Loss (2x)🚫"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.primary_intent, "CLOSE_POSITION")
        self.assertTrue(result.entities.get("manual_close"))
        self.assertEqual(result.entities.get("reported_loss_percent"), 12.0)
        self.assertNotIn("U_STOP_HIT", result.intents)

    def test_reenter_update(self) -> None:
        text = "SIGNAL ID: #2001\nRe-Enter.\nSame Entry level ,Targets & SL"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertEqual(result.primary_intent, "REENTER_POSITION")
        self.assertTrue(result.entities.get("reenter"))
        self.assertEqual(result.entities.get("reenter_note"), "Same Entry level, Targets & SL")

    def test_operational_update_without_strong_target_warns(self) -> None:
        text = "Target 1: 109600✅"
        result = self.parser.parse_message(text, _context(text=text))
        self.assertEqual(result.message_type, "UPDATE")
        self.assertIn("trader_3_update_missing_target", result.warnings)


if __name__ == "__main__":
    unittest.main()
