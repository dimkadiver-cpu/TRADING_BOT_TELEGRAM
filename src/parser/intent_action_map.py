"""Canonical semantic mapping from parser intents to operational actions."""

from __future__ import annotations

from typing import Any, Iterable

_INTENT_TO_ACTIONS: dict[str, tuple[str, ...]] = {
    "U_MOVE_STOP": ("ACT_MOVE_STOP_LOSS",),
    "U_MOVE_STOP_TO_BE": ("ACT_MOVE_STOP_LOSS",),
    "U_CLOSE_PARTIAL": ("ACT_CLOSE_PARTIAL",),
    "U_CLOSE_FULL": ("ACT_CLOSE_FULL", "ACT_MARK_POSITION_CLOSED"),
    "U_CANCEL_PENDING_ORDERS": ("ACT_CANCEL_ALL_PENDING_ENTRIES",),
    "U_INVALIDATE_SETUP": ("ACT_MARK_SIGNAL_INVALID",),
    "U_MARK_FILLED": ("ACT_MARK_ORDER_FILLED",),
    "U_TP_HIT": ("ACT_MARK_TP_HIT",),
    "U_STOP_HIT": ("ACT_MARK_STOP_HIT", "ACT_MARK_POSITION_CLOSED"),
    "U_REPORT_FINAL_RESULT": ("ACT_ATTACH_RESULT",),
    "U_MANUAL_CLOSE": ("ACT_REQUEST_MANUAL_REVIEW",),
    "U_ADD_ENTRY": ("ACT_REQUEST_MANUAL_REVIEW",),
}


def map_intents_to_actions(intents: Iterable[str]) -> list[str]:
    actions: list[str] = []
    for intent in intents:
        mapped = _INTENT_TO_ACTIONS.get(intent, ())
        for action in mapped:
            if action not in actions:
                actions.append(action)
    return actions


def derive_primary_intent(intents: Iterable[str]) -> str | None:
    ordered = list(intents)
    for value in ordered:
        if value in _INTENT_TO_ACTIONS:
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
        action_type = _intent_to_action_type(intent)
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
                "raw_fragment": _extract_raw_fragment(intent=intent, raw_text=raw_text),
            }
        )
    return out


def _intent_to_action_type(intent: str) -> str | None:
    mapping = {
        "U_MOVE_STOP": "MOVE_STOP",
        "U_MOVE_STOP_TO_BE": "MOVE_STOP_TO_BE",
        "U_CLOSE_PARTIAL": "CLOSE_PARTIAL",
        "U_CLOSE_FULL": "CLOSE_FULL",
        "U_CANCEL_PENDING_ORDERS": "CANCEL_PENDING",
        "U_INVALIDATE_SETUP": "INVALIDATE_SETUP",
        "U_MARK_FILLED": "MARK_FILLED",
        "U_TP_HIT": "TP_HIT",
        "U_STOP_HIT": "STOP_HIT",
        "U_REPORT_FINAL_RESULT": "REPORT_RESULT",
        "U_MANUAL_CLOSE": "MANUAL_CLOSE",
        "U_ADD_ENTRY": "ADD_ENTRY",
    }
    return mapping.get(intent)


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


def _extract_applies_to(entities: dict[str, Any], target_scope: dict[str, Any]) -> str | None:
    close_scope = entities.get("close_scope")
    if isinstance(close_scope, str) and close_scope.strip():
        return close_scope.strip().upper()
    scope = target_scope.get("scope")
    if isinstance(scope, str) and scope.strip():
        return scope.strip().upper()
    return None


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


def infer_update_intents_from_text(normalized_text: str) -> list[str]:
    intents: list[str] = []
    text = f" {normalized_text} "

    if any(marker in text for marker in (" breakeven ", " move to be ", " to entry ", " stop to be ")):
        intents.append("U_MOVE_STOP_TO_BE")
    if any(marker in text for marker in (" move stop ", " move sl ", " adjust stop ", " modify stop ")):
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
