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
    "REPORT_FINAL_RESULT",
    "REPORT_PARTIAL_RESULT",
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


def validate_intent_name(name: str) -> IntentName:
    if name not in INTENT_NAMES:
        raise ValueError(f"{name!r} is not a valid intent name")
    return name  # type: ignore[return-value]
