"""
Canonical Parser Model v1 - Pydantic schema

This schema follows the final decisions agreed in chat, including:
- top-level `intents` and `primary_intent`
- canonical business payloads as source of truth
- `entry_type` only at entry-leg level
- 5 canonical update operations:
  SET_STOP, CLOSE, CANCEL_PENDING, MODIFY_ENTRIES, MODIFY_TARGETS
- composite messages allowed for UPDATE + REPORT
- SIGNAL + UPDATE forbidden
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict, model_validator


# -----------------------------------------------------------------------------
# Top-level literals
# -----------------------------------------------------------------------------

MessageClass = Literal["SIGNAL", "UPDATE", "REPORT", "INFO"]
ParseStatus = Literal["PARSED", "PARTIAL", "UNCLASSIFIED", "ERROR"]

AcquisitionMode = Literal["live", "catchup"]

Side = Literal["LONG", "SHORT"]
EntryType = Literal["MARKET", "LIMIT"]
EntryStructure = Literal["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"]

TargetingStrategy = Literal[
    "REPLY_OR_LINK",
    "SYMBOL_MATCH",
    "GLOBAL_SCOPE",
    "UNRESOLVED",
]

TargetScopeKind = Literal[
    "SINGLE_SIGNAL",
    "SYMBOL",
    "PORTFOLIO_SIDE",
    "ALL_OPEN",
    "UNKNOWN",
]

TargetRefType = Literal[
    "REPLY",
    "TELEGRAM_LINK",
    "MESSAGE_ID",
    "EXPLICIT_ID",
    "SYMBOL",
]

UpdateOperationType = Literal[
    "SET_STOP",
    "CLOSE",
    "CANCEL_PENDING",
    "MODIFY_ENTRIES",
    "MODIFY_TARGETS",
]

StopTargetType = Literal["PRICE", "ENTRY", "TP_LEVEL"]
ModifyEntriesMode = Literal["ADD", "REENTER", "UPDATE"]
ModifyTargetsMode = Literal["REPLACE_ALL", "ADD", "UPDATE_ONE", "REMOVE_ONE"]

ReportEventType = Literal[
    "ENTRY_FILLED",
    "TP_HIT",
    "STOP_HIT",
    "BREAKEVEN_EXIT",
    "FINAL_RESULT",
]

ResultUnit = Literal["R", "PERCENT", "TEXT", "UNKNOWN"]
RiskHintUnit = Literal["PERCENT", "ABSOLUTE", "UNKNOWN"]


# -----------------------------------------------------------------------------
# Price normalisation
# -----------------------------------------------------------------------------

def normalize_price(
    raw: str,
    *,
    decimal_separator: str = ".",
    thousands_separator: str | None = None,
) -> float:
    """Normalise a raw price string extracted from a message to a Python float.

    Handles common formats used by Telegram traders:
        "90 000.5"  → 90000.5
        "90,000.5"  → 90000.5
        "90.000,5"  → 90000.5  (decimal_separator=",")
        "0.1772"    → 0.1772
        "1 234,56"  → 1234.56  (decimal_separator=",")
    """
    if not raw or not raw.strip():
        raise ValueError(f"Cannot normalise empty price string: {raw!r}")

    s = raw.strip()

    if thousands_separator is not None:
        s = s.replace(thousands_separator, "")

    # Remove spaces (RU/FR thousands grouping: "90 000")
    s = s.replace(" ", "")

    if decimal_separator == ",":
        s = s.replace(".", "")   # period = thousands grouping
        s = s.replace(",", ".")  # comma → period
    else:
        s = s.replace(",", "")   # comma = thousands grouping

    try:
        return float(s)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse {raw!r} as float after normalisation (result: {s!r})"
        ) from exc


# -----------------------------------------------------------------------------
# Base helpers
# -----------------------------------------------------------------------------

class CanonicalBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Price(CanonicalBaseModel):
    raw: str
    value: float

    @classmethod
    def from_raw(
        cls,
        raw: str,
        *,
        decimal_separator: str = ".",
        thousands_separator: str | None = None,
    ) -> Price:
        value = normalize_price(
            raw,
            decimal_separator=decimal_separator,
            thousands_separator=thousands_separator,
        )
        return cls(raw=raw, value=value)

    @classmethod
    def from_float(cls, value: float) -> Price:
        return cls(raw=str(value), value=value)


# -----------------------------------------------------------------------------
# Raw context
# -----------------------------------------------------------------------------

class RawContext(CanonicalBaseModel):
    raw_text: str
    reply_to_message_id: int | None = None
    extracted_links: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    source_chat_id: str | None = None
    source_topic_id: int | None = None
    acquisition_mode: AcquisitionMode | None = None


# -----------------------------------------------------------------------------
# Targeting
# -----------------------------------------------------------------------------

class TargetRef(CanonicalBaseModel):
    ref_type: TargetRefType
    value: str | int


class TargetScope(CanonicalBaseModel):
    kind: TargetScopeKind
    value: str | None = None
    side_filter: Side | None = None
    applies_to_all: bool = False


class Targeting(CanonicalBaseModel):
    refs: list[TargetRef] = Field(default_factory=list)
    scope: TargetScope
    strategy: TargetingStrategy
    targeted: bool = False


# -----------------------------------------------------------------------------
# Signal payload
# -----------------------------------------------------------------------------

class EntryLeg(CanonicalBaseModel):
    sequence: int = Field(ge=1)
    entry_type: EntryType
    price: Price | None = None
    role: Literal["PRIMARY", "AVERAGING", "UNKNOWN"] = "UNKNOWN"
    size_hint: str | None = None
    note: str | None = None
    is_optional: bool = False

    @model_validator(mode="after")
    def _validate_entry_leg(self) -> "EntryLeg":
        if self.entry_type == "LIMIT" and self.price is None:
            raise ValueError("LIMIT entry leg requires price")
        return self


class StopLoss(CanonicalBaseModel):
    price: Price | None = None


class TakeProfit(CanonicalBaseModel):
    sequence: int = Field(ge=1)
    price: Price
    label: str | None = None
    close_fraction: float | None = Field(default=None, ge=0.0, le=1.0)


class RiskHint(CanonicalBaseModel):
    raw: str | None = None
    value: float | None = None
    unit: RiskHintUnit = "UNKNOWN"


class SignalPayload(CanonicalBaseModel):
    symbol: str | None = None
    side: Side | None = None

    entry_structure: EntryStructure | None = None
    entries: list[EntryLeg] = Field(default_factory=list)

    stop_loss: StopLoss | None = None
    take_profits: list[TakeProfit] = Field(default_factory=list)

    leverage_hint: float | None = None
    risk_hint: RiskHint | None = None

    invalidation_rule: str | None = None
    conditions: str | None = None

    completeness: Literal["COMPLETE", "INCOMPLETE"] | None = None
    missing_fields: list[str] = Field(default_factory=list)

    raw_fragments: dict[str, str | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_signal_payload(self) -> "SignalPayload":
        if self.entry_structure == "ONE_SHOT":
            if len(self.entries) != 1:
                raise ValueError("ONE_SHOT requires exactly 1 entry leg")
        elif self.entry_structure == "TWO_STEP":
            if len(self.entries) != 2:
                raise ValueError("TWO_STEP requires exactly 2 entry legs")
        elif self.entry_structure == "RANGE":
            if len(self.entries) != 2:
                raise ValueError("RANGE requires exactly 2 entry legs")
        elif self.entry_structure == "LADDER":
            if len(self.entries) < 3:
                raise ValueError("LADDER requires at least 3 entry legs")

        return self


# -----------------------------------------------------------------------------
# Update payload
# -----------------------------------------------------------------------------

class StopTarget(CanonicalBaseModel):
    target_type: StopTargetType
    value: float | int | None = None

    @model_validator(mode="after")
    def _validate_stop_target(self) -> "StopTarget":
        if self.target_type == "PRICE" and not isinstance(self.value, (int, float)):
            raise ValueError("PRICE stop target requires numeric value")
        if self.target_type == "TP_LEVEL" and not isinstance(self.value, int):
            raise ValueError("TP_LEVEL stop target requires integer level")
        return self


class CloseOperation(CanonicalBaseModel):
    close_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    close_price: Price | None = None
    close_scope: str | None = None

    @model_validator(mode="after")
    def _validate_close_op(self) -> "CloseOperation":
        if self.close_fraction is None and self.close_price is None and self.close_scope is None:
            raise ValueError(
                "CLOSE requires at least one of close_fraction, close_price, close_scope"
            )
        return self


class CancelPendingOperation(CanonicalBaseModel):
    cancel_scope: str | None = None


class ModifyEntriesOperation(CanonicalBaseModel):
    mode: ModifyEntriesMode
    entries: list[EntryLeg] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_modify_entries(self) -> "ModifyEntriesOperation":
        if not self.entries:
            raise ValueError("MODIFY_ENTRIES requires non-empty entries")
        return self


class ModifyTargetsOperation(CanonicalBaseModel):
    mode: ModifyTargetsMode
    take_profits: list[TakeProfit] = Field(default_factory=list)
    target_tp_level: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_modify_targets(self) -> "ModifyTargetsOperation":
        if not self.take_profits:
            raise ValueError("MODIFY_TARGETS requires non-empty take_profits")
        return self


class UpdateOperation(CanonicalBaseModel):
    op_type: UpdateOperationType

    set_stop: StopTarget | None = None
    close: CloseOperation | None = None
    cancel_pending: CancelPendingOperation | None = None
    modify_entries: ModifyEntriesOperation | None = None
    modify_targets: ModifyTargetsOperation | None = None

    raw_fragment: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_update_operation(self) -> "UpdateOperation":
        expected_map = {
            "SET_STOP": "set_stop",
            "CLOSE": "close",
            "CANCEL_PENDING": "cancel_pending",
            "MODIFY_ENTRIES": "modify_entries",
            "MODIFY_TARGETS": "modify_targets",
        }
        expected = expected_map[self.op_type]
        populated = [
            name for name in expected_map.values()
            if getattr(self, name) is not None
        ]
        if populated != [expected]:
            raise ValueError(
                f"{self.op_type} requires only `{expected}` to be populated; got {populated}"
            )
        return self


class UpdatePayload(CanonicalBaseModel):
    operations: list[UpdateOperation] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Report payload
# -----------------------------------------------------------------------------

class ReportedResult(CanonicalBaseModel):
    value: float | None = None
    unit: ResultUnit = "UNKNOWN"
    text: str | None = None


class ReportEvent(CanonicalBaseModel):
    event_type: ReportEventType
    level: int | None = Field(default=None, ge=1)
    price: Price | None = None
    result: ReportedResult | None = None
    raw_fragment: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ReportPayload(CanonicalBaseModel):
    events: list[ReportEvent] = Field(default_factory=list)
    reported_result: ReportedResult | None = None
    notes: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Top-level canonical message
# -----------------------------------------------------------------------------

class CanonicalMessage(CanonicalBaseModel):
    schema_version: str = "1.0"
    parser_profile: str

    primary_class: MessageClass
    parse_status: ParseStatus
    confidence: float = Field(ge=0.0, le=1.0)

    intents: list[str] = Field(default_factory=list)
    primary_intent: str | None = None

    targeting: Targeting | None = None

    signal: SignalPayload | None = None
    update: UpdatePayload | None = None
    report: ReportPayload | None = None

    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    raw_context: RawContext

    @model_validator(mode="after")
    def _validate_top_level(self) -> "CanonicalMessage":
        if self.primary_class == "SIGNAL":
            if self.signal is None:
                raise ValueError("primary_class=SIGNAL requires signal payload")
            if self.update is not None:
                raise ValueError("primary_class=SIGNAL forbids update payload")

        elif self.primary_class == "UPDATE":
            if self.update is None:
                raise ValueError("primary_class=UPDATE requires update payload")
            if self.signal is not None:
                raise ValueError("primary_class=UPDATE forbids signal payload")

        elif self.primary_class == "REPORT":
            if self.report is None:
                raise ValueError("primary_class=REPORT requires report payload")
            if self.signal is not None or self.update is not None:
                raise ValueError("primary_class=REPORT forbids signal/update payloads")

        elif self.primary_class == "INFO":
            if self.signal is not None or self.update is not None or self.report is not None:
                raise ValueError("primary_class=INFO requires signal/update/report to be absent")

        if self.primary_class != "INFO":
            if self.signal is None and self.update is None and self.report is None:
                raise ValueError(
                    "At least one business payload among signal/update/report must be present"
                )

        if self.parse_status == "PARSED":
            if self.primary_class == "SIGNAL":
                assert self.signal is not None
                if not self.signal.symbol:
                    raise ValueError("PARSED SIGNAL requires signal.symbol")
                if self.signal.side is None:
                    raise ValueError("PARSED SIGNAL requires signal.side")
                if self.signal.entry_structure is None:
                    raise ValueError("PARSED SIGNAL requires signal.entry_structure")
                if self.signal.stop_loss is None:
                    raise ValueError("PARSED SIGNAL requires signal.stop_loss")
                if not self.signal.take_profits:
                    raise ValueError("PARSED SIGNAL requires at least one take_profit")
                if self.signal.entry_structure == "ONE_SHOT" and len(self.signal.entries) != 1:
                    raise ValueError("PARSED SIGNAL ONE_SHOT requires exactly 1 entry leg")
                if self.signal.entry_structure == "TWO_STEP" and len(self.signal.entries) != 2:
                    raise ValueError("PARSED SIGNAL TWO_STEP requires exactly 2 entry legs")
                if self.signal.entry_structure == "RANGE" and len(self.signal.entries) != 2:
                    raise ValueError("PARSED SIGNAL RANGE requires exactly 2 entry legs")
                if self.signal.entry_structure == "LADDER" and len(self.signal.entries) < 3:
                    raise ValueError("PARSED SIGNAL LADDER requires at least 3 entry legs")

            elif self.primary_class == "UPDATE":
                assert self.update is not None
                if not self.update.operations:
                    raise ValueError("PARSED UPDATE requires at least one operation")

            elif self.primary_class == "REPORT":
                assert self.report is not None
                if not self.report.events and self.report.reported_result is None:
                    raise ValueError("PARSED REPORT requires at least one event or reported_result")

        return self


__all__ = [
    "AcquisitionMode",
    "CancelPendingOperation",
    "CanonicalBaseModel",
    "CanonicalMessage",
    "CloseOperation",
    "EntryLeg",
    "EntryStructure",
    "EntryType",
    "MessageClass",
    "ModifyEntriesMode",
    "ModifyEntriesOperation",
    "ModifyTargetsMode",
    "ModifyTargetsOperation",
    "ParseStatus",
    "Price",
    "RawContext",
    "ReportEvent",
    "ReportEventType",
    "ReportedResult",
    "ReportPayload",
    "ResultUnit",
    "RiskHint",
    "RiskHintUnit",
    "Side",
    "SignalPayload",
    "StopLoss",
    "StopTarget",
    "StopTargetType",
    "TakeProfit",
    "TargetRef",
    "TargetRefType",
    "TargetScope",
    "TargetScopeKind",
    "Targeting",
    "TargetingStrategy",
    "UpdateOperation",
    "UpdateOperationType",
    "UpdatePayload",
    "normalize_price",
]
