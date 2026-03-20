"""Canonical semantic mapping from parser intents to operational actions."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable

from src.parser.canonical_schema import canonical_action_for_intent, load_canonical_intent_schema, normalize_intent_name

_DEFAULT_INTENT_POLICY: dict[str, Any] = {"state_change": False, "action": None}

_STATE_CHANGING_INTENT_POLICIES: dict[str, dict[str, Any]] = {
    "NS_CREATE_SIGNAL": {"state_change": True, "action": "ACT_CREATE_SIGNAL"},
    "U_MOVE_STOP": {"state_change": True, "action": "ACT_MOVE_STOP_LOSS"},
    "U_MOVE_STOP_TO_BE": {"state_change": True, "action": "ACT_MOVE_STOP_LOSS"},
    "U_CANCEL_PENDING_ORDERS": {"state_change": True, "action": "ACT_CANCEL_ALL_PENDING_ENTRIES"},
    "U_REMOVE_PENDING_ENTRY": {"state_change": True, "action": "ACT_REMOVE_PENDING_ENTRY"},
    "U_CLOSE_PARTIAL": {"state_change": True, "action": "ACT_CLOSE_PARTIAL"},
    "U_CLOSE_FULL": {"state_change": True, "action": "ACT_CLOSE_FULL"},
    "U_REENTER": {"state_change": True, "action": "ACT_REENTER_POSITION"},
    "U_INVALIDATE_SETUP": {"state_change": True, "action": "ACT_MARK_SIGNAL_INVALID"},
}

_INFO_ONLY_INTENTS = {
    "U_MARK_FILLED",
    "U_TP_HIT",
    "U_STOP_HIT",
    "U_REPORT_FINAL_RESULT",
}


@lru_cache(maxsize=1)
def load_intent_policy_map() -> dict[str, dict[str, Any]]:
    schema = load_canonical_intent_schema()
    policy = {intent: dict(_DEFAULT_INTENT_POLICY) for intent in schema}
    for intent, entry in _STATE_CHANGING_INTENT_POLICIES.items():
        policy[intent] = dict(entry)
    for intent in _INFO_ONLY_INTENTS:
        policy.setdefault(intent, dict(_DEFAULT_INTENT_POLICY))
        policy[intent] = dict(_DEFAULT_INTENT_POLICY)
    return policy


intent_policy_map = load_intent_policy_map()



def map_intents_to_actions(intents: Iterable[str], entities: dict[str, Any] | None = None) -> list[str]:
    payload = entities if isinstance(entities, dict) else {}
    actions: list[str] = []
    for intent in intents:
        canonical_intent = normalize_intent_name(intent)
        policy = intent_policy_for_intent(canonical_intent)
        if not policy.get("state_change"):
            continue
        action = policy.get("action")
        if not isinstance(action, str) or not action:
            continue
        if action == "ACT_CLOSE_FULL" and payload.get("close_status_passive"):
            action = "ACT_CLOSE_FULL"
        if action not in actions:
            actions.append(action)
    return actions


def derive_primary_intent(intents: Iterable[str]) -> str | None:
    ordered = [normalize_intent_name(v) for v in intents]
    for value in ordered:
        if value in intent_policy_map:
            return value
    return ordered[0] if ordered else None


def build_actions_structured(
    intents: Iterable[str],
    entities: dict[str, Any] | None,
    raw_text: str,
    target_scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = entities if isinstance(entities, dict) else {}
    scope = target_scope if isinstance(target_scope, dict) else {}

    out: list[dict[str, Any]] = []
    for intent in intents:
        canonical_intent = normalize_intent_name(intent)
        policy = intent_policy_for_intent(canonical_intent)
        if not policy.get("state_change"):
            continue
        action_type = _intent_to_action_type(canonical_intent, payload)
        if action_type is None:
            continue
        out.append(
            {
                "action_type": action_type,
                "confidence": 0.7,
                "target_tp_level": _extract_target_tp_level(payload),
                "target_entry_id": scope.get("target_entry_id"),
                "new_stop_price": _extract_new_stop_price(payload),
                "close_fraction": _extract_close_fraction(payload),
                "result_value": _extract_result_value(payload),
                "result_unit": _extract_result_unit(payload),
                "applies_to": _extract_applies_to(payload, scope),
                "raw_fragment": _extract_raw_fragment(intent=canonical_intent, raw_text=raw_text),
            }
        )
    return out


def _intent_to_action_type(intent: str, entities: dict[str, Any] | None = None) -> str | None:
    payload = entities if isinstance(entities, dict) else {}
    mapping = {
        "NS_CREATE_SIGNAL": "CREATE_SIGNAL",
        "U_MOVE_STOP": "MOVE_STOP",
        "U_MOVE_STOP_TO_BE": "MOVE_STOP_TO_BE",
        "U_CLOSE_PARTIAL": "CLOSE_PARTIAL",
        "U_CLOSE_FULL": "CLOSE_FULL",
        "U_CANCEL_PENDING_ORDERS": "CANCEL_PENDING",
        "U_REMOVE_PENDING_ENTRY": "REMOVE_PENDING_ENTRY",
        "U_INVALIDATE_SETUP": "INVALIDATE_SETUP",
        "U_MARK_FILLED": "MARK_FILLED",
        "U_TP_HIT": "TP_HIT",
        "U_STOP_HIT": "STOP_HIT",
        "U_REPORT_FINAL_RESULT": "REPORT_RESULT",
        "U_MANUAL_CLOSE": "MANUAL_CLOSE",
        "U_ADD_ENTRY": "ADD_ENTRY",
        "U_ACTIVATION": "ACTIVATION",
        "U_UPDATE_PENDING_ENTRY": "UPDATE_PENDING_ENTRY",
        "U_UPDATE_TAKE_PROFITS": "UPDATE_TAKE_PROFITS",
        "U_EXIT_BE": "EXIT_BE",
        "U_REENTER": "REENTER",
        "U_REVERSE_SIGNAL": "REVERSE_SIGNAL",
        "U_RISK_NOTE": "RISK_NOTE",
    }
    return mapping.get(intent)


def _intent_to_actions(intent: str, entities: dict[str, Any] | None = None) -> tuple[str, ...]:
    payload = entities if isinstance(entities, dict) else {}
    if intent == "U_CLOSE_FULL" and payload.get("close_status_passive"):
        return ("ACT_CLOSE_FULL_AND_MARK_CLOSED",)
    csv_action = canonical_action_for_intent(intent)
    if csv_action:
        return (csv_action,)
    return _INTENT_TO_ACTIONS.get(intent, ())


def _extract_target_tp_level(entities: dict[str, Any]) -> int | None:
    hit_target = entities.get("hit_target")
    if not isinstance(hit_target, str):
        return None
    marker = hit_target.strip().upper()
    if marker.startswith("TP") and marker[2:].isdigit():
        return int(marker[2:])
    return None


def _extract_new_stop_price(entities: dict[str, Any]) -> float | None:
    value = entities.get("new_stop_level")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_close_fraction(entities: dict[str, Any]) -> float | None:
    value = entities.get("close_fraction")
    if isinstance(value, (int, float)):
        bounded = max(0.0, min(1.0, float(value)))
        return round(bounded, 6)
    return None


def _extract_result_value(entities: dict[str, Any]) -> float | None:
    for key in ("result_value", "result_percent"):
        value = entities.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _extract_result_unit(entities: dict[str, Any]) -> str | None:
    if isinstance(entities.get("result_unit"), str):
        return str(entities.get("result_unit"))
    if isinstance(entities.get("result_percent"), (int, float)):
        return "PERCENT"
    return None


def _extract_applies_to(entities: dict[str, Any], target_scope: dict[str, Any]) -> dict[str, Any]:
    default = {"scope_type": None, "scope_value": None}

    close_scope = entities.get("close_scope")
    if isinstance(close_scope, str) and close_scope.strip():
        return {"scope_type": "close_scope", "scope_value": close_scope.strip().upper()}

    if isinstance(target_scope, dict) and target_scope:
        if "scope_type" in target_scope or "scope_value" in target_scope:
            return {
                "scope_type": target_scope.get("scope_type"),
                "scope_value": target_scope.get("scope_value"),
            }
        kind = target_scope.get("kind")
        scope = target_scope.get("scope")
        if kind is not None or scope is not None:
            return {
                "scope_type": str(kind) if kind is not None else None,
                "scope_value": scope,
            }
        if "target_entry_id" in target_scope and target_scope.get("target_entry_id") is not None:
            return {"scope_type": "entry", "scope_value": target_scope.get("target_entry_id")}

    return default


def _extract_raw_fragment(*, intent: str, raw_text: str) -> str | None:
    text = (raw_text or "").strip()
    if not text:
        return None
    hints: dict[str, tuple[str, ...]] = {
        "U_MOVE_STOP_TO_BE": ("breakeven", "to be", "в бу"),
        "U_MOVE_STOP": ("move stop", "move sl", "переносим"),
        "U_CLOSE_FULL": ("close", "закры"),
        "U_CLOSE_PARTIAL": ("partial", "част"),
        "U_TP_HIT": ("tp", "тейк"),
        "U_STOP_HIT": ("stop", "стоп"),
        "U_CANCEL_PENDING_ORDERS": ("cancel", "не актуально", "отмен"),
    }
    lowered = text.lower()
    markers = hints.get(intent, ())
    for marker in markers:
        idx = lowered.find(marker)
        if idx >= 0:
            start = max(0, idx - 32)
            end = min(len(text), idx + len(marker) + 48)
            return text[start:end].strip()
    return text[:120]


def intent_policy_for_intent(intent: str) -> dict[str, Any]:
    canonical_intent = normalize_intent_name(intent)
    return dict(intent_policy_map.get(canonical_intent, _DEFAULT_INTENT_POLICY))


def infer_update_intents_from_text(normalized_text: str) -> list[str]:
    intents: list[str] = []
    text = f" {normalized_text} "

    move_to_be_markers = (
        " breakeven ",
        " break even ",
        " move to be ",
        " to entry ",
        " stop to be ",
        " stop to breakeven ",
        " stop to entry ",
        " в бу ",
        " в безубыток ",
        " стоп на точку входа ",
    )
    move_stop_markers = (
        " move stop ",
        " move sl ",
        " adjust stop ",
        " modify stop ",
        " update stop ",
        " new sl ",
        " stop on 1 tp ",
        " stop on tp1 ",
        " стоп на 1 тейк ",
        " стоп на первый тейк ",
        " переносим стоп ",
        " перенос стопа ",
    )

    if any(marker in text for marker in move_to_be_markers):
        intents.append("U_MOVE_STOP_TO_BE")
    elif any(marker in text for marker in move_stop_markers):
        intents.append("U_MOVE_STOP")
    if any(marker in text for marker in (" tp hit ", " target hit ", " take profit ")):
        intents.append("U_TP_HIT")
    if any(marker in text for marker in (" close partial ", " partial close ", " close 50%", " close half ")):
        intents.append("U_CLOSE_PARTIAL")
    if any(marker in text for marker in (" close all ", " close full ", " exit all ", " close position ")):
        intents.append("U_CLOSE_FULL")
    if any(marker in text for marker in (" stop hit ", " stopped out ", " sl hit ")):
        intents.append("U_STOP_HIT")
    if any(marker in text for marker in (" cancel pending ", " remove limit ", " delete entry ", " cancel orders ")):
        intents.append("U_CANCEL_PENDING_ORDERS")
    if any(marker in text for marker in (" invalidate setup ", " setup invalid ", " cancel setup ")):
        intents.append("U_INVALIDATE_SETUP")
    if any(marker in text for marker in (" add entry ", " add position ", " averaging in ")):
        intents.append("U_ADD_ENTRY")
    if any(marker in text for marker in (" manual close ", " i close ", " we close now ")):
        intents.append("U_MANUAL_CLOSE")
    if any(marker in text for marker in (" order filled ", " filled at ", " limit filled ")):
        intents.append("U_MARK_FILLED")

    return intents
