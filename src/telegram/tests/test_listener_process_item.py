"""Tests for TelegramListener._process_item with runtime_v2 pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelEntry
from src.telegram.channel_config import ChannelsConfig
from src.telegram.listener import TelegramListener, _QueueItem


def _make_config() -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=[],
        channels=[],
    )


def _make_listener(
    *,
    channel_resolver: MagicMock | None = None,
    parser_pipeline: MagicMock | None = None,
    raw_repo: MagicMock | None = None,
) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        raw_repo=raw_repo or MagicMock(),
        channel_resolver=channel_resolver or MagicMock(),
        parser_pipeline=parser_pipeline or MagicMock(),
        logger=MagicMock(),
        channels_config=_make_config(),
    )


def _make_queue_item(raw_message_id: int = 1) -> _QueueItem:
    return _QueueItem(
        raw_message_id=raw_message_id,
        source_chat_id="-100123",
        telegram_message_id=42,
        raw_text="BUY BTCUSDT",
        source_trader_id=None,
        reply_to_message_id=None,
        acquisition_mode="live",
        source_topic_id=None,
    )


def _make_channel_entry(*, active: bool = True, parser_profile: str = "trader_a") -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100123",
        topic_id=None,
        label="test",
        active=active,
        trader_id="trader_a",
        parser_profile=parser_profile,
        blacklist=[],
    )


def _make_envelope():
    from src.runtime_v2.intake.models import RawMessageEnvelope
    return RawMessageEnvelope(
        raw_message_id=1,
        source_chat_id="-100123",
        source_chat_title=None,
        source_type=None,
        source_topic_id=None,
        telegram_message_id=42,
        reply_to_message_id=None,
        raw_text="BUY BTCUSDT",
        message_ts=datetime.now(timezone.utc),
        acquired_at=datetime.now(timezone.utc),
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="done",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def test_process_item_no_channel_entry_skips() -> None:
    """Se il canale non è configurato, il messaggio viene ignorato."""
    resolver = MagicMock()
    resolver.lookup.return_value = None
    pipeline = MagicMock()

    listener = _make_listener(channel_resolver=resolver, parser_pipeline=pipeline)
    listener._process_item(_make_queue_item())

    pipeline.process.assert_not_called()


def test_process_item_inactive_channel_skips() -> None:
    """Se il canale è configurato ma non attivo, il messaggio viene ignorato."""
    resolver = MagicMock()
    resolver.lookup.return_value = _make_channel_entry(active=False)
    pipeline = MagicMock()

    listener = _make_listener(channel_resolver=resolver, parser_pipeline=pipeline)
    listener._process_item(_make_queue_item())

    pipeline.process.assert_not_called()


def test_process_item_calls_pipeline_on_active_channel() -> None:
    """Un canale attivo produce una chiamata a pipeline.process()."""
    resolver = MagicMock()
    resolver.lookup.return_value = _make_channel_entry()

    raw_repo = MagicMock()
    raw_repo.get_by_id.return_value = _make_envelope()

    parse_result = MagicMock(spec=CanonicalParseResult)
    parse_result.canonical_message_id = 99
    parse_result.primary_class = "SIGNAL"
    parse_result.parse_status = "PARSED"
    pipeline = MagicMock()
    pipeline.process.return_value = parse_result

    listener = _make_listener(
        channel_resolver=resolver,
        raw_repo=raw_repo,
        parser_pipeline=pipeline,
    )
    listener._process_item(_make_queue_item(raw_message_id=1))

    raw_repo.get_by_id.assert_called_once_with(1)
    pipeline.process.assert_called_once()
    candidate = pipeline.process.call_args[0][0]
    assert candidate.parser_profile == "trader_a"
    assert candidate.raw_message.raw_message_id == 1


def test_process_item_logs_warning_on_failed_parse() -> None:
    """Un ParserJobStatus(failed) produce un log warning, non un'eccezione."""
    resolver = MagicMock()
    resolver.lookup.return_value = _make_channel_entry()

    raw_repo = MagicMock()
    raw_repo.get_by_id.return_value = _make_envelope()

    failed = ParserJobStatus(raw_message_id=1, status="failed", reason="unknown_parser_profile")
    pipeline = MagicMock()
    pipeline.process.return_value = failed

    logger = MagicMock()
    listener = TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        raw_repo=raw_repo,
        channel_resolver=resolver,
        parser_pipeline=pipeline,
        logger=logger,
        channels_config=_make_config(),
    )
    listener._process_item(_make_queue_item(raw_message_id=1))

    logger.warning.assert_called_once()
    assert "parse failed" in logger.warning.call_args[0][0]
