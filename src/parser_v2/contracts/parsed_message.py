from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .context import RawContext, TargetHints
from .entities import IntentEntities, SignalFields
from .enums import (
    EvidenceStatus,
    IntentCategory,
    IntentType,
    MessageClass,
    PARSED_MESSAGE_SCHEMA_VERSION,
    ParseStatus,
)
from .markers import MarkerEvidence


class ParsedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignalDraft(SignalFields):
    pass


class ParsedIntent(ParsedModel):
    type: IntentType
    category: IntentCategory
    status: EvidenceStatus = "RESOLVED"
    confidence: float = Field(ge=0.0, le=1.0)
    entities: IntentEntities = Field(default_factory=IntentEntities)
    evidence: list[MarkerEvidence] = Field(default_factory=list)
    raw_fragment: str | None = None
    line_index: int | None = Field(default=None, ge=0)
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)
    intent_id: str | None = None
    occurrence_index: int | None = None
    target_hints: TargetHints | None = None


class ParsedMessage(ParsedModel):
    schema_version: str = PARSED_MESSAGE_SCHEMA_VERSION
    parser_profile: str
    primary_class: MessageClass
    parse_status: ParseStatus
    confidence: float = Field(ge=0.0, le=1.0)
    signal: SignalDraft | None = None
    intents: list[ParsedIntent] = Field(default_factory=list)
    primary_intent: IntentType | None = None
    evidence_status: EvidenceStatus = "RESOLVED"
    target_hints: TargetHints | None = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    raw_context: RawContext
