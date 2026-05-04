"""Tests for TraderAProfileParser.parse_canonical() - CanonicalMessage v1 native output."""

from __future__ import annotations

import unittest

from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


def _ctx(
    *,
    text: str,
    reply_to: int | None = None,
    links: list[str] | None = None,
    reply_raw_text: str | None = None,
) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=5100,
        reply_to_message_id=reply_to,
        channel_id="-1001",
        raw_text=text,
        reply_raw_text=reply_raw_text,
        extracted_links=links or [],
        hashtags=[],
    )


class TestTraderACanonicalSignal(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_signal_complete(self) -> None:
        text = "BTCUSDT long entry 62000 sl 61000 tp1 63000 tp2 64000"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.signal is not None
        self.assertEqual(msg.signal.symbol, "BTCUSDT")
        self.assertEqual(msg.signal.side, "LONG")
        self.assertEqual(msg.signal.entry_structure, "ONE_SHOT")
        self.assertEqual(len(msg.signal.entries), 1)
        self.assertEqual(msg.signal.entries[0].entry_type, "LIMIT")
        assert msg.signal.stop_loss is not None
        assert msg.signal.stop_loss.price is not None
        self.assertEqual(msg.signal.stop_loss.price.value, 61000.0)
        self.assertEqual(len(msg.signal.take_profits), 2)

    def test_setup_incomplete_emits_partial_signal(self) -> None:
        text = "ETHUSDT long entry only, sl later"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        self.assertEqual(msg.parse_status, "PARTIAL")
        assert msg.signal is not None
        self.assertEqual(msg.signal.completeness, "INCOMPLETE")


class TestTraderACanonicalUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_update_set_stop_entry(self) -> None:
        text = "move stop to be now"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=501))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.update is not None
        self.assertEqual(len(msg.update.operations), 1)
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "SET_STOP")
        assert op.set_stop is not None
        self.assertEqual(op.set_stop.target_type, "ENTRY")
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.strategy, "REPLY_OR_LINK")

    def test_update_close_partial_fraction(self) -> None:
        text = "partial close 50%"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=603))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        close_ops = [op for op in msg.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        assert close_ops[0].close is not None
        self.assertEqual(close_ops[0].close.close_scope, "PARTIAL")
        self.assertAlmostEqual(close_ops[0].close.close_fraction or 0.0, 0.5, places=6)

    def test_update_cancel_pending_targeted(self) -> None:
        text = "cancel pending limits https://t.me/c/77/601"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        cancel_ops = [op for op in msg.update.operations if op.op_type == "CANCEL_PENDING"]
        self.assertEqual(len(cancel_ops), 1)
        assert cancel_ops[0].cancel_pending is not None
        self.assertEqual(cancel_ops[0].cancel_pending.cancel_scope, "TARGETED")
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.strategy, "REPLY_OR_LINK")

    def test_update_close_global_all_shorts(self) -> None:
        text = "зафиксировать все шорты"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        close_ops = [op for op in msg.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        assert close_ops[0].close is not None
        self.assertEqual(close_ops[0].close.close_scope, "ALL_SHORTS")
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.strategy, "GLOBAL_SCOPE")
        self.assertEqual(msg.targeting.scope.kind, "PORTFOLIO_SIDE")
        self.assertEqual(msg.targeting.scope.side_filter, "SHORT")


    def test_reply_closed_in_be_with_parent_signal_stays_report_exit_be(self) -> None:
        text = "\u0437\u0430\u043a\u0440\u044b\u043b\u0430\u0441\u044c \u0432 \u0431\u0443"
        parent_text = "BTCUSDT long entry 62000 sl 61000 tp1 63000"
        msg = self.parser.parse_canonical(
            text,
            _ctx(text=text, reply_to=701, reply_raw_text=parent_text),
        )
        self.assertEqual(msg.primary_class, "REPORT")
        self.assertEqual(msg.parse_status, "PARSED")
        self.assertEqual(msg.intents, ["EXIT_BE"])
        self.assertEqual(msg.primary_intent, "EXIT_BE")
        self.assertIn("semantic_resolver", msg.diagnostics)
        self.assertEqual(msg.diagnostics["semantic_resolver"]["final_intents"], ["EXIT_BE"])
        self.assertEqual(msg.warnings, [])
        assert msg.report is not None
        self.assertEqual([event.event_type for event in msg.report.events], ["BREAKEVEN_EXIT"])

    def test_reply_closed_in_be_without_parent_history_degrades_to_info(self) -> None:
        text = "\u0437\u0430\u043a\u0440\u044b\u043b\u0430\u0441\u044c \u0432 \u0431\u0443"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=702))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")
        self.assertEqual(msg.intents, ["INFO_ONLY"])
        self.assertEqual(msg.primary_intent, "INFO_ONLY")
        self.assertIn("semantic_resolver", msg.diagnostics)
        self.assertEqual(msg.diagnostics["semantic_resolver"]["final_intents"], ["INFO_ONLY"])
        self.assertIsNone(msg.update)
        self.assertIsNone(msg.report)

    def test_update_without_target_degrades_to_info_via_semantic_resolver(self) -> None:
        text = "\u0441\u0442\u043e\u043f \u043d\u0430 \u0442\u043e\u0447\u043a\u0443 \u0432\u0445\u043e\u0434\u0430"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")
        self.assertEqual(msg.intents, ["INFO_ONLY"])
        self.assertEqual(msg.primary_intent, "INFO_ONLY")
        self.assertIn("trader_a_update_missing_target", msg.warnings)
        self.assertIn("semantic_resolver", msg.diagnostics)
        self.assertEqual(msg.diagnostics["semantic_resolver"]["final_intents"], ["INFO_ONLY"])

    def test_stop_hit_and_close_full_keep_both_and_prefer_stop_hit(self) -> None:
        text = "\u0441\u0442\u043e\u043f, \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e \u043e\u0441\u0442\u0430\u0442\u043e\u043a \u043f\u043e \u0442\u0435\u043a\u0443\u0449\u0438\u043c"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=703))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.intents, ["CLOSE_FULL"])
        self.assertEqual(msg.primary_intent, "CLOSE_FULL")
        self.assertIn("semantic_resolver", msg.diagnostics)
        self.assertEqual(
            msg.diagnostics["semantic_resolver"]["final_intents"],
            ["CLOSE_FULL"],
        )
        assert msg.update is not None
        close_ops = [op for op in msg.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)


class TestTraderACanonicalReportAndInfo(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_report_final_result_from_r_multiples(self) -> None:
        text = "Final result BTCUSDT - 1.2R ETHUSDT - -0.3R"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "REPORT")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.report is not None
        event_types = {event.event_type for event in msg.report.events}
        self.assertIn("FINAL_RESULT", event_types)
        assert msg.report.reported_result is not None
        self.assertEqual(msg.report.reported_result.unit, "R")
        self.assertEqual(msg.report.reported_result.value, 1.2)

    def test_composite_update_and_report(self) -> None:
        text = (
            "хочу зафиксировать некоторые монеты, фиксация 100% по текущим отметкам\n"
            "https://t.me/c/3171748254/2361\n"
            "результаты в RR дополняю этот пост  fart - 0.38R"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.update is not None
        assert msg.report is not None
        close_ops = [op for op in msg.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        report_types = {event.event_type for event in msg.report.events}
        self.assertIn("FINAL_RESULT", report_types)

    def test_unclassified_falls_back_to_info(self) -> None:
        text = "good morning everyone"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "UNCLASSIFIED")


class TestTraderACanonicalMetadata(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    def test_model_roundtrip_validation(self) -> None:
        texts = [
            "BTCUSDT long entry 62000 sl 61000 tp1 63000",
            "move stop to be now",
            "Final result BTCUSDT - 1.2R",
            "good morning everyone",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=999))
                self.assertIsInstance(msg, CanonicalMessage)
                reloaded = CanonicalMessage.model_validate_json(
                    msg.model_dump_json(exclude_none=True)
                )
                self.assertEqual(reloaded.primary_class, msg.primary_class)


if __name__ == "__main__":
    unittest.main()
