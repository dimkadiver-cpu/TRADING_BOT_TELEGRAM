"""
Parser Event Envelope v1 - minimal parser-side contract proposal

Purpose:
- single uniform upstream contract for all trader-specific parsers
- minimal effective structure
- preserve domain-relevant information
- stay close to CanonicalMessage v1 to keep the adapter thin

This file is a proposal artifact under docs/, not production code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Shared literals
# ---------------------------------------------------------------------------

MessageTypeHint = Literal["NEW_SIGNAL", "UPDATE", "INFO_ONLY", "UNCLASSIFIED"]
Side = Literal["LONG", "SHORT"]
MarketType = Literal["SPOT", "FUTURES", "UNKNOWN"]

EntryType = Literal["MARKET", "LIMIT"]
EntryStructure = Literal["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"]
EntryRole = Literal["PRIMARY", "AVERAGING", "RANGE_LOW", "RANGE_HIGH", "REENTRY", "UNKNOWN"]

UpdateOperationType = Literal[
    "SET_STOP",
    "CLOSE",
    "CANCEL_PENDING",
    "MODIFY_ENTRIES",
    "MODIFY_TARGETS",
]
StopTargetType = Literal["PRICE", "ENTRY", "TP_LEVEL"]
ModifyEntriesMode = Literal["ADD", "REENTER", "UPDATE", "REMOVE", "REPLACE_ALL"]
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

TargetRefKind = Literal["REPLY", "TELEGRAM_LINK", "MESSAGE_ID", "EXPLICIT_ID", "SYMBOL", "UNKNOWN"]


class EnvelopeBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Core helper models
# ---------------------------------------------------------------------------

class PriceHint(EnvelopeBaseModel):
    value: float | None = None
    raw: str | None = None


class RiskHintRaw(EnvelopeBaseModel):
    value: float | None = None
    unit: RiskHintUnit = "UNKNOWN"
    raw: str | None = None


class TargetRefRaw(EnvelopeBaseModel):
    kind: TargetRefKind
    value: str | int | None = None


# ---------------------------------------------------------------------------
# Instrument
# ---------------------------------------------------------------------------

class InstrumentRaw(EnvelopeBaseModel):
    symbol: str | None = None
    side: Side | None = None
    market_type: MarketType | None = None


# ---------------------------------------------------------------------------
# SIGNAL raw payload
# ---------------------------------------------------------------------------

class EntryLegRaw(EnvelopeBaseModel):
    sequence: int = Field(ge=1)
    entry_type: EntryType | None = None
    price: float | None = None
    role: EntryRole = "UNKNOWN"
    size_hint: str | None = None
    is_optional: bool | None = None

    @model_validator(mode="after")
    def _validate_limit_leg(self) -> "EntryLegRaw":
        if self.entry_type == "LIMIT" and self.price is None:
            raise ValueError("LIMIT entry leg requires price")
        return self


class StopLossRaw(EnvelopeBaseModel):
    price: float | None = None
    raw: str | None = None


class TakeProfitRaw(EnvelopeBaseModel):
    sequence: int = Field(ge=1)
    price: float | None = None
    label: str | None = None
    close_fraction: float | None = Field(default=None, ge=0.0, le=1.0)


class SignalPayloadRaw(EnvelopeBaseModel):
    entry_structure: EntryStructure | None = None
    entries: list[EntryLegRaw] = Field(default_factory=list)
    stop_loss: StopLossRaw | None = None
    take_profits: list[TakeProfitRaw] = Field(default_factory=list)
    risk_hint: RiskHintRaw | None = None
    raw_fragments: dict[str, str | None] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# UPDATE raw payload
# ---------------------------------------------------------------------------

class StopTargetRaw(EnvelopeBaseModel):
    target_type: StopTargetType
    value: float | int | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> "StopTargetRaw":
        if self.target_type == "PRICE" and not isinstance(self.value, (int, float)):
            raise ValueError("PRICE target requires numeric value")
        if self.target_type == "TP_LEVEL" and not isinstance(self.value, int):
            raise ValueError("TP_LEVEL target requires integer value")
        return self


class CloseOperationRaw(EnvelopeBaseModel):
    close_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    close_price: float | None = None
    close_scope: str | None = None

    @model_validator(mode="after")
    def _validate_close(self) -> "CloseOperationRaw":
        if self.close_fraction is None and self.close_price is None and self.close_scope is None:
            raise ValueError("CLOSE requires at least one of close_fraction, close_price, close_scope")
        return self


class CancelPendingOperationRaw(EnvelopeBaseModel):
    cancel_scope: str | None = None


class ModifyEntriesOperationRaw(EnvelopeBaseModel):
    mode: ModifyEntriesMode
    entries: list[EntryLegRaw] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_entries(self) -> "ModifyEntriesOperationRaw":
        if not self.entries:
            raise ValueError("MODIFY_ENTRIES requires non-empty entries")
        return self


class ModifyTargetsOperationRaw(EnvelopeBaseModel):
    mode: ModifyTargetsMode
    take_profits: list[TakeProfitRaw] = Field(default_factory=list)
    target_tp_level: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_targets(self) -> "ModifyTargetsOperationRaw":
        if not self.take_profits:
            raise ValueError("MODIFY_TARGETS requires non-empty take_profits")
        return self


class UpdateOperationRaw(EnvelopeBaseModel):
    op_type: UpdateOperationType
    set_stop: StopTargetRaw | None = None
    close: CloseOperationRaw | None = None
    cancel_pending: CancelPendingOperationRaw | None = None
    modify_entries: ModifyEntriesOperationRaw | None = None
    modify_targets: ModifyTargetsOperationRaw | None = None
    source_intent: str | None = None

    @model_validator(mode="after")
    def _validate_op(self) -> "UpdateOperationRaw":
        expected_map = {
            "SET_STOP": "set_stop",
            "CLOSE": "close",
            "CANCEL_PENDING": "cancel_pending",
            "MODIFY_ENTRIES": "modify_entries",
            "MODIFY_TARGETS": "modify_targets",
        }
        expected = expected_map[self.op_type]
        populated = [
            name
            for name in expected_map.values()
            if getattr(self, name) is not None
        ]
        if populated != [expected]:
            raise ValueError(f"{self.op_type} requires only `{expected}`; got {populated}")
        return self


class UpdatePayloadRaw(EnvelopeBaseModel):
    operations: list[UpdateOperationRaw] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# REPORT raw payload
# ---------------------------------------------------------------------------

class ReportedResultRaw(EnvelopeBaseModel):
    value: float | None = None
    unit: ResultUnit = "UNKNOWN"
    text: str | None = None


class ReportEventRaw(EnvelopeBaseModel):
    event_type: ReportEventType
    level: int | None = Field(default=None, ge=1)
    price: float | None = None
    result: ReportedResultRaw | None = None


class ReportPayloadRaw(EnvelopeBaseModel):
    events: list[ReportEventRaw] = Field(default_factory=list)
    reported_result: ReportedResultRaw | None = None
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

class TraderEventEnvelopeV1(EnvelopeBaseModel):
    schema_version: str = "trader_event_envelope_v1"
    message_type_hint: MessageTypeHint | None = None
    intents_detected: list[str] = Field(default_factory=list)
    primary_intent_hint: str | None = None

    instrument: InstrumentRaw = Field(default_factory=InstrumentRaw)
    signal_payload_raw: SignalPayloadRaw = Field(default_factory=SignalPayloadRaw)
    update_payload_raw: UpdatePayloadRaw = Field(default_factory=UpdatePayloadRaw)
    report_payload_raw: ReportPayloadRaw = Field(default_factory=ReportPayloadRaw)
    targets_raw: list[TargetRefRaw] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "CancelPendingOperationRaw",
    "CloseOperationRaw",
    "EntryLegRaw",
    "EntryRole",
    "EntryStructure",
    "EntryType",
    "InstrumentRaw",
    "MessageTypeHint",
    "ModifyEntriesMode",
    "ModifyEntriesOperationRaw",
    "ModifyTargetsMode",
    "ModifyTargetsOperationRaw",
    "ReportEventRaw",
    "ReportEventType",
    "ReportedResultRaw",
    "ReportPayloadRaw",
    "ResultUnit",
    "RiskHintRaw",
    "RiskHintUnit",
    "SignalPayloadRaw",
    "Side",
    "StopLossRaw",
    "StopTargetRaw",
    "StopTargetType",
    "TakeProfitRaw",
    "TargetRefKind",
    "TargetRefRaw",
    "TraderEventEnvelopeV1",
    "UpdateOperationRaw",
    "UpdateOperationType",
    "UpdatePayloadRaw",
]
