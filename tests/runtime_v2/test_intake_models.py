from __future__ import annotations
import pytest
from datetime import datetime, timezone
from src.runtime_v2.intake.models import (
    RawMessageEnvelope,
    RawIngestItem,
    IntakeConfig,
)

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _make_envelope(**overrides) -> RawMessageEnvelope:
    defaults = dict(
        raw_message_id=1,
        source_chat_id="-100123",
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=456,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="pending",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )
    defaults.update(overrides)
    return RawMessageEnvelope(**defaults)


def test_raw_ingest_item_construction():
    item = RawIngestItem(
        source_chat_id="-100123",
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=456,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquisition_mode="live",
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )
    assert item.source_chat_id == "-100123"
    assert item.acquisition_mode == "live"


def test_raw_message_envelope_valid():
    env = _make_envelope()
    assert env.raw_message_id == 1
    assert env.acquisition_status == "ACQUIRED"
    assert env.processing_status == "pending"


def test_raw_message_envelope_rejects_invalid_acquisition_status():
    with pytest.raises(Exception):
        _make_envelope(acquisition_status="ACQUIRED_REVIEW_ONLY")


def test_raw_message_envelope_rejects_invalid_processing_status():
    with pytest.raises(Exception):
        _make_envelope(processing_status="unknown_status")


def test_intake_config_defaults():
    cfg = IntakeConfig()
    assert cfg.reply_chain_depth_limit == 5


def test_intake_config_custom():
    cfg = IntakeConfig(reply_chain_depth_limit=10)
    assert cfg.reply_chain_depth_limit == 10
