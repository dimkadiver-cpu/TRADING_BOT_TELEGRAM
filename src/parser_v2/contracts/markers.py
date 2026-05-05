from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import MarkerKind, MarkerStrength


class MarkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NormalizedText(MarkerModel):
    raw_text: str
    normalized_text: str
    lines: list[str] = Field(default_factory=list)


class MarkerMatch(MarkerModel):
    name: str
    kind: MarkerKind
    strength: MarkerStrength
    marker: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class MarkerEvidence(MarkerModel):
    name: str
    kind: MarkerKind
    strength: MarkerStrength
    marker: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    suppressed: bool = False
    suppressed_by: str | None = None
    reason: str | None = None
