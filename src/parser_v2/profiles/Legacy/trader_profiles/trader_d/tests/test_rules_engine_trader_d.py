"""Tests for RulesEngine using trader_d's parsing_rules.json.

Validates the RulesEngine classification layer in isolation — no profile.py
entity extraction, no ParserContext, pure marker matching.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from src.parser.rules_engine import RulesEngine

_RULES_PATH = Path(__file__).resolve().parents[1] / "parsing_rules.json"


def _engine() -> RulesEngine:
    return RulesEngine.load(_RULES_PATH)


class TestRulesEngineJsonLoads(unittest.TestCase):
    def test_load_returns_engine_instance(self) -> None:
        engine = _engine()
        self.assertIsInstance(engine, RulesEngine)

    def test_language_is_ru(self) -> None:
        engine = _engine()
        self.assertEqual(engine._rules.get("language"), "ru")

    def test_number_format_declared(self) -> None:
        engine = _engine()
        nf = engine._rules.get("number_format", {})
        self.assertEqual(nf.get("decimal_separator"), ".")
        self.assertEqual(nf.get("thousands_separator"), " ")

    def test_classification_markers_have_strong_weak_structure(self) -> None:
        engine = _engine()
        cm = engine._rules.get("classification_markers", {})
        for category in ("new_signal", "update", "info_only"):
            self.assertIn(category, cm, f"Missing category: {category}")
            self.assertIn("strong", cm[category])
            self.assertIn("weak", cm[category])

    def test_intent_markers_present(self) -> None:
        engine = _engine()
        im = engine._rules.get("intent_markers", {})
        for intent in (
            "U_MOVE_STOP_TO_BE", "U_MOVE_STOP", "U_CANCEL_PENDING_ORDERS",
            "U_CLOSE_FULL", "U_CLOSE_PARTIAL", "U_TP_HIT", "U_STOP_HIT",
            "U_MARK_FILLED", "U_REPORT_FINAL_RESULT", "U_EXIT_BE",
            "U_UPDATE_TAKE_PROFITS", "U_REVERSE_SIGNAL", "U_RISK_NOTE",
        ):
            self.assertIn(intent, im, f"Missing intent: {intent}")

    def test_intent_markers_are_flat_lists(self) -> None:
        engine = _engine()
        im = engine._rules.get("intent_markers", {})
        for intent, markers in im.items():
            self.assertIsInstance(markers, list, f"{intent} should be a flat list")
            for marker in markers:
                self.assertIsInstance(marker, str, f"{intent} marker should be a string, got {type(marker)}")

    def test_target_ref_markers_present(self) -> None:
        engine = _engine()
        trm = engine._rules.get("target_ref_markers", {})
        self.assertIn("strong", trm)
        self.assertIn("weak", trm)

    def test_blacklist_present(self) -> None:
        engine = _engine()
        bl = engine._rules.get("blacklist", [])
        self.assertIsInstance(bl, list)
        self.assertIn("dyor", bl)

    def test_fallback_hook_disabled(self) -> None:
        engine = _engine()
        fh = engine._rules.get("fallback_hook", {})
        self.assertFalse(fh.get("enabled"))

    def test_combination_rules_present(self) -> None:
        engine = _engine()
        self.assertIsInstance(engine._rules.get("combination_rules"), list)
        self.assertGreater(len(engine._rules["combination_rules"]), 0)

    def test_extra_trader_d_sections_preserved(self) -> None:
        engine = _engine()
        for key in ("global_target_markers", "cancel_scope_vocabulary",
                    "partial_exit_markers", "final_exit_markers",
                    "result_markers", "disambiguation_rules"):
            self.assertIn(key, engine._rules, f"Missing trader_d section: {key}")


class TestRulesEngineClassifyNewSignal(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_full_new_signal_with_sl_tp(self) -> None:
        text = "BTCUSDT LONG\nВход с текущих\nSL: 90000\nTP1: 95000\nTP2: 97000"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_new_signal_with_entry_colon(self) -> None:
        text = "ETHUSDT LONG\nВход: 3200\nСтоп: 3100\nЦели: 3400"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_new_signal_long_setup(self) -> None:
        result = self.engine.classify("SOLUSDT long setup\nentry 150\nsl: 140\ntp1: 160")
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_new_signal_buy_zone(self) -> None:
        result = self.engine.classify("buy zone 90000-91000\nstop: 88000\ntp1: 95000")
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_new_signal_confidence_above_zero(self) -> None:
        result = self.engine.classify("BTCUSDT LONG\nВход: 90000\nСтоп: 89000")
        self.assertGreater(result.confidence, 0.0)

    def test_new_signal_with_averaging_plan(self) -> None:
        text = "b (усреднение)\nEntry: 91000\nSL: 89500\nTP1: 93000"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_new_signal_setup_incomplete_marker(self) -> None:
        text = "ETHUSDT SHORT\nВход с текущих\nSL: 3500\nТейки позже"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")


class TestRulesEngineClassifyUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_stop_to_be_classified_as_update(self) -> None:
        result = self.engine.classify("Стоп в бу")
        self.assertEqual(result.message_type, "UPDATE")

    def test_tp_taken_classified_as_update(self) -> None:
        result = self.engine.classify("Тейк взят 🎉")
        self.assertEqual(result.message_type, "UPDATE")

    def test_entry_filled_classified_as_update(self) -> None:
        result = self.engine.classify("Вход исполнен")
        self.assertEqual(result.message_type, "UPDATE")

    def test_stop_hit_classified_as_update(self) -> None:
        result = self.engine.classify("Выбило по стопу")
        self.assertEqual(result.message_type, "UPDATE")

    def test_cancel_limits_classified_as_update(self) -> None:
        result = self.engine.classify("Убираем лимитки")
        self.assertEqual(result.message_type, "UPDATE")

    def test_close_all_classified_as_update(self) -> None:
        result = self.engine.classify("Закрываю все позиции")
        self.assertEqual(result.message_type, "UPDATE")

    def test_partial_close_classified_as_update(self) -> None:
        result = self.engine.classify("Частично закрываем")
        self.assertEqual(result.message_type, "UPDATE")

    def test_result_report_classified_as_update(self) -> None:
        result = self.engine.classify("Итог: +2R 🔥")
        self.assertEqual(result.message_type, "UPDATE")

    def test_move_to_be_classified_as_update(self) -> None:
        result = self.engine.classify("Переводим в бу")
        self.assertEqual(result.message_type, "UPDATE")

    def test_fix_classified_as_update(self) -> None:
        result = self.engine.classify("Фикс 50%")
        self.assertEqual(result.message_type, "UPDATE")

    def test_zafixiroval_classified_as_update(self) -> None:
        result = self.engine.classify("Зафиксировал позицию")
        self.assertEqual(result.message_type, "UPDATE")

    def test_doshli_do_tejkov_classified_as_update(self) -> None:
        result = self.engine.classify("Дошли до 2-х тейков")
        self.assertEqual(result.message_type, "UPDATE")


class TestRulesEngineClassifyUnclassified(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_empty_message_unclassified(self) -> None:
        result = self.engine.classify("")
        self.assertEqual(result.message_type, "UNCLASSIFIED")
        self.assertEqual(result.confidence, 0.0)

    def test_random_text_unclassified(self) -> None:
        result = self.engine.classify("Привет всем!")
        self.assertEqual(result.message_type, "UNCLASSIFIED")

    def test_unclassified_has_no_matched_markers(self) -> None:
        result = self.engine.classify("Привет всем!")
        self.assertEqual(result.matched_markers, [])


class TestRulesEngineDetectIntents(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_move_stop_to_be_detected(self) -> None:
        intents = self.engine.detect_intents("Стоп в бу")
        self.assertIn("U_MOVE_STOP_TO_BE", intents)

    def test_move_stop_numeric_detected(self) -> None:
        intents = self.engine.detect_intents("Стоп на первый тейк")
        self.assertIn("U_MOVE_STOP", intents)

    def test_cancel_pending_detected(self) -> None:
        intents = self.engine.detect_intents("Убираем лимитки")
        self.assertIn("U_CANCEL_PENDING_ORDERS", intents)

    def test_close_full_detected(self) -> None:
        intents = self.engine.detect_intents("Закрываю все позиции")
        self.assertIn("U_CLOSE_FULL", intents)

    def test_close_partial_detected(self) -> None:
        intents = self.engine.detect_intents("Частично закрываем 50%")
        self.assertIn("U_CLOSE_PARTIAL", intents)

    def test_tp_hit_detected(self) -> None:
        intents = self.engine.detect_intents("Тейк взят")
        self.assertIn("U_TP_HIT", intents)

    def test_stop_hit_detected(self) -> None:
        intents = self.engine.detect_intents("Выбило по стопу")
        self.assertIn("U_STOP_HIT", intents)

    def test_mark_filled_detected(self) -> None:
        intents = self.engine.detect_intents("Вход исполнен")
        self.assertIn("U_MARK_FILLED", intents)

    def test_report_final_result_detected(self) -> None:
        intents = self.engine.detect_intents("Итог по сделке")
        self.assertIn("U_REPORT_FINAL_RESULT", intents)

    def test_exit_be_detected(self) -> None:
        intents = self.engine.detect_intents("Ушел в бу")
        self.assertIn("U_EXIT_BE", intents)

    def test_update_take_profits_detected(self) -> None:
        intents = self.engine.detect_intents("Первый тейк убираем")
        self.assertIn("U_UPDATE_TAKE_PROFITS", intents)

    def test_reverse_signal_detected(self) -> None:
        intents = self.engine.detect_intents("Перезайдем по новым уровням")
        self.assertIn("U_REVERSE_SIGNAL", intents)

    def test_risk_note_detected(self) -> None:
        intents = self.engine.detect_intents("Риск 0.5%")
        self.assertIn("U_RISK_NOTE", intents)

    def test_no_intents_for_unrelated_text(self) -> None:
        intents = self.engine.detect_intents("Привет, как дела?")
        self.assertEqual(intents, [])

    def test_intents_hint_in_classify_result(self) -> None:
        result = self.engine.classify("Закрываю все позиции")
        self.assertIn("U_CLOSE_FULL", result.intents_hint)

    def test_multiple_intents_from_composite_message(self) -> None:
        text = "Стоп в бу / Взяли лимитку / Частично закрываем"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_MOVE_STOP_TO_BE", intents)
        self.assertIn("U_MARK_FILLED", intents)
        self.assertIn("U_CLOSE_PARTIAL", intents)


class TestRulesEngineCombinationRules(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_entry_and_sl_boost_new_signal(self) -> None:
        text = "BTCUSDT LONG\nВход: 90000\nSL: 88000\nTP1: 93000"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertGreater(result.confidence, 0.4)

    def test_stop_bu_boost_update(self) -> None:
        text = "Стоп в бу, тейк 1 взяли"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")
        self.assertGreater(result.confidence, 0.3)


if __name__ == "__main__":
    unittest.main()
