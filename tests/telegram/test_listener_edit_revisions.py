from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.telegram.channel_config import ChannelsConfig
from src.telegram.ingestion import IngestionResult
from src.telegram.listener import TelegramListener


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

    def ingest(self, incoming) -> IngestionResult:
        return self.result


class FakeRevisionStore:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    def append_edit(self, **kwargs) -> None:
        self.edits.append(kwargs)


class FakeMessage:
    def __init__(
        self,
        msg_id: int,
        text: str,
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


def _run(coro) -> None:
    asyncio.run(coro)


def _make_listener(
    *,
    raw_repo: FakeRawRepo,
    chain_exists,
    revision_store: FakeRevisionStore,
) -> tuple[TelegramListener, FakeStatusStore]:
    status_store = FakeStatusStore()
    listener = TelegramListener(
        ingestion_service=FakeIngestion(IngestionResult(saved=True, raw_message_id=7)),
        processing_status_store=status_store,
        raw_repo=raw_repo,
        channel_resolver=None,
        parser_pipeline=None,
        enrichment_processor=None,
        trader_resolver=None,
        logger=logging.getLogger("test"),
        channels_config=ChannelsConfig(recovery_max_hours=4, blacklist_global=[], channels=[]),
        chain_exists_for_raw=chain_exists,
        revision_store=revision_store,
    )
    return listener, status_store


def test_reprocessable_edit_persists_revision_and_updates_current_text() -> None:
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "LONG BTCUSTD entry 60000")
    revision_store = FakeRevisionStore()
    listener, status_store = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: False,
        revision_store=revision_store,
    )

    edit_ts = datetime(2026, 6, 16, 10, 15, 0, tzinfo=timezone.utc)
    _run(
        listener._handle_edited_message(
            FakeEvent(FakeMessage(10, "LONG BTCUSDT entry 60000", edit_date=edit_ts))
        )
    )

    assert raw_repo.text_updates == [(55, "LONG BTCUSDT entry 60000")]
    assert status_store.updates == [(55, "pending")]
    assert len(revision_store.edits) == 1
    revision = revision_store.edits[0]
    assert revision["raw_message_id"] == 55
    assert revision["raw_text"] == "LONG BTCUSDT entry 60000"
    assert revision["run_context"] == f"edit:{int(edit_ts.timestamp())}"
    assert revision["applied_to_current"] is True


def test_skipped_edit_still_persists_observed_revision() -> None:
    raw_repo = FakeRawRepo()
    raw_repo.rows[("-100123", 10)] = (55, "LONG BTCUSDT entry 60000")
    revision_store = FakeRevisionStore()
    listener, status_store = _make_listener(
        raw_repo=raw_repo,
        chain_exists=lambda _rid: True,
        revision_store=revision_store,
    )

    edit_ts = datetime(2026, 6, 16, 10, 20, 0, tzinfo=timezone.utc)
    _run(
        listener._handle_edited_message(
            FakeEvent(FakeMessage(10, "LONG BTCUSDT entry 61000", edit_date=edit_ts))
        )
    )

    assert raw_repo.text_updates == []
    assert status_store.updates == []
    assert len(revision_store.edits) == 1
    revision = revision_store.edits[0]
    assert revision["raw_message_id"] == 55
    assert revision["raw_text"] == "LONG BTCUSDT entry 61000"
    assert revision["run_context"] == f"edit:{int(edit_ts.timestamp())}"
    assert revision["applied_to_current"] is False
