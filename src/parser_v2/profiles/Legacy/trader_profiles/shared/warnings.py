"""Shared warning codes and diagnostic constants.

All codes are lowercase snake_case strings. Add new codes here rather than
defining trader-local strings, unless the warning is genuinely trader-specific.
"""

from __future__ import annotations

WarningCode = str

# --- Common structural warnings ---

MISSING_TARGET = "missing_target"
CONFLICTING_INTENTS = "conflicting_intents"
PARTIAL_SIGNAL = "partial_signal"
UNCLASSIFIED_WITH_MARKERS = "unclassified_with_markers"
UNKNOWN_INTENT_DETECTED = "unknown_intent_detected"
AMBIGUOUS_UPDATE_WITHOUT_TARGET = "ambiguous_update_without_target"
MULTIPLE_PRIMARY_INTENT_CANDIDATES = "multiple_primary_intent_candidates"
LEGACY_FIELD_RESIDUE = "legacy_field_residue"
INTENT_OUTSIDE_TAXONOMY = "intent_outside_taxonomy"
EMPTY_SIGNAL_PAYLOAD = "empty_signal_payload"
EMPTY_UPDATE_PAYLOAD = "empty_update_payload"
CLOSE_FRACTION_OUT_OF_RANGE = "close_fraction_out_of_range"
STOP_LOSS_MISSING_ON_NEW_SETUP = "stop_loss_missing_on_new_setup"
TAKE_PROFITS_MISSING_ON_NEW_SETUP = "take_profits_missing_on_new_setup"
ENTRY_MISSING_ON_NEW_SETUP = "entry_missing_on_new_setup"

ALL_WARNING_CODES: tuple[WarningCode, ...] = (
    MISSING_TARGET,
    CONFLICTING_INTENTS,
    PARTIAL_SIGNAL,
    UNCLASSIFIED_WITH_MARKERS,
    UNKNOWN_INTENT_DETECTED,
    AMBIGUOUS_UPDATE_WITHOUT_TARGET,
    MULTIPLE_PRIMARY_INTENT_CANDIDATES,
    LEGACY_FIELD_RESIDUE,
    INTENT_OUTSIDE_TAXONOMY,
    EMPTY_SIGNAL_PAYLOAD,
    EMPTY_UPDATE_PAYLOAD,
    CLOSE_FRACTION_OUT_OF_RANGE,
    STOP_LOSS_MISSING_ON_NEW_SETUP,
    TAKE_PROFITS_MISSING_ON_NEW_SETUP,
    ENTRY_MISSING_ON_NEW_SETUP,
)
