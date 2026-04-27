"""Shared intent taxonomy: official intents, legacy aliases, precedences, compatibility rules.

This is the single source of truth for intent names across all trader profiles.
"""

from __future__ import annotations

OFFICIAL_INTENTS: list[str] = [
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

_OFFICIAL_SET: frozenset[str] = frozenset(OFFICIAL_INTENTS)

# Legacy intent name -> official intent name.
# Profiles that still emit legacy intents must normalize through resolve_alias().
LEGACY_ALIASES: dict[str, str] = {
    # trader_3 / trader_a legacy names
    "NS_CREATE_SIGNAL": "NEW_SETUP",
    "U_MOVE_STOP_TO_BE": "MOVE_STOP_TO_BE",
    "U_MOVE_STOP": "MOVE_STOP",
    "U_CLOSE_FULL": "CLOSE_FULL",
    "U_CLOSE_PARTIAL": "CLOSE_PARTIAL",
    "U_CANCEL_PENDING_ORDERS": "CANCEL_PENDING_ORDERS",
    "U_INVALIDATE_SETUP": "INVALIDATE_SETUP",
    "U_UPDATE_TAKE_PROFITS": "UPDATE_TAKE_PROFITS",
    "U_MARK_FILLED": "ENTRY_FILLED",
    "U_TP_HIT": "TP_HIT",
    "U_STOP_HIT": "SL_HIT",
    "U_EXIT_BE": "EXIT_BE",
    "U_REPORT_FINAL_RESULT": "REPORT_FINAL_RESULT",
    # U_REENTER is used in trader_3 and maps directly.
    "U_REENTER": "REENTER",
}

# Ordered from highest to lowest precedence for primary_intent_hint selection.
# When multiple intents are detected, the one appearing earliest in this list wins.
PRIMARY_INTENT_PRECEDENCE: list[str] = [
    "SL_HIT",
    "EXIT_BE",
    "TP_HIT",
    "REPORT_FINAL_RESULT",
    "REPORT_PARTIAL_RESULT",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING_ORDERS",
    "INVALIDATE_SETUP",
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "UPDATE_TAKE_PROFITS",
    "ADD_ENTRY",
    "REENTER",
    "ENTRY_FILLED",
    "NEW_SETUP",
    "INFO_ONLY",
]

# Intent -> set of intents that cannot be the primary at the same time.
# Symmetry is NOT assumed: if A excludes B it does not automatically mean B excludes A.
# Only primary_intent_hint selection is affected; both can still appear in intents_detected.
MUTUAL_EXCLUSIONS: dict[str, set[str]] = {
    "NEW_SETUP": {"SL_HIT", "CLOSE_FULL", "CLOSE_PARTIAL", "INVALIDATE_SETUP", "EXIT_BE"},
    "INFO_ONLY": {"NEW_SETUP", "SL_HIT", "CLOSE_FULL"},
}

# Intent -> set of intents that commonly appear together as multi-intent messages.
# Used downstream for validation and diagnostics.
COMPATIBLE_MULTI_INTENT: dict[str, set[str]] = {
    "SL_HIT": {"CLOSE_FULL", "REPORT_FINAL_RESULT"},
    "TP_HIT": {"CLOSE_PARTIAL", "MOVE_STOP_TO_BE", "REPORT_PARTIAL_RESULT"},
    "EXIT_BE": {"CLOSE_FULL"},
    "NEW_SETUP": {"CANCEL_PENDING_ORDERS", "INVALIDATE_SETUP"},
    "REPORT_FINAL_RESULT": {"CLOSE_FULL", "SL_HIT"},
    "ENTRY_FILLED": {"MOVE_STOP_TO_BE"},
}


def resolve_alias(intent: str) -> str:
    """Return the official intent for *intent*.

    If *intent* is already official, it is returned unchanged.
    If *intent* is a legacy alias, the corresponding official intent is returned.
    Raises ValueError for unrecognised names.
    """
    if intent in _OFFICIAL_SET:
        return intent
    if intent in LEGACY_ALIASES:
        return LEGACY_ALIASES[intent]
    raise ValueError(f"Unknown intent: {intent!r}. Not in OFFICIAL_INTENTS or LEGACY_ALIASES.")


def normalize_intents(intents: list[str]) -> list[str]:
    """Resolve all intents to official names, deduplicate while preserving first-seen order."""
    seen: dict[str, None] = {}
    for raw in intents:
        official = resolve_alias(raw)
        seen[official] = None
    return list(seen.keys())


def select_primary_intent(intents: list[str]) -> str | None:
    """Select the highest-precedence intent from *intents*.

    All intents must already be official (not legacy). Raises ValueError otherwise.
    Returns None for an empty list.
    """
    if not intents:
        return None
    for intent in intents:
        if intent not in _OFFICIAL_SET:
            raise ValueError(
                f"Intent {intent!r} is not an official intent. "
                "Call normalize_intents() first."
            )
    for candidate in PRIMARY_INTENT_PRECEDENCE:
        if candidate in intents:
            return candidate
    # Intent present but not in precedence list — return first alphabetically as tiebreak.
    return sorted(intents)[0]
