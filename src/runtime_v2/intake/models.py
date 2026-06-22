from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AcquisitionStatus = Literal["ACQUIRED", "ACQUIRED_ELIGIBLE", "BLACKLISTED", "MEDIA_ONLY_SKIPPED"]
ProcessingStatusV2 = Literal[
    "pending", "processing", "done", "failed", "blacklisted", "review", "skipped"
]
AcquisitionMode = Literal["live", "catchup", "import"]
MessagePresentationType = Literal["PLAIN", "INLINE_BUTTONS"]


@dataclass(slots=True, frozen=True)
class IntakeConfig:
    """Global configuration for the runtime_v2 intake pipeline."""
    reply_chain_depth_limit: int = 5


@dataclass(slots=True)
class RawIngestItem:
    """Raw Telegram event received by the intake processor from the listener."""
    source_chat_id: str
    source_chat_title: str | None
    source_type: str | None
    source_topic_id: int | None
    telegram_message_id: int
    reply_to_message_id: int | None
    raw_text: str | None
    message_ts: datetime
    acquisition_mode: AcquisitionMode
    message_presentation_type: MessagePresentationType
    has_media: bool
    media_kind: str | None
    media_mime_type: str | None
    media_filename: str | None


class RawMessageEnvelope(BaseModel):
    """Persisted raw message contract.

    acquisition_status is set once at ingest and never changes.
    processing_status tracks intake pipeline progress and is mutable.
    """

    raw_message_id: int
    source_chat_id: str
    source_chat_title: str | None
    source_type: str | None
    source_topic_id: int | None
    telegram_message_id: int
    reply_to_message_id: int | None
    raw_text: str | None
    message_ts: datetime
    acquired_at: datetime
    acquisition_mode: AcquisitionMode
    acquisition_status: AcquisitionStatus
    processing_status: ProcessingStatusV2
    message_presentation_type: MessagePresentationType = "PLAIN"
    source_trader_id: str | None
    resolved_trader_id: str | None
    resolution_method: str | None
    resolution_detail: str | None
    has_media: bool
    media_kind: str | None
    media_mime_type: str | None
    media_filename: str | None
