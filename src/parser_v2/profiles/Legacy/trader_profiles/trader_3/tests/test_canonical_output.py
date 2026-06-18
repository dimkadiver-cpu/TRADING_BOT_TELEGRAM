"""Tests for Trader3ProfileParser.parse_canonical() — CanonicalMessage v1 native output."""

from __future__ import annotations

import unittest

from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_3.profile import Trader3ProfileParser


def _ctx(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_3",
        message_id=3000,
        reply_to_message_id=reply_to,
        channel_id="-1003",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TestTrader3CanonicalInfo(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Trader3ProfileParser()

    def test_info_vip_market_update(self) -> None:
        text = "VIP MARKET UPDATE: $BTC"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertIsInstance(msg, CanonicalMessage)
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")
        self.assertIsNone(msg.signal)
        self.assertIsNone(msg.update)
        self.assertIsNone(msg.report)

    def test_info_market_analysis(self) -> None:
        text = "MARKET ANALYSIS: BTC likely consolidating"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")

    def test_unclassified_falls_through_to_info(self) -> None:
        text = "Random unrecognised message with no known markers"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "UNCLASSIFIED")


class TestTrader3CanonicalNewSignal(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Trader3ProfileParser()

    def _classic_signal(self) -> str:
        return (
            "SIGNAL ID: #1997\n"
            "COIN: $BTC/USDT\n"
            "Direction: LONG\n"
            "ENTRY: 105200 - 107878\n"
            "TARGETS: 109600, 112300, 115000\n"
            "STOP LOSS: 102450"
        )

    def test_signal_primary_class(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        self.assertEqual(msg.parse_status, "PARSED")

    def test_signal_payload_present(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertIsNotNone(msg.signal)
        self.assertIsNone(msg.update)
        self.assertIsNone(msg.report)

    def test_signal_symbol_and_side(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(msg.signal.symbol, "BTCUSDT")
        self.assertEqual(msg.signal.side, "LONG")

    def test_signal_entry_structure_range(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(msg.signal.entry_structure, "RANGE")
        self.assertEqual(len(msg.signal.entries), 2)

    def test_signal_entry_legs_prices(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        legs = msg.signal.entries
        self.assertEqual(legs[0].sequence, 1)
        self.assertEqual(legs[0].price.value, 105200.0)  # type: ignore[union-attr]
        self.assertEqual(legs[1].sequence, 2)
        self.assertEqual(legs[1].price.value, 107878.0)  # type: ignore[union-attr]
        self.assertEqual(legs[0].entry_type, "LIMIT")

    def test_signal_stop_loss(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        assert msg.signal.stop_loss is not None
        assert msg.signal.stop_loss.price is not None
        self.assertEqual(msg.signal.stop_loss.price.value, 102450.0)

    def test_signal_take_profits(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        tps = msg.signal.take_profits
        self.assertEqual(len(tps), 3)
        self.assertEqual(tps[0].price.value, 109600.0)
        self.assertEqual(tps[1].price.value, 112300.0)
        self.assertEqual(tps[2].price.value, 115000.0)

    def test_signal_completeness_complete(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(msg.signal.completeness, "COMPLETE")
        self.assertEqual(msg.signal.missing_fields, [])

    def test_signal_no_targeting(self) -> None:
        text = self._classic_signal()
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertIsNone(msg.targeting)

    def test_signal_short_side(self) -> None:
        text = (
            "SIGNAL ID: #2002\n"
            "COIN: $ETH/USDT\n"
            "DIRECTION: SHORT\n"
            "ENTRY: 3,840 – 3,870\n"
            "TARGETS: 3,700, 3,610\n"
            "SL: 3,955"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        assert msg.signal is not None
        self.assertEqual(msg.signal.side, "SHORT")

    def test_signal_missing_stop_falls_to_unclassified(self) -> None:
        # Classifier requires ALL signal fields — missing stop → UNCLASSIFIED
        text = (
            "SIGNAL ID: #1001\n"
            "COIN: $BTC/USDT\n"
            "Direction: LONG\n"
            "ENTRY: 50000 - 51000\n"
            "TARGETS: 52000, 53000"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "UNCLASSIFIED")

    def test_signal_entry_decimal_space_typo(self) -> None:
        text = (
            "SIGNAL ID: #2078\n"
            "COIN: $CRO/USDT (2-5x)\n"
            "DIRECTION: LONG\n"
            "ENTRY: 0. 0745 - 0.0750\n"
            "TARGETS: 0.0775 - 0.0800 - 0.0840\n"
            "STOP LOSS: 0.0700"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        assert msg.signal is not None
        self.assertEqual(msg.signal.entries[0].price.value, 0.0745)  # type: ignore[union-attr]
        self.assertEqual(msg.signal.entries[1].price.value, 0.0750)  # type: ignore[union-attr]


class TestTrader3CanonicalReport(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Trader3ProfileParser()

    def test_tp_hit_single_target(self) -> None:
        text = (
            "SIGNAL ID: #1997\n"
            "Target 1: 109600✅\n"
            "🔥15% Profit (5x)🔥"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "REPORT")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.report is not None
        self.assertEqual(len(msg.report.events), 1)
        evt = msg.report.events[0]
        self.assertEqual(evt.event_type, "TP_HIT")
        self.assertEqual(evt.level, 1)
        assert evt.result is not None
        self.assertEqual(evt.result.value, 15.0)
        self.assertEqual(evt.result.unit, "PERCENT")

    def test_tp_hit_multi_target(self) -> None:
        text = (
            "SIGNAL ID: #1998\n"
            "Target 1: 109600✅\n"
            "Target 2: 112300✅\n"
            "Target 3: 115000✅\n"
            "🔥38.8% Profit (4x)🔥"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "REPORT")
        assert msg.report is not None
        evt = msg.report.events[0]
        self.assertEqual(evt.event_type, "TP_HIT")
        self.assertEqual(evt.level, 3)

    def test_stop_hit_with_stop_price(self) -> None:
        text = (
            "SIGNAL ID: #1999\n"
            "Unfortunately, it broke down\n"
            "STOP LOSS: 3298\n"
            "🚫3.13% Loss (2x)🚫"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "REPORT")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.report is not None
        self.assertEqual(len(msg.report.events), 1)
        evt = msg.report.events[0]
        self.assertEqual(evt.event_type, "STOP_HIT")
        assert evt.result is not None
        self.assertEqual(evt.result.value, 3.13)
        self.assertEqual(evt.result.unit, "PERCENT")

    def test_loss_without_explicit_stop_hit(self) -> None:
        # Loss reported without stop price extracted — no U_STOP_HIT intent
        text = "SIGNAL ID: #2000\n🚫3.85% Loss (2x)🚫"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "REPORT")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.report is not None
        assert msg.report.reported_result is not None
        self.assertEqual(msg.report.reported_result.value, 3.85)
        self.assertEqual(msg.report.reported_result.unit, "PERCENT")

    def test_report_has_targeting_with_signal_id(self) -> None:
        text = (
            "SIGNAL ID: #1997\n"
            "Target 1: 109600✅\n"
            "🔥15% Profit (5x)🔥"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertIsNotNone(msg.targeting)
        assert msg.targeting is not None
        ref_types = {r.ref_type for r in msg.targeting.refs}
        self.assertIn("EXPLICIT_ID", ref_types)

    def test_report_missing_target_warning(self) -> None:
        text = "Target 1: 109600✅"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "REPORT")
        self.assertIn("trader_3_update_missing_target", msg.warnings)


class TestTrader3CanonicalUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Trader3ProfileParser()

    def test_close_full_manual(self) -> None:
        text = "SIGNAL ID: #2001\nClosed Manually\n🚫12% Loss (2x)🚫"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.update is not None
        self.assertEqual(len(msg.update.operations), 1)
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "CLOSE")
        assert op.close is not None
        self.assertEqual(op.close.close_scope, "FULL")

    def test_close_full_no_report_side_payload(self) -> None:
        # manual close with no tp_hit → UPDATE only, no report payload
        text = "SIGNAL ID: #2001\nClosed Manually\n🚫12% Loss (2x)🚫"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertIsNone(msg.report)

    def test_reenter_partial_no_entry_prices(self) -> None:
        text = "SIGNAL ID: #2001\nRe-Enter.\nSame Entry level ,Targets & SL"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARTIAL")
        assert msg.update is not None
        # No entry prices → operations list empty
        self.assertEqual(msg.update.operations, [])
        self.assertIn("trader_3_reenter_no_explicit_entry_prices", msg.warnings)

    def test_reenter_has_targeting(self) -> None:
        text = "SIGNAL ID: #2001\nRe-Enter.\nSame Entry level ,Targets & SL"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertIsNotNone(msg.targeting)
        assert msg.targeting is not None
        ref_types = {r.ref_type for r in msg.targeting.refs}
        self.assertIn("EXPLICIT_ID", ref_types)


class TestTrader3CanonicalMetadata(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Trader3ProfileParser()

    def test_parser_profile_field(self) -> None:
        text = "VIP MARKET UPDATE: $BTC"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.parser_profile, "trader_3")

    def test_schema_version(self) -> None:
        text = "VIP MARKET UPDATE: $BTC"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.schema_version, "1.0")

    def test_confidence_range(self) -> None:
        texts = [
            "VIP MARKET UPDATE: $BTC",
            (
                "SIGNAL ID: #1\nCOIN: $BTC/USDT\nDirection: LONG\n"
                "ENTRY: 100-110\nTARGETS: 120\nSTOP LOSS: 90"
            ),
        ]
        for text in texts:
            msg = self.parser.parse_canonical(text, _ctx(text=text))
            self.assertGreaterEqual(msg.confidence, 0.0)
            self.assertLessEqual(msg.confidence, 1.0)

    def test_raw_context_populated(self) -> None:
        text = "VIP MARKET UPDATE: $BTC"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.raw_context.raw_text, text)
        self.assertEqual(msg.raw_context.source_chat_id, "-1003")

    def test_model_is_valid_canonical_message(self) -> None:
        texts = [
            "VIP MARKET UPDATE: $BTC",
            (
                "SIGNAL ID: #1\nCOIN: $BTC/USDT\nDirection: LONG\n"
                "ENTRY: 100-110\nTARGETS: 120\nSTOP LOSS: 90"
            ),
            "SIGNAL ID: #1997\nTarget 1: 109600✅\n🔥15% Profit (5x)🔥",
            "SIGNAL ID: #2001\nClosed Manually\n🚫12% Loss (2x)🚫",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                msg = self.parser.parse_canonical(text, _ctx(text=text))
                # Pydantic validation passes (model was constructed without error)
                self.assertIsInstance(msg, CanonicalMessage)
                # Round-trip through JSON is lossless
                reloaded = CanonicalMessage.model_validate_json(
                    msg.model_dump_json(exclude_none=True)
                )
                self.assertEqual(reloaded.primary_class, msg.primary_class)


if __name__ == "__main__":
    unittest.main()
