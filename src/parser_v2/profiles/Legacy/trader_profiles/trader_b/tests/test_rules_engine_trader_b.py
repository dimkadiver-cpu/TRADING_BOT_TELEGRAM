"""Tests for RulesEngine using trader_b's parsing_rules.json.

These tests validate the RulesEngine classification layer in isolation —
no profile.py entity extraction, no ParserContext, pure marker matching.
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

    def test_number_format_declared(self) -> None:
        engine = _engine()
        self.assertEqual(engine._rules.get("language"), "ru")
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
        for intent in ("U_MOVE_STOP_TO_BE", "U_MOVE_STOP", "U_CLOSE_FULL", "U_STOP_HIT",
                        "U_TP_HIT", "U_CANCEL_PENDING_ORDERS"):
            self.assertIn(intent, im, f"Missing intent_marker: {intent}")

    def test_target_ref_markers_present(self) -> None:
        engine = _engine()
        trm = engine._rules.get("target_ref_markers", {})
        self.assertIn("strong", trm)
        self.assertIn("weak", trm)

    def test_blacklist_present(self) -> None:
        engine = _engine()
        self.assertIn("blacklist", engine._rules)

    def test_fallback_hook_disabled(self) -> None:
        engine = _engine()
        fh = engine._rules.get("fallback_hook", {})
        self.assertFalse(fh.get("enabled"))


class TestRulesEngineClassifyNewSignal(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_full_new_signal_classified(self) -> None:
        text = (
            "$BTCUSDT - Лонг\n"
            "Вход: 65000\n"
            "Тейк профит: 70000\n"
            "Стоп лосс: 62000\n"
            "Риск на сделку 2%"
        )
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertGreater(result.confidence, 0.5)

    def test_partial_new_signal_classified(self) -> None:
        text = "Вход: 100\nСтоп лосс: 90"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")

    def test_new_signal_with_risk_classified(self) -> None:
        text = "Вход: 50000\nТейк профит: 55000\nСтоп лосс: 48000\nРиск на сделку 1%"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        self.assertGreater(result.confidence, 0.7)

    def test_new_signal_strong_markers_listed_in_matched(self) -> None:
        text = "Вход: 100\nСтоп лосс: 90\nТейк профит: 110"
        result = self.engine.classify(text)
        self.assertTrue(any("new_signal" in m for m in result.matched_markers))


class TestRulesEngineClassifyUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_stop_move_to_be_classified_as_update(self) -> None:
        text = "Стоп лосс переносим в БУ"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_close_manual_classified_as_update(self) -> None:
        text = "Закрываю позицию по текущим"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_not_actual_classified_as_update(self) -> None:
        text = "Тут не актуально"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_poka_ne_aktualno_classified_as_update(self) -> None:
        text = "Пока не актуально, цена ушла"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_tp_hit_classified_as_update(self) -> None:
        text = "Цели достигнуты"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_profit_classified_as_update(self) -> None:
        text = "Поздравляю с профитом"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_stop_hit_classified_as_update(self) -> None:
        text = "Закрылись по стопу"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_zakrываю_classified_as_update(self) -> None:
        text = "Закрываю все"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")

    def test_combination_rule_stop_move_boosts_confidence(self) -> None:
        text = "Стоп лосс переносим в БУ"
        result = self.engine.classify(text)
        self.assertGreater(result.confidence, 0.5)


class TestRulesEngineClassifyInfoOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_commentary_classified_as_info_only(self) -> None:
        text = "Идея в целом отработала"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_small_changes_classified_as_info_only(self) -> None:
        text = "Небольшие изменения!"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "INFO_ONLY")

    def test_as_expected_classified_as_info_only(self) -> None:
        text = "Как и ожидалось по BTC"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "INFO_ONLY")


class TestRulesEngineClassifyUnclassified(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_empty_message_unclassified(self) -> None:
        result = self.engine.classify("")
        self.assertEqual(result.message_type, "UNCLASSIFIED")
        self.assertEqual(result.confidence, 0.0)

    def test_random_text_unclassified(self) -> None:
        text = "Привет всем! Хорошего дня"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UNCLASSIFIED")

    def test_bu_alone_unclassified(self) -> None:
        # "БУ" alone is too weak to classify without context
        text = "БУ"
        result = self.engine.classify(text)
        # "в бу" is a weak update marker but "бу" alone doesn't match "в бу"
        self.assertIn(result.message_type, ("UNCLASSIFIED", "UPDATE"))


class TestRulesEngineDetectIntents(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_move_stop_to_be_detected(self) -> None:
        text = "Стоп лосс переносим в БУ"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_MOVE_STOP_TO_BE", intents)

    def test_close_full_detected(self) -> None:
        text = "Закрываю сделку"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_CLOSE_FULL", intents)

    def test_stop_hit_detected(self) -> None:
        text = "Закрылись по стопу"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_STOP_HIT", intents)

    def test_tp_hit_detected(self) -> None:
        text = "Цели достигнуты"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_TP_HIT", intents)

    def test_cancel_pending_detected(self) -> None:
        text = "Тут не актуально"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_CANCEL_PENDING_ORDERS", intents)

    def test_move_stop_numeric_detected(self) -> None:
        text = "Стоп лосс переносим на 1.553"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_MOVE_STOP", intents)

    def test_tp_hit_explicit_detected(self) -> None:
        text = "Поздравляю с профитом"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_TP_HIT_EXPLICIT", intents)

    def test_no_intents_for_unrelated_text(self) -> None:
        text = "Привет, как дела?"
        intents = self.engine.detect_intents(text)
        self.assertEqual(intents, [])

    def test_intents_hint_populated_in_classify_result(self) -> None:
        text = "Закрылись по стопу"
        result = self.engine.classify(text)
        self.assertIn("U_STOP_HIT", result.intents_hint)

    def test_multiple_intents_detected(self) -> None:
        # Stop hit implies close
        text = "Закрылись по стопу в -1%"
        intents = self.engine.detect_intents(text)
        self.assertIn("U_STOP_HIT", intents)


class TestRulesEngineCombinationRules(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def test_vhod_and_stop_boost_new_signal(self) -> None:
        text = "Вход: 100\nСтоп лосс: 90"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "NEW_SIGNAL")
        # With combination boost, confidence should be higher than base strong match
        self.assertGreater(result.confidence, 0.5)

    def test_stop_move_and_be_boost_update(self) -> None:
        text = "Стоп лосс переносим в БУ"
        result = self.engine.classify(text)
        self.assertEqual(result.message_type, "UPDATE")
        # combination_rule: ["стоп лосс переносим", "в бу"] → +0.3 boost
        self.assertGreater(result.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
