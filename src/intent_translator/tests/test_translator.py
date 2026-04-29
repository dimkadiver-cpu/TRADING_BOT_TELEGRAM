from __future__ import annotations

import pytest

from src.parser.canonical_v1.models import (
    Price,
    RawContext,
    ReportedResult,
    TargetRef,
    TargetScope,
    Targeting,
)
from src.parser.intent_types import IntentType
from src.parser.parsed_message import (
    AddEntryEntities,
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    ExitBeEntities,
    InfoOnlyEntities,
    IntentResult,
    InvalidateSetupEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    ParsedMessage,
    ReenterEntities,
    ReportFinalResultEntities,
    ReportPartialResultEntities,
    SlHitEntities,
    TpHitEntities,
    UpdateTakeProfitsEntities,
)
from src.intent_translator.translator import IntentTranslator


def _raw_context(text: str = "test message") -> RawContext:
    return RawContext(
        raw_text=text,
        reply_to_message_id=321,
        extracted_links=[],
        hashtags=[],
        source_chat_id="-100123",
        acquisition_mode="live",
    )


def _parsed_message(
    *intents: IntentResult,
    primary_class: str = "UPDATE",
    parse_status: str = "PARSED",
    signal=None,
    targeting: Targeting | None = None,
    warnings: list[str] | None = None,
    diagnostics: dict | None = None,
) -> ParsedMessage:
    return ParsedMessage(
        parser_profile="trader_a",
        primary_class=primary_class,
        parse_status=parse_status,
        confidence=0.91,
        signal=signal,
        intents=list(intents),
        primary_intent=intents[0].type if intents else None,
        targeting=targeting,
        validation_status="VALIDATED",
        warnings=warnings or [],
        diagnostics=diagnostics or {},
        raw_context=_raw_context(),
    )


@pytest.mark.parametrize(
    ("intent", "assertion"),
    [
        (
            IntentResult(
                type=IntentType.MOVE_STOP_TO_BE,
                category="UPDATE",
                entities=MoveStopToBEEntities(),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "SET_STOP"
                and op.set_stop.target_type == "ENTRY"
            ),
        ),
        (
            IntentResult(
                type=IntentType.MOVE_STOP,
                category="UPDATE",
                entities=MoveStopEntities(new_stop_price=Price.from_float(101.5)),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "SET_STOP"
                and op.set_stop.target_type == "PRICE"
                and op.set_stop.value == 101.5
            ),
        ),
        (
            IntentResult(
                type=IntentType.CLOSE_FULL,
                category="UPDATE",
                entities=CloseFullEntities(close_price=Price.from_float(99.1)),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "CLOSE"
                and op.close.close_scope == "FULL"
                and op.close.close_price.value == 99.1
            ),
        ),
        (
            IntentResult(
                type=IntentType.CLOSE_PARTIAL,
                category="UPDATE",
                entities=ClosePartialEntities(fraction=0.5, close_price=Price.from_float(98.0)),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "CLOSE"
                and op.close.close_scope == "PARTIAL"
                and op.close.close_fraction == 0.5
                and op.close.close_price.value == 98.0
            ),
        ),
        (
            IntentResult(
                type=IntentType.CANCEL_PENDING,
                category="UPDATE",
                entities=CancelPendingEntities(scope="ALL_SHORT"),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "CANCEL_PENDING"
                and op.cancel_pending.cancel_scope == "ALL_SHORT"
            ),
        ),
        (
            IntentResult(
                type=IntentType.INVALIDATE_SETUP,
                category="UPDATE",
                entities=InvalidateSetupEntities(),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "CANCEL_PENDING"
                and op.cancel_pending.cancel_scope == "ALL_POSITIONS"
            ),
        ),
        (
            IntentResult(
                type=IntentType.REENTER,
                category="UPDATE",
                entities=ReenterEntities(
                    entries=[Price.from_float(100.0), Price.from_float(99.0)],
                    entry_type="LIMIT",
                    entry_structure="TWO_STEP",
                ),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "MODIFY_ENTRIES"
                and op.modify_entries.mode == "REENTER"
                and op.modify_entries.entry_structure == "TWO_STEP"
                and [leg.price.value for leg in op.modify_entries.entries] == [100.0, 99.0]
            ),
        ),
        (
            IntentResult(
                type=IntentType.ADD_ENTRY,
                category="UPDATE",
                entities=AddEntryEntities(
                    entry_price=Price.from_float(97.5),
                    entry_type="LIMIT",
                ),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "MODIFY_ENTRIES"
                and op.modify_entries.mode == "ADD"
                and op.modify_entries.entry_structure is None
                and len(op.modify_entries.entries) == 1
                and op.modify_entries.entries[0].price.value == 97.5
            ),
        ),
        (
            IntentResult(
                type=IntentType.UPDATE_TAKE_PROFITS,
                category="UPDATE",
                entities=UpdateTakeProfitsEntities(
                    new_take_profits=[Price.from_float(110.0)],
                    target_tp_level=2,
                    mode="UPDATE_ONE",
                ),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda op: (
                op.op_type == "MODIFY_TARGETS"
                and op.modify_targets.mode == "UPDATE_ONE"
                and op.modify_targets.target_tp_level == 2
                and [tp.price.value for tp in op.modify_targets.take_profits] == [110.0]
            ),
        ),
        (
            IntentResult(
                type=IntentType.ENTRY_FILLED,
                category="REPORT",
                entities=EntryFilledEntities(fill_price=Price.from_float(100.0), level=1),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda event: (
                event.event_type == "ENTRY_FILLED"
                and event.level == 1
                and event.price.value == 100.0
            ),
        ),
        (
            IntentResult(
                type=IntentType.TP_HIT,
                category="REPORT",
                entities=TpHitEntities(
                    level=2,
                    price=Price.from_float(120.0),
                    result=ReportedResult(value=2.5, unit="R"),
                ),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda event: (
                event.event_type == "TP_HIT"
                and event.level == 2
                and event.price.value == 120.0
                and event.result.value == 2.5
            ),
        ),
        (
            IntentResult(
                type=IntentType.SL_HIT,
                category="REPORT",
                entities=SlHitEntities(
                    price=Price.from_float(95.0),
                    result=ReportedResult(value=-1.0, unit="R"),
                ),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda event: (
                event.event_type == "STOP_HIT"
                and event.price.value == 95.0
                and event.result.value == -1.0
            ),
        ),
        (
            IntentResult(
                type=IntentType.EXIT_BE,
                category="REPORT",
                entities=ExitBeEntities(price=Price.from_float(100.0)),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda event: (
                event.event_type == "BREAKEVEN_EXIT"
                and event.price.value == 100.0
            ),
        ),
        (
            IntentResult(
                type=IntentType.REPORT_FINAL_RESULT,
                category="REPORT",
                entities=ReportFinalResultEntities(
                    result=ReportedResult(value=3.0, unit="R", text="+3R"),
                ),
                confidence=0.7,
                status="CONFIRMED",
            ),
            lambda event: (
                event.event_type == "FINAL_RESULT"
                and event.result.value == 3.0
                and event.result.text == "+3R"
            ),
        ),
    ],
)
def test_translate_maps_individual_intents_to_expected_payload(intent: IntentResult, assertion) -> None:
    translator = IntentTranslator()
    parsed = _parsed_message(
        intent,
        primary_class="UPDATE" if intent.category == "UPDATE" else "REPORT",
    )

    canonical = translator.translate(parsed)

    if intent.category == "UPDATE":
        assert canonical.update is not None
        assert len(canonical.update.operations) == 1
        assert assertion(canonical.update.operations[0])
    else:
        assert canonical.report is not None
        assert len(canonical.report.events) == 1
        assert assertion(canonical.report.events[0])


def test_translate_maps_report_partial_result_into_report_payload_summary() -> None:
    translator = IntentTranslator()
    parsed = _parsed_message(
        IntentResult(
            type=IntentType.REPORT_PARTIAL_RESULT,
            category="REPORT",
            entities=ReportPartialResultEntities(
                result=ReportedResult(value=1.2, unit="PERCENT", text="+1.2%"),
            ),
            confidence=0.8,
            status="CONFIRMED",
        ),
        primary_class="REPORT",
    )

    canonical = translator.translate(parsed)

    assert canonical.primary_class == "REPORT"
    assert canonical.report is not None
    assert canonical.report.events == []
    assert canonical.report.reported_result is not None
    assert canonical.report.reported_result.value == 1.2


def test_translate_supports_modify_targets_remove_one_without_take_profit_list() -> None:
    translator = IntentTranslator()
    parsed = _parsed_message(
        IntentResult(
            type=IntentType.UPDATE_TAKE_PROFITS,
            category="UPDATE",
            entities=UpdateTakeProfitsEntities(
                new_take_profits=[],
                target_tp_level=3,
                mode="REMOVE_ONE",
            ),
            confidence=0.8,
            status="CONFIRMED",
        ),
    )

    canonical = translator.translate(parsed)

    operation = canonical.update.operations[0]
    assert operation.modify_targets.mode == "REMOVE_ONE"
    assert operation.modify_targets.target_tp_level == 3
    assert operation.modify_targets.take_profits == []


def test_translate_uses_valid_refs_for_targeted_actions_not_original_override_refs() -> None:
    translator = IntentTranslator()
    override = Targeting(
        refs=[
            TargetRef(ref_type="MESSAGE_ID", value=777),
            TargetRef(ref_type="MESSAGE_ID", value=888),
        ],
        scope=TargetScope(kind="SINGLE_SIGNAL"),
        strategy="REPLY_OR_LINK",
        targeted=True,
    )
    parsed = _parsed_message(
        IntentResult(
            type=IntentType.CLOSE_FULL,
            category="UPDATE",
            entities=CloseFullEntities(),
            confidence=0.9,
            status="CONFIRMED",
            targeting_override=override,
            valid_refs=[42],
            invalid_refs=[777, 888],
        ),
        targeting=override,
    )

    canonical = translator.translate(parsed)

    assert canonical.update is not None
    assert canonical.update.operations == []
    assert len(canonical.targeted_actions) == 1
    action = canonical.targeted_actions[0]
    assert action.action_type == "CLOSE"
    assert action.targeting.mode == "EXPLICIT_TARGETS"
    assert action.targeting.targets == [42]


def test_translate_preserves_info_only_in_update_composite_diagnostics() -> None:
    translator = IntentTranslator()
    parsed = _parsed_message(
        IntentResult(
            type=IntentType.MOVE_STOP_TO_BE,
            category="UPDATE",
            entities=MoveStopToBEEntities(),
            confidence=0.9,
            status="CONFIRMED",
        ),
        IntentResult(
            type=IntentType.INFO_ONLY,
            category="INFO",
            entities=InfoOnlyEntities(),
            confidence=0.3,
            raw_fragment="mercato volatile oggi",
            status="CONFIRMED",
        ),
        primary_class="UPDATE",
        diagnostics={"existing": True},
    )

    canonical = translator.translate(parsed)

    assert canonical.primary_class == "UPDATE"
    assert canonical.intents == ["MOVE_STOP_TO_BE", "INFO_ONLY"]
    assert canonical.diagnostics["existing"] is True
    assert canonical.diagnostics["info_fragments"] == ["mercato volatile oggi"]


def test_translate_signal_has_priority_and_suppresses_non_signal_intents() -> None:
    from src.parser.canonical_v1.models import EntryLeg, SignalPayload, StopLoss, TakeProfit

    translator = IntentTranslator()
    signal = SignalPayload(
        symbol="BTCUSDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[EntryLeg(sequence=1, entry_type="LIMIT", price=Price.from_float(100.0), role="PRIMARY")],
        stop_loss=StopLoss(price=Price.from_float(95.0)),
        take_profits=[TakeProfit(sequence=1, price=Price.from_float(110.0))],
        completeness="COMPLETE",
    )
    parsed = _parsed_message(
        IntentResult(
            type=IntentType.CLOSE_FULL,
            category="UPDATE",
            entities=CloseFullEntities(),
            confidence=0.9,
            status="CONFIRMED",
        ),
        primary_class="SIGNAL",
        signal=signal,
    )

    canonical = translator.translate(parsed)

    assert canonical.primary_class == "SIGNAL"
    assert canonical.signal is not None
    assert canonical.update is None
    assert "composite_with_signal_dropped:CLOSE_FULL" in canonical.warnings


def test_translate_requires_validated_parsed_message() -> None:
    translator = IntentTranslator()
    parsed = ParsedMessage(
        parser_profile="trader_a",
        primary_class="INFO",
        parse_status="UNCLASSIFIED",
        confidence=0.0,
        validation_status="PENDING",
        raw_context=_raw_context(),
    )

    with pytest.raises(ValueError, match="VALIDATED"):
        translator.translate(parsed)


def test_translate_builds_canonical_message_that_passes_pydantic_validation() -> None:
    translator = IntentTranslator()
    parsed = _parsed_message(
        IntentResult(
            type=IntentType.MOVE_STOP,
            category="UPDATE",
            entities=MoveStopEntities(stop_to_tp_level=1),
            confidence=0.85,
            status="CONFIRMED",
            raw_fragment="stop to tp1",
        ),
        IntentResult(
            type=IntentType.ENTRY_FILLED,
            category="REPORT",
            entities=EntryFilledEntities(fill_price=Price.from_float(100.5), level=1),
            confidence=0.8,
            status="CONFIRMED",
            raw_fragment="entry filled",
        ),
        IntentResult(
            type=IntentType.INFO_ONLY,
            category="INFO",
            entities=InfoOnlyEntities(),
            confidence=0.2,
            status="CONFIRMED",
            raw_fragment="mercato lento",
        ),
        primary_class="UPDATE",
        targeting=Targeting(
            refs=[TargetRef(ref_type="MESSAGE_ID", value=321)],
            scope=TargetScope(kind="SINGLE_SIGNAL"),
            strategy="REPLY_OR_LINK",
            targeted=True,
        ),
        diagnostics={"resolution_unit": "MESSAGE_WIDE"},
    )

    canonical = translator.translate(parsed)

    assert canonical.primary_class == "UPDATE"
    assert canonical.update is not None
    assert canonical.report is not None
    assert canonical.model_dump()["report"]["events"][0]["event_type"] == "ENTRY_FILLED"
