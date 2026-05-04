from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from src.parser.canonical_v1.intent_taxonomy import IntentName, validate_intent_name

RelationType = Literal[
    "compatible",
    "exclusive",
    "specific_vs_generic",
    "stateful_requires_context",
]


class IntentCompatibilityPair(BaseModel):
    intents: list[IntentName]
    relation: RelationType
    preferred: IntentName | None = None
    requires_resolution: bool
    requires_context_validation: bool = False
    warning_if_unresolved: bool = True

    @field_validator("intents", mode="before")
    @classmethod
    def _validate_intents(cls, v: object) -> object:
        if isinstance(v, list):
            if len(v) != 2:
                raise ValueError("intents must contain exactly 2 elements")
            for name in v:
                if isinstance(name, str):
                    validate_intent_name(name)
        return v

    @field_validator("preferred", mode="before")
    @classmethod
    def _validate_preferred(cls, v: object) -> object:
        if isinstance(v, str):
            validate_intent_name(v)
        return v

    @model_validator(mode="after")
    def _preferred_must_be_in_intents(self) -> IntentCompatibilityPair:
        if self.preferred is not None and self.preferred not in self.intents:
            raise ValueError(
                f"preferred {self.preferred!r} must be one of the declared intents {self.intents}"
            )
        return self


class IntentCompatibilityBlock(BaseModel):
    pairs: list[IntentCompatibilityPair]
