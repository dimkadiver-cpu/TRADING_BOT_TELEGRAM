from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import EntryType, IntentType, ModifyEntryMode, ScopeHint, Side


class RulesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarkerSet(RulesModel):
    strong: list[str] = Field(default_factory=list)
    weak: list[str] = Field(default_factory=list)


class SemanticMarkers(RulesModel):
    model_config = ConfigDict(extra="ignore")
    language: str | None = None
    intent_markers: dict[IntentType, MarkerSet] = Field(default_factory=dict)
    field_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    side_markers: dict[Side, MarkerSet] = Field(default_factory=dict)
    entry_type_markers: dict[EntryType, MarkerSet] = Field(default_factory=dict)
    modify_entry_mode_markers: dict[ModifyEntryMode, MarkerSet] = Field(default_factory=dict)
    info_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    target_hint_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    ignore_markers: list[str] = Field(default_factory=list)


class CrossIntentSuppressionRule(RulesModel):
    if_strong: IntentType
    suppress_weak: list[IntentType] = Field(default_factory=list)
    reason: str | None = None


class MarkerResolutionRules(RulesModel):
    suppress_weak_inside_strong_same_intent: bool = False
    cross_intent_suppression: list[CrossIntentSuppressionRule] = Field(default_factory=list)


class ParserRules(RulesModel):
    marker_resolution: MarkerResolutionRules = Field(default_factory=MarkerResolutionRules)
    disambiguation: list[dict[str, Any]] = Field(default_factory=list)
    primary_intent_precedence: list[IntentType] = Field(default_factory=list)
    extraction_markers: dict[str, MarkerSet] = Field(default_factory=dict)
