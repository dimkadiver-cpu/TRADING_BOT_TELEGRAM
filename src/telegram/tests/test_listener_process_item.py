"""Tests for TelegramListener._process_item with runtime_v2 pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import sqlite3
from unittest.mock import MagicMock

from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelEntry
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext
from src.storage.raw_message_revisions import RawMessageRevisionStore
from src.storage.raw_messages import RawMessageStore
from src.telegram.channel_config import ChannelsConfig
from src.telegram.ingestion import RawMessageIngestionService, TelegramIncomingMessage
from src.telegram.listener import TelegramListener, _QueueItem, _build_incoming


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
    enrichment_processor: MagicMock | None = None,
    trader_resolver: MagicMock | None = None,
) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        raw_repo=raw_repo or MagicMock(),
        channel_resolver=channel_resolver or MagicMock(),
        parser_pipeline=parser_pipeline or MagicMock(),
        enrichment_processor=enrichment_processor or MagicMock(),
        trader_resolver=trader_resolver or MagicMock(),
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
        aliases={},
        resolution_max_depth=5,
    )


def _make_resolved_trader(trader_id: str = "trader_a") -> ResolvedTraderContext:
    return ResolvedTraderContext(
        raw_message_id=1,
        trader_id=trader_id,
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=datetime.now(timezone.utc),
    )


def _make_trader_resolver_mock(trader_id: str = "trader_a") -> MagicMock:
    mock = MagicMock()
    mock.resolve.return_value = _make_resolved_trader(trader_id)
    return mock


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
        message_presentation_type="PLAIN",
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
        trader_resolver=_make_trader_resolver_mock(),
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
        enrichment_processor=MagicMock(),
        trader_resolver=_make_trader_resolver_mock(),
        logger=logger,
        channels_config=_make_config(),
    )
    listener._process_item(_make_queue_item(raw_message_id=1))

    logger.warning.assert_called_once()
    assert "parse failed" in logger.warning.call_args[0][0]


def test_build_incoming_marks_plain_message() -> None:
    message = MagicMock()
    message.id = 42
    message.message = "BUY BTCUSDT"
    message.date = datetime.now(timezone.utc)
    message.reply_markup = None
    message.reply_to = None

    incoming = _build_incoming(
        message=message,
        source_chat_id="-100123",
        chat_title="Test",
        chat_username=None,
        trader_id=None,
        acquisition_status="ACQUIRED_ELIGIBLE",
        acquisition_mode="live",
        source_topic_id=None,
    )

    assert incoming.message_presentation_type == "PLAIN"


def test_build_incoming_marks_inline_buttons_message() -> None:
    message = MagicMock()
    message.id = 42
    message.message = "BUY BTCUSDT"
    message.date = datetime.now(timezone.utc)
    message.reply_markup = MagicMock()
    message.reply_to = None

    incoming = _build_incoming(
        message=message,
        source_chat_id="-100123",
        chat_title="Test",
        chat_username=None,
        trader_id=None,
        acquisition_status="ACQUIRED_ELIGIBLE",
        acquisition_mode="live",
        source_topic_id=None,
    )

    assert incoming.message_presentation_type == "INLINE_BUTTONS"


def test_ingest_passes_message_presentation_type_to_store_record() -> None:
    store = MagicMock()
    store.save_with_id.return_value = MagicMock(saved=True, raw_message_id=77)
    service = RawMessageIngestionService(store=store, revision_store=None, logger=logging.getLogger("test"))

    result = service.ingest(
        TelegramIncomingMessage(
            source_chat_id="-100123",
            source_chat_title="Test",
            source_type="channel",
            source_trader_id=None,
            telegram_message_id=42,
            reply_to_message_id=None,
            raw_text="BUY BTCUSDT",
            message_ts=datetime.now(timezone.utc),
            acquisition_status="ACQUIRED_ELIGIBLE",
            source_topic_id=None,
            message_presentation_type="INLINE_BUTTONS",
        )
    )

    assert result.saved is True
    assert result.raw_message_id == 77
    store.save_with_id.assert_called_once()
    record = store.save_with_id.call_args[0][0]
    assert record.message_presentation_type == "INLINE_BUTTONS"


def test_ingest_persists_acquisition_mode_and_revision_run_context(tmp_path) -> None:
    db_path = tmp_path / "raw.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE raw_messages (
                raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id TEXT NOT NULL,
                source_chat_title TEXT,
                source_type TEXT,
                source_trader_id TEXT,
                telegram_message_id INTEGER NOT NULL,
                reply_to_message_id INTEGER,
                raw_text TEXT,
                message_ts TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                acquisition_status TEXT,
                acquisition_mode TEXT,
                source_topic_id INTEGER,
                message_presentation_type TEXT DEFAULT 'PLAIN'
            );
            CREATE UNIQUE INDEX idx_raw_messages_dedup
            ON raw_messages(source_chat_id, telegram_message_id);

            CREATE TABLE raw_message_revisions (
                revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_message_id INTEGER NOT NULL,
                source_chat_id TEXT NOT NULL,
                telegram_message_id INTEGER NOT NULL,
                revision_kind TEXT NOT NULL,
                run_context TEXT NOT NULL,
                raw_text TEXT,
                message_ts TEXT NOT NULL,
                revision_ts TEXT NOT NULL,
                telegram_edit_ts TEXT,
                acquisition_status TEXT,
                reply_to_message_id INTEGER,
                source_topic_id INTEGER,
                has_media INTEGER NOT NULL DEFAULT 0,
                media_kind TEXT,
                media_mime_type TEXT,
                media_filename TEXT,
                applied_to_current INTEGER NOT NULL DEFAULT 1
            );
            """
        )

    service = RawMessageIngestionService(
        store=RawMessageStore(str(db_path)),
        revision_store=RawMessageRevisionStore(str(db_path)),
        logger=logging.getLogger("test"),
    )

    result = service.ingest(
        TelegramIncomingMessage(
            source_chat_id="-100123",
            source_chat_title="Test",
            source_type="channel",
            source_trader_id=None,
            telegram_message_id=42,
            reply_to_message_id=None,
            raw_text="BUY BTCUSDT",
            message_ts=datetime.now(timezone.utc),
            acquisition_status="ACQUIRED_ELIGIBLE",
            acquisition_mode="edit",
            source_topic_id=None,
            message_presentation_type="INLINE_BUTTONS",
        )
    )

    assert result.saved is True
    assert result.raw_message_id is not None
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT acquisition_mode FROM raw_messages WHERE raw_message_id = ?",
            (result.raw_message_id,),
        ).fetchone()
        revision = conn.execute(
            "SELECT revision_kind, run_context FROM raw_message_revisions WHERE raw_message_id = ?",
            (result.raw_message_id,),
        ).fetchone()

    assert row == ("edit",)
    assert revision == ("initial", "edit")
