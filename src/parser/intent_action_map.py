"""Canonical semantic mapping from parser intents to operational actions."""

from __future__ import annotations

from typing import Iterable

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
