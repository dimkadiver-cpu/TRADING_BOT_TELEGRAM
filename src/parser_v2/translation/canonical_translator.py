from __future__ import annotations

from typing import Any

from src.parser_v2.contracts.canonical_message import (
    CancelPendingOperation,
    CanonicalMessage,
    CloseOperation,
    InfoPayload,
    InvalidateSetupOperation,
    ModifyEntriesOperation,
    ModifyTargetsOperation,
    ReportEvent,
    ReportPayload,
    ReportResult,
    SetStopOperation,
    SignalPayload,
    TargetedAction,
    UpdateOperation,
    UpdatePayload,
)
from src.parser_v2.contracts.context import TargetHints
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
    ReenterEntities,
    ReportResultEntities,
    SlHitEntities,
    TakeProfit,
    TpHitEntities,
)
from src.parser_v2.contracts.enums import CancelScopeHint, IntentType, ParseStatus
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage, SignalDraft


UPDATE_INTENTS = {
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING",
    "INVALIDATE_SETUP",
    "REENTER",
    "ADD_ENTRY",
    "MODIFY_ENTRY",
    "MODIFY_TARGETS",
}

REPORT_EVENT_INTENTS = {"ENTRY_FILLED", "TP_HIT", "SL_HIT", "EXIT_BE"}
GLOBAL_SCOPE_HINTS = {"ALL_LONG", "ALL_SHORT", "ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"}


class CanonicalTranslator:
    def translate(self, parsed: ParsedMessage) -> CanonicalMessage:
        warnings = list(parsed.warnings)
        intents = [intent.type for intent in parsed.intents]
        parse_status = parsed.parse_status

        if parsed.primary_class == "SIGNAL":
            if parsed.signal is None:
                raise ValueError("SIGNAL ParsedMessage requires signal")
            if any(intent.type in UPDATE_INTENTS for intent in parsed.intents):
                warnings = _append_once(warnings, "update_intents_dropped_in_signal_message")
                intents = [intent.type for intent in parsed.intents if intent.type not in UPDATE_INTENTS]

            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class=parsed.primary_class,
                parse_status=parse_status,
                confidence=parsed.confidence,
                primary_intent=parsed.primary_intent if parsed.primary_intent not in UPDATE_INTENTS else None,
                intents=intents,
                signal=_signal_payload(parsed.signal),
                target_hints=parsed.target_hints,
                warnings=warnings,
                diagnostics=parsed.diagnostics,
                raw_context=parsed.raw_context,
            )

        if parsed.primary_class == "UPDATE":
            intent_op_pairs = [
                (intent, _operation_from_intent(intent))
                for intent in parsed.intents
                if intent.type in UPDATE_INTENTS
            ]
            intent_op_pairs = [(i, op) for i, op in intent_op_pairs if op is not None]

            has_any_local_target = any(
                i.target_hints is not None for i, _ in intent_op_pairs
            )
            use_targeted = (
                _should_use_targeted_actions(parsed.target_hints) or has_any_local_target
            )

            targeted_actions: list[TargetedAction] = []
            plain_operations: list[UpdateOperation] = []

            if use_targeted and intent_op_pairs:
                targeted_actions = [
                    _make_targeted_action(intent, op, parsed.target_hints)
                    for intent, op in intent_op_pairs
                ]
            else:
                plain_operations = [op for _, op in intent_op_pairs]

            if (
                not plain_operations
                and not targeted_actions
                and parse_status in {"PARSED", "PARTIAL"}
                and "ambiguous_target_intent_binding" not in warnings
            ):
                parse_status = "ERROR"
                warnings = _append_once(warnings, "canonical_translation_without_update_operation")

            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class=parsed.primary_class,
                parse_status=parse_status,
                confidence=parsed.confidence,
                primary_intent=parsed.primary_intent,
                intents=list(dict.fromkeys(intents)),
                update=UpdatePayload(operations=plain_operations),
                report=_report_payload(parsed.intents),
                targeted_actions=targeted_actions,
                target_hints=parsed.target_hints,
                warnings=warnings,
                diagnostics=parsed.diagnostics,
                raw_context=parsed.raw_context,
            )

        if parsed.primary_class == "REPORT":
            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class=parsed.primary_class,
                parse_status=parse_status,
                confidence=parsed.confidence,
                primary_intent=parsed.primary_intent,
                intents=intents,
                report=_report_payload(parsed.intents) or ReportPayload(),
                target_hints=parsed.target_hints,
                warnings=warnings,
                diagnostics=parsed.diagnostics,
                raw_context=parsed.raw_context,
            )

        return CanonicalMessage(
            parser_profile=parsed.parser_profile,
            primary_class=parsed.primary_class,
            parse_status=parse_status,
            confidence=parsed.confidence,
            primary_intent=parsed.primary_intent,
            intents=intents,
            info=_info_payload(parsed),
            target_hints=parsed.target_hints,
            warnings=warnings,
            diagnostics=parsed.diagnostics,
            raw_context=parsed.raw_context,
        )


def _signal_payload(signal: SignalDraft) -> SignalPayload:
    return SignalPayload(**signal.model_dump())


def _operation_from_intent(intent: ParsedIntent) -> UpdateOperation | None:
    entities = intent.entities

    if intent.type == "MOVE_STOP_TO_BE":
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=SetStopOperation(target_type="ENTRY"),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "MOVE_STOP" and isinstance(entities, MoveStopEntities):
        if entities.new_stop_price is not None:
            set_stop = SetStopOperation(target_type="PRICE", price=entities.new_stop_price)
        elif entities.stop_to_tp_level is not None:
            set_stop = SetStopOperation(target_type="TP_LEVEL", tp_level=entities.stop_to_tp_level)
        else:
            return None
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=set_stop,
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CLOSE_FULL":
        close_price = entities.close_price if isinstance(entities, CloseFullEntities) else None
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(close_scope="FULL", close_price=close_price),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CLOSE_PARTIAL" and isinstance(entities, ClosePartialEntities):
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(
                close_scope="PARTIAL",
                fraction=entities.fraction,
                close_price=entities.close_price,
            ),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CANCEL_PENDING":
        cancel_scope_hint: CancelScopeHint = (
            entities.cancel_scope_hint if isinstance(entities, CancelPendingEntities) else "UNKNOWN"
        )
        return UpdateOperation(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope_hint=cancel_scope_hint),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "INVALIDATE_SETUP" and isinstance(entities, InvalidateSetupEntities):
        return UpdateOperation(
            op_type="INVALIDATE_SETUP",
            invalidate_setup=InvalidateSetupOperation(reason_text=entities.reason_text),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "MODIFY_ENTRY" and isinstance(entities, ModifyEntryEntities):
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(kind=entities.mode, entries=entities.entries),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "ADD_ENTRY" and isinstance(entities, AddEntryEntities):
        entries: list[EntryLeg] = []
        if entities.entry_price is not None or entities.entry_type is not None:
            entries.append(
                EntryLeg(
                    sequence=1,
                    entry_type=entities.entry_type or "LIMIT",
                    price=entities.entry_price,
                )
            )
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(kind="ADD", entries=entries),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "REENTER" and isinstance(entities, ReenterEntities):
        entries = [
            EntryLeg(
                sequence=index,
                entry_type=entities.entry_type or "LIMIT",
                price=price,
            )
            for index, price in enumerate(entities.entries, start=1)
        ]
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(
                kind="REENTER",
                entries=entries,
                entry_structure=entities.entry_structure,
            ),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "MODIFY_TARGETS" and isinstance(entities, ModifyTargetsEntities):
        return UpdateOperation(
            op_type="MODIFY_TARGETS",
            modify_targets=ModifyTargetsOperation(
                mode=entities.mode,
                take_profits=[
                    TakeProfit(sequence=index, price=price)
                    for index, price in enumerate(entities.take_profits, start=1)
                ],
                target_tp_level=entities.target_tp_level,
            ),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    return None


def _report_payload(intents: list[ParsedIntent]) -> ReportPayload | None:
    events: list[ReportEvent] = []
    result: ReportResult | None = None

    for intent in intents:
        entities = intent.entities
        if intent.type == "ENTRY_FILLED" and isinstance(entities, EntryFilledEntities):
            events.append(
                ReportEvent(
                    event_type="ENTRY_FILLED",
                    level=entities.level,
                    price=entities.fill_price,
                    source_intent=intent.type,
                    raw_fragment=intent.raw_fragment,
                )
            )
        elif intent.type == "TP_HIT" and isinstance(entities, TpHitEntities):
            events.append(
                ReportEvent(
                    event_type="TP_HIT",
                    level=entities.level,
                    price=entities.price,
                    source_intent=intent.type,
                    raw_fragment=intent.raw_fragment,
                )
            )
        elif intent.type == "SL_HIT" and isinstance(entities, SlHitEntities):
            events.append(
                ReportEvent(
                    event_type="SL_HIT",
                    price=entities.price,
                    source_intent=intent.type,
                    raw_fragment=intent.raw_fragment,
                )
            )
        elif intent.type == "EXIT_BE" and isinstance(entities, ExitBeEntities):
            events.append(
                ReportEvent(
                    event_type="EXIT_BE",
                    price=entities.price,
                    source_intent=intent.type,
                    raw_fragment=intent.raw_fragment,
                )
            )
        elif intent.type == "REPORT_RESULT" and isinstance(entities, ReportResultEntities):
            result = ReportResult(raw_fragment=entities.raw_summary or intent.raw_fragment)

    if not events and result is None:
        return None
    return ReportPayload(events=events, result=result)


def _info_payload(parsed: ParsedMessage) -> InfoPayload:
    for intent in parsed.intents:
        if intent.type == "INFO_ONLY" and isinstance(intent.entities, InfoOnlyEntities):
            return InfoPayload(raw_fragment=intent.entities.raw_fragment or intent.raw_fragment)
    return InfoPayload(raw_fragment=parsed.raw_context.raw_text)


def _should_use_targeted_actions(target_hints: TargetHints | None) -> bool:
    if target_hints is None:
        return False
    return bool(
        target_hints.telegram_message_ids
        or target_hints.telegram_links
        or target_hints.explicit_ids
        or target_hints.reply_to_message_id
        or target_hints.scope_hint in GLOBAL_SCOPE_HINTS
    )


def _make_targeted_action(
    intent: ParsedIntent,
    op: UpdateOperation,
    message_target_hints: TargetHints | None,
) -> TargetedAction:
    resolved_hints = intent.target_hints or message_target_hints
    if resolved_hints is None:
        resolved_hints = TargetHints(scope_hint="SINGLE_SIGNAL")
    elif (
        resolved_hints.scope_hint == "UNKNOWN"
        and (resolved_hints.telegram_message_ids or resolved_hints.telegram_links or resolved_hints.explicit_ids)
    ):
        resolved_hints = resolved_hints.model_copy(update={"scope_hint": "SINGLE_SIGNAL"})

    return TargetedAction(
        action_type=op.op_type,
        params=_operation_params(op),
        target_hints=resolved_hints,
        source_intent=op.source_intent,
        source_intent_id=intent.intent_id,
        raw_fragment=op.raw_fragment,
        confidence=op.confidence,
    )


def _operation_params(operation: UpdateOperation) -> dict[str, Any]:
    if operation.set_stop is not None:
        return operation.set_stop.model_dump(exclude_none=True)
    if operation.close is not None:
        return operation.close.model_dump(exclude_none=True)
    if operation.cancel_pending is not None:
        return operation.cancel_pending.model_dump(exclude_none=True)
    if operation.modify_entries is not None:
        return operation.modify_entries.model_dump(exclude_none=True)
    if operation.modify_targets is not None:
        return operation.modify_targets.model_dump(exclude_none=True)
    if operation.invalidate_setup is not None:
        return operation.invalidate_setup.model_dump(exclude_none=True)
    return {}


def _append_once(values: list[str], value: str) -> list[str]:
    if value in values:
        return values
    return [*values, value]


__all__ = ["CanonicalTranslator"]
