"""Shared envelope builder: constructs TraderEventEnvelopeV1 from structured inputs.

Centralizes:
- Intent normalization (legacy -> official)
- primary_intent_hint selection via intent taxonomy
- Common warning generation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.parser.event_envelope_v1 import (
    InstrumentRaw,
    ReportPayloadRaw,
    SignalPayloadRaw,
    TargetRefRaw,
    TraderEventEnvelopeV1,
    UpdatePayloadRaw,
)
from src.parser.trader_profiles.shared.intent_taxonomy import (
    MUTUAL_EXCLUSIONS,
    normalize_intents,
    resolve_alias,
    select_primary_intent,
)
from src.parser.trader_profiles.shared.warnings import (
    CONFLICTING_INTENTS,
    INTENT_OUTSIDE_TAXONOMY,
    MISSING_TARGET,
    UNCLASSIFIED_WITH_MARKERS,
)

logger = logging.getLogger(__name__)


@dataclass
class EnvelopeInputs:
    """Structured inputs for build_envelope().

    All fields are optional; sensible defaults are applied inside build_envelope().
    """

    message_type_hint: str | None = None
    intents_raw: list[str] = field(default_factory=list)
    instrument: InstrumentRaw | None = None
    signal_payload_raw: SignalPayloadRaw | None = None
    update_payload_raw: UpdatePayloadRaw | None = None
    report_payload_raw: ReportPayloadRaw | None = None
    targets_raw: list[TargetRefRaw] = field(default_factory=list)
    confidence: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


def build_envelope(inputs: EnvelopeInputs) -> TraderEventEnvelopeV1:
    """Build a TraderEventEnvelopeV1 from structured parser-side inputs.

    Steps:
    1. Normalize intent names (legacy aliases → official).
    2. Select primary_intent_hint by precedence.
    3. Add common warnings.
    4. Construct and return the envelope.
    """
    warnings: list[str] = []

    # 1. Normalize intents, handling unknown names gracefully
    intents_detected = _safe_normalize_intents(inputs.intents_raw, warnings)

    # 2. Select primary intent by precedence
    primary_intent_hint = select_primary_intent(intents_detected)

    # 3. Common warnings
    _check_missing_target(inputs, warnings)
    _check_conflicting_intents(intents_detected, warnings)
    _check_unclassified_with_markers(inputs, intents_detected, warnings)

    return TraderEventEnvelopeV1(
        message_type_hint=inputs.message_type_hint,
        intents_detected=intents_detected,
        primary_intent_hint=primary_intent_hint,
        instrument=inputs.instrument or InstrumentRaw(),
        signal_payload_raw=inputs.signal_payload_raw or SignalPayloadRaw(),
        update_payload_raw=inputs.update_payload_raw or UpdatePayloadRaw(),
        report_payload_raw=inputs.report_payload_raw or ReportPayloadRaw(),
        targets_raw=inputs.targets_raw,
        warnings=warnings,
        confidence=inputs.confidence,
        diagnostics=inputs.diagnostics,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _safe_normalize_intents(intents_raw: list[str], warnings: list[str]) -> list[str]:
    """Normalize intents to official names, adding INTENT_OUTSIDE_TAXONOMY for unknowns."""
    seen: dict[str, None] = {}
    has_unknown = False
    for raw in intents_raw:
        try:
            official = resolve_alias(raw)
            seen[official] = None
        except ValueError:
            logger.debug("Intent %r outside taxonomy — skipping", raw)
            has_unknown = True
    if has_unknown and INTENT_OUTSIDE_TAXONOMY not in warnings:
        warnings.append(INTENT_OUTSIDE_TAXONOMY)
    return list(seen.keys())


def _check_missing_target(inputs: EnvelopeInputs, warnings: list[str]) -> None:
    """Warn when an UPDATE message has no target refs."""
    if inputs.message_type_hint == "UPDATE" and not inputs.targets_raw:
        if MISSING_TARGET not in warnings:
            warnings.append(MISSING_TARGET)


def _check_conflicting_intents(intents: list[str], warnings: list[str]) -> None:
    """Warn when mutually exclusive intents are both present."""
    intent_set = set(intents)
    for intent, excluded in MUTUAL_EXCLUSIONS.items():
        if intent in intent_set and intent_set & excluded:
            if CONFLICTING_INTENTS not in warnings:
                warnings.append(CONFLICTING_INTENTS)
            break


def _check_unclassified_with_markers(
    inputs: EnvelopeInputs,
    intents: list[str],
    warnings: list[str],
) -> None:
    """Warn when message is UNCLASSIFIED but intents were detected."""
    if inputs.message_type_hint == "UNCLASSIFIED" and intents:
        if UNCLASSIFIED_WITH_MARKERS not in warnings:
            warnings.append(UNCLASSIFIED_WITH_MARKERS)
