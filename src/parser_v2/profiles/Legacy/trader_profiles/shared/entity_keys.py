"""Shared entity key vocabulary: defines the allowed keys for each raw payload block.

These constants act as a schema guard so profiles cannot invent ad-hoc top-level keys.
They mirror the field names in TraderEventEnvelopeV1 sub-shapes.
"""

from __future__ import annotations

INSTRUMENT_KEYS: tuple[str, ...] = (
    "symbol",
    "side",
    "market_type",
)

SIGNAL_KEYS: tuple[str, ...] = (
    "entry_structure",
    "entries",
    "stop_loss",
    "take_profits",
    "leverage_hint",
    "risk_hint",
    "invalidation_rule",
    "conditions",
    "raw_fragments",
)

UPDATE_KEYS: tuple[str, ...] = (
    "stop_update",
    "close_update",
    "cancel_update",
    "entry_update",
    "targets_update",
    "raw_fragments",
)

REPORT_KEYS: tuple[str, ...] = (
    "events",
    "reported_results",
    "notes",
    "summary_text_raw",
)

ALL_RAW_BLOCK_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(INSTRUMENT_KEYS + SIGNAL_KEYS + UPDATE_KEYS + REPORT_KEYS)
)
