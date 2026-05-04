from __future__ import annotations

from src.parser.adapters.legacy_to_event_envelope_v1 import adapt_legacy_parse_result_to_event_envelope
from src.parser.canonical_v1.normalizer import normalize
from src.parser.trader_profiles.base import ParserContext, TraderParseResult


def _context(raw_text: str = "message") -> ParserContext:
    return ParserContext(
        trader_code="trader_c",
        message_id=1,
        reply_to_message_id=None,
        channel_id="-1001",
        raw_text=raw_text,
    )


def test_adapter_prefers_structured_entries_over_flat_entry_list() -> None:
    result = TraderParseResult(
        message_type="NEW_SIGNAL",
        intents=["NS_CREATE_SIGNAL"],
        entities={
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "entry_structure": "TWO_STEP",
            "entries": [
                {"sequence": 1, "price": 88650.0, "size_hint": "1/3"},
                {"sequence": 2, "price": 89100.0, "size_hint": "2/3"},
            ],
            "entry": [88650.0],
            "stop_loss": 89450.0,
            "take_profits": [87500.0, 86800.0, 85800.0],
        },
        confidence=0.95,
    )

    envelope = adapt_legacy_parse_result_to_event_envelope(result)

    assert envelope.signal_payload_raw.entry_structure == "TWO_STEP"
    assert [leg.price for leg in envelope.signal_payload_raw.entries] == [88650.0, 89100.0]
    assert [leg.size_hint for leg in envelope.signal_payload_raw.entries] == ["1/3", "2/3"]


def test_adapter_maps_move_stop_to_be_to_set_stop() -> None:
    result = TraderParseResult(
        message_type="UPDATE",
        intents=["U_MOVE_STOP_TO_BE"],
        primary_intent="U_MOVE_STOP_TO_BE",
        entities={"new_stop_level": "BE"},
        target_refs=[{"kind": "reply", "ref": 1701}],
    )

    envelope = adapt_legacy_parse_result_to_event_envelope(result)

    assert len(envelope.update_payload_raw.operations) == 1
    op = envelope.update_payload_raw.operations[0]
    assert op.op_type == "SET_STOP"
    assert op.set_stop is not None
    assert op.set_stop.target_type == "ENTRY"
    assert envelope.targets_raw[0].kind == "REPLY"
    assert envelope.targets_raw[0].value == 1701


def test_adapter_maps_update_stop_and_remove_pending_entry() -> None:
    result = TraderParseResult(
        message_type="UPDATE",
        intents=["U_UPDATE_STOP", "U_REMOVE_PENDING_ENTRY"],
        entities={"new_stop_price": 89950.0},
    )

    envelope = adapt_legacy_parse_result_to_event_envelope(result)

    assert [op.op_type for op in envelope.update_payload_raw.operations] == ["SET_STOP", "CANCEL_PENDING"]
    assert envelope.update_payload_raw.operations[0].set_stop is not None
    assert envelope.update_payload_raw.operations[0].set_stop.target_type == "PRICE"
    assert envelope.update_payload_raw.operations[0].set_stop.value == 89950.0
    assert envelope.update_payload_raw.operations[1].cancel_pending is not None
    assert envelope.update_payload_raw.operations[1].cancel_pending.cancel_scope == "REMOVE_PENDING_ENTRY"


def test_normalizer_uses_adapter_for_two_step_entries_from_entities_entries() -> None:
    result = TraderParseResult(
        message_type="NEW_SIGNAL",
        intents=["NS_CREATE_SIGNAL"],
        entities={
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "entry_structure": "TWO_STEP",
            "entries": [
                {"sequence": 1, "price": 88650.0, "size_hint": "1/3"},
                {"sequence": 2, "price": 89100.0, "size_hint": "2/3"},
            ],
            "entry": [88650.0],
            "stop_loss": 89450.0,
            "take_profits": [87500.0, 86800.0, 85800.0],
            "risk_percent": 1.0,
            "risk_value_raw": "1% dep",
        },
        confidence=0.96,
    )

    message = normalize(result, _context())

    assert message.primary_class == "SIGNAL"
    assert message.parse_status == "PARSED"
    assert message.signal is not None
    assert message.signal.entry_structure == "TWO_STEP"
    assert len(message.signal.entries) == 2
    assert [leg.price.value for leg in message.signal.entries if leg.price is not None] == [88650.0, 89100.0]
    assert message.signal.risk_hint is not None
    assert message.signal.risk_hint.unit == "PERCENT"


def test_normalizer_supports_update_and_report_from_same_legacy_message() -> None:
    result = TraderParseResult(
        message_type="UPDATE",
        intents=["U_TP_HIT", "U_CLOSE_PARTIAL"],
        primary_intent="U_TP_HIT",
        entities={
            "hit_target": "TP1",
            "close_fraction": 0.5,
            "close_price": 87500.0,
        },
        reported_results=[{"value": 2.0, "unit": "R", "text": "+2R"}],
        confidence=0.94,
    )

    message = normalize(result, _context())

    assert message.primary_class == "UPDATE"
    assert message.parse_status == "PARSED"
    assert message.update is not None
    assert len(message.update.operations) == 1
    assert message.update.operations[0].op_type == "CLOSE"
    assert message.update.operations[0].close is not None
    assert message.update.operations[0].close.close_fraction == 0.5
    assert message.report is not None
    assert len(message.report.events) == 1
    assert message.report.events[0].event_type == "TP_HIT"
    assert message.report.events[0].level == 1
    assert message.report.reported_result is not None
    assert message.report.reported_result.unit == "R"
