from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.parser.intent_types import IntentType
from src.parser.canonical_v1.intent_taxonomy import IntentName, validate_intent_name

DisambiguationAction = Literal["prefer", "suppress", "keep_multi"]
_NEW_INTENT_NAMES = frozenset(intent.value for intent in IntentType)


def _validate_supported_intent_name(value: str) -> None:
    if value in _NEW_INTENT_NAMES:
        return
    validate_intent_name(value)


class IntentConditions(BaseModel):
    strong: list[str] = Field(default_factory=list)
    weak: list[str] = Field(default_factory=list)

    @field_validator("strong", "weak", mode="before")
    @classmethod
    def _validate_lists(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    _validate_supported_intent_name(name)
        return v


class TextConditions(BaseModel):
    any: list[str] = Field(default_factory=list)
    none: list[str] = Field(default_factory=list)


class MessageConditions(BaseModel):
    composite: bool | None = None
    has_targeting: bool | None = None


class EntityConditions(BaseModel):
    present: list[str] = Field(default_factory=list)
    absent: list[str] = Field(default_factory=list)


class DisambiguationConditions(BaseModel):
    intents: IntentConditions = Field(default_factory=IntentConditions)
    text: TextConditions = Field(default_factory=TextConditions)
    message: MessageConditions = Field(default_factory=MessageConditions)
    entities: EntityConditions = Field(default_factory=EntityConditions)

    def is_empty(self) -> bool:
        return not any(
            (
                self.intents.strong,
                self.intents.weak,
                self.text.any,
                self.text.none,
                self.message.composite is not None,
                self.message.has_targeting is not None,
                self.entities.present,
                self.entities.absent,
            )
        )


class DisambiguationRule(BaseModel):
    name: str
    action: DisambiguationAction
    priority: int = 0
    conditions: DisambiguationConditions = Field(default_factory=DisambiguationConditions)
    when_all_detected: list[IntentName] | None = None
    when_any_detected: list[IntentName] | None = None
    if_contains_any: list[str] | None = None
    unless_contains_any: list[str] | None = None
    prefer: IntentName | None = None
    over: list[str] | None = None
    suppress: list[IntentName] | None = None
    keep: list[IntentName] | None = None

    @field_validator("when_all_detected", "when_any_detected", "over", mode="before")
    @classmethod
    def _validate_intent_list(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    _validate_supported_intent_name(name)
        return v

    @field_validator("prefer", mode="before")
    @classmethod
    def _validate_prefer(cls, v: object) -> object:
        if isinstance(v, str):
            _validate_supported_intent_name(v)
        return v

    @field_validator("suppress", "keep", mode="before")
    @classmethod
    def _validate_intent_list_field(cls, v: object) -> object:
        if isinstance(v, list):
            for name in v:
                if isinstance(name, str):
                    _validate_supported_intent_name(name)
        return v

    @model_validator(mode="after")
    def _check_when_condition(self) -> DisambiguationRule:
        if (
            self.when_all_detected is None
            and self.when_any_detected is None
            and self.conditions.is_empty()
        ):
            raise ValueError(
                "at least one legacy condition or nested 'conditions' block is required"
            )
        return self

    @model_validator(mode="after")
    def _check_action_fields(self) -> DisambiguationRule:
        if self.action == "prefer" and self.prefer is None:
            raise ValueError("action='prefer' requires the 'prefer' field")
        if self.action == "suppress" and self.suppress is None:
            raise ValueError("action='suppress' requires the 'suppress' field")
        return self

    @property
    def when_strong(self) -> list[str]:
        return list(self.conditions.intents.strong)

    @property
    def when_weak(self) -> list[str]:
        return list(self.conditions.intents.weak)

    @property
    def text_any(self) -> list[str]:
        return list(self.conditions.text.any)

    @property
    def text_none(self) -> list[str]:
        return list(self.conditions.text.none)

    @property
    def message_composite(self) -> bool | None:
        return self.conditions.message.composite

    @property
    def message_has_targeting(self) -> bool | None:
        return self.conditions.message.has_targeting

    @property
    def entities_present(self) -> list[str]:
        return list(self.conditions.entities.present)

    @property
    def entities_absent(self) -> list[str]:
        return list(self.conditions.entities.absent)


class DisambiguationRulesBlock(BaseModel):
    rules: list[DisambiguationRule]
