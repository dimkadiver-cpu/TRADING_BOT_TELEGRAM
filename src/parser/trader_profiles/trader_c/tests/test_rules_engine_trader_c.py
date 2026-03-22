"""Tests for RulesEngine using trader_c's parsing_rules.json.

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
            "U_ACTIVATION", "U_TP_HIT", "U_MOVE_STOP_TO_BE", "U_EXIT_BE",
            "U_CLOSE_PARTIAL", "U_CLOSE_FULL", "U_CANCEL_PENDING_ORDERS",
            "U_REMOVE_PENDING_ENTRY", "U_UPDATE_TAKE_PROFITS", "U_UPDATE_STOP",
            "U_STOP_HIT", "U_REENTER",
        ):
            self.assertIn(intent, im, f"Missing intent: {intent}")

    def test_target_ref_markers_present(self) -> None:
        engine = _engine()
        trm = engine._rules.get("target_ref_markers", {})
        self.assertIn("strong", trm)
        self.assertIn("weak", trm)

    def test_fallback_hook_disabled(self) -> None:
        engine = _engine()
        fh = engine._rules.get("fallback_hook", {})
        self.assertFalse(fh.get("enabled"))

    def test_combination_rules_present(self) -> None:
        engine = _engine()
        self.assertIsInstance(engine._rules.get("combination_rules"), list)
        self.assertGreater(len(engine._rules["combination_rules"]), 0)


class TestRulesEngineClassifyInfoOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_market_overview_is_info_only(self) -> None:
        result = self.engine.classify("Market overview for today")
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_news_is_info_only(self) -> None:
        result = self.engine.classify("News: BTC ETF approved")
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_vopros_is_info_only(self) -> None:
        result = self.engine.classify("Ребята, возникли вопросы по сделке")
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_khochu_objasnit_is_info_only(self) -> None:
        result = self.engine.classify("Хочу объяснить логику этой сделки")
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_khochu_utochnit_is_info_only(self) -> None:
        result = self.engine.classify("Хочу уточнить по последнему сигналу")
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_info_only_confidence_above_zero(self) -> None:
        result = self.engine.classify("Market overview for today")
        self.assertGreater(result.confidence, 0.0)

    def test_info_only_matched_markers_populated(self) -> None:
        result = self.engine.classify("Market overview")
        self.assertTrue(any("info_only" in m for m in result.matched_markers))


class TestRulesEngineClassifyUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_activation_classified_as_update(self) -> None:
        result = self.engine.classify("Первая лимитка сработала")
        self.assertEqual(result.message_type, "UPDATE")

    def test_aktivirovalas_classified_as_update(self) -> None:
        result = self.engine.classify("Активировалась")
        self.assertEqual(result.message_type, "UPDATE")

    def test_move_be_classified_as_update(self) -> None:
        result = self.engine.classify("В бу перевел")
        self.assertEqual(result.message_type, "UPDATE")

    def test_stоp_v_bu_classified_as_update(self) -> None:
        result = self.engine.classify("Стоп в бу")
        self.assertEqual(result.message_type, "UPDATE")

    def test_close_partial_classified_as_update(self) -> None:
        result = self.engine.classify("Скинул часть по текущим")
        self.assertEqual(result.message_type, "UPDATE")

    def test_close_full_classified_as_update(self) -> None:
        result = self.engine.classify("Закрываю по рынку")
        self.assertEqual(result.message_type, "UPDATE")

    def test_ne_aktualno_classified_as_update(self) -> None:
        result = self.engine.classify("Не актуально")
        self.assertEqual(result.message_type, "UPDATE")

    def test_tp_hit_marker_classified_as_update(self) -> None:
        result = self.engine.classify("Тп1 🥳")
        self.assertEqual(result.message_type, "UPDATE")

    def test_stop_move_classified_as_update(self) -> None:
        result = self.engine.classify("Стоп переносим на 88650")
        self.assertEqual(result.message_type, "UPDATE")

    def test_reenter_classified_as_update(self) -> None:
        result = self.engine.classify("Перезаход в позицию")
        self.assertEqual(result.message_type, "UPDATE")

    def test_remove_pending_classified_as_update(self) -> None:
        result = self.engine.classify("Доливку убрал")
        self.assertEqual(result.message_type, "UPDATE")

    def test_update_tp_classified_as_update(self) -> None:
        result = self.engine.classify("Изменения - Тп2 88150")
        self.assertEqual(result.message_type, "UPDATE")


class TestRulesEngineClassifyNewSignal(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_takeprofit_dep_classified_as_new_signal(self) -> None:
        text = "BTCUSDT LONG\nВход лимитка 92550\nСтоп лосс 91800\nТейк-профит 93200\n1% деп"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_new_signal_confidence_above_zero(self) -> None:
        text = "Тейк-профит 93200\n% деп"
        result = self.engine.classify(text)
        self.assertGreater(result.confidence, 0.0)
        self.assertEqual(result.message_type, "NEW_SIGNAL")


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

    def test_activation_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Первая лимитка сработала")
        self.assertIn("U_ACTIVATION", intents)

    def test_tp_hit_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Тп1 🥳")
        self.assertIn("U_TP_HIT", intents)

    def test_move_stop_to_be_intent_detected(self) -> None:
        intents = self.engine.detect_intents("В бу перевел")
        self.assertIn("U_MOVE_STOP_TO_BE", intents)

    def test_exit_be_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Ушли в б/у")
        self.assertIn("U_EXIT_BE", intents)

    def test_close_partial_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Скинул часть по текущим")
        self.assertIn("U_CLOSE_PARTIAL", intents)

    def test_close_full_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Закрываю по рынку")
        self.assertIn("U_CLOSE_FULL", intents)

    def test_cancel_pending_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Не актуально")
        self.assertIn("U_CANCEL_PENDING_ORDERS", intents)

    def test_remove_pending_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Доливку убрал")
        self.assertIn("U_REMOVE_PENDING_ENTRY", intents)

    def test_update_take_profits_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Изменения - тп 2 88150")
        self.assertIn("U_UPDATE_TAKE_PROFITS", intents)

    def test_update_stop_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Стоп переносим")
        self.assertIn("U_UPDATE_STOP", intents)

    def test_stop_hit_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Стоп -0,5")
        self.assertIn("U_STOP_HIT", intents)

    def test_reenter_intent_detected(self) -> None:
        intents = self.engine.detect_intents("Перезаход в позицию")
        self.assertIn("U_REENTER", intents)

    def test_no_intents_for_unrelated_text(self) -> None:
        intents = self.engine.detect_intents("Привет, как дела?")
        self.assertEqual(intents, [])

    def test_intents_hint_in_classify_result(self) -> None:
        result = self.engine.classify("Закрываю по рынку")
        self.assertIn("U_CLOSE_FULL", result.intents_hint)

    def test_multiple_intents_from_composite_message(self) -> None:
        text = "Скинул часть / в бу перевел / доливку убрал"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_CLOSE_PARTIAL", intents)
        self.assertIn("U_MOVE_STOP_TO_BE", intents)
        self.assertIn("U_REMOVE_PENDING_ENTRY", intents)


class TestRulesEngineCombinationRules(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_takeprofit_and_dep_boost_new_signal(self) -> None:
        text = "Тейк-профит 93200\n1% деп"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        # Combination rule adds 0.5 boost → confident classification
        self.assertGreater(result.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
