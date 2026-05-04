"""Tests for TraderBProfileParser.parse_canonical() — CanonicalMessage v1 native output."""

from __future__ import annotations

import unittest

from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser


def _ctx(*, text: str, reply_to: int | None = None, links: list[str] | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_b",
        message_id=2000,
        reply_to_message_id=reply_to,
        channel_id="-1002",
        raw_text=text,
        extracted_links=links or [],
        hashtags=[],
    )


class TestTraderBCanonicalNewSignal(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def _limit_signal(self) -> str:
        return (
            "$SOLUSDT - Лонг\n"
            "Вход: 125\n"
            "Тейк профит: 130\n"
            "ТП2: 140\n"
            "Стоп лосс: 119"
        )

    def test_signal_primary_class(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        self.assertEqual(msg.primary_class, "SIGNAL")
        self.assertEqual(msg.parse_status, "PARSED")

    def test_signal_payload_present(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        self.assertIsNotNone(msg.signal)
        self.assertIsNone(msg.update)
        self.assertIsNone(msg.report)

    def test_signal_symbol_and_side(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        assert msg.signal is not None
        self.assertEqual(msg.signal.symbol, "SOLUSDT")
        self.assertEqual(msg.signal.side, "LONG")

    def test_signal_limit_entry_one_shot(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        assert msg.signal is not None
        self.assertEqual(msg.signal.entry_structure, "ONE_SHOT")
        self.assertEqual(len(msg.signal.entries), 1)
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "LIMIT")
        assert leg.price is not None
        self.assertEqual(leg.price.value, 125.0)
        self.assertEqual(leg.role, "PRIMARY")

    def test_signal_multiple_take_profits(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        assert msg.signal is not None
        self.assertEqual(len(msg.signal.take_profits), 2)
        self.assertEqual(msg.signal.take_profits[0].price.value, 130.0)
        self.assertEqual(msg.signal.take_profits[1].price.value, 140.0)

    def test_signal_stop_loss(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        assert msg.signal is not None
        assert msg.signal.stop_loss is not None
        assert msg.signal.stop_loss.price is not None
        self.assertEqual(msg.signal.stop_loss.price.value, 119.0)

    def test_signal_completeness_complete(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        assert msg.signal is not None
        self.assertEqual(msg.signal.completeness, "COMPLETE")
        self.assertEqual(msg.signal.missing_fields, [])

    def test_signal_no_targeting(self) -> None:
        msg = self.parser.parse_canonical(self._limit_signal(), _ctx(text=self._limit_signal()))
        self.assertIsNone(msg.targeting)

    def test_signal_market_entry_with_price(self) -> None:
        text = (
            "$ARBUSDT - Лонг (Сделка на споте)\n"
            "Вход: 1.25 (+- по текущим)\n"
            "Тейк профит: 1.40\n"
            "Стоп лосс: 1.10\n"
            "Риск на сделку 2%"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        assert msg.signal is not None
        self.assertEqual(msg.signal.entry_structure, "ONE_SHOT")
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "MARKET")
        assert leg.price is not None
        self.assertEqual(leg.price.value, 1.25)

    def test_signal_market_entry_without_price(self) -> None:
        text = (
            "$COAIUSDT - Шорт (вход с текущих)\n"
            "Вход с текущих\n"
            "Тейк профит: 0.8627\n"
            "Стоп лосс: 1.2769"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        assert msg.signal is not None
        self.assertEqual(msg.signal.entry_structure, "ONE_SHOT")
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "MARKET")
        self.assertIsNone(leg.price)
        self.assertEqual(msg.signal.completeness, "COMPLETE")

    def test_signal_short_side(self) -> None:
        text = (
            "$FARTCOINUSDT.P - Шорт (вход с текущих)\n"
            "Вход с текущих: 0.3053\n"
            "Тейк профит: 0.2737\n"
            "Стоп лосс: 0.3307"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(msg.signal.side, "SHORT")


class TestTraderBCanonicalSignalPartial(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_setup_incomplete_is_signal_partial(self) -> None:
        # Has symbol, side, entry, stop but no TPs — SETUP_INCOMPLETE
        text = "$SOLUSDT - Лонг\nВход: 125\nСтоп лосс: 119"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        self.assertEqual(msg.parse_status, "PARTIAL")
        assert msg.signal is not None
        self.assertEqual(msg.signal.completeness, "INCOMPLETE")
        self.assertIn("take_profits", msg.signal.missing_fields)


class TestTraderBCanonicalInfo(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_info_only_commentary(self) -> None:
        text = "Небольшие изменения!"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")
        self.assertIsNone(msg.signal)
        self.assertIsNone(msg.update)
        self.assertIsNone(msg.report)

    def test_info_only_passive_stop_closed(self) -> None:
        text = "К сожалению стоп лосс, рынок прям медвежий"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=500))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")

    def test_info_only_passive_breakeven(self) -> None:
        text = "Закрыта в БУ"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=501))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "PARSED")

    def test_unclassified_is_info_unclassified(self) -> None:
        text = "БУ"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=502))
        self.assertEqual(msg.primary_class, "INFO")
        self.assertEqual(msg.parse_status, "UNCLASSIFIED")


class TestTraderBCanonicalUpdateSetStop(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_move_stop_to_be_entry(self) -> None:
        text = "Все пока идет по плану, стоп лосс переносим в БУ"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=503))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.update is not None
        self.assertEqual(len(msg.update.operations), 1)
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "SET_STOP")
        assert op.set_stop is not None
        self.assertEqual(op.set_stop.target_type, "ENTRY")

    def test_move_stop_numeric_price(self) -> None:
        text = "Стоп лосс переносим на 1.553"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=504))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "SET_STOP")
        assert op.set_stop is not None
        self.assertEqual(op.set_stop.target_type, "PRICE")
        self.assertEqual(op.set_stop.value, 1.553)

    def test_move_stop_to_be_with_explicit_price(self) -> None:
        # Both U_MOVE_STOP_TO_BE and U_MOVE_STOP — price takes precedence
        text = "Стоп лосс переносим в БУ на уровень 2941"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=505))
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.set_stop.target_type, "PRICE")  # type: ignore[union-attr]
        self.assertEqual(op.set_stop.value, 2941.0)  # type: ignore[union-attr]

    def test_structural_stop_reference_is_partial(self) -> None:
        text = "Переносим за указанный минимум"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=506))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARTIAL")
        assert msg.update is not None
        self.assertEqual(msg.update.operations, [])
        self.assertTrue(any("structural_reference" in w or "move_stop" in w for w in msg.warnings))


class TestTraderBCanonicalUpdateClose(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_close_full(self) -> None:
        text = "Закрыта вручную"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=507))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "CLOSE")
        assert op.close is not None
        self.assertEqual(op.close.close_scope, "FULL")

    def test_close_global_all_all(self) -> None:
        text = "Закрыть все позиции по текущим"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "CLOSE")
        assert op.close is not None
        self.assertEqual(op.close.close_scope, "ALL_ALL")

    def test_close_global_targeting_scope(self) -> None:
        text = "Закрыть все позиции по текущим"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.strategy, "GLOBAL_SCOPE")
        self.assertEqual(msg.targeting.scope.applies_to_all, True)

    def test_close_longs_global_scope(self) -> None:
        text = "Закрываем все лонги"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.scope.kind, "PORTFOLIO_SIDE")
        self.assertEqual(msg.targeting.scope.side_filter, "LONG")


class TestTraderBCanonicalUpdateCancel(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_cancel_pending_orders(self) -> None:
        text = "Тут не актуально, лонгов открытых нет"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=508))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "CANCEL_PENDING")
        assert op.cancel_pending is not None
        self.assertEqual(op.cancel_pending.cancel_scope, "TARGETED")


class TestTraderBCanonicalReport(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_stop_hit_is_report(self) -> None:
        text = "Закрылись по стопу"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=509))
        self.assertEqual(msg.primary_class, "REPORT")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.report is not None
        self.assertEqual(len(msg.report.events), 1)
        self.assertEqual(msg.report.events[0].event_type, "STOP_HIT")
        self.assertIsNone(msg.update)

    def test_stop_hit_with_result_percent(self) -> None:
        text = "закрыта по стоп лоссу (-1%)"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=510))
        self.assertEqual(msg.primary_class, "REPORT")
        assert msg.report is not None
        evt = msg.report.events[0]
        self.assertEqual(evt.event_type, "STOP_HIT")
        assert evt.result is not None
        self.assertEqual(evt.result.unit, "PERCENT")

    def test_tp_hit_is_report(self) -> None:
        text = "Поздравляю с профитом"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=511))
        self.assertEqual(msg.primary_class, "REPORT")
        assert msg.report is not None
        self.assertEqual(msg.report.events[0].event_type, "TP_HIT")
        self.assertIsNone(msg.update)

    def test_final_result_event(self) -> None:
        # "реализована" triggers UPDATE classification; "итог" fires U_REPORT_FINAL_RESULT
        text = "Сделка полностью реализована, итог +5%"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=512))
        assert msg.report is not None
        event_types = {e.event_type for e in msg.report.events}
        self.assertTrue(event_types & {"FINAL_RESULT", "TP_HIT"})


class TestTraderBCanonicalComposite(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_close_full_plus_tp_hit_is_composite(self) -> None:
        # "Закрываю в +" → U_CLOSE_FULL + U_TP_HIT → UPDATE/CLOSE + REPORT/TP_HIT
        text = "Закрываю в +"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=513))
        self.assertEqual(msg.primary_class, "UPDATE")
        self.assertEqual(msg.parse_status, "PARSED")
        assert msg.update is not None
        assert msg.report is not None
        close_ops = [op for op in msg.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        tp_events = [e for e in msg.report.events if e.event_type == "TP_HIT"]
        self.assertEqual(len(tp_events), 1)

    def test_stop_hit_no_close_op_in_update(self) -> None:
        # Stop hit: report only, no separate CLOSE update op
        text = "Обидный стоп (-1%)"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=514))
        self.assertIsNone(msg.update)
        self.assertEqual(msg.primary_class, "REPORT")

    def test_tp_hit_with_final_result_report_events(self) -> None:
        text = "Сделка полностью реализована в +2% по текущим, поздравляю с профитом"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=515))
        # Either REPORT or composite UPDATE depending on close scope
        assert msg.report is not None
        event_types = {e.event_type for e in msg.report.events}
        self.assertIn("TP_HIT", event_types)


class TestTraderBCanonicalTargeting(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_reply_targeting(self) -> None:
        text = "Стоп лосс переносим в БУ"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=520))
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.targeted, True)
        ref_types = {r.ref_type for r in msg.targeting.refs}
        self.assertIn("REPLY", ref_types)

    def test_telegram_link_targeting(self) -> None:
        text = "Закрыта вручную https://t.me/c/123/456"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.targeting is not None
        ref_types = {r.ref_type for r in msg.targeting.refs}
        self.assertIn("TELEGRAM_LINK", ref_types)
        self.assertIn("MESSAGE_ID", ref_types)

    def test_no_targeting_for_new_signal(self) -> None:
        text = "$SOLUSDT - Лонг\nВход: 125\nТейк профит: 130\nСтоп лосс: 119"
        msg = self.parser.parse_canonical(text, _ctx(text=text, reply_to=521))
        self.assertIsNone(msg.targeting)

    def test_no_targeting_when_no_refs(self) -> None:
        text = "Закрыта вручную"  # no reply, no link, no symbol
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertIsNone(msg.targeting)

    def test_global_all_shorts_targeting(self) -> None:
        text = "Закрываем все шорты"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.targeting is not None
        self.assertEqual(msg.targeting.strategy, "GLOBAL_SCOPE")
        self.assertEqual(msg.targeting.scope.kind, "PORTFOLIO_SIDE")
        self.assertEqual(msg.targeting.scope.side_filter, "SHORT")


class TestTraderBCanonicalMetadata(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderBProfileParser()

    def test_parser_profile(self) -> None:
        text = "Небольшие изменения"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.parser_profile, "trader_b")

    def test_schema_version(self) -> None:
        text = "Небольшие изменения"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.schema_version, "1.0")

    def test_raw_context_populated(self) -> None:
        text = "Небольшие изменения"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.raw_context.raw_text, text)
        self.assertEqual(msg.raw_context.source_chat_id, "-1002")

    def test_confidence_in_range(self) -> None:
        texts = [
            "Небольшие изменения",
            "$SOLUSDT - Лонг\nВход: 125\nТейк профит: 130\nСтоп лосс: 119",
            "Закрылись по стопу",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                msg = self.parser.parse_canonical(text, _ctx(text=text))
                self.assertGreaterEqual(msg.confidence, 0.0)
                self.assertLessEqual(msg.confidence, 1.0)

    def test_model_is_valid_canonical_message(self) -> None:
        texts = [
            "Небольшие изменения",
            "$SOLUSDT - Лонг\nВход: 125\nТейк профит: 130\nСтоп лосс: 119",
            "Стоп лосс переносим в БУ",
            "Закрылись по стопу",
            "Поздравляю с профитом",
            "Тут не актуально",
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                ctx = _ctx(text=text, reply_to=999)
                msg = self.parser.parse_canonical(text, ctx)
                self.assertIsInstance(msg, CanonicalMessage)
                reloaded = CanonicalMessage.model_validate_json(
                    msg.model_dump_json(exclude_none=True)
                )
                self.assertEqual(reloaded.primary_class, msg.primary_class)

    def test_multi_link_move_stop_to_be(self) -> None:
        text = (
            "https://t.me/c/3171748254/1001 "
            "https://t.me/c/3171748254/1002 "
            "По обоим сделкам - стопы переносим в БУ"
        )
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "UPDATE")
        assert msg.update is not None
        op = msg.update.operations[0]
        self.assertEqual(op.op_type, "SET_STOP")
        assert msg.targeting is not None
        link_refs = [r for r in msg.targeting.refs if r.ref_type == "TELEGRAM_LINK"]
        self.assertGreaterEqual(len(link_refs), 2)


if __name__ == "__main__":
    unittest.main()
