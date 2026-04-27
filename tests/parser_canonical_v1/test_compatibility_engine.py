from __future__ import annotations

from typing import Iterable

import pytest

from src.parser.canonical_v1.intent_taxonomy import IntentName
from src.parser.shared.compatibility_engine import (
    CompatibilityResult,
    evaluate_intent_compatibility,
)
from src.parser.shared.intent_compatibility_schema import IntentCompatibilityPair


def _pair(
    intents: Iterable[IntentName],
    *,
    relation: str,
    requires_resolution: bool,
    requires_context_validation: bool = False,
) -> IntentCompatibilityPair:
    return IntentCompatibilityPair(
        intents=list(intents),
        relation=relation,  # type: ignore[arg-type]
        requires_resolution=requires_resolution,
        requires_context_validation=requires_context_validation,
    )


PROPOSAL_PAIRS = [
    _pair(
        ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        relation="specific_vs_generic",
        requires_resolution=True,
    ),
    _pair(
        ["EXIT_BE", "CLOSE_FULL"],
        relation="specific_vs_generic",
        requires_resolution=True,
        requires_context_validation=True,
    ),
    _pair(
        ["TP_HIT", "REPORT_FINAL_RESULT"],
        relation="compatible",
        requires_resolution=False,
    ),
    _pair(
        ["SL_HIT", "CLOSE_FULL"],
        relation="exclusive",
        requires_resolution=True,
    ),
]


@pytest.mark.parametrize(
    ("detected", "expected_local", "expected_context", "expected_conflicts"),
    [
        (
            ["MOVE_STOP_TO_BE", "MOVE_STOP"],
            True,
            False,
            [("MOVE_STOP_TO_BE", "MOVE_STOP")],
        ),
        (
            ["TP_HIT", "REPORT_FINAL_RESULT"],
            False,
            False,
            [],
        ),
        (
            ["EXIT_BE", "CLOSE_FULL"],
            True,
            True,
            [("EXIT_BE", "CLOSE_FULL")],
        ),
        (
            ["SL_HIT", "CLOSE_FULL"],
            True,
            False,
            [("SL_HIT", "CLOSE_FULL")],
        ),
        (
            ["NEW_SETUP", "INFO_ONLY"],
            False,
            False,
            [],
        ),
        (
            ["MOVE_STOP_TO_BE", "MOVE_STOP", "INFO_ONLY"],
            True,
            False,
            [("MOVE_STOP_TO_BE", "MOVE_STOP")],
        ),
    ],
)
def test_evaluate_intent_compatibility(
    detected: list[IntentName],
    expected_local: bool,
    expected_context: bool,
    expected_conflicts: list[tuple[IntentName, IntentName]],
) -> None:
    result = evaluate_intent_compatibility(detected, PROPOSAL_PAIRS)

    assert isinstance(result, CompatibilityResult)
    assert result.requires_local_resolution is expected_local
    assert result.requires_context_validation is expected_context
    assert result.resolved is False
    assert [tuple(pair.intents) for pair in result.conflicting_pairs] == expected_conflicts

