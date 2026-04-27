from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from src.parser.canonical_v1.intent_taxonomy import IntentName, validate_intent_name

ContextResolutionAction = Literal["promote", "resolve_as", "set_primary", "suppress"]


class ContextResolutionWhen(BaseModel):
    has_weak_intent: IntentName | None = None
    has_strong_intent: IntentName | None = None
    has_any_intent: list[IntentName] | None = None
    has_target_ref: bool | None = None
    message_type_hint_in: list[str] | None = None

    @field_validator("has_weak_intent", "has_strong_intent", mode="before")
    @classmethod
    def _validate_single_intent(cls, v: object) -> object:
        if isinstance(v, str):
            validate_intent_name(v)
        return v

    @field_validator("has_any_intent", mode="before")
    @classmethod
    def _validate_intent_list(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    validate_intent_name(name)
        return v

    @model_validator(mode="after")
    def _require_at_least_one_intent_signal(self) -> ContextResolutionWhen:
        if (
            self.has_weak_intent is None
            and self.has_strong_intent is None
            and self.has_any_intent is None
        ):
            raise ValueError(
                "at least one of 'has_weak_intent', 'has_strong_intent', or 'has_any_intent' is required"
            )
        return self


class ContextResolutionRule(BaseModel):
    name: str
    action: ContextResolutionAction
    when: ContextResolutionWhen
    if_target_history_has_any: list[IntentName] | None = None
    if_target_history_lacks_all: list[IntentName] | None = None
    if_target_exists: bool | None = None
    intent: IntentName | None = None
    resolve_as: IntentName | None = None
    otherwise_resolve_as: IntentName | None = None
    primary: IntentName | None = None
    suppress: list[IntentName] | None = None

    @field_validator("if_target_history_has_any", "if_target_history_lacks_all", mode="before")
    @classmethod
    def _validate_history_intents(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    validate_intent_name(name)
        return v

    @field_validator("intent", "resolve_as", "otherwise_resolve_as", "primary", mode="before")
    @classmethod
    def _validate_single_intent(cls, v: object) -> object:
        if isinstance(v, str):
            validate_intent_name(v)
        return v

    @field_validator("suppress", mode="before")
    @classmethod
    def _validate_suppress_list(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    validate_intent_name(name)
        return v

    @model_validator(mode="after")
    def _check_action_fields(self) -> ContextResolutionRule:
        if self.action == "resolve_as" and self.resolve_as is None:
            raise ValueError("action='resolve_as' requires the 'resolve_as' field")
        if self.action == "promote" and self.intent is None:
            raise ValueError("action='promote' requires the 'intent' field")
        if self.action == "set_primary" and self.primary is None:
            raise ValueError("action='set_primary' requires the 'primary' field")
        if self.action == "suppress" and self.suppress is None:
            raise ValueError("action='suppress' requires the 'suppress' field")
        if self.otherwise_resolve_as is not None and self.action != "resolve_as":
            raise ValueError(
                "'otherwise_resolve_as' is only valid with action='resolve_as'"
            )
        return self


class ContextResolutionRulesBlock(BaseModel):
    rules: list[ContextResolutionRule]
