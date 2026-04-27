from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from src.parser.canonical_v1.intent_taxonomy import IntentName, validate_intent_name

DisambiguationAction = Literal["prefer", "suppress", "keep_multi"]


class DisambiguationRule(BaseModel):
    name: str
    action: DisambiguationAction
    when_all_detected: list[IntentName] | None = None
    when_any_detected: list[IntentName] | None = None
    if_contains_any: list[str] | None = None
    unless_contains_any: list[str] | None = None
    prefer: IntentName | None = None
    suppress: list[IntentName] | None = None
    keep: list[IntentName] | None = None

    @field_validator("when_all_detected", "when_any_detected", mode="before")
    @classmethod
    def _validate_intent_list(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    validate_intent_name(name)
        return v

    @field_validator("prefer", mode="before")
    @classmethod
    def _validate_prefer(cls, v: object) -> object:
        if isinstance(v, str):
            validate_intent_name(v)
        return v

    @field_validator("suppress", "keep", mode="before")
    @classmethod
    def _validate_intent_list_field(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    validate_intent_name(name)
        return v

    @model_validator(mode="after")
    def _check_when_condition(self) -> DisambiguationRule:
        if self.when_all_detected is None and self.when_any_detected is None:
            raise ValueError(
                "at least one of 'when_all_detected' or 'when_any_detected' is required"
            )
        return self

    @model_validator(mode="after")
    def _check_action_fields(self) -> DisambiguationRule:
        if self.action == "prefer" and self.prefer is None:
            raise ValueError("action='prefer' requires the 'prefer' field")
        if self.action == "suppress" and self.suppress is None:
            raise ValueError("action='suppress' requires the 'suppress' field")
        return self


class DisambiguationRulesBlock(BaseModel):
    rules: list[DisambiguationRule]
