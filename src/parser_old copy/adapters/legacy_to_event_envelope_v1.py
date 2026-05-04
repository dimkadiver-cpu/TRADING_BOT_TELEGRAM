"""Central adapter: legacy TraderParseResult -> TraderEventEnvelopeV1."""

from __future__ import annotations

import re
from typing import Any

from src.parser.event_envelope_v1 import (
    CancelPendingOperationRaw,
    CloseOperationRaw,
    EntryLegRaw,
    InstrumentRaw,
    ModifyEntriesOperationRaw,
    ModifyTargetsOperationRaw,
    ReportEventRaw,
    ReportPayloadRaw,
    ReportedResultRaw,
    RiskHintRaw,
    SignalPayloadRaw,
    StopLossRaw,
    StopTargetRaw,
    TakeProfitRaw,
    TargetRefRaw,
    TraderEventEnvelopeV1,
    UpdateOperationRaw,
    UpdatePayloadRaw,
)
from src.parser.trader_profiles.base import TraderParseResult


def adapt_legacy_parse_result_to_event_envelope(result: TraderParseResult) -> TraderEventEnvelopeV1:
    entities = dict(result.entities or {})

    diagnostics = dict(result.diagnostics or {})
    diagnostics.update({
        "legacy_actions_structured": list(result.actions_structured or []),
        "legacy_target_scope": dict(result.target_scope or {}),
        "legacy_linking": dict(result.linking or {}),
    })
    if "entry" in entities:
        diagnostics["legacy_entities_entry"] = entities.get("entry")
    if "entry_order_type" in entities:
        diagnostics["legacy_entities_entry_order_type"] = entities.get("entry_order_type")
    if "entry_plan_type" in entities:
        diagnostics["legacy_entities_entry_plan_type"] = entities.get("entry_plan_type")

    adapter_warnings: list[str] = []
    update_payload_raw = _build_update_payload(result.intents or [], entities, adapter_warnings)

    envelope = TraderEventEnvelopeV1(
        message_type_hint=_normalize_message_type(result.message_type),
        intents_detected=[str(item) for item in (result.intents or []) if isinstance(item, str) and item.strip()],
        primary_intent_hint=_str_or_none(result.primary_intent),
        instrument=_build_instrument(entities),
        signal_payload_raw=_build_signal_payload(entities),
        update_payload_raw=update_payload_raw,
        report_payload_raw=_build_report_payload(result.intents or [], entities, list(result.reported_results or [])),
        targets_raw=_build_targets(list(result.target_refs or [])),
        warnings=[str(item) for item in (result.warnings or []) if isinstance(item, str)] + adapter_warnings,
        confidence=max(0.0, min(1.0, float(result.confidence or 0.0))),
        diagnostics=diagnostics,
    )
    return envelope


def _normalize_message_type(value: Any) -> str | None:
    raw = _str_or_none(value)
    if raw is None:
        return None
    upper = raw.upper()
    if upper in {"NEW_SIGNAL", "UPDATE", "INFO_ONLY", "UNCLASSIFIED"}:
        return upper
    if upper in {"INFO"}:
        return "INFO_ONLY"
    return None


def _build_instrument(entities: dict[str, Any]) -> InstrumentRaw:
    side = _normalize_side(entities.get("side"))
    if side is None:
        side = _normalize_side(entities.get("direction"))

    return InstrumentRaw(
        symbol=_str_or_none(entities.get("symbol")),
        side=side,
        market_type=_normalize_market_type(entities.get("market_type")),
    )


def _build_signal_payload(entities: dict[str, Any]) -> SignalPayloadRaw:
    risk_value = _coerce_float(entities.get("risk_value_normalized"))
    if risk_value is None:
        risk_value = _coerce_float(entities.get("risk_percent"))

    risk_hint = None
    risk_raw = _str_or_none(entities.get("risk_value_raw"))
    if risk_value is not None or risk_raw is not None:
        risk_hint = RiskHintRaw(
            value=risk_value,
            unit=_infer_risk_unit(risk_raw, entities),
            raw=risk_raw,
        )

    stop_loss = None
    stop_price = _coerce_float(entities.get("stop_loss"))
    stop_raw = _str_or_none(entities.get("stop_text_raw"))
    if stop_price is not None or stop_raw is not None:
        stop_loss = StopLossRaw(price=stop_price, raw=stop_raw)

    return SignalPayloadRaw(
        entry_structure=_normalize_entry_structure(entities.get("entry_structure")),
        entries=_build_entry_legs_from_entities(entities),
        stop_loss=stop_loss,
        take_profits=_build_take_profits(entities.get("take_profits")),
        risk_hint=risk_hint,
        raw_fragments={
            "entry": _str_or_none(entities.get("entry_text_raw")),
            "stop": stop_raw,
            "take_profits": _str_or_none(entities.get("take_profits_text_raw")),
        },
    )


def _build_update_payload(
    intents: list[str],
    entities: dict[str, Any],
    warnings: list[str] | None = None,
) -> UpdatePayloadRaw:
    operations: list[UpdateOperationRaw] = []
    for intent in intents:
        op = _intent_to_update_operation(intent, entities, warnings)
        if op is not None:
            operations.append(op)
    return UpdatePayloadRaw(operations=operations)


def _intent_to_update_operation(
    intent: str,
    entities: dict[str, Any],
    warnings: list[str] | None = None,
) -> UpdateOperationRaw | None:
    if intent == "U_MOVE_STOP_TO_BE":
        return UpdateOperationRaw(
            op_type="SET_STOP",
            set_stop=StopTargetRaw(target_type="ENTRY"),
            source_intent=intent,
        )

    if intent in {"U_MOVE_STOP", "U_UPDATE_STOP"}:
        target = _resolve_stop_target(entities.get("new_stop_level"))
        if target is None:
            target = _resolve_stop_target(entities.get("new_stop_price"))
        if target is None:
            if warnings is not None:
                warnings.append("U_MOVE_STOP: new_stop_level missing or unresolvable")
            return None
        return UpdateOperationRaw(op_type="SET_STOP", set_stop=target, source_intent=intent)

    if intent == "U_CLOSE_FULL":
        close_price = _coerce_float(entities.get("close_price"))
        return UpdateOperationRaw(
            op_type="CLOSE",
            close=CloseOperationRaw(close_scope=_str_or_none(entities.get("close_scope")) or "FULL", close_price=close_price),
            source_intent=intent,
        )

    if intent == "U_CLOSE_PARTIAL":
        close_fraction = _resolve_close_fraction(entities)
        close_price = _coerce_float(entities.get("close_price"))
        close_scope = _str_or_none(entities.get("close_scope")) or "PARTIAL"
        if close_fraction is None and close_price is None and close_scope is None:
            return None
        return UpdateOperationRaw(
            op_type="CLOSE",
            close=CloseOperationRaw(close_fraction=close_fraction, close_price=close_price, close_scope=close_scope),
            source_intent=intent,
        )

    if intent == "U_CANCEL_PENDING_ORDERS":
        return UpdateOperationRaw(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperationRaw(cancel_scope=_str_or_none(entities.get("cancel_scope"))),
            source_intent=intent,
        )

    if intent == "U_INVALIDATE_SETUP":
        return UpdateOperationRaw(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperationRaw(
                cancel_scope=_str_or_none(entities.get("cancel_scope")) or "ALL_PENDING_ENTRIES"
            ),
            source_intent=intent,
        )

    if intent == "U_REVERSE_SIGNAL":
        if warnings is not None:
            warnings.append("U_REVERSE_SIGNAL: new signal component ignored; mapped to CLOSE only")
        return UpdateOperationRaw(
            op_type="CLOSE",
            close=CloseOperationRaw(close_scope="FULL"),
            source_intent=intent,
        )

    if intent == "U_REMOVE_PENDING_ENTRY":
        return UpdateOperationRaw(
            op_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperationRaw(
                cancel_scope=_str_or_none(entities.get("cancel_scope")) or "REMOVE_PENDING_ENTRY"
            ),
            source_intent=intent,
        )

    if intent in {"U_REENTER", "U_ADD_ENTRY"}:
        mode = "REENTER" if intent == "U_REENTER" else "ADD"
        entries = _build_entry_legs_from_entities(entities)
        if not entries:
            new_entry_price = _coerce_float(entities.get("new_entry_price"))
            if new_entry_price is None:
                return None
            entries = [
                EntryLegRaw(
                    sequence=1,
                    entry_type="LIMIT",
                    price=new_entry_price,
                    role="REENTRY" if mode == "REENTER" else "UNKNOWN",
                )
            ]
        return UpdateOperationRaw(
            op_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperationRaw(mode=mode, entries=entries),
            source_intent=intent,
        )

    if intent == "U_UPDATE_TAKE_PROFITS":
        take_profits = _build_take_profits(entities.get("take_profits"))
        if not take_profits:
            return None
        return UpdateOperationRaw(
            op_type="MODIFY_TARGETS",
            modify_targets=ModifyTargetsOperationRaw(mode="REPLACE_ALL", take_profits=take_profits),
            source_intent=intent,
        )

    return None


def _build_report_payload(
    intents: list[str],
    entities: dict[str, Any],
    reported_results: list[dict[str, Any]],
) -> ReportPayloadRaw:
    events: list[ReportEventRaw] = []
    for intent in intents:
        event = _intent_to_report_event(intent, entities, reported_results)
        if event is not None:
            events.append(event)

    return ReportPayloadRaw(
        events=events,
        reported_result=_build_reported_result(reported_results),
    )


def _intent_to_report_event(
    intent: str,
    entities: dict[str, Any],
    reported_results: list[dict[str, Any]],
) -> ReportEventRaw | None:
    result = _build_reported_result(reported_results)
    close_price = _coerce_float(entities.get("close_price"))

    if intent in {"U_ACTIVATION", "U_MARK_FILLED"}:
        return ReportEventRaw(event_type="ENTRY_FILLED", price=close_price)

    if intent == "U_TP_HIT":
        return ReportEventRaw(
            event_type="TP_HIT",
            level=_parse_tp_level(entities.get("hit_target")),
            price=close_price,
            result=result,
        )

    if intent == "U_STOP_HIT":
        return ReportEventRaw(event_type="STOP_HIT", price=close_price, result=result)

    if intent == "U_EXIT_BE":
        return ReportEventRaw(event_type="BREAKEVEN_EXIT", price=close_price, result=result)

    if intent == "U_REPORT_FINAL_RESULT":
        return ReportEventRaw(event_type="FINAL_RESULT", price=close_price, result=result)

    return None


def _build_reported_result(reported_results: list[dict[str, Any]]) -> ReportedResultRaw | None:
    if not reported_results:
        return None
    first = reported_results[0]
    if not isinstance(first, dict):
        return None
    value = _coerce_float(first.get("value"))
    if value is None:
        value = _coerce_float(first.get("r_multiple"))
    text = _str_or_none(first.get("text"))
    unit = _normalize_result_unit(first.get("unit"), value_num=value, text=text)
    return ReportedResultRaw(value=value, unit=unit, text=text)


def _build_targets(target_refs: list[dict[str, Any]]) -> list[TargetRefRaw]:
    out: list[TargetRefRaw] = []
    for item in target_refs:
        if not isinstance(item, dict):
            continue
        out.append(
            TargetRefRaw(
                kind=_normalize_target_kind(item.get("kind")),
                value=item.get("ref"),
            )
        )
    return out


def _build_entry_legs_from_entities(entities: dict[str, Any]) -> list[EntryLegRaw]:
    source = entities.get("entry_plan_entries")
    if not source:
        source = entities.get("entries")
    if source:
        return _build_entry_legs_from_structured_source(source)

    entry_values = entities.get("entry")
    return _build_entry_legs_from_flat_prices(entry_values)


def _build_entry_legs_from_structured_source(source: Any) -> list[EntryLegRaw]:
    if not isinstance(source, list):
        return []
    legs: list[EntryLegRaw] = []
    for index, item in enumerate(source, start=1):
        if isinstance(item, dict):
            price = _coerce_float(item.get("price"))
            entry_type = _normalize_entry_type(item.get("order_type"), price=price)
            if entry_type == "LIMIT" and price is None:
                continue
            try:
                legs.append(
                    EntryLegRaw(
                        sequence=_coerce_int(item.get("sequence")) or index,
                        entry_type=entry_type,
                        price=price,
                        role=_normalize_entry_role(item.get("role")),
                        size_hint=_str_or_none(item.get("size_hint")),
                        is_optional=_coerce_bool(item.get("is_optional")),
                    )
                )
            except ValueError:
                continue
        elif isinstance(item, (int, float)):
            legs.append(
                EntryLegRaw(sequence=index, entry_type="LIMIT", price=float(item))
            )
    return legs


def _build_entry_legs_from_flat_prices(source: Any) -> list[EntryLegRaw]:
    if not isinstance(source, list):
        return []
    legs: list[EntryLegRaw] = []
    for index, value in enumerate(source, start=1):
        price = _coerce_float(value)
        if price is None:
            continue
        legs.append(EntryLegRaw(sequence=index, entry_type="LIMIT", price=price))
    return legs


def _build_take_profits(source: Any) -> list[TakeProfitRaw]:
    if not isinstance(source, list):
        return []
    take_profits: list[TakeProfitRaw] = []
    for index, item in enumerate(source, start=1):
        if isinstance(item, dict):
            price = _coerce_float(item.get("price"))
            if price is None:
                continue
            take_profits.append(
                TakeProfitRaw(
                    sequence=_coerce_int(item.get("sequence")) or index,
                    price=price,
                    label=_str_or_none(item.get("label")),
                    close_fraction=_resolve_close_fraction(item),
                )
            )
        else:
            price = _coerce_float(item)
            if price is None:
                continue
            take_profits.append(TakeProfitRaw(sequence=index, price=price))
    return take_profits


def _resolve_stop_target(value: Any) -> StopTargetRaw | None:
    if isinstance(value, (int, float)):
        return StopTargetRaw(target_type="PRICE", value=float(value))
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if normalized in {"ENTRY", "BE", "BREAKEVEN"}:
        return StopTargetRaw(target_type="ENTRY")
    match = re.match(r"^TP(\d+)$", normalized)
    if match:
        return StopTargetRaw(target_type="TP_LEVEL", value=int(match.group(1)))
    parsed = _coerce_float(normalized.replace(",", "."))
    if parsed is not None:
        return StopTargetRaw(target_type="PRICE", value=parsed)
    return None


def _parse_tp_level(value: Any) -> int | None:
    text = _str_or_none(value)
    if text is None:
        return None
    match = re.match(r"^TP(\d+)$", text.strip().upper())
    if not match:
        return None
    return int(match.group(1))


def _normalize_entry_structure(value: Any) -> str | None:
    text = _str_or_none(value)
    if text is None:
        return None
    upper = text.upper()
    if upper in {"ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"}:
        return upper
    if upper in {"SINGLE"}:
        return "ONE_SHOT"
    return None


def _normalize_entry_type(value: Any, *, price: float | None) -> str | None:
    text = _str_or_none(value)
    if text is None:
        return "LIMIT" if price is not None else None
    upper = text.upper()
    if upper == "MARKET":
        return "MARKET"
    return "LIMIT" if price is not None else None


def _normalize_entry_role(value: Any) -> str:
    text = _str_or_none(value)
    if text is None:
        return "UNKNOWN"
    upper = text.upper()
    allowed = {"PRIMARY", "AVERAGING", "RANGE_LOW", "RANGE_HIGH", "REENTRY", "UNKNOWN"}
    return upper if upper in allowed else "UNKNOWN"


def _normalize_side(value: Any) -> str | None:
    text = _str_or_none(value)
    if text is None:
        return None
    upper = text.upper()
    if upper in {"LONG", "BUY"}:
        return "LONG"
    if upper in {"SHORT", "SELL"}:
        return "SHORT"
    return None


def _normalize_market_type(value: Any) -> str | None:
    text = _str_or_none(value)
    if text is None:
        return None
    upper = text.upper()
    if upper == "SPOT":
        return "SPOT"
    if upper in {"FUTURES", "FUTURE", "PERP", "PERPETUAL"}:
        return "FUTURES"
    return "UNKNOWN"


def _normalize_result_unit(unit_value: Any, *, value_num: float | None = None, text: str | None = None) -> str:
    unit = _str_or_none(unit_value)
    if unit is None:
        if value_num is not None and text is None:
            return "UNKNOWN"
        if text:
            return "TEXT"
        return "UNKNOWN"
    upper = unit.upper()
    if upper in {"R", "RR"}:
        return "R"
    if upper in {"PERCENT", "%", "PCT"}:
        return "PERCENT"
    if upper == "TEXT":
        return "TEXT"
    return "UNKNOWN"


def _normalize_target_kind(value: Any) -> str:
    text = _str_or_none(value)
    if text is None:
        return "UNKNOWN"
    upper = text.upper()
    mapping = {
        "REPLY": "REPLY",
        "TELEGRAM_LINK": "TELEGRAM_LINK",
        "MESSAGE_ID": "MESSAGE_ID",
        "EXPLICIT_ID": "EXPLICIT_ID",
        "SYMBOL": "SYMBOL",
    }
    return mapping.get(upper, "UNKNOWN")


def _infer_risk_unit(risk_raw: str | None, entities: dict[str, Any]) -> str:
    if risk_raw and "%" in risk_raw:
        return "PERCENT"
    if entities.get("risk_percent") is not None:
        return "PERCENT"
    return "UNKNOWN"


def _resolve_close_fraction(source: dict[str, Any]) -> float | None:
    fraction = _coerce_float(source.get("close_fraction"))
    if fraction is not None:
        return fraction if fraction <= 1.0 else fraction / 100.0
    percent = _coerce_float(source.get("partial_close_percent"))
    if percent is not None:
        return percent / 100.0 if percent > 1.0 else percent
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped.replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _str_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
