"""Base abstractions for trader-specific parser profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ParserContext:
    trader_code: str
    message_id: int | None
    reply_to_message_id: int | None
    channel_id: str | None
    raw_text: str
    extracted_links: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TraderParseResult:
    message_type: str
    intents: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    target_refs: list[dict[str, Any]] = field(default_factory=list)
    reported_results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0


class TraderProfileParser(Protocol):
    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        """Parse a trader message and return normalized profile output."""
