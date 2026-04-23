"""Tests for TraderCProfileParser.parse_canonical() - CanonicalMessage v1 native output."""

from __future__ import annotations

import unittest

from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_c.profile import TraderCProfileParser


def _ctx(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_c",
        message_id=4100,
        reply_to_message_id=reply_to,
        channel_id="-1005",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


class TestTraderCCanonicalSignal(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderCProfileParser()

    def test_signal_range_parsed(self) -> None:
        text = (
            "$BTCUSDT - SHORT\n"
            "\u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 (88000-87900)\n"
            "Stop 88450. 1% \u0434\u0435\u043f\n"
            "tp1 87500\ntp2 87000"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.signal is not None
        self.assertEqual(msg.signal.symbol, "BTCUSDT")
        self.assertEqual(msg.signal.side, "SHORT")
        self.assertEqual(msg.signal.entry_structure, "RANGE")
        self.assertEqual(len(msg.signal.entries), 2)
        self.assertEqual(len(msg.signal.take_profits), 2)

    def test_signal_market_entry_builds_market_leg(self) -> None:
        text = (
            "$BTCUSDT - LONG\n"
            "\u0412\u0445\u043e\u0434 \u043f\u043e \u0440\u044b\u043d\u043a\u0443\n"
            "Stop 91800\n"
            "tp1 93200"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        assert msg.signal is not None
        self.assertEqual(msg.signal.entry_structure, "ONE_SHOT")
        self.assertEqual(len(msg.signal.entries), 1)
        self.assertEqual(msg.signal.entries[0].entry_type, "MARKET")


class TestTraderCCanonicalInfo(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderCProfileParser()

    def test_info_only_admin(self) -> None:
        text = (
            "\u0414\u0440\u0443\u0437\u044c\u044f, \u044d\u0442\u043e \u0441\u043d\u043e\u0432\u0430 #\u0430\u0434\u043c\u0438\u043d\n"
            "TP1 - 30%\nTP2 - 30%\nTP3 - 40%"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")
        self.assertIsNone(msg.signal)
        self.assertIsNone(msg.update)
        self.assertIsNone(msg.report)

    def test_unclassified_falls_to_info(self) -> None:
        text = "hello team"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "UNCLASSIFIED")


class TestTraderCCanonicalUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderCProfileParser()

    def test_update_close_full(self) -> None:
        text = "\u0417\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0440\u044b\u043d\u043a\u0443"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=8))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        close_ops = [op for op in msg.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        assert close_ops[0].close is not None
        self.assertEqual(close_ops[0].close.close_scope, "FULL")

    def test_update_close_partial_fraction(self) -> None:
        text = "\u0421\u043a\u0438\u043d\u0443\u043b \u0447\u0430\u0441\u0442\u044c \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c 0,0765 (30%)"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=7))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        close_ops = [op for op in msg.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        assert close_ops[0].close is not None
        self.assertEqual(close_ops[0].close.close_scope, "PARTIAL")
        self.assertAlmostEqual(close_ops[0].close.close_fraction or 0.0, 0.30, places=6)

    def test_update_set_stop_entry(self) -> None:
        text = "\u0412 \u0431\u0443 \u043f\u0435\u0440\u0435\u0432\u0435\u043b"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=4))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "SET_STOP")
        assert op.set_stop is not None
        self.assertEqual(op.set_stop.target_type, "ENTRY")

    def test_update_set_stop_price(self) -> None:
        text = "\u0421\u0442\u043e\u043f \u043f\u0435\u0440\u0435\u043d\u043e\u0441\u0438\u043c \u043d\u0430 88650 \u0432 -0,5 RR"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=13))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "SET_STOP")
        assert op.set_stop is not None
        self.assertEqual(op.set_stop.target_type, "PRICE")
        self.assertEqual(op.set_stop.value, 88650.0)

    def test_update_modify_targets_update_one(self) -> None:
        text = "\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f - \u0422\u043f2 88150"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=12))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        ops = [op for op in msg.update.operations if op.op_type == "MODIFY_TARGETS"]
        self.assertEqual(len(ops), 1)
        assert ops[0].modify_targets is not None
        self.assertEqual(ops[0].modify_targets.mode, "UPDATE_ONE")
        self.assertEqual(ops[0].modify_targets.target_tp_level, 2)

    def test_update_cancel_pending(self) -> None:
        text = "\u041d\u0435 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u043e"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=9))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        ops = [op for op in msg.update.operations if op.op_type == "CANCEL_PENDING"]
        self.assertEqual(len(ops), 1)

    def test_update_remove_pending_entry(self) -> None:
        text = "\u0414\u043e\u043b\u0438\u0432\u043a\u0443 \u0443\u0431\u0440\u0430\u043b"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=10))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        ops = [op for op in msg.update.operations if op.op_type == "CANCEL_PENDING"]
        self.assertEqual(len(ops), 1)
        assert ops[0].cancel_pending is not None
        self.assertEqual(ops[0].cancel_pending.cancel_scope, "REMOVE_PENDING_ENTRY")

    def test_symbol_only_targeting_is_symbol_match(self) -> None:
        text = (
            "Btcusdt SHORT\n\n"
            "\u0434\u043e\u043b\u0438\u0432\u0430\u044e \u0447\u0430\u0441\u0442\u044c \u0448\u043e\u0440\u0442\u0430:87800-900\n"
            "\u0447\u0442\u043e\u0431\u044b \u0441\u0440\u0435\u0434\u043d\u044f\u044f \u043e\u043a\u0430\u0437\u0430\u043b\u0430\u0441\u044c \u0432 88500 "
            "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARSED")
        self.assertIn("trader_c_update_weak_target_only", msg.warnings)
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.strategy, "SYMBOL_MATCH")


class TestTraderCCanonicalReport(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderCProfileParser()

    def test_report_tp_hit(self) -> None:
        text = "\u041f\u043e\u0437\u0438\u0446\u0438\u044f \u0437\u0430\u043a\u0440\u044b\u0442\u0430 \u043f\u043e \u0442\u0435\u0439\u043a\u0443. \u041f\u043e\u0437\u0434\u0440\u0430\u0432\u043b\u044f\u044e!"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=3))
        self.assertEqual(msg.primary_class, "REPORT")
        assert msg.report is not None
        self.assertEqual(len(msg.report.events), 1)
        self.assertEqual(msg.report.events[0].event_type, "TP_HIT")

    def test_report_activation(self) -> None:
        text = "\u041f\u0435\u0440\u0432\u0430\u044f \u043b\u0438\u043c\u0438\u0442\u043a\u0430 \u0441\u0440\u0430\u0431\u043e\u0442\u0430\u043b\u0430"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=1))
        self.assertEqual(msg.primary_class, "REPORT")
        assert msg.report is not None
        self.assertEqual(msg.report.events[0].event_type, "ENTRY_FILLED")

    def test_composite_update_and_report(self) -> None:
        text = (
            "90500 \u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0437\u0430\u043a\u0440\u044b\u0442\u0430 \u043f\u043e \u0442\u0435\u0439\u043a\u0443. "
            "\u041f\u043e\u0437\u0434\u0440\u0430\u0432\u043b\u044f\u044e! "
            "\u0417\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043f\u043e \u0440\u044b\u043d\u043a\u0443"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=100))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        assert msg.report is not None
        event_types = {evt.event_type for evt in msg.report.events}
        self.assertIn("TP_HIT", event_types)


class TestTraderCCanonicalMetadata(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderCProfileParser()

    def test_model_is_valid_canonical_message(self) -> None:
        texts = [
            "hello team",
            (
                "$BTCUSDT - SHORT\n"
                "\u0412\u0445\u043e\u0434 \u0441 \u0442\u0435\u043a\u0443\u0449\u0438\u0445 (88000-87900)\n"
                "Stop 88450. 1% \u0434\u0435\u043f\n"
                "tp1 87500"
            ),
            "\u0412 \u0431\u0443 \u043f\u0435\u0440\u0435\u0432\u0435\u043b",
            "\u041f\u0435\u0440\u0432\u0430\u044f \u043b\u0438\u043c\u0438\u0442\u043a\u0430 \u0441\u0440\u0430\u0431\u043e\u0442\u0430\u043b\u0430",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=777))
                self.assertIsInstance(msg, CanonicalMessage)
                reloaded = CanonicalMessage.model_validate_json(
                    msg.model_dump_json(exclude_none=True)
                )
                self.assertEqual(reloaded.primary_class, msg.primary_class)


if __name__ == "__main__":
    unittest.main()
