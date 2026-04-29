from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.parser.canonical_v1.models import (
    CancelPendingOperation,
    CloseOperation,
    EntryLeg,
    ModifyEntriesOperation,
    ModifyTargetsOperation,
    RawContext,
    ReportEvent,
    ReportPayload,
    ReportedResult,
    SetStopParams,
    SignalPayload,
    StopTarget,
    TakeProfit,
    TargetedAction,
    TargetedActionDiagnostics,
    TargetedActionTargeting,
    TargetedReport,
    TargetedReportResult,
    UpdateOperation,
    UpdatePayload,
    CanonicalMessage,
)
from src.parser.intent_types import IntentType
from src.parser.parsed_message import (
    AddEntryEntities,
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    ExitBeEntities,
    IntentResult,
    MoveStopEntities,
    ReenterEntities,
    ReportFinalResultEntities,
    ReportPartialResultEntities,
    SlHitEntities,
    TpHitEntities,
    UpdateTakeProfitsEntities,
    ParsedMessage,
)

_SCOPE_MAPPING_PATH = Path(__file__).with_name("scope_mapping.json")
_RAW_SCOPE_MAPPING: dict[str, list[str]] = json.loads(_SCOPE_MAPPING_PATH.read_text(encoding="utf-8"))
_SCOPE_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in _RAW_SCOPE_MAPPING.items()
    for alias in aliases
}

_INTENT_TO_UPDATE_OP = {
    IntentType.MOVE_STOP_TO_BE: "SET_STOP",
    IntentType.MOVE_STOP: "SET_STOP",
    IntentType.CLOSE_FULL: "CLOSE",
    IntentType.CLOSE_PARTIAL: "CLOSE",
    IntentType.CANCEL_PENDING: "CANCEL_PENDING",
    IntentType.INVALIDATE_SETUP: "CANCEL_PENDING",
    IntentType.REENTER: "MODIFY_ENTRIES",
    IntentType.ADD_ENTRY: "MODIFY_ENTRIES",
    IntentType.UPDATE_TAKE_PROFITS: "MODIFY_TARGETS",
}

_INTENT_TO_REPORT_EVENT = {
    IntentType.ENTRY_FILLED: "ENTRY_FILLED",
    IntentType.TP_HIT: "TP_HIT",
    IntentType.SL_HIT: "STOP_HIT",
    IntentType.EXIT_BE: "BREAKEVEN_EXIT",
    IntentType.REPORT_FINAL_RESULT: "FINAL_RESULT",
}


class IntentTranslator:
    def translate(self, parsed: ParsedMessage) -> CanonicalMessage:
        if parsed.validation_status != "VALIDATED":
            raise ValueError("IntentTranslator requires ParsedMessage.validation_status=VALIDATED")

        warnings = list(parsed.warnings)
        diagnostics = dict(parsed.diagnostics)
        confirmed = [intent for intent in parsed.intents if intent.status == "CONFIRMED"]

        if parsed.signal is not None:
            kept_intents: list[str] = []
            for intent in confirmed:
                warnings.append(f"composite_with_signal_dropped:{intent.type.value}")
            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class="SIGNAL",
                parse_status=parsed.parse_status,
                confidence=parsed.confidence,
                intents=kept_intents,
                primary_intent=None,
                targeting=parsed.targeting,
                signal=parsed.signal,
                warnings=warnings,
                diagnostics=diagnostics,
                raw_context=parsed.raw_context,
            )

        update_intents = [intent for intent in confirmed if intent.category == "UPDATE"]
        report_intents = [intent for intent in confirmed if intent.category == "REPORT"]
        info_intents = [intent for intent in confirmed if intent.type == IntentType.INFO_ONLY]

        operations: list[UpdateOperation] = []
        targeted_actions: list[TargetedAction] = []
        for intent in update_intents:
            operation = _build_update_operation(intent)
            if intent.targeting_override is None:
                operations.append(operation)
            else:
                targeted_actions.append(_build_targeted_action(intent, operation, parsed.raw_context))

        events: list[ReportEvent] = []
        targeted_reports: list[TargetedReport] = []
        reported_result: ReportedResult | None = None
        for intent in report_intents:
            if intent.type == IntentType.REPORT_PARTIAL_RESULT:
                entities = intent.entities
                assert isinstance(entities, ReportPartialResultEntities)
                reported_result = entities.result
                continue
            event = _build_report_event(intent)
            if intent.targeting_override is None:
                events.append(event)
            else:
                targeted_reports.append(_build_targeted_report(intent, event, parsed.raw_context))

        if info_intents:
            info_fragments = [
                fragment
                for fragment in (intent.raw_fragment for intent in info_intents)
                if fragment
            ]
            if info_fragments:
                diagnostics["info_fragments"] = info_fragments

        canonical_intents = [intent.type.value for intent in confirmed]
        primary_intent = _choose_primary_intent(parsed=parsed, confirmed=confirmed)

        if update_intents:
            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class="UPDATE",
                parse_status=parsed.parse_status,
                confidence=parsed.confidence,
                intents=canonical_intents,
                primary_intent=primary_intent,
                targeting=parsed.targeting,
                update=UpdatePayload(operations=operations),
                report=ReportPayload(events=events, reported_result=reported_result) if (events or reported_result or targeted_reports) else None,
                targeted_actions=targeted_actions,
                targeted_reports=targeted_reports,
                warnings=warnings,
                diagnostics=diagnostics,
                raw_context=parsed.raw_context,
            )

        if report_intents:
            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class="REPORT",
                parse_status=parsed.parse_status,
                confidence=parsed.confidence,
                intents=canonical_intents,
                primary_intent=primary_intent,
                targeting=parsed.targeting,
                report=ReportPayload(events=events, reported_result=reported_result),
                targeted_reports=targeted_reports,
                warnings=warnings,
                diagnostics=diagnostics,
                raw_context=parsed.raw_context,
            )

        return CanonicalMessage(
            parser_profile=parsed.parser_profile,
            primary_class="INFO",
            parse_status=parsed.parse_status,
            confidence=parsed.confidence,
            intents=canonical_intents,
            primary_intent=primary_intent,
            targeting=parsed.targeting,
            warnings=warnings,
            diagnostics=diagnostics,
            raw_context=parsed.raw_context,
        )


def _choose_primary_intent(*, parsed: ParsedMessage, confirmed: list[IntentResult]) -> str | None:
    if parsed.primary_intent is not None:
        primary_value = parsed.primary_intent.value
        if any(intent.type.value == primary_value for intent in confirmed):
            return primary_value
    if confirmed:
        return confirmed[0].type.value
    return None


def _build_update_operation(intent: IntentResult) -> UpdateOperation:
    if intent.type == IntentType.MOVE_STOP_TO_BE:
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=StopTarget(target_type="ENTRY"),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.MOVE_STOP:
        entities = intent.entities
        assert isinstance(entities, MoveStopEntities)
        if entities.new_stop_price is not None:
            stop_target = StopTarget(target_type="PRICE", value=entities.new_stop_price.value)
        elif entities.stop_to_tp_level is not None:
            stop_target = StopTarget(target_type="TP_LEVEL", value=entities.stop_to_tp_level)
        else:
            raise ValueError("MOVE_STOP requires new_stop_price or stop_to_tp_level")
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=stop_target,
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.CLOSE_FULL:
        entities = intent.entities
        assert isinstance(entities, CloseFullEntities)
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(close_scope="FULL", close_price=entities.close_price),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.CLOSE_PARTIAL:
        entities = intent.entities
        assert isinstance(entities, ClosePartialEntities)
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(
                close_scope="PARTIAL",
                close_fraction=entities.fraction,
                close_price=entities.close_price,
            ),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.CANCEL_PENDING:
        entities = intent.entities
        assert isinstance(entities, CancelPendingEntities)
        return UpdateOperation(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(
                cancel_scope=_map_cancel_scope(entities.scope),
            ),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.INVALIDATE_SETUP:
        return UpdateOperation(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope="ALL_POSITIONS"),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.REENTER:
        entities = intent.entities
        assert isinstance(entities, ReenterEntities)
        entry_structure = entities.entry_structure if len(entities.entries) > 1 else None
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(
                mode="REENTER",
                entries=_build_entry_legs(
                    prices=entities.entries,
                    entry_type=entities.entry_type,
                ),
                entry_structure=entry_structure,
            ),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.ADD_ENTRY:
        entities = intent.entities
        assert isinstance(entities, AddEntryEntities)
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(
                mode="ADD",
                entries=_build_entry_legs(
                    prices=[entities.entry_price],
                    entry_type=entities.entry_type,
                ),
                entry_structure=None,
            ),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.UPDATE_TAKE_PROFITS:
        entities = intent.entities
        assert isinstance(entities, UpdateTakeProfitsEntities)
        mode = _resolve_modify_targets_mode(entities)
        return UpdateOperation(
            op_type="MODIFY_TARGETS",
            modify_targets=ModifyTargetsOperation(
                mode=mode,
                take_profits=_build_take_profits(entities.new_take_profits),
                target_tp_level=entities.target_tp_level,
            ),
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    raise ValueError(f"Unsupported UPDATE intent: {intent.type.value}")


def _build_report_event(intent: IntentResult) -> ReportEvent:
    if intent.type == IntentType.ENTRY_FILLED:
        entities = intent.entities
        assert isinstance(entities, EntryFilledEntities)
        return ReportEvent(
            event_type="ENTRY_FILLED",
            level=entities.level,
            price=entities.fill_price,
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.TP_HIT:
        entities = intent.entities
        assert isinstance(entities, TpHitEntities)
        return ReportEvent(
            event_type="TP_HIT",
            level=entities.level,
            price=entities.price,
            result=entities.result,
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.SL_HIT:
        entities = intent.entities
        assert isinstance(entities, SlHitEntities)
        return ReportEvent(
            event_type="STOP_HIT",
            price=entities.price,
            result=entities.result,
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.EXIT_BE:
        entities = intent.entities
        assert isinstance(entities, ExitBeEntities)
        return ReportEvent(
            event_type="BREAKEVEN_EXIT",
            price=entities.price,
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    if intent.type == IntentType.REPORT_FINAL_RESULT:
        entities = intent.entities
        assert isinstance(entities, ReportFinalResultEntities)
        return ReportEvent(
            event_type="FINAL_RESULT",
            result=entities.result,
            raw_fragment=intent.raw_fragment,
            confidence=intent.confidence,
        )

    raise ValueError(f"Unsupported REPORT intent: {intent.type.value}")


def _build_entry_legs(*, prices: list[Any], entry_type: str | None) -> list[EntryLeg]:
    resolved_entry_type = entry_type or "LIMIT"
    roles = ["PRIMARY"] + ["AVERAGING"] * max(0, len(prices) - 1)
    return [
        EntryLeg(
            sequence=index,
            entry_type=resolved_entry_type,
            price=price,
            role=roles[index - 1],
        )
        for index, price in enumerate(prices, start=1)
    ]


def _build_take_profits(prices: list[Any]) -> list[TakeProfit]:
    return [
        TakeProfit(sequence=index, price=price)
        for index, price in enumerate(prices, start=1)
    ]


def _resolve_modify_targets_mode(entities: UpdateTakeProfitsEntities) -> str:
    if entities.mode is not None:
        return entities.mode
    if entities.new_take_profits:
        return "REPLACE_ALL"
    raise ValueError("UPDATE_TAKE_PROFITS requires mode when new_take_profits is empty")


def _build_targeted_action(
    intent: IntentResult,
    operation: UpdateOperation,
    raw_context: RawContext,
) -> TargetedAction:
    targets = _validated_targets(intent)
    params = _targeted_action_params(operation)
    return TargetedAction(
        action_type=_INTENT_TO_UPDATE_OP[intent.type],
        params=params,
        targeting=TargetedActionTargeting(
            mode="TARGET_GROUP" if len(targets) > 1 else "EXPLICIT_TARGETS",
            targets=targets,
        ),
        raw_fragment=intent.raw_fragment,
        confidence=intent.confidence,
        diagnostics=_targeted_diagnostics(raw_context=raw_context),
    )


def _build_targeted_report(
    intent: IntentResult,
    event: ReportEvent,
    raw_context: RawContext,
) -> TargetedReport:
    targets = _validated_targets(intent)
    return TargetedReport(
        event_type=_INTENT_TO_REPORT_EVENT[intent.type],
        result=_to_targeted_report_result(event.result),
        level=event.level,
        targeting=TargetedActionTargeting(
            mode="TARGET_GROUP" if len(targets) > 1 else "EXPLICIT_TARGETS",
            targets=targets,
        ),
        raw_fragment=intent.raw_fragment,
        confidence=intent.confidence,
        diagnostics=_targeted_diagnostics(raw_context=raw_context),
    )


def _validated_targets(intent: IntentResult) -> list[int]:
    if not intent.valid_refs:
        raise ValueError(f"Targeted intent {intent.type.value} requires non-empty valid_refs")
    return list(intent.valid_refs)


def _targeted_action_params(operation: UpdateOperation) -> dict[str, Any]:
    if operation.set_stop is not None:
        params = SetStopParams(
            target_type=operation.set_stop.target_type,
            value=operation.set_stop.value if isinstance(operation.set_stop.value, int) else None,
            price=float(operation.set_stop.value) if isinstance(operation.set_stop.value, float) else None,
        )
        return params.model_dump(exclude_none=True)
    if operation.close is not None:
        return operation.close.model_dump(exclude_none=True)
    if operation.cancel_pending is not None:
        return operation.cancel_pending.model_dump(exclude_none=True)
    if operation.modify_entries is not None:
        return operation.modify_entries.model_dump(exclude_none=True)
    if operation.modify_targets is not None:
        return operation.modify_targets.model_dump(exclude_none=True)
    raise ValueError(f"Unsupported targeted action payload for {operation.op_type}")


def _targeted_diagnostics(*, raw_context: RawContext) -> TargetedActionDiagnostics | None:
    return None


def _to_targeted_report_result(result: ReportedResult | None) -> TargetedReportResult | None:
    if result is None:
        return None
    return TargetedReportResult(value=result.value, unit=result.unit, text=result.text)


def _map_cancel_scope(scope: str | None) -> str:
    raw_scope = scope or "TARGETED"
    return _SCOPE_ALIAS_TO_CANONICAL.get(raw_scope, raw_scope)
