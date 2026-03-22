"""Pydantic model for UPDATE parser output.

UpdateEntities is the structured result produced by the parser when it classifies
a message as message_type="UPDATE". All fields are optional because any single
update message activates only a subset of intents.

Intent-to-field mapping:
    U_MOVE_STOP           → new_sl_level
    U_MOVE_STOP_TO_BE     → (new_sl_level is None ≡ move to breakeven)
    U_CLOSE_FULL          → close_price (optional)
    U_CLOSE_PARTIAL       → close_pct
    U_CANCEL_PENDING      → (no entity fields)
    U_REENTER             → reenter_entries, reenter_entry_type
    U_ADD_ENTRY           → new_entry_price, new_entry_type
    U_MODIFY_ENTRY        → old_entry_price, modified_entry_price
    U_UPDATE_TAKE_PROFITS → old_take_profits, new_take_profits
    U_TP_HIT (context)    → tp_hit_number, reported_profit_r, reported_profit_pct
    U_SL_HIT (context)    → reported_profit_r, reported_profit_pct

Usage:
    from src.parser.models.update import UpdateEntities
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.parser.models.canonical import Price
from src.parser.models.new_signal import EntryLevel


class UpdateEntities(BaseModel):
    """All entities that can appear in an UPDATE message.

    All fields default to None / empty list. A profile sets only the fields
    relevant to the intents it detected; consumers inspect the intent list to
    know which fields are meaningful.
    """

    # --- U_MOVE_STOP / U_MOVE_STOP_TO_BE -----------------------------------

    new_sl_level: Price | None = None
    """New stop-loss price. None when the intent is move-to-breakeven."""

    # --- U_CLOSE_FULL -------------------------------------------------------

    close_price: Price | None = None
    """Price at which the position was closed, if reported."""

    # --- U_CLOSE_PARTIAL ----------------------------------------------------

    close_pct: float | None = None
    """Percentage of position to close (0–100)."""

    # --- U_REENTER ----------------------------------------------------------

    reenter_entries: list[EntryLevel] = []
    """New entry levels for re-entering the trade."""

    reenter_entry_type: Literal["MARKET", "LIMIT", "AVERAGING", "ZONE"] | None = None

    # --- U_ADD_ENTRY --------------------------------------------------------

    new_entry_price: Price | None = None
    """Price of the additional entry to add."""

    new_entry_type: Literal["MARKET", "LIMIT"] | None = None

    # --- U_MODIFY_ENTRY -----------------------------------------------------

    old_entry_price: Price | None = None
    """Existing entry price to be modified or removed."""

    modified_entry_price: Price | None = None
    """Replacement price for the modified entry. None = remove the entry."""

    # --- U_UPDATE_TAKE_PROFITS ----------------------------------------------

    old_take_profits: list[Price] | None = None
    """Previous take-profit levels being replaced. None = not reported."""

    new_take_profits: list[Price] = []
    """Replacement take-profit levels."""

    # --- Context / reporting fields (U_TP_HIT, U_SL_HIT) -------------------

    tp_hit_number: int | None = None
    """Index of the take-profit that was hit (1-based), if reported."""

    reported_profit_r: float | None = None
    """Reported profit or loss in R-multiples (positive = profit)."""

    reported_profit_pct: float | None = None
    """Reported profit or loss as a percentage (positive = profit)."""
