from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.parser_v2.contracts.context import ParserContext
from src.runtime_v2.intake.models import RawMessageEnvelope

ResolutionMethod = Literal[
    "content_alias",
    "content_alias_ambiguous",
    "reply_chain",
    "reply_chain_alias",
    "source_chat_id",
    "source_chat_username",
    "source_chat_title",
    "source_topic_config",
    "assume_trader",
    "link",
    "link_multi",
    "unresolved",
]


class ResolvedTraderContext(BaseModel):
    raw_message_id: int
    trader_id: str | None
    method: ResolutionMethod
    detail: str | None
    is_ambiguous: bool
    resolved_at: datetime


class ParserDispatchCandidate(BaseModel):
    raw_message: RawMessageEnvelope
    resolved_trader: ResolvedTraderContext
    parser_profile: str
    parser_context: ParserContext
