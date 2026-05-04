"""Test RulesEngine caricato dal parsing_rules.json reale di trader_3.

Verifica che:
- RulesEngine.load() carichi il file senza errori
- number_format, language, fallback_hook siano corretti
- classify() identifichi correttamente NEW_SIGNAL, UPDATE, INFO_ONLY
- detect_intents() riconosca U_TP_HIT, U_STOP_HIT, U_CLOSE_FULL, U_REENTER
- I messaggi reali estratti dai report vengano classificati correttamente
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parser.rules_engine import RulesEngine

_RULES_PATH = Path(__file__).resolve().parents[1] / "parsing_rules.json"


@pytest.fixture(scope="module")
def engine() -> RulesEngine:
    return RulesEngine.load(_RULES_PATH)


# ---------------------------------------------------------------------------
# Caricamento e proprietà
# ---------------------------------------------------------------------------

class TestRulesEngineLoad:
    def test_loads_without_error(self, engine: RulesEngine) -> None:
        assert isinstance(engine, RulesEngine)

    def test_language_is_en(self, engine: RulesEngine) -> None:
        assert engine.language == "en"

    def test_number_format_dot_decimal(self, engine: RulesEngine) -> None:
        nf = engine.number_format
        assert nf["decimal_separator"] == "."

    def test_number_format_comma_thousands(self, engine: RulesEngine) -> None:
        nf = engine.number_format
        assert nf["thousands_separator"] == ","

    def test_fallback_hook_disabled(self, engine: RulesEngine) -> None:
        assert engine.fallback_hook_enabled is False

    def test_blacklist_is_empty(self, engine: RulesEngine) -> None:
        assert engine.is_blacklisted("SIGNAL ID: #1 BTC LONG") is False


# ---------------------------------------------------------------------------
# NEW_SIGNAL — messaggi reali da trader_3
# ---------------------------------------------------------------------------

class TestClassifyNewSignal:
    def test_full_signal_btc_long(self, engine: RulesEngine) -> None:
        text = (
            "[trader#3] 📍SIGNAL ID: #1997📍\n"
            "COIN: $BTC/USDT\n"
            "Direction: LONG\n"
            "ENTRY: 105200 - 107878\n"
            "TARGETS: 109600, 112300, 115000\n"
            "STOP LOSS: 102450"
        )
        result = engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"
        assert result.confidence > 0.5

    def test_full_signal_eth_short(self, engine: RulesEngine) -> None:
        text = (
            "[trader#3] 📍SIGNAL ID: #2002📍\n"
            "COIN: $ETH/USDT\n"
            "DIRECTION: SHORT\n"
            "ENTRY: 3,840 – 3,870\n"
            "TARGETS: 3,700, 3,610\n"
            "SL: 3,955"
        )
        result = engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"

    def test_full_signal_avax_long(self, engine: RulesEngine) -> None:
        text = (
            "[trader#3] 📍SIGNAL ID: #2005📍\n"
            "COIN: $AVAX/USDT (2–5x)\n"
            "DIRECTION: LONG\n"
            "ENTRY: 17.40 – 18.00\n"
            "TARGETS: 18.6 – 19.6 – 21.5\n"
            "STOP LOSS: 15.95"
        )
        result = engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"

    def test_full_signal_btc_comma_thousands(self, engine: RulesEngine) -> None:
        """Segnale BTC con prezzi in migliaia con virgola: 100,000."""
        text = (
            "[trader#3] 📍SIGNAL ID: #2006📍\n"
            "COIN: $BTC/USDT (2–5x)\n"
            "DIRECTION: LONG\n"
            "ENTRY: 100,000 – 101,600\n"
            "TARGETS: 102,000 – 103,000 – 105,000\n"
            "STOP LOSS: 97,000"
        )
        result = engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"
        assert result.confidence == 1.0

    def test_signal_matched_markers_contain_new_signal_labels(self, engine: RulesEngine) -> None:
        text = (
            "COIN: WLFIUSDT\n"
            "DIRECTION: LONG\n"
            "ENTRY: 0.12\n"
            "STOP LOSS: 0.11\n"
            "TARGETS: 0.14"
        )
        result = engine.classify(text)
        assert result.message_type == "NEW_SIGNAL"
        assert any("new_signal/" in m for m in result.matched_markers)

    def test_combination_rule_boosts_confidence(self, engine: RulesEngine) -> None:
        """STOP LOSS + COIN combo deve far scattare la combination rule."""
        base = engine.classify("COIN: BTCUSDT")
        full = engine.classify("COIN: BTCUSDT\nSTOP LOSS: 90000")
        assert full.confidence >= base.confidence


# ---------------------------------------------------------------------------
# UPDATE — messaggi reali da trader_3
# ---------------------------------------------------------------------------

class TestClassifyUpdate:
    def test_tp_hit_with_profit(self, engine: RulesEngine) -> None:
        text = (
            "SIGNAL ID: #1997\n"
            "Target 1: 109600 ✅\n"
            "🔥15% Profit (5x)🔥"
        )
        result = engine.classify(text)
        assert result.message_type == "UPDATE"

    def test_tp_hit_multi_targets(self, engine: RulesEngine) -> None:
        text = (
            "SIGNAL ID: #1998\n"
            "Target 1: 109600 ✅\n"
            "Target 2: 112300 ✅\n"
            "🔥38.8% Profit (4x)🔥"
        )
        result = engine.classify(text)
        assert result.message_type == "UPDATE"

    def test_loss_update(self, engine: RulesEngine) -> None:
        text = (
            "SIGNAL ID: #1999\n"
            "Unfortunately, it broke down\n"
            "STOP LOSS: 3298\n"
            "🚫3.13% Loss (2x)🚫"
        )
        result = engine.classify(text)
        assert result.message_type == "UPDATE"

    def test_loss_update_no_stop_price(self, engine: RulesEngine) -> None:
        text = "SIGNAL ID: #2000\n🚫3.85% Loss (2x)🚫"
        result = engine.classify(text)
        assert result.message_type == "UPDATE"

    def test_closed_manually(self, engine: RulesEngine) -> None:
        text = "SIGNAL ID: #2001\nClosed Manually\n🚫12% Loss (2x)🚫"
        result = engine.classify(text)
        assert result.message_type == "UPDATE"

    def test_reenter(self, engine: RulesEngine) -> None:
        text = "SIGNAL ID: #2001\nRe-Enter.\nSame Entry level, Targets & SL"
        result = engine.classify(text)
        assert result.message_type == "UPDATE"

    def test_target_hit_no_signal_id(self, engine: RulesEngine) -> None:
        """Aggiornamento senza SIGNAL ID — solo ✅ è sufficiente per UPDATE."""
        text = "Target 1: 109600 ✅"
        result = engine.classify(text)
        assert result.message_type == "UPDATE"


# ---------------------------------------------------------------------------
# INFO_ONLY — messaggi reali da trader_3
# ---------------------------------------------------------------------------

class TestClassifyInfoOnly:
    def test_vip_market_update_btc(self, engine: RulesEngine) -> None:
        text = "[trader#3] VIP MARKET UPDATE: $BTC\n➖➖➖➖➖"
        result = engine.classify(text)
        assert result.message_type == "INFO_ONLY"

    def test_vip_market_update_bnb(self, engine: RulesEngine) -> None:
        text = "VIP MARKET UPDATE: $BNB analysis"
        result = engine.classify(text)
        assert result.message_type == "INFO_ONLY"

    def test_market_analysis(self, engine: RulesEngine) -> None:
        text = "MARKET ANALYSIS: BTC likely consolidating before breakout"
        result = engine.classify(text)
        assert result.message_type == "INFO_ONLY"


# ---------------------------------------------------------------------------
# UNCLASSIFIED
# ---------------------------------------------------------------------------

class TestClassifyUnclassified:
    def test_empty_text(self, engine: RulesEngine) -> None:
        result = engine.classify("")
        assert result.message_type == "UNCLASSIFIED"
        assert result.confidence == 0.0

    def test_unrecognised_text(self, engine: RulesEngine) -> None:
        result = engine.classify("👋 hello everyone")
        assert result.message_type == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# detect_intents — messaggi reali
# ---------------------------------------------------------------------------

class TestDetectIntents:
    def test_tp_hit_via_checkmark(self, engine: RulesEngine) -> None:
        text = "Target 1: 109600 ✅\n🔥15% Profit (5x)🔥"
        intents = engine.detect_intents(text)
        assert "U_TP_HIT" in intents

    def test_tp_hit_via_profit_marker(self, engine: RulesEngine) -> None:
        text = "🔥38.8% Profit (4x)🔥"
        intents = engine.detect_intents(text)
        assert "U_TP_HIT" in intents

    def test_stop_hit_via_loss_marker(self, engine: RulesEngine) -> None:
        text = "🚫3.13% Loss (2x)🚫"
        intents = engine.detect_intents(text)
        assert "U_STOP_HIT" in intents

    def test_stop_hit_via_broke_down(self, engine: RulesEngine) -> None:
        text = "Unfortunately, it broke down\n🚫3.13% Loss (2x)🚫"
        intents = engine.detect_intents(text)
        assert "U_STOP_HIT" in intents

    def test_close_full_via_closed_manually(self, engine: RulesEngine) -> None:
        text = "Closed Manually — took profit"
        intents = engine.detect_intents(text)
        assert "U_CLOSE_FULL" in intents

    def test_reenter_via_re_enter(self, engine: RulesEngine) -> None:
        text = "Re-Enter. Same Entry level, Targets & SL"
        intents = engine.detect_intents(text)
        assert "U_REENTER" in intents

    def test_no_intents_for_new_signal(self, engine: RulesEngine) -> None:
        """I segnali nuovi non hanno intent UPDATE."""
        text = (
            "COIN: $BTC/USDT\nDIRECTION: LONG\n"
            "ENTRY: 100000\nSTOP LOSS: 97000\nTARGETS: 105000"
        )
        intents = engine.detect_intents(text)
        # Nessun intent UPDATE deve matchare (assenza di marker di update)
        assert "U_CLOSE_FULL" not in intents
        assert "U_REENTER" not in intents

    def test_intents_hint_present_in_classify_result(self, engine: RulesEngine) -> None:
        text = "Target 1: 109600 ✅\n🔥15% Profit (5x)🔥"
        result = engine.classify(text)
        assert "U_TP_HIT" in result.intents_hint
