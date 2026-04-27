from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from src.parser.canonical_v1.intent_taxonomy import IntentName, validate_intent_name

IntentStrength = Literal["strong", "weak"]


class IntentCandidate(BaseModel):
    intent: IntentName
    strength: IntentStrength
    evidence: list[str]

    @field_validator("intent", mode="before")
    @classmethod
    def _validate_intent(cls, v: object) -> object:
        if isinstance(v, str):
            validate_intent_name(v)
        return v

    @property
    def is_strong(self) -> bool:
        return self.strength == "strong"

    @property
    def is_weak(self) -> bool:
        return self.strength == "weak"
