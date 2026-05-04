"""Unit test per RulesEngine.

Test organizzati per:
  - ClassificationResult struttura dati
  - RulesEngine.from_dict() con formato nuovo (strong/weak)
  - RulesEngine.from_dict() con formato legacy (flat list)
  - RulesEngine.load() da file JSON
  - RulesEngine.classify() con marcatori strong/weak/combination_rules
  - RulesEngine.detect_intents() con intent_markers
  - RulesEngine.is_blacklisted()
  - merge vocabolario condiviso
  - number_format, language, fallback_hook_enabled properties
  - Testi reali trader_3 (segnali, update, info_only, unclassified)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.parser.rules_engine import (
    ClassificationResult,
    RulesEngine,
    _merge_rules,
    _normalise_classification_markers,
)


# ---------------------------------------------------------------------------
# Fixtures di regole inline (formato nuovo)
# ---------------------------------------------------------------------------

TRADER3_RULES: dict[str, Any] = {
    "language": "en",
    "shared_vocabulary": None,
    "number_format": {
        "decimal_separator": ".",
        "thousands_separator": ",",
    },
    "classification_markers": {
        "new_signal": {
            "strong": ["signal id", "coin:", "direction:", "entry:", "stop loss:"],
            "weak": ["long", "short"],
        },
        "update": {
            "strong": ["target", "closed manually", "re-enter", "sl hit", "reenter"],
            "weak": ["update", "closed"],
        },
        "info_only": {
            "strong": ["vip market update", "market analysis"],
            "weak": ["analysis", "outlook"],
        },
    },
    "combination_rules": [
        {
            "if": ["signal id", "stop loss:"],
            "then": "new_signal",
            "confidence_boost": 0.5,
        },
        {
            "if": ["coin:", "direction:"],
            "then": "new_signal",
            "confidence_boost": 0.3,
        },
    ],
    "intent_markers": {
        "U_MOVE_STOP": ["move sl", "move stop", "trailing stop"],
        "U_CLOSE_FULL": ["closed manually", "sl hit", "closed", "position closed"],
        "U_CLOSE_PARTIAL": ["partial close", "partial profit", "take partial"],
        "U_CANCEL_PENDING": ["cancel", "cancelled", "void"],
        "U_REENTER": ["re-enter", "reenter", "re-entry"],
        "U_ADD_ENTRY": ["add entry", "additional entry"],
        "U_MODIFY_ENTRY": ["modify entry", "change entry"],
        "U_UPDATE_TAKE_PROFITS": ["update targets", "new targets"],
    },
    "target_ref_markers": {
        "strong": {
            "telegram_link": "t\\.me/",
            "explicit_id": [],
        },
        "weak": {
            "pronouns": [],
        },
    },
    "blacklist": ["spam", "advertisement"],
    "fallback_hook": {
        "enabled": False,
        "provider": None,
        "model": None,
    },
}

# Formato legacy con flat list — usato da trader_3 production parsing_rules.json
LEGACY_RULES: dict[str, Any] = {
    "classification_markers": {
        "info_only": ["vip market update", "market analysis"],
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine(rules: dict[str, Any] | None = None) -> RulesEngine:
    return RulesEngine.from_dict(rules or TRADER3_RULES)


# ---------------------------------------------------------------------------
# ClassificationResult
# ---------------------------------------------------------------------------

class TestClassificationResult:
    def test_structure(self) -> None:
        r = ClassificationResult(
            message_type="NEW_SIGNAL",
            confidence=0.9,
            matched_markers=["new_signal/signal id"],
            intents_hint=[],
        )
        assert r.message_type == "NEW_SIGNAL"
        assert r.confidence == 0.9
        assert r.matched_markers == ["new_signal/signal id"]
        assert r.intents_hint == []

    def test_defaults(self) -> None:
        r = ClassificationResult(message_type="UNCLASSIFIED", confidence=0.0)
        assert r.matched_markers == []
        assert r.intents_hint == []


# ---------------------------------------------------------------------------
# RulesEngine.from_dict — formato nuovo
# ---------------------------------------------------------------------------

class TestRulesEngineFromDict:
    def test_creates_instance(self) -> None:
        engine = make_engine()
        assert isinstance(engine, RulesEngine)

    def test_number_format_from_rules(self) -> None:
        engine = make_engine()
        nf = engine.number_format
        assert nf["decimal_separator"] == "."
        assert nf["thousands_separator"] == ","

    def test_number_format_defaults(self) -> None:
        engine = RulesEngine.from_dict({})
        nf = engine.number_format
        assert nf["decimal_separator"] == "."
        assert nf["thousands_separator"] is None

    def test_language(self) -> None:
        engine = make_engine()
        assert engine.language == "en"

    def test_language_none_when_missing(self) -> None:
        engine = RulesEngine.from_dict({})
        assert engine.language is None

    def test_fallback_hook_disabled(self) -> None:
        engine = make_engine()
        assert engine.fallback_hook_enabled is False

    def test_fallback_hook_enabled(self) -> None:
        rules = {**TRADER3_RULES, "fallback_hook": {"enabled": True}}
        engine = RulesEngine.from_dict(rules)
        assert engine.fallback_hook_enabled is True


# ---------------------------------------------------------------------------
# RulesEngine.load() — file JSON
# ---------------------------------------------------------------------------

class TestRulesEngineLoad:
    def test_load_from_file(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "parsing_rules.json"
        rules_file.write_text(json.dumps(TRADER3_RULES), encoding="utf-8")
        engine = RulesEngine.load(rules_file)
        assert engine.language == "en"

    def test_load_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            RulesEngine.load(tmp_path / "nonexistent.json")

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json }", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            RulesEngine.load(bad)

    def test_load_real_trader3_rules(self) -> None:
        """Verifica che il parsing_rules.json reale di trader_3 venga caricato."""
        path = Path("src/parser/trader_profiles/trader_3/parsing_rules.json")
        engine = RulesEngine.load(path)
        assert isinstance(engine, RulesEngine)
        # Il file reale usa il formato legacy — non deve sollevare eccezioni
        assert engine.number_format["decimal_separator"] in (".", ",")

    def test_load_with_shared_vocabulary_missing_file(self, tmp_path: Path) -> None:
        """Un shared_vocabulary dichiarato ma file assente non deve far crashare."""
        rules = {**TRADER3_RULES, "shared_vocabulary": "nonexistent_vocab"}
        rules_file = tmp_path / "parsing_rules.json"
        rules_file.write_text(json.dumps(rules), encoding="utf-8")
        engine = RulesEngine.load(rules_file)  # no exception
        assert engine.language == "en"


# ---------------------------------------------------------------------------
# RulesEngine.classify() — marcatori singoli
# ---------------------------------------------------------------------------

class TestClassifySingleMarkers:
    def test_strong_new_signal(self) -> None:
        engine = make_engine()
        result = engine.classify("SIGNAL ID: #123\nCOIN: BTC/USDT\nDIRECTION: LONG")
        assert result.message_type == "NEW_SIGNAL"
        assert result.confidence > 0.0

    def test_strong_update(self) -> None:
        engine = make_engine()
        result = engine.classify("Target 1 hit ✅ — position update")
        assert result.message_type == "UPDATE"

    def test_strong_info_only(self) -> None:
        engine = make_engine()
        result = engine.classify("VIP Market Update: $BNB analysis for this week")
        assert result.message_type == "INFO_ONLY"

    def test_unclassified_no_markers(self) -> None:
        engine = make_engine()
        result = engine.classify("hello world")
        assert result.message_type == "UNCLASSIFIED"
        assert result.confidence == 0.0

    def test_empty_text_unclassified(self) -> None:
        engine = make_engine()
        result = engine.classify("")
        assert result.message_type == "UNCLASSIFIED"
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# RulesEngine.classify() — weak markers
# ---------------------------------------------------------------------------

class TestClassifyWeakMarkers:
    def test_weak_marker_lower_confidence(self) -> None:
        engine = make_engine()
        # "long" è weak per new_signal
        result_weak = engine.classify("BTC long")
        # "signal id" è strong per new_signal
        result_strong = engine.classify("SIGNAL ID: #1 BTC long")
        assert result_weak.confidence <= result_strong.confidence

    def test_weak_marker_alone_gives_positive_score(self) -> None:
        engine = make_engine()
        result = engine.classify("BTC long position")
        # "long" è weak → dovrebbe dare new_signal, ma con confidence bassa
        assert result.message_type == "NEW_SIGNAL"
        assert result.confidence < 1.0

    def test_weak_update_marker(self) -> None:
        engine = make_engine()
        result = engine.classify("position closed at target")
        # "closed" è weak per update, "target" è strong
        assert result.message_type == "UPDATE"


# ---------------------------------------------------------------------------
# RulesEngine.classify() — combination_rules
# ---------------------------------------------------------------------------

class TestClassifyCombinationRules:
    def test_combination_rule_boosts_confidence(self) -> None:
        engine = make_engine()
        # Singolo marcatore strong
        base = engine.classify("SIGNAL ID: #456")
        # Due marcatori strong che attivano anche combination_rule
        boosted = engine.classify("SIGNAL ID: #456\nSTOP LOSS: 90000")
        assert boosted.confidence >= base.confidence

    def test_combination_rule_coin_direction(self) -> None:
        engine = make_engine()
        result = engine.classify("COIN: BTCUSDT\nDIRECTION: SHORT\nEntry: 92000")
        assert result.message_type == "NEW_SIGNAL"
        # Combination rule [coin:, direction:] → new_signal +0.3
        assert result.confidence > 0.5

    def test_combination_rule_only_partial_if_no_boost(self) -> None:
        engine = make_engine()
        # Solo "signal id" senza "stop loss:" → combination non scatta
        result_partial = engine.classify("SIGNAL ID: #1")
        # "signal id" + "stop loss:" → combination scatta
        result_full = engine.classify("SIGNAL ID: #1\nSTOP LOSS: 50")
        # full ha score più alto grazie al boost
        assert result_full.confidence >= result_partial.confidence


# ---------------------------------------------------------------------------
# RulesEngine.classify() — matched_markers
# ---------------------------------------------------------------------------

class TestClassifyMatchedMarkers:
    def test_matched_markers_format(self) -> None:
        engine = make_engine()
        result = engine.classify("SIGNAL ID: #1\nCOIN: BTC\nDIRECTION: LONG")
        assert any("new_signal/" in m for m in result.matched_markers)

    def test_no_matched_markers_when_unclassified(self) -> None:
        engine = make_engine()
        result = engine.classify("random unrecognised text")
        assert result.matched_markers == []

    def test_only_matched_categories_in_markers(self) -> None:
        engine = make_engine()
        result = engine.classify("VIP Market Update: $ETH")
        # Solo info_only deve comparire
        categories_hit = {m.split("/")[0] for m in result.matched_markers}
        assert "info_only" in categories_hit
        assert "new_signal" not in categories_hit


# ---------------------------------------------------------------------------
# RulesEngine.classify() — case insensitivity
# ---------------------------------------------------------------------------

class TestClassifyCaseInsensitive:
    def test_uppercase_text_matches(self) -> None:
        engine = make_engine()
        result = engine.classify("VIP MARKET UPDATE — BNB OUTLOOK")
        assert result.message_type == "INFO_ONLY"

    def test_lowercase_text_matches(self) -> None:
        engine = make_engine()
        result = engine.classify("vip market update — bnb")
        assert result.message_type == "INFO_ONLY"

    def test_mixed_case_markers(self) -> None:
        engine = make_engine()
        result = engine.classify("Signal Id: #789 Coin: ETH/USDT Direction: Long")
        assert result.message_type == "NEW_SIGNAL"


# ---------------------------------------------------------------------------
# RulesEngine.detect_intents()
# ---------------------------------------------------------------------------

class TestDetectIntents:
    def test_close_full_from_closed_manually(self) -> None:
        engine = make_engine()
        intents = engine.detect_intents("Closed Manually — all positions")
        assert "U_CLOSE_FULL" in intents

    def test_reenter_detected(self) -> None:
        engine = make_engine()
        intents = engine.detect_intents("Re-Enter at 92000 — same setup")
        assert "U_REENTER" in intents

    def test_move_stop_detected(self) -> None:
        engine = make_engine()
        intents = engine.detect_intents("Move SL to breakeven")
        assert "U_MOVE_STOP" in intents

    def test_no_intents_when_no_match(self) -> None:
        engine = make_engine()
        intents = engine.detect_intents("hello world nothing here")
        assert intents == []

    def test_multiple_intents_detected(self) -> None:
        engine = make_engine()
        text = "SL hit — position closed. Re-enter tomorrow."
        intents = engine.detect_intents(text)
        assert "U_CLOSE_FULL" in intents
        assert "U_REENTER" in intents

    def test_no_duplicate_intents(self) -> None:
        engine = make_engine()
        # "closed manually" e "closed" sono entrambi marker per U_CLOSE_FULL
        intents = engine.detect_intents("Closed Manually — also closed earlier")
        assert intents.count("U_CLOSE_FULL") == 1

    def test_intents_hint_in_classify_result(self) -> None:
        engine = make_engine()
        result = engine.classify("Re-Enter at 92000")
        assert "U_REENTER" in result.intents_hint

    def test_empty_intent_markers_ok(self) -> None:
        engine = RulesEngine.from_dict({})
        intents = engine.detect_intents("some text")
        assert intents == []


# ---------------------------------------------------------------------------
# RulesEngine.is_blacklisted()
# ---------------------------------------------------------------------------

class TestIsBlacklisted:
    def test_blacklisted_text(self) -> None:
        engine = make_engine()
        assert engine.is_blacklisted("advertisement: buy signals now") is True

    def test_not_blacklisted(self) -> None:
        engine = make_engine()
        assert engine.is_blacklisted("SIGNAL ID: #1 BTC LONG") is False

    def test_empty_blacklist(self) -> None:
        engine = RulesEngine.from_dict({})
        assert engine.is_blacklisted("anything") is False


# ---------------------------------------------------------------------------
# Formato legacy (flat list)
# ---------------------------------------------------------------------------

class TestLegacyFlatListFormat:
    def test_legacy_info_only_markers(self) -> None:
        engine = RulesEngine.from_dict(LEGACY_RULES)
        result = engine.classify("VIP Market Update: BNB analysis")
        assert result.message_type == "INFO_ONLY"
        assert result.confidence > 0.0

    def test_legacy_unclassified_for_no_match(self) -> None:
        engine = RulesEngine.from_dict(LEGACY_RULES)
        result = engine.classify("SIGNAL ID: #1 BTC LONG")
        # non ci sono marcatori per new_signal nel legacy fixture
        assert result.message_type == "UNCLASSIFIED"

    def test_legacy_and_new_format_coexist(self) -> None:
        """Un dict con mix di formato legacy e nuovo non deve crashare."""
        mixed = {
            "classification_markers": {
                "info_only": ["market analysis"],  # legacy (flat)
                "update": {"strong": ["closed manually"], "weak": []},  # new
            }
        }
        engine = RulesEngine.from_dict(mixed)
        r1 = engine.classify("Market analysis report")
        assert r1.message_type == "INFO_ONLY"
        r2 = engine.classify("Closed manually")
        assert r2.message_type == "UPDATE"


# ---------------------------------------------------------------------------
# _normalise_classification_markers — funzione privata
# ---------------------------------------------------------------------------

class TestNormaliseClassificationMarkers:
    def test_new_format_preserved(self) -> None:
        raw = {"new_signal": {"strong": ["Signal ID"], "weak": ["long"]}}
        result = _normalise_classification_markers(raw)
        assert result["new_signal"]["strong"] == ["signal id"]
        assert result["new_signal"]["weak"] == ["long"]

    def test_legacy_list_becomes_strong(self) -> None:
        raw = {"info_only": ["vip market update", "analysis"]}
        result = _normalise_classification_markers(raw)
        assert result["info_only"]["strong"] == ["vip market update", "analysis"]
        assert result["info_only"]["weak"] == []

    def test_markers_lowercased(self) -> None:
        raw = {"update": {"strong": ["CLOSED MANUALLY", "SL HIT"], "weak": []}}
        result = _normalise_classification_markers(raw)
        assert "closed manually" in result["update"]["strong"]
        assert "sl hit" in result["update"]["strong"]


# ---------------------------------------------------------------------------
# _merge_rules — merge vocabolario condiviso
# ---------------------------------------------------------------------------

class TestMergeRules:
    def test_profile_scalars_override_shared(self) -> None:
        base = {"language": "en", "number_format": {"decimal_separator": "."}}
        override = {"language": "ru"}
        result = _merge_rules(base=base, override=override)
        assert result["language"] == "ru"

    def test_classification_markers_merged(self) -> None:
        base = {
            "classification_markers": {
                "new_signal": {"strong": ["shared_marker"], "weak": []},
            }
        }
        override = {
            "classification_markers": {
                "new_signal": {"strong": ["profile_marker"], "weak": ["weak1"]},
            }
        }
        result = _merge_rules(base=base, override=override)
        markers = result["classification_markers"]["new_signal"]
        assert "shared_marker" in markers["strong"]
        assert "profile_marker" in markers["strong"]
        assert "weak1" in markers["weak"]

    def test_blacklist_deduped(self) -> None:
        base = {"blacklist": ["spam", "ads"]}
        override = {"blacklist": ["ads", "promo"]}
        result = _merge_rules(base=base, override=override)
        assert result["blacklist"].count("ads") == 1
        assert "spam" in result["blacklist"]
        assert "promo" in result["blacklist"]

    def test_intent_markers_merged(self) -> None:
        base = {"intent_markers": {"U_CLOSE_FULL": ["closed"]}}
        override = {"intent_markers": {"U_CLOSE_FULL": ["manually closed"], "U_REENTER": ["re-enter"]}}
        result = _merge_rules(base=base, override=override)
        assert "closed" in result["intent_markers"]["U_CLOSE_FULL"]
        assert "manually closed" in result["intent_markers"]["U_CLOSE_FULL"]
        assert "U_REENTER" in result["intent_markers"]

    def test_new_key_in_override_added(self) -> None:
        base = {"language": "en"}
        override = {"fallback_hook": {"enabled": True}}
        result = _merge_rules(base=base, override=override)
        assert result["fallback_hook"]["enabled"] is True
        assert result["language"] == "en"


# ---------------------------------------------------------------------------
# Testi reali trader_3 — testi estratti dalla produzione
# ---------------------------------------------------------------------------

class TestTrader3RealMessages:
    """Testi reali osservati nei messaggi di trader_3.

    I messaggi usano un engine configurato con i marcatori di trader_3 (TRADER3_RULES).
    Verificano che la classificazione sia coerente con la struttura reale.
    """

    engine = make_engine()

    def test_real_new_signal(self) -> None:
        text = (
            "[trader#3] 📍SIGNAL ID: #2003📍\n"
            "COIN: $WLFI/USDT (2–5x)\n"
            "DIRECTION: LONG\n"
            "ENTRY: 0.12 – 0.124\n"
            "TARGETS: 0.130 – 0.140\n"
            "STOP LOSS: 0.11"
        )
        result = self.engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"
        assert result.confidence > 0.5
        # Combination rule [signal id, stop loss:] deve scattare
        assert any("new_signal/signal id" in m for m in result.matched_markers)

    def test_real_new_signal_avax(self) -> None:
        text = (
            "[trader#3] 📍SIGNAL ID: #2005📍\n"
            "COIN: $AVAX/USDT (2–5x)\n"
            "DIRECTION: LONG\n"
            "ENTRY: 17.40 – 18.00\n"
            "TARGETS: 18.6 – 19.6\n"
            "STOP LOSS: 15.95"
        )
        result = self.engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"

    def test_real_update_target_hit(self) -> None:
        text = (
            "[trader#3] 📍SIGNAL ID: #2002📍\n"
            "COIN: $TAO/USDT\n"
            "Target 1: 550 ✅"
        )
        result = self.engine.classify(text)
        # Contiene "target" (strong update) e "signal id" / "coin:" (strong new_signal)
        # La presenza di "target" deve dare abbastanza peso a UPDATE o NEW_SIGNAL
        # In base ai pesi: signal id + coin: + direction = 3 strong new_signal vs target = 1 strong update
        # Ma senza direction, il peso dipende da quanti strong matchano
        assert result.message_type in ("NEW_SIGNAL", "UPDATE")

    def test_real_info_only_market_update(self) -> None:
        text = "[trader#3] VIP MARKET UPDATE: $BNB\n➖➖➖➖➖"
        result = self.engine.classify(text)
        assert result.message_type == "INFO_ONLY"

    def test_real_info_only_market_analysis(self) -> None:
        text = "Market analysis: BTC outlook for the week"
        result = self.engine.classify(text)
        assert result.message_type == "INFO_ONLY"

    def test_real_intent_reenter(self) -> None:
        text = "Re-Enter at the same levels — same entry targets and SL"
        intents = self.engine.detect_intents(text)
        assert "U_REENTER" in intents

    def test_real_intent_closed_manually(self) -> None:
        text = "Closed Manually — 2.4% Profit (3x)"
        intents = self.engine.detect_intents(text)
        assert "U_CLOSE_FULL" in intents

    def test_confidence_cap_at_one(self) -> None:
        """Anche con molti marcatori, confidence non supera 1.0."""
        text = (
            "SIGNAL ID: #999\nCOIN: BTC\nDIRECTION: LONG\n"
            "ENTRY: 90000\nSTOP LOSS: 88000\nlong long long"
        )
        result = self.engine.classify(text)
        assert result.confidence <= 1.0

    def test_multiline_uppercase_signal(self) -> None:
        text = (
            "SIGNAL ID: #2006\n"
            "COIN: $BTC/USDT (2–5x)\n"
            "DIRECTION: LONG\n"
            "ENTRY: 100,000 – 101,600\n"
            "TARGETS: 102,000 – 103,000\n"
            "STOP LOSS: 97,000"
        )
        result = self.engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"
        assert result.confidence == 1.0  # score >= 1.0, capped
