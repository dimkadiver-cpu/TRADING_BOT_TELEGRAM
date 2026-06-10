from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import EntryType, IntentType, MarkerStrength, ModifyEntryMode, ScopeHint, Side


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
    entry_selector_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    info_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    target_hint_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    ignore_markers: list[str] = Field(default_factory=list)


class CrossIntentSuppressionRule(RulesModel):
    if_strong: IntentType
    suppress_weak: list[IntentType] = Field(default_factory=list)
    reason: str | None = None


class WeakContextExclusionRule(RulesModel):
    name: str
    intent: IntentType
    markers: Union[list[str], dict[str, str]]
    scope: Literal["same_sentence", "same_line", "window", "whole_message"]
    window_chars: int | None = None
    if_contains_any: list[str] = Field(default_factory=list)
    if_regex_any: list[str] = Field(default_factory=list)
    unless_contains_any: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_condition(self) -> WeakContextExclusionRule:
        if not self.if_contains_any and not self.if_regex_any:
            raise ValueError(
                f"WeakContextExclusionRule '{self.name}' requires if_contains_any or if_regex_any"
            )
        return self


class MarkerContextExclusionRule(RulesModel):
    name: str
    strength: MarkerStrength
    marker_name: Union[str, list[str]]
    markers: Union[list[str], dict[str, str]]
    scope: Literal["same_sentence", "same_line", "window", "whole_message"]
    window_chars: int | None = None
    if_contains_any: list[str] = Field(default_factory=list)
    if_regex_any: list[str] = Field(default_factory=list)
    unless_contains_any: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_condition(self) -> MarkerContextExclusionRule:
        if not self.if_contains_any and not self.if_regex_any:
            raise ValueError(
                f"MarkerContextExclusionRule '{self.name}' requires if_contains_any or if_regex_any"
            )
        return self


class MarkerResolutionRules(RulesModel):
    suppress_weak_inside_strong_same_intent: bool = False
    cross_intent_suppression: list[CrossIntentSuppressionRule] = Field(default_factory=list)
    weak_context_exclusions: list[WeakContextExclusionRule] = Field(default_factory=list)
    marker_context_exclusions: list[MarkerContextExclusionRule] = Field(default_factory=list)


class ConvergenceRules(RulesModel):
    intent: dict[str, str] = Field(default_factory=dict)
    scope_hint: dict[str, str] = Field(default_factory=dict)


class ParserRules(RulesModel):
    marker_resolution: MarkerResolutionRules = Field(default_factory=MarkerResolutionRules)
    disambiguation: list[dict[str, Any]] = Field(default_factory=list)
    primary_intent_precedence: list[IntentType] = Field(default_factory=list)
    extraction_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    convergence: ConvergenceRules = Field(default_factory=ConvergenceRules)
    default_entry_type: EntryType | None = None
