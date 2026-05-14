from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context import RawContext, TargetHints
from .entities import EntryLeg, EntrySelector, Price, RiskHint, SignalFields, StopLoss, TakeProfit
from .enums import (
    CANONICAL_MESSAGE_SCHEMA_VERSION,
    CancelScopeHint,
    CloseScope,
    EntryStructure,
    IntentType,
    MessageClass,
    ModifyEntriesOperationKind,
    ModifyTargetsMode,
    ParseStatus,
    ReportEventType,
    SetStopTargetType,
    UpdateOperationType,
)


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignalPayload(SignalFields):
    pass


class SetStopOperation(CanonicalModel):
    target_type: SetStopTargetType
    price: Price | None = None
    tp_level: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_target_payload(self) -> SetStopOperation:
        if self.target_type == "PRICE" and self.price is None:
            raise ValueError("SET_STOP PRICE requires price")
        if self.target_type == "TP_LEVEL" and self.tp_level is None:
            raise ValueError("SET_STOP TP_LEVEL requires tp_level")
        if self.target_type == "ENTRY" and (self.price is not None or self.tp_level is not None):
            raise ValueError("SET_STOP ENTRY forbids price/tp_level")
        return self


class CloseOperation(CanonicalModel):
    close_scope: CloseScope
    fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    close_price: Price | None = None


class CancelPendingOperation(CanonicalModel):
    cancel_scope_hint: CancelScopeHint = "UNKNOWN"


class ModifyEntriesOperation(CanonicalModel):
    kind: ModifyEntriesOperationKind
    entries: list[EntryLeg] = Field(default_factory=list)
    entry_structure: EntryStructure | None = None
    entry_selector: EntrySelector | None = None


class ModifyTargetsOperation(CanonicalModel):
    mode: ModifyTargetsMode = "UNKNOWN"
    take_profits: list[TakeProfit] = Field(default_factory=list)
    target_tp_level: int | None = Field(default=None, ge=1)


class InvalidateSetupOperation(CanonicalModel):
    reason_text: str | None = None


class ActionItem(CanonicalModel):
    action_type: UpdateOperationType
    set_stop: SetStopOperation | None = None
    close: CloseOperation | None = None
    cancel_pending: CancelPendingOperation | None = None
    modify_entries: ModifyEntriesOperation | None = None
    modify_targets: ModifyTargetsOperation | None = None
    invalidate_setup: InvalidateSetupOperation | None = None
    source_intent: IntentType
    source_intent_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    raw_fragment: str | None = None

    @model_validator(mode="after")
    def _validate_payload_matches_type(self) -> ActionItem:
        expected_by_type = {
            "SET_STOP": "set_stop",
            "CLOSE": "close",
            "CANCEL_PENDING": "cancel_pending",
            "MODIFY_ENTRIES": "modify_entries",
            "MODIFY_TARGETS": "modify_targets",
            "INVALIDATE_SETUP": "invalidate_setup",
        }
        expected = expected_by_type[self.action_type]
        populated = [f for f in expected_by_type.values() if getattr(self, f) is not None]
        if populated != [expected]:
            raise ValueError(
                f"{self.action_type} requires only `{expected}` to be populated; got {populated}"
            )
        return self


class TargetActionGroup(CanonicalModel):
    targeting: TargetHints
    secondary_targeting: TargetHints | None = None
    actions: list[ActionItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_actions_non_empty(self) -> TargetActionGroup:
        if not self.actions:
            raise ValueError("TargetActionGroup requires non-empty actions")
        return self




class ReportEvent(CanonicalModel):
    event_type: ReportEventType
    level: int | None = Field(default=None, ge=1)
    price: Price | None = None
    source_intent: IntentType
    raw_fragment: str | None = None


class ReportResult(CanonicalModel):
    raw_fragment: str | None = None


class ReportPayload(CanonicalModel):
    events: list[ReportEvent] = Field(default_factory=list)
    result: ReportResult | None = None


class InfoPayload(CanonicalModel):
    raw_fragment: str | None = None



class CanonicalMessage(CanonicalModel):
    schema_version: str = CANONICAL_MESSAGE_SCHEMA_VERSION
    parser_profile: str
    primary_class: MessageClass
    parse_status: ParseStatus
    confidence: float = Field(ge=0.0, le=1.0)
    primary_intent: IntentType | None = None
    intents: list[IntentType] = Field(default_factory=list)
    signal: SignalPayload | None = None
    report: ReportPayload | None = None
    info: InfoPayload | None = None
    target_action_groups: list[TargetActionGroup] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    raw_context: RawContext

    @model_validator(mode="after")
    def _validate_primary_class_payloads(self) -> CanonicalMessage:
        has_update_work = bool(self.target_action_groups)

        if self.primary_class == "SIGNAL":
            if self.signal is None:
                raise ValueError("SIGNAL requires signal payload")
            if self.target_action_groups:
                raise ValueError("SIGNAL forbids target_action_groups")

        elif self.primary_class == "UPDATE":
            if self.signal is not None:
                raise ValueError("UPDATE forbids signal payload")
            if self.parse_status == "PARSED" and not has_update_work:
                raise ValueError("PARSED UPDATE requires at least one target_action_group")
            if (
                self.parse_status == "PARTIAL"
                and not has_update_work
                and "ambiguous_target_intent_binding" not in self.warnings
            ):
                raise ValueError(
                    "PARTIAL UPDATE without target_action_groups requires "
                    "ambiguous_target_intent_binding warning"
                )

        elif self.primary_class == "REPORT":
            if self.report is None:
                raise ValueError("REPORT requires report payload")
            if self.signal is not None:
                raise ValueError("REPORT forbids signal payload")
            if self.target_action_groups:
                raise ValueError("REPORT forbids target_action_groups")

        elif self.primary_class == "INFO":
            if (
                self.signal is not None
                or self.report is not None
                or self.target_action_groups
            ):
                raise ValueError("INFO forbids signal/report payloads and target_action_groups")

        return self


__all__ = [
    "CanonicalMessage",
    "SignalPayload",
    "ReportPayload",
    "InfoPayload",
    "ReportEvent",
    "ReportResult",
    "SetStopOperation",
    "CloseOperation",
    "CancelPendingOperation",
    "ModifyEntriesOperation",
    "ModifyTargetsOperation",
    "InvalidateSetupOperation",
    "ActionItem",
    "TargetActionGroup",
    "EntryLeg",
    "EntrySelector",
    "Price",
    "RiskHint",
    "StopLoss",
    "TakeProfit",
]
