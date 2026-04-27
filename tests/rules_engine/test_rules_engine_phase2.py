"""Tests for RulesEngine Phase 2 features:
- detect_intents supports {strong, weak} format
- detect_intents_with_evidence returns IntentMatchResult with strength
- classification_rules.when_all_fields_present triggers score boost
- context_resolution_rules silently disabled (not operative)
"""

from __future__ import annotations

import pytest

from src.parser.rules_engine import RulesEngine, IntentMatchResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _engine_with_strong_weak_intent_markers() -> RulesEngine:
    return RulesEngine.from_dict({
        "intent_markers": {
            "MOVE_STOP_TO_BE": {
                "strong": ["stop to breakeven", "move to be"],
                "weak": ["breakeven", "be"],
            },
            "SL_HIT": {
                "strong": ["stopped out", "stop hit"],
                "weak": ["стоп"],
            },
        }
    })


def _engine_with_flat_intent_markers() -> RulesEngine:
    return RulesEngine.from_dict({
        "intent_markers": {
            "U_STOP_HIT": ["stopped out", "stop hit", "стоп"],
            "U_CLOSE_FULL": ["close all", "full close"],
        }
    })


def _engine_with_field_markers_and_classification_rules() -> RulesEngine:
    return RulesEngine.from_dict({
        "classification_markers": {
            "new_signal": {"strong": ["entry", "sl:"], "weak": []},
            "update": {"strong": ["stop hit"], "weak": []},
        },
        "field_markers": {
            "entry": {"strong": ["entry:", "вход:"], "weak": ["entry"]},
            "stop_loss": {"strong": ["sl:", "стоп:"], "weak": ["sl"]},
            "take_profit": {"strong": ["tp1:", "тейк 1:"], "weak": ["tp"]},
        },
        "classification_rules": [
            {
                "name": "complete_new_signal",
                "when_all_fields_present": ["entry", "stop_loss", "take_profit"],
                "then": "new_signal",
                "score": 1.0,
            },
            {
                "name": "partial_new_signal",
                "when_all_fields_present": ["entry", "stop_loss"],
                "then": "new_signal",
                "score": 0.7,
            },
        ],
    })


def _engine_with_context_resolution_rules() -> RulesEngine:
    return RulesEngine.from_dict({
        "context_resolution_rules": [
            {
                "name": "resolve_be",
                "when": {
                    "has_weak_intent": "MOVE_STOP_TO_BE",
                    "has_target_ref": True,
                    "message_has_no_strong_markers_for": ["EXIT_BE"],
                },
                "if_target_history_has_any": ["SL_HIT"],
                "resolve_as": "EXIT_BE",
            }
        ]
    })


# ---------------------------------------------------------------------------
# IntentMatchResult type
# ---------------------------------------------------------------------------

class TestIntentMatchResultType:
    def test_intent_match_result_is_importable(self) -> None:
        from src.parser.rules_engine import IntentMatchResult
        assert IntentMatchResult is not None

    def test_intent_match_result_has_intent_field(self) -> None:
        r = IntentMatchResult(intent="SL_HIT", strength="strong", matched_marker="stop hit")
        assert r.intent == "SL_HIT"

    def test_intent_match_result_has_strength_field(self) -> None:
        r = IntentMatchResult(intent="SL_HIT", strength="strong", matched_marker="stop hit")
        assert r.strength == "strong"

    def test_intent_match_result_has_matched_marker_field(self) -> None:
        r = IntentMatchResult(intent="SL_HIT", strength="strong", matched_marker="stop hit")
        assert r.matched_marker == "stop hit"

    def test_intent_match_result_weak_strength(self) -> None:
        r = IntentMatchResult(intent="MOVE_STOP_TO_BE", strength="weak", matched_marker="be")
        assert r.strength == "weak"


# ---------------------------------------------------------------------------
# detect_intents_with_evidence — strong/weak format
# ---------------------------------------------------------------------------

class TestDetectIntentsWithEvidenceMethod:
    def test_method_exists_on_rules_engine(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        assert hasattr(engine, "detect_intents_with_evidence")

    def test_returns_list_of_intent_match_results(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        results = engine.detect_intents_with_evidence("stop hit")
        assert isinstance(results, list)
        assert all(isinstance(r, IntentMatchResult) for r in results)

    def test_strong_marker_returns_strong_strength(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        results = engine.detect_intents_with_evidence("stopped out at 95.5")
        sl_hits = [r for r in results if r.intent == "SL_HIT"]
        assert len(sl_hits) == 1
        assert sl_hits[0].strength == "strong"

    def test_weak_only_marker_returns_weak_strength(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        # "breakeven" is weak, "stop to breakeven" is strong — use text with only weak marker
        results = engine.detect_intents_with_evidence("went to breakeven level")
        be_hits = [r for r in results if r.intent == "MOVE_STOP_TO_BE"]
        assert len(be_hits) == 1
        assert be_hits[0].strength == "weak"

    def test_strong_marker_wins_over_weak_when_both_present(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        # text contains both "stop to breakeven" (strong) and "be" (weak)
        results = engine.detect_intents_with_evidence("stop to breakeven, be careful")
        be_hits = [r for r in results if r.intent == "MOVE_STOP_TO_BE"]
        assert len(be_hits) == 1
        assert be_hits[0].strength == "strong"

    def test_only_one_result_per_intent(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        results = engine.detect_intents_with_evidence("stopped out and stop hit")
        sl_hits = [r for r in results if r.intent == "SL_HIT"]
        assert len(sl_hits) == 1

    def test_no_match_returns_empty(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        results = engine.detect_intents_with_evidence("hello world no markers here")
        assert results == []

    def test_matched_marker_recorded(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        results = engine.detect_intents_with_evidence("stop hit confirmed")
        sl_hits = [r for r in results if r.intent == "SL_HIT"]
        assert sl_hits[0].matched_marker == "stop hit"


# ---------------------------------------------------------------------------
# detect_intents — backward compat with flat list markers
# ---------------------------------------------------------------------------

class TestDetectIntentsBackwardCompat:
    def test_flat_list_markers_still_detected(self) -> None:
        engine = _engine_with_flat_intent_markers()
        result = engine.detect_intents("stopped out")
        assert "U_STOP_HIT" in result

    def test_flat_list_markers_evidence_treated_as_strong(self) -> None:
        engine = _engine_with_flat_intent_markers()
        results = engine.detect_intents_with_evidence("stopped out")
        assert any(r.intent == "U_STOP_HIT" and r.strength == "strong" for r in results)

    def test_detect_intents_returns_list_of_strings(self) -> None:
        engine = _engine_with_flat_intent_markers()
        result = engine.detect_intents("close all")
        assert isinstance(result, list)
        assert all(isinstance(i, str) for i in result)

    def test_detect_intents_strong_weak_returns_correct_intent_names(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        result = engine.detect_intents("stopped out")
        assert "SL_HIT" in result

    def test_detect_intents_deduplicates(self) -> None:
        engine = _engine_with_strong_weak_intent_markers()
        # text contains both strong and weak markers for SL_HIT
        result = engine.detect_intents("stopped out стоп")
        assert result.count("SL_HIT") == 1


# ---------------------------------------------------------------------------
# classification_rules.when_all_fields_present
# ---------------------------------------------------------------------------

class TestClassificationRulesWhenAllFieldsPresent:
    def test_complete_signal_fields_boost_new_signal(self) -> None:
        engine = _engine_with_field_markers_and_classification_rules()
        # text contains entry, sl, tp markers
        result = engine.classify("entry: 100 sl: 90 tp1: 110")
        assert result.message_type == "NEW_SIGNAL"

    def test_partial_signal_fields_still_boosts(self) -> None:
        engine = _engine_with_field_markers_and_classification_rules()
        # entry + sl but no tp → partial_new_signal rule fires
        result = engine.classify("entry: 100 sl: 90")
        assert result.message_type == "NEW_SIGNAL"

    def test_missing_required_field_no_rule_boost(self) -> None:
        engine = _engine_with_field_markers_and_classification_rules()
        # only stop_loss present, no entry → no classification_rule fires
        # score from classification_rules alone = 0
        result = engine.classify("sl: 90")
        # sl: is a strong classification_markers for new_signal, so it might still classify
        # but classification_rules should NOT trigger
        # we just verify no crash and the method runs
        assert result is not None

    def test_classification_rules_applied_when_no_classification_markers_match(self) -> None:
        engine_no_cm = RulesEngine.from_dict({
            "field_markers": {
                "entry": {"strong": ["вход:"], "weak": []},
                "stop_loss": {"strong": ["стоп:"], "weak": []},
                "take_profit": {"strong": ["тейк:"], "weak": []},
            },
            "classification_rules": [
                {
                    "name": "complete",
                    "when_all_fields_present": ["entry", "stop_loss", "take_profit"],
                    "then": "new_signal",
                    "score": 1.0,
                }
            ],
        })
        result = engine_no_cm.classify("вход: 100 стоп: 90 тейк: 110")
        assert result.message_type == "NEW_SIGNAL"
        assert result.confidence > 0.0

    def test_classification_rule_without_matching_fields_does_not_trigger(self) -> None:
        engine_no_cm = RulesEngine.from_dict({
            "field_markers": {
                "entry": {"strong": ["вход:"], "weak": []},
                "stop_loss": {"strong": ["стоп:"], "weak": []},
                "take_profit": {"strong": ["тейк:"], "weak": []},
            },
            "classification_rules": [
                {
                    "name": "complete",
                    "when_all_fields_present": ["entry", "stop_loss", "take_profit"],
                    "then": "new_signal",
                    "score": 1.0,
                }
            ],
        })
        result = engine_no_cm.classify("some random text without field markers")
        assert result.message_type == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# context_resolution_rules — explicitly disabled
# ---------------------------------------------------------------------------

class TestContextResolutionRulesDisabled:
    def test_engine_init_with_context_resolution_rules_does_not_crash(self) -> None:
        engine = _engine_with_context_resolution_rules()
        assert engine is not None

    def test_classify_with_context_resolution_rules_does_not_crash(self) -> None:
        engine = _engine_with_context_resolution_rules()
        result = engine.classify("some text")
        assert result is not None

    def test_detect_intents_with_context_resolution_rules_does_not_crash(self) -> None:
        engine = _engine_with_context_resolution_rules()
        result = engine.detect_intents("some text")
        assert isinstance(result, list)

    def test_context_resolution_rules_not_applied_in_classify(self) -> None:
        # Context resolution requires target history — cannot be evaluated by classify alone.
        # Verify the result is the same as without context_resolution_rules.
        engine_with = _engine_with_context_resolution_rules()
        engine_without = RulesEngine.from_dict({})
        result_with = engine_with.classify("some text")
        result_without = engine_without.classify("some text")
        assert result_with.message_type == result_without.message_type
