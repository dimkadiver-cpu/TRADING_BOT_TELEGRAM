from __future__ import annotations

from src.parser_v2.contracts.canonical_message import (
    ActionItem,
    TargetActionGroup,
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
SIGNAL_NOISE_INTENTS = UPDATE_INTENTS | REPORT_EVENT_INTENTS


class CanonicalTranslator:
    def translate(self, parsed: ParsedMessage) -> CanonicalMessage:
        warnings = list(parsed.warnings)
        intents = [intent.type for intent in parsed.intents]
        parse_status = parsed.parse_status

        if parsed.primary_class == "SIGNAL":
            if parsed.signal is None:
                raise ValueError("SIGNAL ParsedMessage requires signal")

            signal_noise_intents = UPDATE_INTENTS | REPORT_EVENT_INTENTS

            if any(intent.type in signal_noise_intents for intent in parsed.intents):
                warnings = _append_once(warnings, "non_signal_intents_dropped_in_signal_message")
                intents = [
                    intent.type
                    for intent in parsed.intents
                    if intent.type not in signal_noise_intents
                ]

            primary_intent = (
                None
                if parsed.primary_intent in signal_noise_intents
                else parsed.primary_intent
            )

            signal_diag = dict(parsed.diagnostics)
            if parsed.target_hints and parsed.target_hints.explicit_ids:
                signal_diag["signal_explicit_ids"] = list(parsed.target_hints.explicit_ids)

            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class=parsed.primary_class,
                parse_status=parse_status,
                confidence=parsed.confidence,
                primary_intent=primary_intent,
                intents=intents,
                signal=_signal_payload(parsed.signal),
                warnings=warnings,
                diagnostics=signal_diag,
                raw_context=parsed.raw_context,
            )

        if parsed.primary_class == "UPDATE":
            intent_op_pairs = [
                (intent, _operation_from_intent(intent))
                for intent in parsed.intents
                if intent.type in UPDATE_INTENTS
            ]
            intent_op_pairs = [(i, op) for i, op in intent_op_pairs if op is not None]

            for _intent, _op in intent_op_pairs:
                if (
                    _intent.type == "MOVE_STOP"
                    and _op.set_stop is not None
                    and _op.set_stop.target_type == "ENTRY"
                ):
                    warnings = _append_once(warnings, "move_stop_no_price_defaulted_to_be")

            target_action_groups = _build_target_action_groups(intent_op_pairs, parsed.target_hints)

            if (
                not target_action_groups
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
                report=_report_payload(parsed.intents),
                target_action_groups=target_action_groups,
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
            warnings=warnings,
            diagnostics=parsed.diagnostics,
            raw_context=parsed.raw_context,
        )


def _signal_payload(signal: SignalDraft) -> SignalPayload:
    return SignalPayload(**signal.model_dump())


def _operation_from_intent(intent: ParsedIntent) -> ActionItem | None:
    entities = intent.entities

    if intent.type == "MOVE_STOP_TO_BE":
        return ActionItem(
            action_type="SET_STOP",
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
            set_stop = SetStopOperation(target_type="ENTRY")
        return ActionItem(
            action_type="SET_STOP",
            set_stop=set_stop,
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CLOSE_FULL":
        close_price = entities.close_price if isinstance(entities, CloseFullEntities) else None
        return ActionItem(
            action_type="CLOSE",
            close=CloseOperation(close_scope="FULL", close_price=close_price),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CLOSE_PARTIAL" and isinstance(entities, ClosePartialEntities):
        return ActionItem(
            action_type="CLOSE",
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
        return ActionItem(
            action_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope_hint=cancel_scope_hint),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "INVALIDATE_SETUP" and isinstance(entities, InvalidateSetupEntities):
        return ActionItem(
            action_type="INVALIDATE_SETUP",
            invalidate_setup=InvalidateSetupOperation(reason_text=entities.reason_text),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "MODIFY_ENTRY" and isinstance(entities, ModifyEntryEntities):
        return ActionItem(
            action_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(
                kind=entities.mode,
                entries=entities.entries,
                entry_structure=entities.entry_structure,
                entry_selector=entities.entry_selector,
            ),
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
        return ActionItem(
            action_type="MODIFY_ENTRIES",
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
        return ActionItem(
            action_type="MODIFY_ENTRIES",
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
        return ActionItem(
            action_type="MODIFY_TARGETS",
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


def _build_target_action_groups(
    intent_op_pairs: list[tuple[ParsedIntent, ActionItem]],
    message_target_hints: TargetHints | None,
) -> list[TargetActionGroup]:
    if not intent_op_pairs:
        return []

    groups: dict[str, tuple[TargetHints, TargetHints | None, list[ActionItem]]] = {}

    for intent, action in intent_op_pairs:
        primary_hints, secondary_hints = _resolve_target_hints(intent, message_target_hints)
        group_key = _targeting_key(primary_hints)

        if group_key not in groups:
            groups[group_key] = (primary_hints, secondary_hints, [action])
        else:
            groups[group_key][2].append(action)

    return [
        TargetActionGroup(targeting=primary, secondary_targeting=secondary, actions=actions)
        for primary, secondary, actions in groups.values()
    ]


def _resolve_target_hints(
    intent: ParsedIntent,
    message_target_hints: TargetHints | None,
) -> tuple[TargetHints, TargetHints | None]:
    base = intent.target_hints or message_target_hints
    if base is None:
        return TargetHints(scope_hint="SINGLE_SIGNAL"), None

    if (
        base.scope_hint == "UNKNOWN"
        and (base.telegram_message_ids or base.telegram_links or base.explicit_ids)
    ):
        base = base.model_copy(update={"scope_hint": "SINGLE_SIGNAL"})

    if base.target_source == "SYMBOL" and base.scope_hint == "UNKNOWN":
        base = base.model_copy(update={"scope_hint": "SYMBOL"})

    has_explicit = bool(base.telegram_message_ids or base.telegram_links or base.explicit_ids)
    has_reply = bool(base.reply_to_message_id)

    if has_explicit and has_reply:
        secondary = TargetHints(reply_to_message_id=base.reply_to_message_id)
        primary = base.model_copy(update={"reply_to_message_id": None})
        return primary, secondary

    return base, None


def _targeting_key(hints: TargetHints) -> str:
    ids = "|".join(str(x) for x in sorted(hints.telegram_message_ids))
    links = "|".join(sorted(hints.telegram_links))
    explicit = "|".join(sorted(hints.explicit_ids))
    symbols = "|".join(sorted(hints.symbols))
    return f"ids:{ids};links:{links};explicit:{explicit};reply:{hints.reply_to_message_id};scope:{hints.scope_hint};symbols:{symbols}"


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


def _append_once(values: list[str], value: str) -> list[str]:
    if value in values:
        return values
    return [*values, value]


__all__ = ["CanonicalTranslator"]
