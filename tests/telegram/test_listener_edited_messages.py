"""Tests for MessageEdited handling in TelegramListener."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.telegram.channel_config import ChannelsConfig
from src.telegram.ingestion import IngestionResult
from src.telegram.listener import TelegramListener


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeRawRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], tuple[int, str | None]] = {}
        self.text_updates: list[tuple[int, str]] = []

    def get_id_and_text(self, source_chat_id: str, telegram_message_id: int):
        return self.rows.get((source_chat_id, telegram_message_id))

    def update_raw_text(self, raw_message_id: int, raw_text: str) -> None:
        self.text_updates.append((raw_message_id, raw_text))


class FakeStatusStore:
    def __init__(self) -> None:
        self.updates: list[tuple[int, str]] = []

    def update(self, raw_message_id: int, status: str) -> None:
        self.updates.append((raw_message_id, status))


class FakeIngestion:
    def __init__(self, result: IngestionResult) -> None:
        self.result = result
        self.calls: list[object] = []

    def ingest(self, incoming) -> IngestionResult:
        self.calls.append(incoming)
        return self.result


class FakeMessage:
    def __init__(
        self,
        msg_id: int,
        text: str | None,
        *,
        date: datetime | None = None,
        edit_date: datetime | None = None,
    ) -> None:
        self.id = msg_id
        self.message = text
        self.media = None
        self.date = date or datetime.now(timezone.utc)
        self.edit_date = edit_date
        self.reply_to = None


class FakeEvent:
    def __init__(self, message: FakeMessage, chat_id: int = -100123) -> None:
        self.message = message
        self.chat_id = chat_id
        self.chat = None


def _make_listener(
    *,
    raw_repo: FakeRawRepo | None = None,
    chain_exists=None,
    blacklist_global: list[str] | None = None,
    ingestion: FakeIngestion | None = None,
    notify_edit_skipped=None,
) -> tuple[TelegramListener, FakeRawRepo, FakeStatusStore, FakeIngestion]:
    raw_repo = raw_repo or FakeRawRepo()
    status_store = FakeStatusStore()
    ingestion = ingestion or FakeIngestion(IngestionResult(saved=True, raw_message_id=7))
    config = ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=blacklist_global or [],
        channels=[],
    )
    listener = TelegramListener(
        ingestion_service=ingestion,
        processing_status_store=status_store,
        raw_repo=raw_repo,
        channel_resolver=None,
        parser_pipeline=None,
        enrichment_processor=None,
        trader_resolver=None,
        logger=logging.getLogger("test"),
        channels_config=config,
        chain_exists_for_raw=chain_exists,
        notify_edit_skipped=notify_edit_skipped,
    )
    return listener, raw_repo, status_store, ingestion


def _run(coro) -> None:
    asyncio.run(coro)


# ── Tests ────────────────────────────────────────────────────────────────────


def test_edit_without_text_is_ignored():
    listener, raw_repo, status_store, ingestion = _make_listener(
        chain_exists=lambda _rid: False,
    )
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, None))))
    assert not ingestion.calls
    assert listener._queue.empty()


def test_edit_of_never_acquired_message_is_ingested_as_new():
    """Caso reale: foto pubblicata senza caption, testo aggiunto via edit."""
    listener, raw_repo, status_store, ingestion = _make_listener(
        chain_exists=lambda _rid: False,
    )
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "LONG AVAXUSDT entry 30"))))
    assert len(ingestion.calls) == 1
    item = listener._queue.get_nowait()
    assert item.acquisition_mode == "edit"
    assert item.raw_text == "LONG AVAXUSDT entry 30"
    assert item.run_context == "live"


def test_edit_with_unchanged_text_is_skipped():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "LONG BTCUSDT entry 60000")
    listener, _, status_store, ingestion = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: False,
    )
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "LONG BTCUSDT entry 60000"))))
    assert not raw_repo.text_updates
    assert not status_store.updates
    assert listener._queue.empty()


def test_edit_of_rejected_signal_is_reprocessed():
    """Caso reale: segnale con simbolo errato rifiutato, poi corretto via edit."""
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "LONG BTCUSTD entry 60000")
    listener, _, status_store, ingestion = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: False,
    )
    edit_ts = datetime(2026, 6, 11, 14, 0, 0, tzinfo=timezone.utc)
    message = FakeMessage(10, "LONG BTCUSDT entry 60000", edit_date=edit_ts)
    _run(listener._handle_edited_message(FakeEvent(message)))

    assert raw_repo.text_updates == [(55, "LONG BTCUSDT entry 60000")]
    assert status_store.updates == [(55, "pending")]
    item = listener._queue.get_nowait()
    assert item.raw_message_id == 55
    assert item.acquisition_mode == "edit"
    assert item.run_context == f"edit:{int(edit_ts.timestamp())}"


def test_edit_of_message_with_existing_chain_is_skipped():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "LONG BTCUSDT entry 60000")
    listener, _, status_store, ingestion = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: True,
    )
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "LONG BTCUSDT entry 61000"))))
    assert not raw_repo.text_updates
    assert not status_store.updates
    assert listener._queue.empty()


def test_edit_fails_safe_without_chain_lookup():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "old text")
    listener, _, status_store, _ = _make_listener(raw_repo=raw_repo, chain_exists=None)
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "new text"))))
    assert not raw_repo.text_updates
    assert listener._queue.empty()


def test_edit_fails_safe_when_chain_lookup_raises():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "old text")

    def boom(_rid: int) -> bool:
        raise RuntimeError("ops db unavailable")

    listener, _, status_store, _ = _make_listener(raw_repo=raw_repo, chain_exists=boom)
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "new text"))))
    assert not raw_repo.text_updates
    assert listener._queue.empty()


def test_edit_of_old_message_is_skipped():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "old text")
    listener, _, status_store, ingestion = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: False,
    )
    stale_date = datetime.now(timezone.utc) - timedelta(hours=10)
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "new text", date=stale_date))))
    assert not raw_repo.text_updates
    assert not ingestion.calls
    assert listener._queue.empty()


def test_edit_with_existing_chain_emits_tech_log_notification():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "LONG BTCUSDT entry 60000")
    notified: list[dict] = []
    listener, _, _, _ = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: True,
        notify_edit_skipped=notified.append,
    )
    edit_ts = datetime(2026, 6, 11, 15, 0, 0, tzinfo=timezone.utc)
    message = FakeMessage(10, "LONG BTCUSDT entry 61000", edit_date=edit_ts)
    _run(listener._handle_edited_message(FakeEvent(message)))

    assert len(notified) == 1
    context = notified[0]
    assert context["chat"] == "-100123"
    assert context["msg_id"] == 10
    assert context["raw_message_id"] == 55
    assert context["edit_ts"] == int(edit_ts.timestamp())
    assert context["new_text_preview"].startswith("LONG BTCUSDT entry 61000")


def test_reprocessed_edit_does_not_notify():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "old text")
    notified: list[dict] = []
    listener, _, _, _ = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: False,
        notify_edit_skipped=notified.append,
    )
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "new text"))))
    assert not notified
    assert not listener._queue.empty()


def test_notification_failure_does_not_break_handler():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "old text")

    def boom(_context: dict) -> None:
        raise RuntimeError("outbox unavailable")

    listener, _, _, _ = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: True,
        notify_edit_skipped=boom,
    )
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "new text"))))
    assert listener._queue.empty()
    assert not raw_repo.text_updates


def test_edit_with_blacklisted_text_is_skipped():
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "old text")
    listener, _, status_store, _ = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: False,
        blacklist_global=["#promo"],
    )
    _run(listener._handle_edited_message(FakeEvent(FakeMessage(10, "testo con #promo dentro"))))
    assert not raw_repo.text_updates
    assert listener._queue.empty()
