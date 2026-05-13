from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.parser_v2.contracts.canonical_message import CanonicalMessage
from src.parser_v2.contracts.enums import MessageClass, ParseStatus


class CanonicalParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_message_id: int
    canonical_message_id: int
    parser_profile: str
    primary_class: MessageClass
    parse_status: ParseStatus
    canonical_message: CanonicalMessage
    warnings: list[str]
    parsed_at: datetime


class ParserJobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_message_id: int
    status: Literal["parsed", "failed", "skipped"]
    reason: str | None = None
    canonical_message_id: int | None = None


__all__ = ["CanonicalParseResult", "ParserJobStatus"]
