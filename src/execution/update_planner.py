"""Builds a minimal state/domain update plan from normalized parser output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.parser.normalization import ParseResultNormalized


@dataclass(slots=True)
class StateUpdatePlan:
    message_type: str | None
    intents: list[str]
    actions: list[str]
    target_refs: list[int]
    signal_updates: list[dict[str, Any]] = field(default_factory=list)
    order_updates: list[dict[str, Any]] = field(default_factory=list)
    position_updates: list[dict[str, Any]] = field(default_factory=list)
    result_updates: list[dict[str, Any]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_type": self.message_type,
            "intents": self.intents,
            "actions": self.actions,
            "target_refs": self.target_refs,
            "signal_updates": self.signal_updates,
            "order_updates": self.order_updates,
            "position_updates": self.position_updates,
            "result_updates": self.result_updates,
            "events": self.events,
            "warnings": self.warnings,
        }


def build_update_plan(normalized: ParseResultNormalized | Mapping[str, Any]) -> StateUpdatePlan:
    data = _as_mapping(normalized)
    message_type = _as_str_or_none(data.get("message_type"))
    intents = _as_str_list(data.get("intents"))
    actions = _as_str_list(data.get("actions"))
    target_refs = _as_int_list(data.get("target_refs"))
    entities = _as_mapping(data.get("entities"))
    reported_results = data.get("reported_results")
    if not isinstance(reported_results, list):
        reported_results = []

    plan = StateUpdatePlan(
        message_type=message_type,
        intents=intents,
        actions=actions,
        target_refs=target_refs,
    )

    if message_type == "UPDATE" and not target_refs:
        cancel_scope = entities.get("cancel_scope")
        only_cancel_action = bool(actions) and all(action == "ACT_CANCEL_ALL_PENDING_ENTRIES" for action in actions)
        is_global_cancel = isinstance(cancel_scope, str) and cancel_scope in {
            "ALL_ALL",
            "ALL_LONG",
            "ALL_SHORT",
            "ALL_PENDING_ENTRIES",
            "ALL_PENDING_LONG_ENTRIES",
            "ALL_PENDING_SHORT_ENTRIES",
        }
        if not (only_cancel_action and is_global_cancel):
            plan.warnings.append("update_plan_missing_target_refs")

    for action in actions:
        if action == "ACT_MOVE_STOP_LOSS":
            _handle_move_stop(plan, entities)
        elif action == "ACT_CLOSE_PARTIAL":
            _handle_close_partial(plan, entities)
        elif action == "ACT_CLOSE_FULL":
            plan.position_updates.append({"field": "status", "op": "SET", "value": "CLOSED_CANDIDATE"})
            plan.events.append("FULL_CLOSE_REQUESTED")
        elif action == "ACT_CANCEL_ALL_PENDING_ENTRIES":
            plan.order_updates.append(
                {
                    "selector": entities.get("cancel_scope") or "ALL_ALL",
                    "field": "status",
                    "op": "SET",
                    "value": "CANCELLED",
                }
            )
            plan.events.append("PENDING_ENTRIES_CANCELLED")
        elif action == "ACT_MARK_ORDER_FILLED":
            plan.order_updates.append(
                {
                    "field": "status",
                    "op": "SET",
                    "value": entities.get("fill_state") or "FILLED",
                }
            )
            plan.position_updates.append({"field": "status", "op": "SET", "value": "ACTIVE_CANDIDATE"})
            plan.events.append("ENTRY_FILLED")
        elif action == "ACT_MARK_TP_HIT":
            plan.result_updates.append(
                {
                    "field": "target_hit",
                    "op": "MARK",
                    "value": entities.get("hit_target") or "TP",
                }
            )
            plan.events.append("TP_HIT")
        elif action == "ACT_MARK_STOP_HIT":
            plan.position_updates.append({"field": "stop_hit", "op": "SET", "value": True})
            plan.position_updates.append({"field": "status", "op": "SET", "value": "CLOSED_CANDIDATE"})
            plan.events.append("STOP_HIT")
        elif action == "ACT_MARK_SIGNAL_INVALID":
            plan.signal_updates.append({"field": "status", "op": "SET", "value": "INVALID"})
            plan.events.append("SIGNAL_INVALIDATED")
        elif action == "ACT_MARK_POSITION_CLOSED":
            plan.position_updates.append({"field": "status", "op": "SET", "value": "CLOSED"})
            plan.events.append("POSITION_CLOSED")
        elif action == "ACT_ATTACH_RESULT":
            if reported_results:
                plan.result_updates.append(
                    {
                        "field": "reported_results",
                        "op": "ATTACH",
                        "value": reported_results,
                        "mode": entities.get("result_mode"),
                    }
                )
            else:
                plan.warnings.append("update_plan_missing_reported_results")
            plan.events.append("RESULT_ATTACHED")
        else:
            plan.warnings.append(f"update_plan_unknown_action:{action}")

    return plan


def _handle_move_stop(plan: StateUpdatePlan, entities: Mapping[str, Any]) -> None:
    new_stop_level = entities.get("new_stop_level")
    if new_stop_level == "ENTRY":
        plan.position_updates.append({"field": "stop_loss", "op": "SET_FROM_ENTRY", "value": "ENTRY"})
        plan.events.append("STOP_MOVED_TO_BE")
        return
    if isinstance(new_stop_level, (int, float, str)) and str(new_stop_level).strip():
        plan.position_updates.append({"field": "stop_loss", "op": "SET", "value": new_stop_level})
        plan.events.append("STOP_MOVED")
        return
    plan.warnings.append("update_plan_missing_new_stop_level")


def _handle_close_partial(plan: StateUpdatePlan, entities: Mapping[str, Any]) -> None:
    update: dict[str, Any] = {"field": "close_scope", "op": "SET", "value": entities.get("close_scope") or "PARTIAL"}
    close_fraction = entities.get("close_fraction")
    if isinstance(close_fraction, (int, float)):
        update["close_fraction"] = float(close_fraction)
    plan.position_updates.append(update)
    plan.events.append("PARTIAL_CLOSE_REQUESTED")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, ParseResultNormalized):
        return value.as_dict()
    if isinstance(value, Mapping):
        return value
    return {}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _as_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        if isinstance(item, int):
            out.append(item)
    return out


def _as_str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
