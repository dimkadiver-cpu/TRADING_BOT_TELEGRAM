from __future__ import annotations

from src.parser_v2.contracts.context import RawContext, TargetHints
from src.parser_v2.contracts.entities import (
    AddEntryEntities,
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryLeg,
    EntryFilledEntities,
    ExitBeEntities,
    InfoOnlyEntities,
    InvalidateSetupEntities,
    ModifyEntryEntities,
    ModifyTargetsEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    Price,
    ReenterEntities,
    ReportResultEntities,
    SignalFields,
    SlHitEntities,
    StopLoss,
    TakeProfit,
    TpHitEntities,
)
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage, SignalDraft
from src.parser_v2.translation.canonical_translator import CanonicalTranslator


def _raw_context(raw_text: str = "raw") -> RawContext:
    return RawContext(raw_text=raw_text, normalized_text=raw_text.lower())


def _price(raw: str) -> Price:
    return Price(raw=raw, value=float(raw))


def _intent(intent_type: str, category: str, entities, raw_fragment: str | None = None) -> ParsedIntent:
    return ParsedIntent(
        type=intent_type,
        category=category,
        confidence=0.9,
        entities=entities,
        raw_fragment=raw_fragment or intent_type.lower(),
    )


def _parsed_update(intents: list[ParsedIntent], **overrides) -> ParsedMessage:
    return ParsedMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        intents=intents,
        primary_intent=intents[0].type,
        target_hints=overrides.pop("target_hints", None),
        warnings=overrides.pop("warnings", []),
        diagnostics=overrides.pop("diagnostics", {}),
        raw_context=_raw_context(),
        **overrides,
    )


def test_signal_translates_to_signal_payload_only() -> None:
    signal = SignalDraft(
        symbol="ETHUSDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[
            EntryLeg(
                sequence=1,
                entry_type="LIMIT",
                price=_price("2114"),
                role="PRIMARY",
            )
        ],
        stop_loss=StopLoss(price=_price("2100")),
        take_profits=[TakeProfit(sequence=1, price=_price("2200"))],
        missing_fields=[],
        completeness="COMPLETE",
    )
    parsed = ParsedMessage(
        parser_profile="trader_a",
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=1.0,
        signal=signal,
        raw_context=_raw_context("ETHUSDT long"),
    )

    canonical = CanonicalTranslator().translate(parsed)

    assert canonical.primary_class == "SIGNAL"
    assert canonical.signal.model_dump() == signal.model_dump()
    assert canonical.update is None
    assert canonical.report is None
    assert canonical.info is None
    assert canonical.targeted_actions == []


def test_update_intents_translate_to_operations() -> None:
    intents = [
        _intent("MOVE_STOP_TO_BE", "UPDATE", MoveStopToBEEntities()),
        _intent("MOVE_STOP", "UPDATE", MoveStopEntities(new_stop_price=_price("2150"))),
        _intent("MOVE_STOP", "UPDATE", MoveStopEntities(stop_to_tp_level=1)),
        _intent("CLOSE_FULL", "UPDATE", CloseFullEntities(close_price=_price("2160"))),
        _intent("CLOSE_PARTIAL", "UPDATE", ClosePartialEntities(fraction=0.5)),
        _intent("CANCEL_PENDING", "UPDATE", CancelPendingEntities(cancel_scope_hint="ALL_PENDING")),
        _intent("INVALIDATE_SETUP", "UPDATE", InvalidateSetupEntities(reason_text="news")),
        _intent(
            "MODIFY_ENTRY",
            "UPDATE",
            ModifyEntryEntities(
                mode="UPDATE_PRICE",
                entries=[EntryLeg(sequence=1, entry_type="LIMIT", price=_price("2120"))],
            ),
        ),
        _intent("ADD_ENTRY", "UPDATE", AddEntryEntities(entry_price=_price("2110"), entry_type="LIMIT")),
        _intent("REENTER", "UPDATE", ReenterEntities(entries=[_price("2130")], entry_type="LIMIT")),
        _intent(
            "MODIFY_TARGETS",
            "UPDATE",
            ModifyTargetsEntities(take_profits=[_price("2300")], target_tp_level=1, mode="UPDATE_ONE"),
        ),
    ]

    canonical = CanonicalTranslator().translate(_parsed_update(intents))
    operations = canonical.update.operations

    assert [operation.op_type for operation in operations] == [
        "SET_STOP",
        "SET_STOP",
        "SET_STOP",
        "CLOSE",
        "CLOSE",
        "CANCEL_PENDING",
        "INVALIDATE_SETUP",
        "MODIFY_ENTRIES",
        "MODIFY_ENTRIES",
        "MODIFY_ENTRIES",
        "MODIFY_TARGETS",
    ]
    assert operations[0].set_stop.target_type == "ENTRY"
    assert operations[1].set_stop.target_type == "PRICE"
    assert operations[1].set_stop.price == _price("2150")
    assert operations[2].set_stop.target_type == "TP_LEVEL"
    assert operations[2].set_stop.tp_level == 1
    assert operations[3].close.close_scope == "FULL"
    assert operations[4].close.close_scope == "PARTIAL"
    assert operations[4].close.fraction == 0.5
    assert operations[5].cancel_pending.cancel_scope_hint == "ALL_PENDING"
    assert operations[6].invalidate_setup.reason_text == "news"
    assert operations[7].modify_entries.kind == "UPDATE_PRICE"
    assert operations[8].modify_entries.kind == "ADD"
    assert operations[9].modify_entries.kind == "REENTER"
    assert operations[10].modify_targets.mode == "UPDATE_ONE"
    assert operations[10].modify_targets.take_profits == [TakeProfit(sequence=1, price=_price("2300"))]


def test_report_intents_translate_to_minimal_report_payload() -> None:
    parsed = ParsedMessage(
        parser_profile="trader_a",
        primary_class="REPORT",
        parse_status="PARSED",
        confidence=0.9,
        intents=[
            _intent("ENTRY_FILLED", "REPORT", EntryFilledEntities(level=2, fill_price=_price("2150"))),
            _intent("TP_HIT", "REPORT", TpHitEntities(level=1, price=_price("2200"))),
            _intent("SL_HIT", "REPORT", SlHitEntities(price=_price("2100"))),
            _intent("EXIT_BE", "REPORT", ExitBeEntities()),
            _intent("REPORT_RESULT", "REPORT", ReportResultEntities(raw_summary="+2R")),
        ],
        primary_intent="TP_HIT",
        raw_context=_raw_context(),
    )

    canonical = CanonicalTranslator().translate(parsed)

    assert canonical.primary_class == "REPORT"
    assert [event.event_type for event in canonical.report.events] == [
        "ENTRY_FILLED",
        "TP_HIT",
        "SL_HIT",
        "EXIT_BE",
    ]
    assert canonical.report.events[0].level == 2
    assert canonical.report.events[0].price == _price("2150")
    assert canonical.report.events[1].level == 1
    assert canonical.report.events[1].price == _price("2200")
    assert canonical.report.result.raw_fragment == "+2R"


def test_update_with_report_intent_keeps_update_primary_and_report_secondary() -> None:
    parsed = _parsed_update(
        [
            _intent("MOVE_STOP_TO_BE", "UPDATE", MoveStopToBEEntities()),
            _intent("TP_HIT", "REPORT", TpHitEntities(level=1)),
        ]
    )

    canonical = CanonicalTranslator().translate(parsed)

    assert canonical.primary_class == "UPDATE"
    assert canonical.update.operations[0].op_type == "SET_STOP"
    assert canonical.report.events[0].event_type == "TP_HIT"


def test_info_translates_to_raw_fragment_payload() -> None:
    parsed = ParsedMessage(
        parser_profile="trader_a",
        primary_class="INFO",
        parse_status="PARSED",
        confidence=0.8,
        intents=[
            _intent("INFO_ONLY", "INFO", InfoOnlyEntities(raw_fragment="market overview"), "overview")
        ],
        primary_intent="INFO_ONLY",
        raw_context=_raw_context("market overview"),
    )

    canonical = CanonicalTranslator().translate(parsed)

    assert canonical.primary_class == "INFO"
    assert canonical.info.raw_fragment == "market overview"
    assert canonical.update is None
    assert canonical.report is None


def test_explicit_multi_ref_update_uses_grouped_targeted_action_not_operations() -> None:
    parsed = _parsed_update(
        [_intent("MOVE_STOP_TO_BE", "UPDATE", MoveStopToBEEntities())],
        target_hints=TargetHints(
            telegram_message_ids=[978, 1002],
            telegram_links=["https://t.me/c/123/978", "https://t.me/c/123/1002"],
        ),
    )

    canonical = CanonicalTranslator().translate(parsed)

    assert canonical.update.operations == []
    assert len(canonical.targeted_actions) == 1
    assert canonical.targeted_actions[0].action_type == "SET_STOP"
    assert canonical.targeted_actions[0].params == {"target_type": "ENTRY"}
    assert canonical.targeted_actions[0].target_hints.telegram_message_ids == [978, 1002]
    assert canonical.targeted_actions[0].target_hints.scope_hint == "SINGLE_SIGNAL"


def test_mixed_multi_ref_update_is_partial_without_targeted_actions() -> None:
    parsed = _parsed_update(
        [
            _intent("MOVE_STOP_TO_BE", "UPDATE", MoveStopToBEEntities()),
            _intent("CLOSE_FULL", "UPDATE", CloseFullEntities()),
        ],
        target_hints=TargetHints(telegram_message_ids=[111, 222]),
    )

    canonical = CanonicalTranslator().translate(parsed)

    assert canonical.parse_status == "PARTIAL"
    assert canonical.update.operations == []
    assert canonical.targeted_actions == []
    assert canonical.warnings == ["multi_ref_mixed_intents_not_supported"]
