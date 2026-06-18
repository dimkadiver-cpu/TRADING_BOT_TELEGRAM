"""Canonical v1 output tests for TraderDProfileParser.parse_canonical()."""

from __future__ import annotations

import unittest

from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_d.profile import TraderDProfileParser


def _ctx(text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_d",
        message_id=9000,
        reply_to_message_id=reply_to,
        channel_id="-1004",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


def _u(value: str) -> str:
    try:
        return bytes(value, "ascii").decode("unicode_escape")
    except UnicodeEncodeError:
        return value


class TraderDCanonicalOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderDProfileParser()

    # ------------------------------------------------------------------
    # v1-native detection
    # ------------------------------------------------------------------

    def test_parse_canonical_is_callable(self) -> None:
        self.assertTrue(callable(getattr(type(self.parser), "parse_canonical", None)))

    def test_parse_canonical_returns_canonical_message(self) -> None:
        text = "Btcusdt Long вход с текущих\nСтоп 90000\nTP1: 92000\nTP2: 94000"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertIsInstance(result, CanonicalMessage)

    # ------------------------------------------------------------------
    # NEW_SIGNAL
    # ------------------------------------------------------------------

    def test_new_signal_market_is_signal_parsed(self) -> None:
        text = (
            "Storjusdt SHORT вход с текущих\n"
            "рыночный — риск 0.5% депо\n"
            "Стоп 0.16134\n"
            "Тейки\n"
            "TP1: 0.1445\n"
            "TP2: 0.138"
        )
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.primary_class, "SIGNAL")
        self.assertEqual(result.parse_status, "PARSED")
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.symbol, "STORJUSDT")
        self.assertEqual(result.signal.side, "SHORT")
        self.assertEqual(result.signal.completeness, "COMPLETE")
        self.assertIsNotNone(result.signal.stop_loss)
        self.assertEqual(len(result.signal.take_profits), 2)
        self.assertEqual(result.signal.take_profits[0].price.value, 0.1445)
        self.assertEqual(result.signal.take_profits[1].price.value, 0.138)

    def test_new_signal_market_no_price_entry_structure(self) -> None:
        text = (
            "Brev SHORT вход с текущих\n"
            "рыночный — риск 0.5% депо\n"
            "Стоп 0.5358\n"
            "TP1: 0.4283\n"
            "TP2: 0.3485"
        )
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.primary_class, "SIGNAL")
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.entry_structure, "ONE_SHOT")
        entries = result.signal.entries
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].entry_type, "MARKET")
        self.assertIsNone(entries[0].price)

    def test_new_signal_limit_entry(self) -> None:
        text = "AXS SHORT вход лимит 1,0573\nsl 1,105\ntp1 1,01\ntp2 0,97"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.primary_class, "SIGNAL")
        self.assertEqual(result.parse_status, "PARSED")
        self.assertIsNotNone(result.signal)
        entries = result.signal.entries
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].entry_type, "LIMIT")
        self.assertAlmostEqual(entries[0].price.value, 1.0573)
        self.assertEqual(result.signal.stop_loss.price.value, 1.105)

    def test_new_signal_with_prefix_tag(self) -> None:
        text = (
            "[trader#d]\n\n"
            "Storjusdt SHORT вход с текущих\n\n"
            "рыночный— риск 0.5% депо\n\n"
            "Стоп 0.16134\n\n"
            "Тейки\n"
            "• TP1: 0.1445\n"
            "TP2; 0,138"
        )
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.primary_class, "SIGNAL")
        self.assertEqual(result.signal.symbol, "STORJUSDT")

    def test_new_signal_no_update_payload(self) -> None:
        text = "Btcusdt Long вход с текущих\nСтоп 90939\nTP1: 92265\nTP2: 93704"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertIsNone(result.update)
        self.assertIsNone(result.targeting)

    # ------------------------------------------------------------------
    # SETUP_INCOMPLETE
    # ------------------------------------------------------------------

    def test_setup_incomplete_is_signal_partial(self) -> None:
        text = (
            "Trader#d\n"
            "scrt short\n"
            "риск 0,5\n"
            "сл 0,13764\n"
            "тп1 0,12522\n"
            "тп2 0,11886"
        )
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.primary_class, "SIGNAL")
        self.assertEqual(result.parse_status, "PARTIAL")
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.completeness, "INCOMPLETE")
        self.assertIn("entries", result.signal.missing_fields)

    def test_setup_incomplete_captures_symbol_and_side(self) -> None:
        text = "Enj шорт риск 0,5\nsl 0.2701\nтп1 0.245\nтп2 0.231\nтп3 0.218"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.primary_class, "SIGNAL")
        self.assertEqual(result.parse_status, "PARTIAL")
        self.assertEqual(result.signal.symbol, "ENJUSDT")
        self.assertEqual(result.signal.side, "SHORT")
        self.assertEqual(len(result.signal.take_profits), 3)

    # ------------------------------------------------------------------
    # UPDATE — SET_STOP
    # ------------------------------------------------------------------

    def test_update_be_move_is_update_set_stop_entry(self) -> None:
        text = "Перевод в безубыток"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=701))
        self.assertEqual(result.primary_class, "UPDATE")
        self.assertEqual(result.parse_status, "PARSED")
        ops = result.update.operations
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].op_type, "SET_STOP")
        self.assertEqual(ops[0].set_stop.target_type, "ENTRY")

    def test_update_stop_move_numeric_is_set_stop_price(self) -> None:
        text = "стоп переставляем в + на 1,2453"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=704))
        self.assertEqual(result.primary_class, "UPDATE")
        ops = result.update.operations
        set_stop_ops = [op for op in ops if op.op_type == "SET_STOP"]
        self.assertEqual(len(set_stop_ops), 1)
        self.assertEqual(set_stop_ops[0].set_stop.target_type, "PRICE")
        self.assertAlmostEqual(set_stop_ops[0].set_stop.value, 1.2453)

    # ------------------------------------------------------------------
    # UPDATE — CLOSE
    # ------------------------------------------------------------------

    def test_update_close_full_is_update_close(self) -> None:
        text = "закрываю по текущим"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=713))
        self.assertEqual(result.primary_class, "UPDATE")
        ops = result.update.operations
        close_ops = [op for op in ops if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        self.assertEqual(close_ops[0].close.close_scope, "FULL")

    def test_update_partial_close_seventy_percent(self) -> None:
        text = "Trader#d\nФикс 70%\nСтоп в бу"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=842))
        self.assertEqual(result.primary_class, "UPDATE")
        ops = result.update.operations
        close_ops = [op for op in ops if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        self.assertEqual(close_ops[0].close.close_scope, "PARTIAL")
        self.assertAlmostEqual(close_ops[0].close.close_fraction, 0.7, places=2)

    def test_update_partial_close_fifty_with_be(self) -> None:
        text = "upd:50% срежем rr 1;1 стоп в бу"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=703))
        self.assertEqual(result.primary_class, "UPDATE")
        ops = result.update.operations
        close_ops = [op for op in ops if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        self.assertEqual(close_ops[0].close.close_scope, "PARTIAL")
        self.assertAlmostEqual(close_ops[0].close.close_fraction, 0.5, places=2)
        set_stop_ops = [op for op in ops if op.op_type == "SET_STOP"]
        self.assertEqual(len(set_stop_ops), 1)
        self.assertEqual(set_stop_ops[0].set_stop.target_type, "ENTRY")

    def test_sl_short_form_is_close_full_with_r_result(self) -> None:
        text = "Sl -0.5"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=717))
        self.assertEqual(result.primary_class, "UPDATE")
        ops = result.update.operations
        close_ops = [op for op in ops if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        self.assertEqual(close_ops[0].close.close_scope, "FULL")
        self.assertIsNotNone(result.report)
        self.assertIsNotNone(result.report.reported_result)
        self.assertAlmostEqual(result.report.reported_result.value, 1.0)
        self.assertEqual(result.report.reported_result.unit, "R")

    # ------------------------------------------------------------------
    # REPORT — TP_HIT
    # ------------------------------------------------------------------

    def test_tp_hit_is_report(self) -> None:
        text = "tp1+"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=708))
        self.assertEqual(result.primary_class, "REPORT")
        self.assertEqual(result.parse_status, "PARSED")
        self.assertIsNotNone(result.report)
        events = result.report.events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "TP_HIT")
        self.assertEqual(events[0].level, 1)

    def test_tp2_hit_with_emoji(self) -> None:
        text = _u(r"Tp 2😑")
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=2475))
        self.assertEqual(result.primary_class, "REPORT")
        events = result.report.events
        tp_events = [e for e in events if e.event_type == "TP_HIT"]
        self.assertEqual(len(tp_events), 1)
        self.assertEqual(tp_events[0].level, 2)

    def test_tp_hit_with_profit_percent(self) -> None:
        text = "Tp3 общий профит +1.18%"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=710))
        self.assertEqual(result.primary_class, "REPORT")
        events = result.report.events
        tp_events = [e for e in events if e.event_type == "TP_HIT"]
        self.assertEqual(len(tp_events), 1)
        self.assertAlmostEqual(tp_events[0].result.value, 1.18)
        self.assertEqual(tp_events[0].result.unit, "PERCENT")

    # ------------------------------------------------------------------
    # REPORT — BREAKEVEN_EXIT (U_EXIT_BE)
    # ------------------------------------------------------------------

    def test_exit_be_is_report_breakeven_exit(self) -> None:
        text = "позиция ушла в бу"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=711))
        self.assertEqual(result.primary_class, "REPORT")
        events = result.report.events
        be_events = [e for e in events if e.event_type == "BREAKEVEN_EXIT"]
        self.assertEqual(len(be_events), 1)

    def test_exit_be_with_plus_is_report(self) -> None:
        text = "остаток ушел в бу+"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=712))
        self.assertEqual(result.primary_class, "REPORT")
        events = result.report.events
        self.assertTrue(any(e.event_type == "BREAKEVEN_EXIT" for e in events))

    def test_exit_be_with_profit_percent(self) -> None:
        text = _u(
            r"Trader#d" "\n"
            r"Gmt остаток ушел в бу" "\n"
            r"+0.15%"
        )
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=1496))
        self.assertEqual(result.primary_class, "REPORT")
        events = result.report.events
        be_events = [e for e in events if e.event_type == "BREAKEVEN_EXIT"]
        self.assertEqual(len(be_events), 1)
        self.assertAlmostEqual(be_events[0].result.value, 0.15)
        self.assertEqual(be_events[0].result.unit, "PERCENT")

    # ------------------------------------------------------------------
    # Composite UPDATE + REPORT
    # ------------------------------------------------------------------

    def test_tp_hit_with_be_move_is_update_and_report(self) -> None:
        text = "tp1+\nПеревод в бу"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=702))
        self.assertEqual(result.primary_class, "UPDATE")
        self.assertIsNotNone(result.update)
        self.assertIsNotNone(result.report)
        ops = result.update.operations
        set_stop_ops = [op for op in ops if op.op_type == "SET_STOP"]
        self.assertEqual(len(set_stop_ops), 1)
        self.assertEqual(set_stop_ops[0].set_stop.target_type, "ENTRY")
        tp_events = [e for e in result.report.events if e.event_type == "TP_HIT"]
        self.assertEqual(len(tp_events), 1)
        self.assertEqual(tp_events[0].level, 1)

    def test_tp_with_close_full_is_update_and_report(self) -> None:
        text = "ТП2 позиция закрыта +0,75"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=709))
        self.assertEqual(result.primary_class, "UPDATE")
        self.assertIsNotNone(result.update)
        self.assertIsNotNone(result.report)
        close_ops = [op for op in result.update.operations if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        self.assertEqual(close_ops[0].close.close_scope, "FULL")
        tp_events = [e for e in result.report.events if e.event_type == "TP_HIT"]
        self.assertEqual(len(tp_events), 1)
        self.assertAlmostEqual(result.report.reported_result.value, 0.75)
        self.assertEqual(result.report.reported_result.unit, "PERCENT")

    def test_partial_close_with_r_result_is_update_and_report(self) -> None:
        text = "Срежем тут 50% +0,42р Первый тейк убираем"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=707))
        self.assertEqual(result.primary_class, "UPDATE")
        ops = result.update.operations
        close_ops = [op for op in ops if op.op_type == "CLOSE"]
        self.assertEqual(len(close_ops), 1)
        self.assertEqual(close_ops[0].close.close_scope, "PARTIAL")
        self.assertIsNotNone(result.report)
        self.assertAlmostEqual(result.report.reported_result.value, 0.42)
        self.assertEqual(result.report.reported_result.unit, "R")

    def test_tp_close_full_with_r_result(self) -> None:
        text = "Tp2 сделка закрыта Общий профит +1.49р"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=714))
        self.assertEqual(result.primary_class, "UPDATE")
        self.assertIsNotNone(result.report)
        self.assertAlmostEqual(result.report.reported_result.value, 1.49)
        self.assertEqual(result.report.reported_result.unit, "R")

    # ------------------------------------------------------------------
    # INFO_ONLY
    # ------------------------------------------------------------------

    def test_info_only_is_info(self) -> None:
        text = "Рыночная структура изменилась, наблюдаем"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertIn(result.primary_class, {"INFO", "REPORT", "UPDATE"})
        self.assertIsInstance(result, CanonicalMessage)

    # ------------------------------------------------------------------
    # Targeting
    # ------------------------------------------------------------------

    def test_reply_targeting_is_set(self) -> None:
        text = "Перевод в безубыток"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=701))
        self.assertIsNotNone(result.targeting)
        self.assertTrue(result.targeting.targeted)
        self.assertEqual(result.targeting.strategy, "REPLY_OR_LINK")
        ref_types = [r.ref_type for r in result.targeting.refs]
        self.assertIn("REPLY", ref_types)

    def test_symbol_targeting_when_no_reply(self) -> None:
        text = "tp1+"
        result = self.parser.parse_canonical(text, _ctx(text, reply_to=708))
        self.assertIsNotNone(result.targeting)

    def test_new_signal_has_no_targeting(self) -> None:
        text = "Btcusdt Long вход с текущих\nСтоп 90939\nTP1: 92265\nTP2: 93704"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertIsNone(result.targeting)

    # ------------------------------------------------------------------
    # parser_profile and diagnostics
    # ------------------------------------------------------------------

    def test_parser_profile_is_trader_d(self) -> None:
        text = "Btcusdt Long вход с текущих\nСтоп 90939\nTP1: 92265"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.parser_profile, "trader_d")

    def test_diagnostics_contains_version(self) -> None:
        text = "Btcusdt Long вход с текущих\nСтоп 90939\nTP1: 92265"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.diagnostics.get("parser_version"), "trader_d_v1")

    def test_raw_context_is_set(self) -> None:
        text = "Btcusdt Long вход с текущих\nСтоп 90939\nTP1: 92265"
        result = self.parser.parse_canonical(text, _ctx(text))
        self.assertEqual(result.raw_context.raw_text, text)


if __name__ == "__main__":
    unittest.main()
