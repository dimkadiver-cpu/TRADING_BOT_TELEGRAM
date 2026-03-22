"""Canonical Pydantic models for the parser output.

This is the authoritative contract between the parser layer and all downstream
consumers (validation, operation rules, execution, backtesting).

Usage:
    from src.parser.models.canonical import Price, Intent, TargetRef, TraderParseResult
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


# ---------------------------------------------------------------------------
# Price normalisation
# ---------------------------------------------------------------------------

def normalize_price(
    raw: str,
    *,
    decimal_separator: str = ".",
    thousands_separator: str | None = None,
) -> float:
    """Normalise a raw price string extracted from a message to a Python float.

    Handles common formats used by Telegram traders:

    +-------------------+----------------+----------------+-----------+
    | raw               | decimal_sep    | thousands_sep  | result    |
    +-------------------+----------------+----------------+-----------+
    | "90 000.5"        | "."            | " "            | 90000.5   |
    | "90,000.5"        | "."            | ","            | 90000.5   |
    | "90.000,5"        | ","            | "."            | 90000.5   |
    | "0.1772"          | "."            | None           | 0.1772    |
    | "0,1772"          | ","            | None           | 0.1772    |
    | "1 234,56"        | ","            | " "            | 1234.56   |
    +-------------------+----------------+----------------+-----------+

    Algorithm:
        1. Strip surrounding whitespace.
        2. Remove the explicit thousands_separator (if provided).
        3. Remove all remaining space characters (common RU/FR thousands grouping).
        4. If decimal_separator is ",": remove remaining "." (period = thousands),
           then replace "," with ".".
        5. If decimal_separator is ".": remove "," (comma = thousands).
        6. Parse as float.

    Args:
        raw: The raw string value extracted from the message.
        decimal_separator: Character that separates integer and fractional parts.
            Either "." (default) or ",".
        thousands_separator: Character used for thousands grouping. When None the
            algorithm infers it from decimal_separator (the opposite convention).

    Returns:
        The normalised float value.

    Raises:
        ValueError: If *raw* is empty or cannot be parsed as a number.
    """
    if not raw or not raw.strip():
        raise ValueError(f"Cannot normalise empty price string: {raw!r}")

    s = raw.strip()

    # Step 1: remove explicit thousands separator
    if thousands_separator is not None:
        s = s.replace(thousands_separator, "")

    # Step 2: always remove spaces (RU/FR thousands grouping: "90 000")
    s = s.replace(" ", "")

    # Step 3: reconcile decimal separator
    if decimal_separator == ",":
        # European format — period is thousands grouping, comma is decimal
        s = s.replace(".", "")   # remove period (thousands, if any remain)
        s = s.replace(",", ".")  # comma → period (Python float notation)
    else:
        # Standard format — period is decimal, comma is thousands grouping
        s = s.replace(",", "")

    try:
        return float(s)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse {raw!r} as float after normalisation (result: {s!r})"
        ) from exc


# ---------------------------------------------------------------------------
# Price model
# ---------------------------------------------------------------------------

class Price(BaseModel):
    """A normalised price that always preserves the original raw string for audit.

    Use Price.from_raw() to construct from a raw string extracted from a message.
    Use Price.from_float() when the value is already a Python float.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    """Original string as extracted from the message — never modified."""

    value: float
    """Normalised float value ready for use in calculations."""

    @classmethod
    def from_raw(
        cls,
        raw: str,
        *,
        decimal_separator: str = ".",
        thousands_separator: str | None = None,
    ) -> Price:
        """Construct a Price by normalising a raw string.

        Args:
            raw: The raw string extracted from the message.
            decimal_separator: "." (default) or ",".
            thousands_separator: Thousands grouping character, or None to infer.

        Returns:
            Price with raw preserved and value normalised.
        """
        value = normalize_price(
            raw,
            decimal_separator=decimal_separator,
            thousands_separator=thousands_separator,
        )
        return cls(raw=raw, value=value)

    @classmethod
    def from_float(cls, value: float) -> Price:
        """Construct a Price from an already-normalised float.

        The raw field is set to the standard string representation of the float.
        """
        return cls(raw=str(value), value=value)


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class Intent(BaseModel):
    """A parser intent with its semantic kind.

    CONTEXT intents describe *what happened* (informational):
        U_TP_HIT, U_SL_HIT

    ACTION intents describe *what to do* (operational):
        U_MOVE_STOP, U_CLOSE_FULL, U_CLOSE_PARTIAL, U_CANCEL_PENDING,
        U_REENTER, U_ADD_ENTRY, U_MODIFY_ENTRY, U_UPDATE_TAKE_PROFITS
    """

    model_config = ConfigDict(frozen=True)

    name: str
    """Intent identifier, e.g. "U_MOVE_STOP", "U_TP_HIT"."""

    kind: Literal["CONTEXT", "ACTION"]
    """Whether this intent is purely informational or requires an action."""


# ---------------------------------------------------------------------------
# TargetRef
# ---------------------------------------------------------------------------

class TargetRef(BaseModel):
    """Reference to the trade/position/order this message targets.

    Three resolution strategies:
        STRONG  — exact reference via reply, Telegram link, or explicit ID.
                  method must be set.
        SYMBOL  — look up open positions for this symbol (trader-scoped).
                  symbol must be set.
        GLOBAL  — applies to a scope of positions (e.g. "all_long").
                  scope must be set.
    """

    kind: Literal["STRONG", "SYMBOL", "GLOBAL"]
    method: Literal["REPLY", "TELEGRAM_LINK", "EXPLICIT_ID"] | None = None
    ref: int | str | None = None
    symbol: str | None = None
    scope: str | None = None

    @model_validator(mode="after")
    def _validate_kind_consistency(self) -> TargetRef:
        if self.kind == "STRONG" and self.method is None:
            raise ValueError("TargetRef(kind=STRONG) requires method to be set")
        if self.kind == "SYMBOL" and self.symbol is None:
            raise ValueError("TargetRef(kind=SYMBOL) requires symbol to be set")
        if self.kind == "GLOBAL" and self.scope is None:
            raise ValueError("TargetRef(kind=GLOBAL) requires scope to be set")
        return self


# ---------------------------------------------------------------------------
# TraderParseResult
# ---------------------------------------------------------------------------

class TraderParseResult(BaseModel):
    """Canonical output produced by the parser pipeline for every message.

    The *entities* field holds either NewSignalEntities (for NEW_SIGNAL messages)
    or UpdateEntities (for UPDATE messages). It is typed as Any here to avoid a
    circular import; the actual type is enforced by the profile that constructs
    this object.

    completeness is only meaningful for NEW_SIGNAL messages:
        COMPLETE   — all required fields are present (symbol, direction,
                     entry_type, stop_loss, at least one take_profit).
        INCOMPLETE — one or more required fields are missing; see missing_fields.
    """

    message_type: Literal["NEW_SIGNAL", "UPDATE", "INFO_ONLY", "UNCLASSIFIED"]
    completeness: Literal["COMPLETE", "INCOMPLETE"] | None = None
    missing_fields: list[str] = []
    entities: Any = None
    intents: list[Intent] = []
    target_ref: TargetRef | None = None
    confidence: float = 0.0
    warnings: list[str] = []
    trader_id: str
    raw_text: str
    acquisition_mode: Literal["live", "catchup"] = "live"

    @model_validator(mode="after")
    def _validate_completeness_for_new_signal(self) -> TraderParseResult:
        if self.message_type == "NEW_SIGNAL" and self.completeness is None:
            raise ValueError(
                "completeness must be set when message_type is NEW_SIGNAL"
            )
        if self.message_type != "NEW_SIGNAL" and self.completeness is not None:
            raise ValueError(
                "completeness should only be set for NEW_SIGNAL messages; "
                f"got message_type={self.message_type!r}"
            )
        return self
