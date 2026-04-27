"""Tests for TraderEventEnvelopeV1 schema contract.

Verifies that extra top-level fields are rejected, required shapes are accepted,
and key sub-models work as specified.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.parser.event_envelope_v1 import (
    CancelUpdateRaw,
    CloseUpdateRaw,
    EntryLegRaw,
    EntryUpdateRaw,
    InstrumentRaw,
    ReportEventRaw,
    ReportPayloadRaw,
    ReportedResultRaw,
    SignalPayloadRaw,
    SignalRawFragments,
    SizeHintRaw,
    StopLossRaw,
    StopUpdateRaw,
    TakeProfitRaw,
    TargetRefRaw,
    TargetsUpdateRaw,
    TraderEventEnvelopeV1,
    UpdatePayloadRaw,
    UpdateRawFragments,
)


# ---------------------------------------------------------------------------
# Top-level schema enforcement
# ---------------------------------------------------------------------------

class TestTopLevelExtraFieldsRejected:
    def test_extra_field_on_envelope_raises(self) -> None:
        with pytest.raises(ValidationError):
            TraderEventEnvelopeV1(extra_field="forbidden")  # type: ignore[call-arg]

    def test_extra_field_on_signal_payload_raises(self) -> None:
        with pytest.raises(ValidationError):
            SignalPayloadRaw(unknown_key="bad")  # type: ignore[call-arg]

    def test_extra_field_on_update_payload_raises(self) -> None:
        with pytest.raises(ValidationError):
            UpdatePayloadRaw(operations=[])  # type: ignore[call-arg]

    def test_extra_field_on_report_payload_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReportPayloadRaw(reported_result=None)  # type: ignore[call-arg]

    def test_extra_field_on_instrument_raises(self) -> None:
        with pytest.raises(ValidationError):
            InstrumentRaw(unknown="x")  # type: ignore[call-arg]

    def test_extra_field_on_entry_leg_raises(self) -> None:
        with pytest.raises(ValidationError):
            EntryLegRaw(sequence=1, entry_type="LIMIT", price=100.0, legacy_role="x")  # type: ignore[call-arg]

    def test_extra_field_on_stop_update_raises(self) -> None:
        with pytest.raises(ValidationError):
            StopUpdateRaw(op_type="SET_STOP")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# MessageTypeHint — REPORT now valid
# ---------------------------------------------------------------------------

class TestMessageTypeHint:
    def test_report_accepted(self) -> None:
        env = TraderEventEnvelopeV1(message_type_hint="REPORT")
        assert env.message_type_hint == "REPORT"

    def test_all_valid_types(self) -> None:
        for mtype in ("NEW_SIGNAL", "UPDATE", "REPORT", "INFO_ONLY", "UNCLASSIFIED"):
            env = TraderEventEnvelopeV1(message_type_hint=mtype)  # type: ignore[arg-type]
            assert env.message_type_hint == mtype

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            TraderEventEnvelopeV1(message_type_hint="SIGNAL")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# UpdatePayloadRaw — new structured shape (no operations list)
# ---------------------------------------------------------------------------

class TestUpdatePayloadRaw:
    def test_empty_update_payload(self) -> None:
        payload = UpdatePayloadRaw()
        assert payload.stop_update is None
        assert payload.close_update is None
        assert payload.cancel_update is None
        assert payload.entry_update is None
        assert payload.targets_update is None

    def test_stop_update_to_entry(self) -> None:
        payload = UpdatePayloadRaw(
            stop_update=StopUpdateRaw(mode="TO_ENTRY", raw="move stop to breakeven")
        )
        assert payload.stop_update is not None
        assert payload.stop_update.mode == "TO_ENTRY"

    def test_stop_update_to_price(self) -> None:
        payload = UpdatePayloadRaw(
            stop_update=StopUpdateRaw(mode="TO_PRICE", price=62580.0, raw="move stop to 62580")
        )
        assert payload.stop_update.price == 62580.0  # type: ignore[union-attr]

    def test_close_update_full(self) -> None:
        payload = UpdatePayloadRaw(
            close_update=CloseUpdateRaw(
                close_fraction=1.0, close_percent=100.0, close_price=62420.0,
                close_scope="FULL", raw="close full at 62420"
            )
        )
        assert payload.close_update.close_scope == "FULL"  # type: ignore[union-attr]

    def test_close_update_partial(self) -> None:
        payload = UpdatePayloadRaw(
            close_update=CloseUpdateRaw(close_fraction=0.5, close_percent=50.0, close_scope="PARTIAL")
        )
        assert payload.close_update.close_fraction == 0.5  # type: ignore[union-attr]

    def test_cancel_update(self) -> None:
        payload = UpdatePayloadRaw(cancel_update=CancelUpdateRaw(cancel_scope="ALL_OPEN", raw="cancel all"))
        assert payload.cancel_update.cancel_scope == "ALL_OPEN"  # type: ignore[union-attr]

    def test_entry_update_add(self) -> None:
        entry = EntryLegRaw(sequence=1, entry_type="LIMIT", price=141.2, role="AVERAGING")
        payload = UpdatePayloadRaw(entry_update=EntryUpdateRaw(mode="ADD_ENTRY", entries=[entry]))
        assert payload.entry_update.mode == "ADD_ENTRY"  # type: ignore[union-attr]
        assert len(payload.entry_update.entries) == 1  # type: ignore[union-attr]

    def test_targets_update_replace_all(self) -> None:
        tp = TakeProfitRaw(sequence=1, price=63120.0, label="TP1")
        payload = UpdatePayloadRaw(
            targets_update=TargetsUpdateRaw(mode="REPLACE_ALL", take_profits=[tp])
        )
        assert payload.targets_update.mode == "REPLACE_ALL"  # type: ignore[union-attr]

    def test_composite_update_stop_and_close(self) -> None:
        payload = UpdatePayloadRaw(
            stop_update=StopUpdateRaw(mode="TO_ENTRY"),
            close_update=CloseUpdateRaw(close_fraction=0.5, close_scope="PARTIAL"),
        )
        assert payload.stop_update is not None
        assert payload.close_update is not None

    def test_raw_fragments(self) -> None:
        frags = UpdateRawFragments(stop_text_raw="move BE", close_text_raw="close 50%")
        assert frags.stop_text_raw == "move BE"
        assert frags.cancel_text_raw is None


# ---------------------------------------------------------------------------
# ReportPayloadRaw — reported_results plural, summary_text_raw
# ---------------------------------------------------------------------------

class TestReportPayloadRaw:
    def test_reported_results_plural(self) -> None:
        result = ReportedResultRaw(value=3.2, unit="R", text="+3.2R")
        payload = ReportPayloadRaw(
            events=[ReportEventRaw(event_type="FINAL_RESULT", result=result, raw_fragment="final")],
            reported_results=[result],
            summary_text_raw="final result +3.2R",
        )
        assert len(payload.reported_results) == 1
        assert payload.summary_text_raw == "final result +3.2R"

    def test_no_singular_reported_result_field(self) -> None:
        with pytest.raises((ValidationError, TypeError)):
            ReportPayloadRaw(reported_result=ReportedResultRaw(value=1.0, unit="R"))  # type: ignore[call-arg]

    def test_sl_hit_event_type(self) -> None:
        event = ReportEventRaw(event_type="SL_HIT", price=62450.0, raw_fragment="stopped out")
        assert event.event_type == "SL_HIT"

    def test_exit_be_event_type(self) -> None:
        event = ReportEventRaw(event_type="EXIT_BE", price=62510.0)
        assert event.event_type == "EXIT_BE"

    def test_partial_result_event_type(self) -> None:
        event = ReportEventRaw(event_type="PARTIAL_RESULT", level=1)
        assert event.event_type == "PARTIAL_RESULT"

    def test_unknown_event_type(self) -> None:
        event = ReportEventRaw(event_type="UNKNOWN")
        assert event.event_type == "UNKNOWN"

    def test_invalid_event_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReportEventRaw(event_type="STOP_HIT")  # type: ignore[arg-type]

    def test_invalid_event_type_breakeven_exit_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReportEventRaw(event_type="BREAKEVEN_EXIT")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SignalPayloadRaw — SignalRawFragments typed, new fields
# ---------------------------------------------------------------------------

class TestSignalPayloadRaw:
    def test_raw_fragments_typed(self) -> None:
        frags = SignalRawFragments(entry_text_raw="market now", stop_text_raw="SL 62450")
        payload = SignalPayloadRaw(raw_fragments=frags)
        assert payload.raw_fragments.entry_text_raw == "market now"
        assert payload.raw_fragments.stop_text_raw == "SL 62450"
        assert payload.raw_fragments.take_profits_text_raw is None

    def test_new_fields_leverage_invalidation_conditions(self) -> None:
        payload = SignalPayloadRaw(
            leverage_hint=5.0,
            invalidation_rule="cancel if below 62450",
            conditions="only on 15m close",
        )
        assert payload.leverage_hint == 5.0
        assert payload.invalidation_rule == "cancel if below 62450"
        assert payload.conditions == "only on 15m close"

    def test_raw_fragments_extra_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            SignalRawFragments(unknown_raw="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# EntryLegRaw — SizeHintRaw, note field
# ---------------------------------------------------------------------------

class TestEntryLegRaw:
    def test_size_hint_structured(self) -> None:
        size = SizeHintRaw(value=0.33, unit="FRACTION", raw="1/3")
        leg = EntryLegRaw(sequence=1, entry_type="LIMIT", price=88650.0, size_hint=size)
        assert leg.size_hint is not None
        assert leg.size_hint.unit == "FRACTION"

    def test_note_field(self) -> None:
        leg = EntryLegRaw(sequence=1, entry_type="MARKET", note="enter now")
        assert leg.note == "enter now"

    def test_entry_type_unknown(self) -> None:
        leg = EntryLegRaw(sequence=1, entry_type="UNKNOWN")
        assert leg.entry_type == "UNKNOWN"

    def test_limit_without_price_raises(self) -> None:
        with pytest.raises(ValidationError):
            EntryLegRaw(sequence=1, entry_type="LIMIT", price=None)


# ---------------------------------------------------------------------------
# TakeProfitRaw — raw field
# ---------------------------------------------------------------------------

class TestTakeProfitRaw:
    def test_raw_field(self) -> None:
        tp = TakeProfitRaw(sequence=1, price=63700.0, label="TP1", raw="TP1 63700")
        assert tp.raw == "TP1 63700"


# ---------------------------------------------------------------------------
# Full envelope golden case
# ---------------------------------------------------------------------------

class TestFullEnvelopeGoldenCases:
    def test_new_signal_envelope(self) -> None:
        env = TraderEventEnvelopeV1(
            message_type_hint="NEW_SIGNAL",
            intents_detected=["NEW_SETUP"],
            primary_intent_hint="NEW_SETUP",
            instrument=InstrumentRaw(symbol="BTCUSDT", side="SHORT", market_type="FUTURES"),
            signal_payload_raw=SignalPayloadRaw(
                entry_structure="TWO_STEP",
                entries=[EntryLegRaw(sequence=1, entry_type="LIMIT", price=88650.0)],
                stop_loss=StopLossRaw(price=89450.0, raw="SL 89450"),
                take_profits=[TakeProfitRaw(sequence=1, price=87500.0, label="TP1")],
                leverage_hint=3.0,
            ),
            confidence=0.96,
        )
        assert env.message_type_hint == "NEW_SIGNAL"
        assert env.signal_payload_raw.leverage_hint == 3.0

    def test_report_envelope(self) -> None:
        env = TraderEventEnvelopeV1(
            message_type_hint="REPORT",
            intents_detected=["REPORT_FINAL_RESULT"],
            primary_intent_hint="REPORT_FINAL_RESULT",
            report_payload_raw=ReportPayloadRaw(
                events=[ReportEventRaw(
                    event_type="FINAL_RESULT",
                    result=ReportedResultRaw(value=3.2, unit="R", text="+3.2R"),
                    raw_fragment="final result +3.2R",
                )],
                reported_results=[ReportedResultRaw(value=3.2, unit="R", text="+3.2R final")],
                summary_text_raw="final result +3.2R",
            ),
            confidence=0.95,
        )
        assert env.message_type_hint == "REPORT"
        assert len(env.report_payload_raw.reported_results) == 1

    def test_update_envelope_composite(self) -> None:
        env = TraderEventEnvelopeV1(
            message_type_hint="UPDATE",
            intents_detected=["CLOSE_PARTIAL", "MOVE_STOP"],
            primary_intent_hint="CLOSE_PARTIAL",
            update_payload_raw=UpdatePayloadRaw(
                stop_update=StopUpdateRaw(mode="TO_ENTRY", raw="move stop to entry"),
                close_update=CloseUpdateRaw(
                    close_fraction=0.5, close_percent=50.0,
                    close_price=2128.0, close_scope="PARTIAL", raw="close 50% at 2128",
                ),
            ),
            targets_raw=[TargetRefRaw(kind="REPLY", value=1701)],
            confidence=0.94,
        )
        assert env.update_payload_raw.stop_update is not None
        assert env.update_payload_raw.close_update is not None
