from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.parser_v2.contracts.canonical_message import (
    CanonicalMessage,
    CloseOperation,
    ReportEvent,
    ReportPayload,
    ReportResult,
    SetStopOperation,
    SignalPayload,
    UpdateOperation,
    UpdatePayload,
)
from src.parser_v2.contracts.context import RawContext, TargetHints
from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.parser_v2.contracts.enums import (
    CANONICAL_MESSAGE_SCHEMA_VERSION,
    INTENT_CATEGORY_BY_TYPE,
    PARSED_MESSAGE_SCHEMA_VERSION,
)
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage, SignalDraft


def _raw(text: str = "msg") -> RawContext:
    return RawContext(raw_text=text)


def _price(raw: str, value: float) -> Price:
    return Price(raw=raw, value=value)


def _signal_payload() -> SignalPayload:
    return SignalPayload(
        symbol="ETHUSDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[EntryLeg(sequence=1, entry_type="MARKET")],
        stop_loss=StopLoss(price=_price("2100", 2100.0)),
        take_profits=[TakeProfit(sequence=1, price=_price("2200", 2200.0))],
        completeness="COMPLETE",
    )


def test_phase1_schema_versions_and_canonical_names() -> None:
    assert PARSED_MESSAGE_SCHEMA_VERSION == "parsed_message_v2"
    assert CANONICAL_MESSAGE_SCHEMA_VERSION == "canonical_message_v2"
    assert "MOVE_STOP_TO_BE" in INTENT_CATEGORY_BY_TYPE
    assert all(not intent.startswith("U_") for intent in INTENT_CATEGORY_BY_TYPE)


def test_parsed_message_uses_primary_class_parse_status_and_evidence_status() -> None:
    parsed = ParsedMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=1.0,
        intents=[
            ParsedIntent(
                type="MOVE_STOP_TO_BE",
                category="UPDATE",
                confidence=1.0,
            )
        ],
        primary_intent="MOVE_STOP_TO_BE",
        raw_context=_raw("stop to be"),
    )

    dumped = parsed.model_dump()
    assert dumped["schema_version"] == "parsed_message_v2"
    assert dumped["evidence_status"] == "RESOLVED"
    assert "validation_status" not in dumped
    assert "message_type" not in dumped


def test_signal_draft_reuses_signal_contract_shape() -> None:
    draft = SignalDraft(
        symbol="ETHUSDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[EntryLeg(sequence=1, entry_type="MARKET")],
        stop_loss=StopLoss(price=_price("2100", 2100.0)),
        take_profits=[TakeProfit(sequence=1, price=_price("2200", 2200.0))],
        completeness="COMPLETE",
    )

    assert draft.missing_fields == []
    assert draft.completeness == "COMPLETE"


def test_canonical_signal_forbids_update_and_targeted_actions() -> None:
    with pytest.raises(ValidationError, match="SIGNAL forbids update"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=1.0,
            signal=_signal_payload(),
            update=UpdatePayload(
                operations=[
                    UpdateOperation(
                        op_type="SET_STOP",
                        source_intent="MOVE_STOP_TO_BE",
                        set_stop=SetStopOperation(target_type="ENTRY"),
                    )
                ]
            ),
            raw_context=_raw(),
        )


def test_update_parsed_requires_operation_or_targeted_action() -> None:
    with pytest.raises(ValidationError, match="operation or targeted_action"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="UPDATE",
            parse_status="PARSED",
            confidence=1.0,
            update=UpdatePayload(),
            raw_context=_raw(),
        )


def test_update_partial_without_payload_requires_non_executable_warning() -> None:
    msg = CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARTIAL",
        confidence=0.6,
        update=UpdatePayload(),
        warnings=["multi_ref_mixed_intents_not_supported"],
        raw_context=_raw(),
    )

    assert msg.primary_class == "UPDATE"
    assert msg.parse_status == "PARTIAL"


def test_update_partial_without_payload_rejects_missing_warning() -> None:
    with pytest.raises(ValidationError, match="multi_ref_mixed_intents_not_supported"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="UPDATE",
            parse_status="PARTIAL",
            confidence=0.6,
            update=UpdatePayload(),
            raw_context=_raw(),
        )


def test_update_operation_requires_matching_single_payload() -> None:
    with pytest.raises(ValidationError, match="requires only"):
        UpdateOperation(
            op_type="SET_STOP",
            source_intent="MOVE_STOP_TO_BE",
            set_stop=SetStopOperation(target_type="ENTRY"),
            close=CloseOperation(close_scope="FULL"),
        )


def test_report_result_is_not_a_report_event() -> None:
    report = ReportPayload(result=ReportResult(raw_fragment="deal result"))
    msg = CanonicalMessage(
        parser_profile="trader_a",
        primary_class="REPORT",
        parse_status="PARSED",
        confidence=0.8,
        primary_intent="REPORT_RESULT",
        intents=["REPORT_RESULT"],
        report=report,
        raw_context=_raw(),
    )

    assert msg.report is not None
    assert msg.report.result is not None
    with pytest.raises(ValidationError):
        ReportEvent(event_type="REPORT_RESULT", source_intent="REPORT_RESULT")  # type: ignore[arg-type]


def test_info_forbids_business_payloads_and_targets() -> None:
    with pytest.raises(ValidationError, match="INFO forbids"):
        CanonicalMessage(
            parser_profile="trader_a",
            primary_class="INFO",
            parse_status="PARSED",
            confidence=0.1,
            signal=_signal_payload(),
            target_hints=TargetHints(symbols=["ETHUSDT"]),
            raw_context=_raw(),
        )
