"""CanonicalNormalizer: converts TraderParseResult (dataclass) → CanonicalMessage v1.

Migrates business logic from canonical_v2.py into the v1 canonical contract.
Does NOT touch any parser profile — runs as a post-processing adapter.
"""

from __future__ import annotations

import re
from typing import Any

from src.parser.adapters.legacy_to_event_envelope_v1 import adapt_legacy_parse_result_to_event_envelope
from src.parser.canonical_v1.models import (
    CancelPendingOperation,
    CanonicalMessage,
    CloseOperation,
    EntryLeg,
    MessageClass,
    ModifyEntriesOperation,
    ModifyTargetsOperation,
    ParseStatus,
    Price,
    RawContext,
    ReportEvent,
    ReportPayload,
    ReportedResult,
    RiskHint,
    SignalPayload,
    StopLoss,
    StopTarget,
    TakeProfit,
    Targeting,
    TargetRef,
    TargetScope,
    UpdateOperation,
    UpdatePayload,
)
from src.parser.event_envelope_v1 import (
    ReportPayloadRaw,
    ReportedResultRaw,
    SignalPayloadRaw,
    TraderEventEnvelopeV1,
    UpdateOperationRaw,
    UpdatePayloadRaw,
)
from src.parser.trader_profiles.base import ParserContext, TraderParseResult

# ---------------------------------------------------------------------------
# Intent buckets
# ---------------------------------------------------------------------------

_UPDATE_INTENTS: frozenset[str] = frozenset({
    "U_MOVE_STOP",
    "U_UPDATE_STOP",
    "U_MOVE_STOP_TO_BE",
    "U_CLOSE_FULL",
    "U_CLOSE_PARTIAL",
    "U_CANCEL_PENDING_ORDERS",
    "U_REMOVE_PENDING_ENTRY",
    "U_ADD_ENTRY",
    "U_REENTER",
    "U_UPDATE_TAKE_PROFITS",
    "U_INVALIDATE_SETUP",   # orphan → CANCEL_PENDING
    "U_REVERSE_SIGNAL",     # orphan → CLOSE + warning
})

_REPORT_INTENTS: frozenset[str] = frozenset({
    "U_TP_HIT",
    "U_STOP_HIT",
    "U_REPORT_FINAL_RESULT",
    "U_ACTIVATION",    # orphan → ENTRY_FILLED
    "U_MARK_FILLED",   # orphan → ENTRY_FILLED
    "U_EXIT_BE",       # orphan → BREAKEVEN_EXIT
})

_SIGNAL_INTENTS: frozenset[str] = frozenset({"NS_CREATE_SIGNAL"})

_LINK_ID_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(result: TraderParseResult, context: ParserContext) -> CanonicalMessage:
    """Convert a TraderParseResult into a CanonicalMessage v1."""
    envelope = adapt_legacy_parse_result_to_event_envelope(result)
    intents: list[str] = list(envelope.intents_detected or [])
    warnings: list[str] = list(envelope.warnings or [])

    raw_ctx = _build_raw_context(context)
    targeting = _build_targeting(result, context)

    primary_class, parse_status = _resolve_class_and_status(
        result,
        intents,
        dict(result.entities or {}),
        warnings,
    )

    signal = None
    update = None
    report = None

    if primary_class == "SIGNAL":
        signal = _build_signal_payload_from_envelope(envelope, warnings)
        parse_status = _signal_parse_status(signal)

    elif primary_class == "UPDATE":
        update = _build_update_payload_from_envelope(envelope.update_payload_raw)
        report = _build_report_payload_from_envelope(envelope.report_payload_raw)
        if report is not None and not report.events and report.reported_result is None:
            report = None
        parse_status = "PARSED" if (update and update.operations) else "PARTIAL"

    elif primary_class == "REPORT":
        report = _build_report_payload_from_envelope(envelope.report_payload_raw)
        parse_status = "PARSED" if (report and (report.events or report.reported_result)) else "PARTIAL"

    # Degrade to INFO if we couldn't build any payload
    if primary_class != "INFO" and signal is None and update is None and report is None:
        primary_class = "INFO"
        parse_status = "UNCLASSIFIED"

    return CanonicalMessage(
        parser_profile=context.trader_code,
        primary_class=primary_class,
        parse_status=parse_status,
        confidence=max(0.0, min(1.0, float(result.confidence or 0.0))),
        intents=intents,
        primary_intent=result.primary_intent,
        targeting=targeting,
        signal=signal,
        update=update,
        report=report,
        warnings=warnings,
        diagnostics={
            **dict(result.diagnostics or {}),
            "event_envelope_v1": envelope.model_dump(mode="python"),
        },
        raw_context=raw_ctx,
    )


def _build_signal_payload_from_envelope(
    envelope: TraderEventEnvelopeV1,
    warnings: list[str],
) -> SignalPayload:
    signal_raw = envelope.signal_payload_raw
    entry_legs = _build_entry_legs_from_envelope(signal_raw)
    canonical_structure = _map_entry_structure_from_envelope(signal_raw.entry_structure, entry_legs)
    stop_loss = _build_stop_loss_from_envelope(signal_raw)
    take_profits = _build_take_profits_from_envelope(signal_raw)
    risk_hint = _build_risk_hint_from_envelope(signal_raw)

    missing: list[str] = []
    if not envelope.instrument.symbol:
        missing.append("symbol")
    if not envelope.instrument.side:
        missing.append("side")
    if not entry_legs:
        missing.append("entries")
    if stop_loss is None:
        missing.append("stop_loss")
    if not take_profits:
        missing.append("take_profits")
    if canonical_structure is None and entry_legs:
        missing.append("entry_structure")

    completeness = "COMPLETE" if not missing else "INCOMPLETE"

    return SignalPayload(
        symbol=envelope.instrument.symbol,
        side=envelope.instrument.side,
        entry_structure=canonical_structure,
        entries=entry_legs,
        stop_loss=stop_loss,
        take_profits=take_profits,
        risk_hint=risk_hint,
        completeness=completeness,
        missing_fields=missing,
        raw_fragments=dict(signal_raw.raw_fragments or {}),
    )


def _build_update_payload_from_envelope(update_raw: UpdatePayloadRaw) -> UpdatePayload:
    operations: list[UpdateOperation] = []
    for item in update_raw.operations:
        op = _build_update_operation_from_envelope(item)
        if op is not None:
            operations.append(op)
    return UpdatePayload(operations=operations)


def _build_report_payload_from_envelope(report_raw: ReportPayloadRaw) -> ReportPayload | None:
    events: list[ReportEvent] = []
    for item in report_raw.events:
        event = ReportEvent(
            event_type=item.event_type,
            level=item.level,
            price=Price.from_float(float(item.price)) if isinstance(item.price, (int, float)) else None,
            result=_build_reported_result_from_envelope(item.result),
        )
        events.append(event)

    reported_result = _build_reported_result_from_envelope(report_raw.reported_result)
    if not events and reported_result is None and not report_raw.notes:
        return None
    return ReportPayload(events=events, reported_result=reported_result, notes=list(report_raw.notes or []))


def _build_entry_legs_from_envelope(signal_raw: SignalPayloadRaw) -> list[EntryLeg]:
    legs: list[EntryLeg] = []
    for item in signal_raw.entries:
        role = item.role if item.role in {"PRIMARY", "AVERAGING"} else "UNKNOWN"
        try:
            legs.append(
                EntryLeg(
                    sequence=item.sequence,
                    entry_type=item.entry_type or ("LIMIT" if item.price is not None else "MARKET"),
                    price=Price.from_float(float(item.price)) if isinstance(item.price, (int, float)) else None,
                    role=role,
                    size_hint=item.size_hint,
                    is_optional=bool(item.is_optional),
                )
            )
        except Exception:
            continue
    return legs


def _map_entry_structure_from_envelope(raw: str | None, legs: list[EntryLeg]) -> str | None:
    if raw in {"ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"}:
        return raw
    return _map_entry_structure("", legs)


def _build_stop_loss_from_envelope(signal_raw: SignalPayloadRaw) -> StopLoss | None:
    stop = signal_raw.stop_loss
    if stop is None:
        return None
    if isinstance(stop.price, (int, float)):
        return StopLoss(price=Price.from_float(float(stop.price)))
    if stop.raw:
        return StopLoss()
    return None


def _build_take_profits_from_envelope(signal_raw: SignalPayloadRaw) -> list[TakeProfit]:
    take_profits: list[TakeProfit] = []
    for item in signal_raw.take_profits:
        if not isinstance(item.price, (int, float)):
            continue
        take_profits.append(
            TakeProfit(
                sequence=item.sequence,
                price=Price.from_float(float(item.price)),
                label=item.label,
                close_fraction=item.close_fraction,
            )
        )
    return take_profits


def _build_risk_hint_from_envelope(signal_raw: SignalPayloadRaw) -> RiskHint | None:
    risk = signal_raw.risk_hint
    if risk is None:
        return None
    return RiskHint(raw=risk.raw, value=risk.value, unit=risk.unit)


def _build_update_operation_from_envelope(item: UpdateOperationRaw) -> UpdateOperation | None:
    if item.op_type == "SET_STOP" and item.set_stop is not None:
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=StopTarget(target_type=item.set_stop.target_type, value=item.set_stop.value),
        )
    if item.op_type == "CLOSE" and item.close is not None:
        close_price = None
        if isinstance(item.close.close_price, (int, float)):
            close_price = Price.from_float(float(item.close.close_price))
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(
                close_fraction=item.close.close_fraction,
                close_price=close_price,
                close_scope=item.close.close_scope,
            ),
        )
    if item.op_type == "CANCEL_PENDING" and item.cancel_pending is not None:
        return UpdateOperation(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope=item.cancel_pending.cancel_scope),
        )
    if item.op_type == "MODIFY_ENTRIES" and item.modify_entries is not None:
        entries = _build_entry_legs_from_envelope(SignalPayloadRaw(entries=item.modify_entries.entries))
        if not entries:
            return None
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(mode=item.modify_entries.mode, entries=entries),
        )
    if item.op_type == "MODIFY_TARGETS" and item.modify_targets is not None:
        take_profits = _build_take_profits_from_envelope(
            SignalPayloadRaw(take_profits=item.modify_targets.take_profits)
        )
        if not take_profits:
            return None
        return UpdateOperation(
            op_type="MODIFY_TARGETS",
            modify_targets=ModifyTargetsOperation(
                mode=item.modify_targets.mode,
                take_profits=take_profits,
                target_tp_level=item.modify_targets.target_tp_level,
            ),
        )
    return None


def _build_reported_result_from_envelope(item: ReportedResultRaw | None) -> ReportedResult | None:
    if item is None:
        return None
    return ReportedResult(value=item.value, unit=item.unit, text=item.text)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _resolve_class_and_status(
    result: TraderParseResult,
    intents: list[str],
    entities: dict[str, Any],
    warnings: list[str],
) -> tuple[MessageClass, ParseStatus]:
    intent_set = set(intents)

    if intent_set & _SIGNAL_INTENTS:
        return "SIGNAL", "PARTIAL"

    if "U_RISK_NOTE" in intent_set and not (intent_set & (_UPDATE_INTENTS | _REPORT_INTENTS)):
        return "INFO", "PARSED"

    has_update = bool(intent_set & _UPDATE_INTENTS)
    has_report = bool(intent_set & _REPORT_INTENTS)

    if has_update:
        return "UPDATE", "PARTIAL"
    if has_report:
        return "REPORT", "PARTIAL"

    # Fallback: use legacy message_type
    mt = str(result.message_type or "").upper()
    if mt in {"NEW_SIGNAL", "SETUP_INCOMPLETE"}:
        return "SIGNAL", "PARTIAL"
    if mt == "UPDATE":
        return "UPDATE", "PARTIAL"
    if mt in {"INFO_ONLY", "INFO"}:
        return "INFO", "PARSED"

    warnings.append(f"normalizer_unclassified: message_type={result.message_type!r}")
    return "INFO", "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# RawContext
# ---------------------------------------------------------------------------

def _build_raw_context(context: ParserContext) -> RawContext:
    return RawContext(
        raw_text=context.raw_text or "",
        reply_to_message_id=context.reply_to_message_id,
        extracted_links=list(context.extracted_links or []),
        hashtags=list(context.hashtags or []),
        source_chat_id=str(context.channel_id) if context.channel_id is not None else None,
    )


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------

def _build_targeting(result: TraderParseResult, context: ParserContext) -> Targeting | None:
    refs: list[TargetRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(ref_type: str, value: int | str) -> None:
        key = (ref_type, str(value))
        if key in seen:
            return
        seen.add(key)
        refs.append(TargetRef(ref_type=ref_type, value=value))  # type: ignore[arg-type]

    for item in result.target_refs or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        ref = item.get("ref")
        if kind == "reply" and isinstance(ref, int):
            _add("REPLY", ref)
        elif kind == "telegram_link" and isinstance(ref, str):
            _add("TELEGRAM_LINK", ref)
        elif kind == "message_id" and isinstance(ref, int):
            _add("MESSAGE_ID", ref)

    # Also pull from context.reply_to_message_id if not already captured
    if context.reply_to_message_id is not None:
        _add("REPLY", context.reply_to_message_id)

    linking = dict(result.linking or {})
    scope_dict = dict(result.target_scope or {})
    has_global = bool(linking.get("has_global_target_scope") or scope_dict.get("applies_to_all"))
    targeted = bool(refs) or has_global

    scope = _build_target_scope(scope_dict, has_global)

    if not targeted and not refs:
        return None

    if refs:
        strategy = "REPLY_OR_LINK"
    elif has_global:
        strategy = "GLOBAL_SCOPE"
    else:
        strategy = "UNRESOLVED"

    return Targeting(refs=refs, scope=scope, strategy=strategy, targeted=targeted)  # type: ignore[arg-type]


def _build_target_scope(scope_dict: dict[str, Any], has_global: bool) -> TargetScope:
    kind = str(scope_dict.get("kind") or "").lower()
    scope_val = scope_dict.get("scope")

    if kind == "portfolio_side" or has_global:
        val = str(scope_val or "")
        if "short" in val.lower():
            return TargetScope(kind="PORTFOLIO_SIDE", side_filter="SHORT", applies_to_all=True)
        if "long" in val.lower():
            return TargetScope(kind="PORTFOLIO_SIDE", side_filter="LONG", applies_to_all=True)
        return TargetScope(kind="ALL_OPEN", applies_to_all=True)

    return TargetScope(kind="SINGLE_SIGNAL")


# ---------------------------------------------------------------------------
# Signal payload
# ---------------------------------------------------------------------------

def _build_signal_payload(entities: dict[str, Any], warnings: list[str]) -> SignalPayload:
    symbol = _str_or_none(entities.get("symbol"))
    side = _side(entities.get("side"))

    # Entry legs from entry_plan_entries (rich format) or fallback to entry list
    plan_entries: list[dict[str, Any]] = list(entities.get("entry_plan_entries") or [])
    entry_structure_raw = str(entities.get("entry_structure") or "")
    entry_legs = _build_entry_legs(plan_entries, entities, entry_structure_raw)

    canonical_structure = _map_entry_structure(entry_structure_raw, entry_legs)

    stop_loss = _build_stop_loss(entities.get("stop_loss"))

    tps_raw: list[float] = [v for v in (entities.get("take_profits") or []) if isinstance(v, (int, float))]
    take_profits = [
        TakeProfit(sequence=i + 1, price=Price.from_float(float(v)))
        for i, v in enumerate(tps_raw)
    ]

    missing: list[str] = []
    if not symbol:
        missing.append("symbol")
    if not side:
        missing.append("side")
    if not entry_legs:
        missing.append("entries")
    if stop_loss is None:
        missing.append("stop_loss")
    if not take_profits:
        missing.append("take_profits")
    if canonical_structure is None and entry_legs:
        missing.append("entry_structure")

    completeness = "COMPLETE" if not missing else "INCOMPLETE"

    return SignalPayload(
        symbol=symbol,
        side=side,
        entry_structure=canonical_structure,
        entries=entry_legs,
        stop_loss=stop_loss,
        take_profits=take_profits,
        completeness=completeness,
        missing_fields=missing,
    )


def _signal_parse_status(signal: SignalPayload) -> ParseStatus:
    if signal.completeness == "COMPLETE":
        return "PARSED"
    return "PARTIAL"


def _build_entry_legs(
    plan_entries: list[dict[str, Any]],
    entities: dict[str, Any],
    structure_raw: str,
) -> list[EntryLeg]:
    if plan_entries:
        legs: list[EntryLeg] = []
        for item in plan_entries:
            seq = int(item.get("sequence") or len(legs) + 1)
            order_type = str(item.get("order_type") or "LIMIT").upper()
            entry_type = "MARKET" if order_type == "MARKET" else "LIMIT"
            price_val = item.get("price")
            price = Price.from_float(float(price_val)) if isinstance(price_val, (int, float)) else None
            role_raw = str(item.get("role") or "UNKNOWN").upper()
            role = role_raw if role_raw in {"PRIMARY", "AVERAGING"} else "UNKNOWN"
            try:
                legs.append(EntryLeg(
                    sequence=seq,
                    entry_type=entry_type,  # type: ignore[arg-type]
                    price=price,
                    role=role,  # type: ignore[arg-type]
                    is_optional=bool(item.get("is_optional")),
                ))
            except Exception:
                pass
        return legs

    # Fallback: build from flat entry list
    entry_list = [v for v in (entities.get("entry") or []) if isinstance(v, (int, float))]
    if not entry_list:
        return []
    return [
        EntryLeg(sequence=i + 1, entry_type="LIMIT", price=Price.from_float(float(v)))
        for i, v in enumerate(entry_list)
    ]


def _map_entry_structure(raw: str, legs: list[EntryLeg]) -> str | None:
    mapping = {
        "ONE_SHOT": "ONE_SHOT",
        "SINGLE": "ONE_SHOT",
        "TWO_STEP": "TWO_STEP",
        "RANGE": "RANGE",
        "LADDER": "LADDER",
    }
    if raw.upper() in mapping:
        return mapping[raw.upper()]
    # Infer from leg count
    n = len(legs)
    if n == 0:
        return None
    if n == 1:
        return "ONE_SHOT"
    if n == 2:
        return "TWO_STEP"
    return "LADDER"


def _build_stop_loss(value: Any) -> StopLoss | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return StopLoss(price=Price.from_float(float(value)))
    if isinstance(value, dict):
        v = value.get("value")
        if isinstance(v, (int, float)):
            raw = str(value.get("raw") or str(v))
            return StopLoss(price=Price(raw=raw, value=float(v)))
    return StopLoss()


# ---------------------------------------------------------------------------
# Update payload
# ---------------------------------------------------------------------------

def _build_update_payload(
    intents: list[str],
    entities: dict[str, Any],
    warnings: list[str],
) -> UpdatePayload:
    ops: list[UpdateOperation] = []
    for intent in intents:
        op = _intent_to_update_operation(intent, entities, warnings)
        if op is not None:
            ops.append(op)
    return UpdatePayload(operations=ops)


def _intent_to_update_operation(
    intent: str,
    entities: dict[str, Any],
    warnings: list[str],
) -> UpdateOperation | None:
    if intent == "U_MOVE_STOP_TO_BE":
        return UpdateOperation(op_type="SET_STOP", set_stop=StopTarget(target_type="ENTRY"))

    if intent == "U_MOVE_STOP":
        stop_target = _resolve_stop_target(entities.get("new_stop_level"))
        if stop_target is None:
            warnings.append("U_MOVE_STOP: new_stop_level missing or unresolvable")
            return None
        return UpdateOperation(op_type="SET_STOP", set_stop=stop_target)

    if intent == "U_CLOSE_FULL":
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(close_scope="FULL"),
        )

    if intent == "U_CLOSE_PARTIAL":
        fraction = entities.get("close_fraction")
        fraction_val = float(fraction) if isinstance(fraction, (int, float)) else None
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(
                close_scope="PARTIAL",
                close_fraction=fraction_val,
            ),
        )

    if intent == "U_CANCEL_PENDING_ORDERS":
        cancel_scope = _str_or_none(entities.get("cancel_scope"))
        return UpdateOperation(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope=cancel_scope),
        )

    if intent == "U_INVALIDATE_SETUP":
        return UpdateOperation(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope="ALL_PENDING_ENTRIES"),
        )

    if intent == "U_REVERSE_SIGNAL":
        warnings.append("U_REVERSE_SIGNAL: new signal component ignored; mapped to CLOSE only")
        return UpdateOperation(
            op_type="CLOSE",
            close=CloseOperation(close_scope="FULL"),
        )

    if intent == "U_ADD_ENTRY":
        price_val = entities.get("new_entry_price")
        price = Price.from_float(float(price_val)) if isinstance(price_val, (int, float)) else None
        if price is None:
            warnings.append("U_ADD_ENTRY: new_entry_price missing")
            return None
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(
                mode="ADD",
                entries=[EntryLeg(sequence=1, entry_type="LIMIT", price=price)],
            ),
        )

    if intent == "U_REENTER":
        plan_entries = list(entities.get("entry_plan_entries") or entities.get("entries") or [])
        entry_legs = _build_entry_legs(plan_entries, entities, "")
        if not entry_legs:
            warnings.append("U_REENTER: no entry legs found")
            return None
        return UpdateOperation(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(mode="REENTER", entries=entry_legs),
        )

    if intent == "U_UPDATE_TAKE_PROFITS":
        tps_raw = [v for v in (entities.get("take_profits") or []) if isinstance(v, (int, float))]
        if not tps_raw:
            warnings.append("U_UPDATE_TAKE_PROFITS: no take_profits found")
            return None
        tps = [TakeProfit(sequence=i + 1, price=Price.from_float(float(v))) for i, v in enumerate(tps_raw)]
        return UpdateOperation(
            op_type="MODIFY_TARGETS",
            modify_targets=ModifyTargetsOperation(mode="REPLACE_ALL", take_profits=tps),
        )

    return None


def _resolve_stop_target(value: Any) -> StopTarget | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return StopTarget(target_type="PRICE", value=float(value))
    if isinstance(value, str):
        upper = value.strip().upper()
        if upper in {"ENTRY", "BE", "BREAKEVEN"}:
            return StopTarget(target_type="ENTRY")
        # TP level e.g. "TP1", "TP2"
        m = re.match(r"^TP(\d+)$", upper)
        if m:
            return StopTarget(target_type="TP_LEVEL", value=int(m.group(1)))
        # Try to parse as float
        try:
            return StopTarget(target_type="PRICE", value=float(upper.replace(",", ".")))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Report payload
# ---------------------------------------------------------------------------

def _build_report_payload(
    intents: list[str],
    entities: dict[str, Any],
    reported_results: list[dict[str, Any]],
) -> ReportPayload:
    events = _build_report_events(intents, entities, reported_results)
    reported_result = _build_reported_result(reported_results)
    return ReportPayload(events=events, reported_result=reported_result)


def _build_report_events(
    intents: list[str],
    entities: dict[str, Any],
    reported_results: list[dict[str, Any]],
) -> list[ReportEvent]:
    events: list[ReportEvent] = []
    for intent in intents:
        event = _intent_to_report_event(intent, entities, reported_results)
        if event is not None:
            events.append(event)
    return events


def _intent_to_report_event(
    intent: str,
    entities: dict[str, Any],
    reported_results: list[dict[str, Any]],
) -> ReportEvent | None:
    if intent == "U_TP_HIT":
        hit_target = _str_or_none(entities.get("hit_target"))
        level: int | None = None
        if hit_target and hit_target.upper().startswith("TP"):
            suffix = hit_target[2:]
            level = int(suffix) if suffix.isdigit() else None
        result = _build_reported_result(reported_results)
        return ReportEvent(event_type="TP_HIT", level=level, result=result)

    if intent == "U_STOP_HIT":
        return ReportEvent(event_type="STOP_HIT")

    if intent == "U_REPORT_FINAL_RESULT":
        result = _build_reported_result(reported_results)
        return ReportEvent(event_type="FINAL_RESULT", result=result)

    if intent in {"U_ACTIVATION", "U_MARK_FILLED"}:
        return ReportEvent(event_type="ENTRY_FILLED")

    if intent == "U_EXIT_BE":
        return ReportEvent(event_type="BREAKEVEN_EXIT")

    return None


def _build_reported_result(reported_results: list[dict[str, Any]]) -> ReportedResult | None:
    if not reported_results:
        return None
    first = reported_results[0]
    if not isinstance(first, dict):
        return None
    value = first.get("value")
    unit_raw = str(first.get("unit") or "UNKNOWN").upper()
    unit = unit_raw if unit_raw in {"R", "PERCENT", "TEXT", "UNKNOWN"} else "UNKNOWN"
    text = _str_or_none(first.get("text"))
    return ReportedResult(
        value=float(value) if isinstance(value, (int, float)) else None,
        unit=unit,  # type: ignore[arg-type]
        text=text,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _str_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _side(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    up = value.strip().upper()
    return up if up in {"LONG", "SHORT"} else None
