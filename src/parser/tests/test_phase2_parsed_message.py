from __future__ import annotations

from typing import get_args

import pytest

from src.parser.canonical_v1.models import Price, RawContext, SignalPayload, TargetScope, Targeting
from src.parser.intent_types import IntentCategory, IntentType
from src.parser.parsed_message import (
    AddEntryEntities,
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    ExitBeEntities,
    InfoOnlyEntities,
    IntentEntities,
    IntentResult,
    InvalidateSetupEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    ParsedMessage,
    ReenterEntities,
    ReportFinalResultEntities,
    ReportPartialResultEntities,
    ReportedResult,
    SlHitEntities,
    TpHitEntities,
    UpdateTakeProfitsEntities,
)


def _price(raw: str, value: float) -> Price:
    return Price(raw=raw, value=value)


def _raw_context(text: str = "testo") -> RawContext:
    return RawContext(raw_text=text)


def _targeting() -> Targeting:
    return Targeting(
        refs=[],
        scope=TargetScope(kind="UNKNOWN"),
        strategy="UNRESOLVED",
        targeted=False,
    )


def test_phase2_intent_type_contains_all_spec_values() -> None:
    assert {intent.value for intent in IntentType} == {
        "MOVE_STOP_TO_BE",
        "MOVE_STOP",
        "CLOSE_FULL",
        "CLOSE_PARTIAL",
        "CANCEL_PENDING",
        "INVALIDATE_SETUP",
        "REENTER",
        "ADD_ENTRY",
        "UPDATE_TAKE_PROFITS",
        "ENTRY_FILLED",
        "TP_HIT",
        "SL_HIT",
        "EXIT_BE",
        "REPORT_PARTIAL_RESULT",
        "REPORT_FINAL_RESULT",
        "INFO_ONLY",
    }


def test_phase2_intent_category_literal_values() -> None:
    assert set(get_args(IntentCategory)) == {"UPDATE", "REPORT", "INFO"}


@pytest.mark.parametrize(
    ("entity_cls", "payload"),
    [
        (MoveStopToBEEntities, {}),
        (
            MoveStopEntities,
            {
                "new_stop_price": _price("43000", 43000.0),
                "stop_to_tp_level": 1,
            },
        ),
        (CloseFullEntities, {"close_price": _price("44000", 44000.0)}),
        (ClosePartialEntities, {"fraction": 0.5, "close_price": _price("44100", 44100.0)}),
        (CancelPendingEntities, {"scope": "TARGETED"}),
        (InvalidateSetupEntities, {}),
        (
            ReenterEntities,
            {
                "entries": [_price("42000", 42000.0), _price("41900", 41900.0)],
                "entry_type": "LIMIT",
                "entry_structure": "TWO_STEP",
            },
        ),
        (
            AddEntryEntities,
            {
                "entry_price": _price("41800", 41800.0),
                "entry_type": "LIMIT",
            },
        ),
        (
            UpdateTakeProfitsEntities,
            {
                "new_take_profits": [_price("45000", 45000.0)],
                "target_tp_level": 1,
                "mode": "UPDATE_ONE",
            },
        ),
        (
            EntryFilledEntities,
            {
                "fill_price": _price("42100", 42100.0),
                "average_price": _price("42050", 42050.0),
                "level": 2,
            },
        ),
        (
            TpHitEntities,
            {
                "level": 1,
                "price": _price("46000", 46000.0),
                "result": ReportedResult(value=2.0, unit="R"),
            },
        ),
        (
            SlHitEntities,
            {
                "price": _price("41000", 41000.0),
                "result": ReportedResult(value=-1.0, unit="R"),
            },
        ),
        (ExitBeEntities, {"price": _price("42000", 42000.0)}),
        (ReportPartialResultEntities, {"result": ReportedResult(value=1.5, unit="PERCENT")}),
        (ReportFinalResultEntities, {"result": ReportedResult(value=3.0, unit="R", text="+3R")}),
        (InfoOnlyEntities, {}),
    ],
)
def test_phase2_entity_models_roundtrip_to_dict(
    entity_cls: type[IntentEntities],
    payload: dict,
) -> None:
    entity = entity_cls(**payload)

    assert entity.to_dict() == entity.model_dump()


def test_phase2_intent_result_defaults_match_spec() -> None:
    intent = IntentResult(
        type=IntentType.MOVE_STOP,
        category="UPDATE",
        entities=MoveStopEntities(new_stop_price=_price("43000", 43000.0)),
        confidence=0.7,
    )

    assert intent.detection_strength == "weak"
    assert intent.status == "CANDIDATE"
    assert intent.valid_refs == []
    assert intent.invalid_refs == []
    assert intent.invalid_reason is None
    assert intent.targeting_override is None


def test_phase2_parsed_message_serializes_to_json() -> None:
    parsed = ParsedMessage(
        parser_profile="trader_test",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.85,
        intents=[
            IntentResult(
                type=IntentType.MOVE_STOP,
                category="UPDATE",
                entities=MoveStopEntities(new_stop_price=_price("43000", 43000.0)),
                confidence=0.8,
                targeting_override=_targeting(),
            )
        ],
        primary_intent=IntentType.MOVE_STOP,
        targeting=_targeting(),
        validation_status="PENDING",
        raw_context=_raw_context(),
    )

    restored = ParsedMessage.model_validate_json(parsed.model_dump_json())

    assert restored == parsed


def test_phase2_parsed_message_allows_signal_payload() -> None:
    parsed = ParsedMessage(
        parser_profile="trader_test",
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=0.95,
        signal=SignalPayload(),
        raw_context=_raw_context("signal"),
    )

    assert parsed.signal is not None
