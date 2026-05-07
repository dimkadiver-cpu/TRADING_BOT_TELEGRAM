from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import ScopeHint, TargetSource


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RawContext(ContextModel):
    raw_text: str
    normalized_text: str | None = None
    message_id: int | None = None
    reply_to_message_id: int | None = None
    source_chat_id: str | None = None
    source_topic_id: int | None = None
    extracted_links: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)


class TargetHints(ContextModel):
    target_source: TargetSource = "UNKNOWN"
    reply_to_message_id: int | None = None
    telegram_message_ids: list[int] = Field(default_factory=list)
    telegram_links: list[str] = Field(default_factory=list)
    explicit_ids: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    scope_hint: ScopeHint = "UNKNOWN"


class TargetCandidate(ContextModel):
    source: TargetSource
    value: Any
    start: int | None = None
    end: int | None = None
    line_index: int | None = None


class TargetExtractionResult(ContextModel):
    message_target_hints: TargetHints
    candidates: list[TargetCandidate] = Field(default_factory=list)


class ParserContext(ContextModel):
    raw_context: RawContext | None = None
    message_id: int | None = None
    reply_to_message_id: int | None = None
    source_chat_id: str | None = None
    source_topic_id: int | None = None
