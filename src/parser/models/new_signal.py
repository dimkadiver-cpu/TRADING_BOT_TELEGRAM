"""Pydantic models for NEW_SIGNAL parser output.

NewSignalEntities is the structured result produced by the parser when it
classifies a message as message_type="NEW_SIGNAL".

Usage:
    from src.parser.models.new_signal import (
        EntryLevel,
        StopLoss,
        TakeProfit,
        NewSignalEntities,
        compute_completeness,
    )
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from src.parser.models.canonical import Price


# ---------------------------------------------------------------------------
# Component models
# ---------------------------------------------------------------------------

class EntryLevel(BaseModel):
    """A single entry price point with order type.

    price is None for MARKET entries that have no fixed target price (e.g. "enter
    at market"). For LIMIT entries price is always set.
    """

    price: Price | None = None
    order_type: Literal["MARKET", "LIMIT"]
    note: str | None = None
    """Free-text note from the original message, e.g. "from current price"."""


class StopLoss(BaseModel):
    """A stop-loss level.

    Thin wrapper around Price that allows future extension (trailing stops,
    conditional stops, etc.) without changing the NewSignalEntities interface.
    """

    model_config = ConfigDict(frozen=True)

    price: Price
    trailing: bool = False
    condition: str | None = None
    """Free-text stop condition extracted from the message, if any."""


class TakeProfit(BaseModel):
    """A take-profit level with optional label and partial-close percentage.

    label is the TP identifier as it appeared in the message (e.g. "TP1", "TP2").
    close_pct is the percentage of the position to close at this TP, if specified.
    """

    model_config = ConfigDict(frozen=True)

    price: Price
    label: str | None = None
    close_pct: float | None = None


# ---------------------------------------------------------------------------
# NewSignalEntities
# ---------------------------------------------------------------------------

class NewSignalEntities(BaseModel):
    """All entities extracted from a NEW_SIGNAL message.

    Required fields for completeness=COMPLETE:
        symbol, direction, entry_type, stop_loss, take_profits (≥ 1)
        entries is required when entry_type is LIMIT, AVERAGING, or ZONE.

    All fields default to None / empty list so that INCOMPLETE signals can be
    represented and stored for later review.
    """

    # --- Core signal fields -------------------------------------------------

    symbol: str | None = None
    """Normalised trading pair symbol, e.g. "BTCUSDT". Always uppercase."""

    direction: Literal["LONG", "SHORT"] | None = None

    entry_type: Literal["MARKET", "LIMIT", "AVERAGING", "ZONE"] | None = None
    """
    MARKET    — market entry; entries may be empty or hold a indicative price.
    LIMIT     — single exact limit entry; entries has exactly 1 price.
    AVERAGING — multiple discrete limit entries; entries has ≥ 2 prices.
    ZONE      — entry zone defined by [min_price, max_price]; entries has 2 prices.
    """

    entries: list[EntryLevel] = []
    """Entry levels. Required (non-empty) for LIMIT, AVERAGING, ZONE entry types."""

    stop_loss: StopLoss | None = None
    """Stop-loss level. Required for completeness=COMPLETE."""

    take_profits: list[TakeProfit] = []
    """Take-profit levels. At least one required for completeness=COMPLETE."""

    # --- Optional fields ----------------------------------------------------

    leverage: float | None = None
    risk_pct: float | None = None
    conditions: str | None = None
    """Free-text entry conditions not otherwise parsed, e.g. "wait for confirmation"."""

    warnings: list[str] = []
    """Warnings produced during validation. Caller should merge into TraderParseResult.warnings."""

    @field_validator("symbol", mode="before")
    @classmethod
    def _normalise_symbol(cls, v: str | None) -> str | None:
        return v.upper().strip() if v is not None else None

    @model_validator(mode="after")
    def check_entry_magnitude_consistency(self) -> Self:
        """Se ci sono 2+ entries, il rapporto max/min non deve superare 3x.

        Non blocca il parsing. Aggiunge un warning alla lista warnings del modello.
        Solo entries — non tocca TP e SL.
        """
        if len(self.entries) < 2:
            return self
        prices = [e.price.value for e in self.entries if e.price is not None]
        if len(prices) < 2:
            return self
        ratio = max(prices) / min(prices)
        if ratio > 3.0:
            self.warnings.append(
                f"entry_magnitude_inconsistent: ratio={ratio:.1f}"
            )
        return self


# ---------------------------------------------------------------------------
# Completeness helper
# ---------------------------------------------------------------------------

def compute_completeness(
    entities: NewSignalEntities,
) -> tuple[Literal["COMPLETE", "INCOMPLETE"], list[str]]:
    """Determine the completeness of a NewSignalEntities instance.

    Returns:
        A 2-tuple of (completeness, missing_fields) where completeness is
        "COMPLETE" or "INCOMPLETE" and missing_fields lists the names of the
        required fields that are absent.
    """
    missing: list[str] = []

    if entities.symbol is None:
        missing.append("symbol")
    if entities.direction is None:
        missing.append("direction")
    if entities.entry_type is None:
        missing.append("entry_type")
    elif entities.entry_type in {"LIMIT", "AVERAGING", "ZONE"} and not entities.entries:
        missing.append("entries")
    if entities.stop_loss is None:
        missing.append("stop_loss")
    if not entities.take_profits:
        missing.append("take_profits")

    completeness: Literal["COMPLETE", "INCOMPLETE"] = (
        "COMPLETE" if not missing else "INCOMPLETE"
    )
    return completeness, missing
