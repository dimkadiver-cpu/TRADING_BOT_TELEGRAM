from __future__ import annotations

from typing import Literal, get_args

IntentName = Literal[
    "NEW_SETUP",
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING_ORDERS",
    "INVALIDATE_SETUP",
    "REENTER",
    "ADD_ENTRY",
    "UPDATE_TAKE_PROFITS",
    "ENTRY_FILLED",
    "TP_HIT",
    "SL_HIT",
    "EXIT_BE",
    "REPORT_RESULT",           # canonico — sostituisce FINAL/PARTIAL nel tempo
    "REPORT_FINAL_RESULT",     # legacy: alias → REPORT_RESULT + result_scope=FINAL
    "REPORT_PARTIAL_RESULT",   # legacy: alias → REPORT_RESULT + result_scope=PARTIAL
    "INFO_ONLY",
]

INTENT_NAMES: frozenset[str] = frozenset(get_args(IntentName))

STATEFUL_INTENTS: frozenset[str] = frozenset({
    "EXIT_BE",
    "TP_HIT",
    "SL_HIT",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
})

# EXIT_BE requires both a real target reference AND coherent history.
STRONGLY_STATEFUL: frozenset[str] = frozenset({"EXIT_BE"})

UPDATE_INTENTS: frozenset[str] = frozenset({
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING_ORDERS",
    "INVALIDATE_SETUP",
    "REENTER",
    "ADD_ENTRY",
    "UPDATE_TAKE_PROFITS",
})

REPORT_INTENTS: frozenset[str] = frozenset({
    "ENTRY_FILLED",
    "TP_HIT",
    "SL_HIT",
    "EXIT_BE",
    "REPORT_RESULT",
    "REPORT_FINAL_RESULT",
    "REPORT_PARTIAL_RESULT",
})


def validate_intent_name(name: str) -> IntentName:
    if name not in INTENT_NAMES:
        raise ValueError(f"{name!r} is not a valid intent name")
    return name  # type: ignore[return-value]


def is_update_intent(name: str) -> bool:
    return name in UPDATE_INTENTS


def is_report_intent(name: str) -> bool:
    return name in REPORT_INTENTS


def is_state_changing_intent(name: str) -> bool:
    return name in {
        "CLOSE_FULL",
        "CLOSE_PARTIAL",
        "MOVE_STOP_TO_BE",
        "MOVE_STOP",
        "CANCEL_PENDING_ORDERS",
        "INVALIDATE_SETUP",
    }
