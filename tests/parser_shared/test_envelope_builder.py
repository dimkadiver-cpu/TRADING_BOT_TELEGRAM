"""Tests for shared envelope_builder.build_envelope().

Verifies:
- builds a valid TraderEventEnvelopeV1 from inputs
- selects primary_intent_hint by precedence
- normalizes legacy intents (U_STOP_HIT -> SL_HIT)
- adds MISSING_TARGET warning for UPDATE with no targets
- adds CONFLICTING_INTENTS warning for mutual exclusions
- adds UNCLASSIFIED_WITH_MARKERS warning for UNCLASSIFIED + intents
- handles unknown intents gracefully (warning, not crash)
"""

from __future__ import annotations

import pytest

from src.parser.trader_profiles.shared.envelope_builder import EnvelopeInputs, build_envelope
from src.parser.event_envelope_v1 import TraderEventEnvelopeV1, InstrumentRaw
from src.parser.trader_profiles.shared.warnings import (
    MISSING_TARGET,
    CONFLICTING_INTENTS,
    UNCLASSIFIED_WITH_MARKERS,
    INTENT_OUTSIDE_TAXONOMY,
)


class TestBuildEnvelopeBasic:
    def test_build_envelope_returns_trader_event_envelope_v1(self) -> None:
        result = build_envelope(EnvelopeInputs())
        assert isinstance(result, TraderEventEnvelopeV1)

    def test_build_envelope_minimal_input_no_crash(self) -> None:
        result = build_envelope(EnvelopeInputs())
        assert result.schema_version == "trader_event_envelope_v1"

    def test_message_type_hint_propagated(self) -> None:
        result = build_envelope(EnvelopeInputs(message_type_hint="NEW_SIGNAL"))
        assert result.message_type_hint == "NEW_SIGNAL"

    def test_confidence_propagated(self) -> None:
        result = build_envelope(EnvelopeInputs(confidence=0.85))
        assert result.confidence == 0.85

    def test_diagnostics_propagated(self) -> None:
        result = build_envelope(EnvelopeInputs(diagnostics={"trader_code": "trader_a"}))
        assert result.diagnostics["trader_code"] == "trader_a"

    def test_instrument_propagated(self) -> None:
        instrument = InstrumentRaw(symbol="BTCUSDT", side="LONG")
        result = build_envelope(EnvelopeInputs(instrument=instrument))
        assert result.instrument.symbol == "BTCUSDT"
        assert result.instrument.side == "LONG"

    def test_default_instrument_when_none(self) -> None:
        result = build_envelope(EnvelopeInputs(instrument=None))
        assert result.instrument is not None
        assert isinstance(result.instrument, InstrumentRaw)


class TestPrimaryIntentHintSelection:
    def test_primary_intent_selected_from_official_intents(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["MOVE_STOP", "SL_HIT"]))
        assert result.primary_intent_hint == "SL_HIT"

    def test_primary_intent_none_when_no_intents(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=[]))
        assert result.primary_intent_hint is None

    def test_intents_detected_populated(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["SL_HIT", "CLOSE_FULL"]))
        assert "SL_HIT" in result.intents_detected
        assert "CLOSE_FULL" in result.intents_detected

    def test_single_intent_is_primary(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["CLOSE_FULL"]))
        assert result.primary_intent_hint == "CLOSE_FULL"


class TestLegacyIntentNormalization:
    def test_legacy_u_stop_hit_normalized_to_sl_hit(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["U_STOP_HIT"]))
        assert "SL_HIT" in result.intents_detected
        assert "U_STOP_HIT" not in result.intents_detected

    def test_legacy_u_close_full_normalized(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["U_CLOSE_FULL"]))
        assert "CLOSE_FULL" in result.intents_detected

    def test_mixed_legacy_and_official_deduped(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["U_STOP_HIT", "SL_HIT"]))
        assert result.intents_detected.count("SL_HIT") == 1

    def test_unknown_intent_produces_warning_not_crash(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["U_RISK_NOTE", "SL_HIT"]))
        assert "SL_HIT" in result.intents_detected
        assert INTENT_OUTSIDE_TAXONOMY in result.warnings

    def test_completely_unknown_intents_still_returns_valid_envelope(self) -> None:
        result = build_envelope(EnvelopeInputs(intents_raw=["TOTALLY_UNKNOWN"]))
        assert isinstance(result, TraderEventEnvelopeV1)
        assert INTENT_OUTSIDE_TAXONOMY in result.warnings


class TestCommonWarnings:
    def test_missing_target_warning_for_update_with_no_targets(self) -> None:
        result = build_envelope(EnvelopeInputs(
            message_type_hint="UPDATE",
            intents_raw=["MOVE_STOP"],
            targets_raw=[],
        ))
        assert MISSING_TARGET in result.warnings

    def test_no_missing_target_warning_when_targets_present(self) -> None:
        from src.parser.event_envelope_v1 import TargetRefRaw
        target = TargetRefRaw(kind="REPLY", value=12345)
        result = build_envelope(EnvelopeInputs(
            message_type_hint="UPDATE",
            intents_raw=["MOVE_STOP"],
            targets_raw=[target],
        ))
        assert MISSING_TARGET not in result.warnings

    def test_no_missing_target_warning_for_new_signal(self) -> None:
        result = build_envelope(EnvelopeInputs(
            message_type_hint="NEW_SIGNAL",
            intents_raw=["NEW_SETUP"],
            targets_raw=[],
        ))
        assert MISSING_TARGET not in result.warnings

    def test_conflicting_intents_warning_for_mutual_exclusion(self) -> None:
        # NEW_SETUP and SL_HIT are mutually exclusive
        result = build_envelope(EnvelopeInputs(intents_raw=["NEW_SETUP", "SL_HIT"]))
        assert CONFLICTING_INTENTS in result.warnings

    def test_no_conflicting_intents_warning_for_compatible_pair(self) -> None:
        # SL_HIT + CLOSE_FULL is compatible
        result = build_envelope(EnvelopeInputs(intents_raw=["SL_HIT", "CLOSE_FULL"]))
        assert CONFLICTING_INTENTS not in result.warnings

    def test_unclassified_with_markers_warning(self) -> None:
        result = build_envelope(EnvelopeInputs(
            message_type_hint="UNCLASSIFIED",
            intents_raw=["MOVE_STOP"],
        ))
        assert UNCLASSIFIED_WITH_MARKERS in result.warnings

    def test_no_unclassified_warning_when_message_type_known(self) -> None:
        result = build_envelope(EnvelopeInputs(
            message_type_hint="UPDATE",
            intents_raw=["MOVE_STOP"],
            targets_raw=[],
        ))
        assert UNCLASSIFIED_WITH_MARKERS not in result.warnings

    def test_no_warnings_for_clean_new_signal_input(self) -> None:
        result = build_envelope(EnvelopeInputs(
            message_type_hint="NEW_SIGNAL",
            intents_raw=["NEW_SETUP"],
            targets_raw=[],
        ))
        # No warnings for new signal without conflicting/unknown intents
        assert MISSING_TARGET not in result.warnings
        assert CONFLICTING_INTENTS not in result.warnings
        assert INTENT_OUTSIDE_TAXONOMY not in result.warnings
