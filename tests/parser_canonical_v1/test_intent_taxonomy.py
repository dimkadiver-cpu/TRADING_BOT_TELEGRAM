from __future__ import annotations

import pytest

from src.parser.canonical_v1.intent_taxonomy import (
    INTENT_NAMES,
    STATEFUL_INTENTS,
    STRONGLY_STATEFUL,
    validate_intent_name,
)


# --- completeness ---

EXPECTED_INTENTS = {
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
}


def test_intent_names_count() -> None:
    assert len(INTENT_NAMES) == 17


def test_all_expected_intents_present() -> None:
    assert INTENT_NAMES == EXPECTED_INTENTS


def test_no_typos_in_intent_names() -> None:
    for name in INTENT_NAMES:
        assert name.isupper(), f"{name!r} should be all-uppercase"
        assert " " not in name, f"{name!r} should have no spaces"


# --- STATEFUL_INTENTS ---

def test_stateful_intents_members() -> None:
    assert STATEFUL_INTENTS == {"EXIT_BE", "TP_HIT", "SL_HIT", "CLOSE_FULL", "CLOSE_PARTIAL"}


def test_stateful_intents_are_subset_of_official() -> None:
    assert STATEFUL_INTENTS <= INTENT_NAMES


# --- STRONGLY_STATEFUL ---

def test_strongly_stateful_is_only_exit_be() -> None:
    assert STRONGLY_STATEFUL == {"EXIT_BE"}


def test_strongly_stateful_subset_of_stateful() -> None:
    assert STRONGLY_STATEFUL <= STATEFUL_INTENTS


# --- validate_intent_name ---

@pytest.mark.parametrize("name", list(EXPECTED_INTENTS))
def test_validate_intent_name_accepts_valid(name: str) -> None:
    result = validate_intent_name(name)
    assert result == name


@pytest.mark.parametrize("bad", ["", "new_setup", "UNKNOWN_INTENT", "CLOSE FULL", "EXIT_BE "])
def test_validate_intent_name_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="not a valid intent"):
        validate_intent_name(bad)
