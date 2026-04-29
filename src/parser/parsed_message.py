from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from src.parser.canonical_v1.models import (
    CancelScope,
    CanonicalBaseModel,
    EntryStructure,
    EntryType,
    MessageClass,
    ModifyTargetsMode,
    ParseStatus,
    Price,
    RawContext,
    ReportedResult,
    SignalPayload,
    Targeting,
)
from src.parser.intent_types import IntentCategory, IntentType


class ParsedBaseModel(CanonicalBaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentEntities(ParsedBaseModel):
    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class MoveStopToBEEntities(IntentEntities):
    pass


class MoveStopEntities(IntentEntities):
    new_stop_price: Price | None = None
    stop_to_tp_level: int | None = None


class CloseFullEntities(IntentEntities):
    close_price: Price | None = None


class ClosePartialEntities(IntentEntities):
    fraction: float | None = None
    close_price: Price | None = None


class CancelPendingEntities(IntentEntities):
    scope: CancelScope | None = None


class InvalidateSetupEntities(IntentEntities):
    pass


class ReenterEntities(IntentEntities):
    entries: list[Price] = Field(default_factory=list)
    entry_type: EntryType | None = None
    entry_structure: EntryStructure | None = None


class AddEntryEntities(IntentEntities):
    entry_price: Price
    entry_type: EntryType | None = None


class UpdateTakeProfitsEntities(IntentEntities):
    new_take_profits: list[Price] = Field(default_factory=list)
    target_tp_level: int | None = None
    mode: ModifyTargetsMode | None = None


class EntryFilledEntities(IntentEntities):
    fill_price: Price | None = None
    average_price: Price | None = None
    level: int | None = None


class TpHitEntities(IntentEntities):
    level: int | None = None
    price: Price | None = None
    result: ReportedResult | None = None


class SlHitEntities(IntentEntities):
    price: Price | None = None
    result: ReportedResult | None = None


class ExitBeEntities(IntentEntities):
    price: Price | None = None


class ReportPartialResultEntities(IntentEntities):
    result: ReportedResult | None = None


class ReportFinalResultEntities(IntentEntities):
    result: ReportedResult | None = None


class InfoOnlyEntities(IntentEntities):
    pass


IntentEntityPayload = (
    MoveStopToBEEntities
    | MoveStopEntities
    | CloseFullEntities
    | ClosePartialEntities
    | CancelPendingEntities
    | InvalidateSetupEntities
    | ReenterEntities
    | AddEntryEntities
    | UpdateTakeProfitsEntities
    | EntryFilledEntities
    | TpHitEntities
    | SlHitEntities
    | ExitBeEntities
    | ReportPartialResultEntities
    | ReportFinalResultEntities
    | InfoOnlyEntities
)


class IntentResult(ParsedBaseModel):
    type: IntentType
    category: IntentCategory
    entities: IntentEntityPayload
    confidence: float
    raw_fragment: str | None = None
    targeting_override: Targeting | None = None
    detection_strength: Literal["strong", "weak"] = "weak"
    status: Literal["CANDIDATE", "CONFIRMED", "INVALID"] = "CANDIDATE"
    valid_refs: list[int] = Field(default_factory=list)
    invalid_refs: list[int] = Field(default_factory=list)
    invalid_reason: str | None = None


class ParsedMessage(ParsedBaseModel):
    schema_version: str = "parsed_message_v1"
    parser_profile: str
    primary_class: MessageClass
    parse_status: ParseStatus
    confidence: float
    composite: bool = False
    signal: SignalPayload | None = None
    intents: list[IntentResult] = Field(default_factory=list)
    primary_intent: IntentType | None = None
    targeting: Targeting | None = None
    validation_status: Literal["PENDING", "VALIDATED"] = "PENDING"
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    raw_context: RawContext
