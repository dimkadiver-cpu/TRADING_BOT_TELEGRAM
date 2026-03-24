"""Telegram listener with fast-path ingestion, recovery, and worker delegation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Iterable

try:
    from telethon import TelegramClient, events
    from telethon.tl.custom.message import Message
except ModuleNotFoundError:  # pragma: no cover - test fallback when Telethon is unavailable
    TelegramClient = object  # type: ignore[assignment]

    class _EventsModule:
        class NewMessage:
            class Event:
                pass

    events = _EventsModule()  # type: ignore[assignment]
    Message = object  # type: ignore[assignment]

from src.storage.parse_results import ParseResultStore
from src.storage.processing_status import ProcessingStatusStore
from src.storage.raw_messages import RawMessageStore
from src.storage.review_queue import ReviewQueueStore
from src.telegram.channel_config import ChannelsConfig
from src.telegram.effective_trader import EffectiveTraderResolver
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.telegram.ingestion import RawMessageIngestionService, TelegramIncomingMessage
from src.telegram.router import MessageRouter, QueueItem as _QueueItem, is_blacklisted_text
from src.telegram.trader_mapping import TelegramSourceTraderMapper


def build_ingestion_service(db_path: str, logger: logging.Logger) -> RawMessageIngestionService:
    return RawMessageIngestionService(store=RawMessageStore(db_path=db_path), logger=logger)


def build_effective_trader_resolver(
    db_path: str,
    trader_mapper: TelegramSourceTraderMapper,
    trader_aliases: dict[str, str],
    known_trader_ids: set[str],
) -> EffectiveTraderResolver:
    return EffectiveTraderResolver(
        source_mapper=trader_mapper,
        raw_store=RawMessageStore(db_path=db_path),
        trader_aliases=trader_aliases,
        known_trader_ids=known_trader_ids,
    )


def build_eligibility_evaluator(db_path: str) -> MessageEligibilityEvaluator:
    return MessageEligibilityEvaluator(raw_store=RawMessageStore(db_path=db_path))


def build_parse_results_store(db_path: str) -> ParseResultStore:
    return ParseResultStore(db_path=db_path)


def build_processing_status_store(db_path: str) -> ProcessingStatusStore:
    return ProcessingStatusStore(db_path=db_path)


def build_review_queue_store(db_path: str) -> ReviewQueueStore:
    return ReviewQueueStore(db_path=db_path)


class TelegramListener:
    """Producer/consumer listener with recovery and hot reload support."""

    def __init__(
        self,
        *,
        ingestion_service: RawMessageIngestionService,
        processing_status_store: ProcessingStatusStore,
        router: MessageRouter,
        logger: logging.Logger,
        channels_config: ChannelsConfig,
        fallback_allowed_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self._ingestion = ingestion_service
        self._status_store = processing_status_store
        self._router = router
        self._logger = logger
        self._config = channels_config
        self._fallback_ids: set[int] = set(fallback_allowed_chat_ids or [])
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue()

    def update_config(self, new_config: ChannelsConfig) -> None:
        self._config = new_config
        self._router.update_config(new_config)
        self._logger.info(
            "listener config updated | active_channels=%d",
            len(new_config.active_channels),
        )

    def register_handlers(self, client: TelegramClient) -> None:
        @client.on(events.NewMessage)
        async def _on_message(event: events.NewMessage.Event) -> None:
            await self._handle_new_message(event, acquisition_mode="live")

    async def run_recovery(self, client: TelegramClient) -> None:
        await self._reenqueue_stale()
        await self._catchup_from_telegram(client)

    async def _reenqueue_stale(self) -> None:
        stale = self._status_store.get_stale_messages()
        if not stale:
            return
        self._logger.info("recovery: re-enqueuing %d stale messages", len(stale))
        for msg in stale:
            await self._queue.put(
                _QueueItem(
                    raw_message_id=msg.raw_message_id,
                    source_chat_id=msg.source_chat_id,
                    telegram_message_id=msg.telegram_message_id,
                    raw_text=msg.raw_text or "",
                    source_trader_id=msg.source_trader_id,
                    reply_to_message_id=msg.reply_to_message_id,
                    acquisition_mode="catchup",
                )
            )

    async def _catchup_from_telegram(self, client: TelegramClient) -> None:
        active = self._config.active_channels
        if not active and not self._fallback_ids:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._config.recovery_max_hours)
        chat_ids_to_recover = [ch.chat_id for ch in active] if active else list(self._fallback_ids)
        for chat_id in chat_ids_to_recover:
            last_id = self._status_store.get_last_telegram_message_id(str(chat_id))
            try:
                messages = await client.get_messages(chat_id, min_id=last_id or 0, limit=200)
            except Exception:
                self._logger.exception("recovery: failed to fetch messages | chat=%s", chat_id)
                continue

            catchup_messages = [
                msg
                for msg in messages
                if isinstance(msg, Message) and msg.date is not None and _as_utc(msg.date) >= cutoff
            ]
            catchup_messages.sort(key=lambda msg: msg.id)
            if catchup_messages:
                self._logger.info(
                    "recovery: %d catchup messages | chat=%s since_id=%s",
                    len(catchup_messages),
                    chat_id,
                    last_id,
                )
            for msg in catchup_messages:
                await self._ingest_and_enqueue(
                    message=msg,
                    chat_id=chat_id,
                    chat_title=None,
                    chat_username=None,
                    acquisition_mode="catchup",
                )

    async def run_worker(self) -> None:
        self._logger.info("listener worker started")
        while True:
            item = await self._queue.get()
            try:
                self._process_item(item)
            except Exception:
                self._logger.exception(
                    "worker: unhandled error | raw_message_id=%s",
                    item.raw_message_id,
                )
                self._status_store.update(item.raw_message_id, "failed")
            finally:
                self._queue.task_done()

    async def _handle_new_message(
        self,
        event: events.NewMessage.Event,
        acquisition_mode: str,
    ) -> None:
        message: Message = event.message
        chat_id_raw = int(event.chat_id) if event.chat_id is not None else None

        if not self._is_allowed_chat(chat_id_raw):
            return

        if _is_media_only(message):
            self._logger.info("media_only_skipped | chat=%s msg_id=%s", chat_id_raw, message.id)
            return

        chat_title = getattr(event.chat, "title", None) or getattr(event.chat, "username", None)
        chat_username = getattr(event.chat, "username", None)

        await self._ingest_and_enqueue(
            message=message,
            chat_id=chat_id_raw,
            chat_title=chat_title,
            chat_username=chat_username,
            acquisition_mode=acquisition_mode,
        )

    async def _ingest_and_enqueue(
        self,
        *,
        message: Message,
        chat_id: int | None,
        chat_title: str | None,
        chat_username: str | None,
        acquisition_mode: str,
    ) -> None:
        source_chat_id = str(chat_id) if chat_id is not None else "unknown"
        raw_text = message.message or ""

        if self._is_blacklisted(raw_text, chat_id):
            self._logger.info(
                "blacklisted | chat=%s msg_id=%s text_start=%.80r",
                source_chat_id,
                message.id,
                raw_text,
            )
            result = self._ingestion.ingest(
                _build_incoming(
                    message=message,
                    source_chat_id=source_chat_id,
                    chat_title=chat_title,
                    chat_username=chat_username,
                    trader_id=None,
                    acquisition_status="BLACKLISTED",
                )
            )
            if result.raw_message_id is not None:
                self._status_store.update(result.raw_message_id, "blacklisted")
            return

        ingestion = self._ingestion.ingest(
            _build_incoming(
                message=message,
                source_chat_id=source_chat_id,
                chat_title=chat_title,
                chat_username=chat_username,
                trader_id=None,
                acquisition_status="ACQUIRED_ELIGIBLE",
            )
        )
        if not ingestion.saved and ingestion.raw_message_id is not None:
            self._logger.info("duplicate skipped | chat=%s msg_id=%s", source_chat_id, message.id)
            return
        if ingestion.raw_message_id is None:
            self._logger.warning("ingest failed | chat=%s msg_id=%s", source_chat_id, message.id)
            return

        reply_to_message_id = (
            int(message.reply_to.reply_to_msg_id)
            if message.reply_to and getattr(message.reply_to, "reply_to_msg_id", None)
            else None
        )
        await self._queue.put(
            _QueueItem(
                raw_message_id=ingestion.raw_message_id,
                source_chat_id=source_chat_id,
                telegram_message_id=int(message.id),
                raw_text=raw_text,
                source_trader_id=None,
                reply_to_message_id=reply_to_message_id,
                acquisition_mode=acquisition_mode,
            )
        )
        self._logger.info(
            "raw acquired | chat=%s msg_id=%s mode=%s raw_message_id=%s",
            source_chat_id,
            message.id,
            acquisition_mode,
            ingestion.raw_message_id,
        )

    def _process_item(self, item: _QueueItem) -> None:
        self._router.route(item)

    def _is_allowed_chat(self, chat_id: int | None) -> bool:
        if chat_id is None:
            return False
        if self._fallback_ids:
            return chat_id in self._fallback_ids
        active = self._config.active_chat_ids
        if not active:
            return True
        return chat_id in active

    def _is_blacklisted(self, raw_text: str, chat_id: int | None) -> bool:
        return is_blacklisted_text(self._config, raw_text, chat_id)


def _is_media_only(message: Message) -> bool:
    return message.media is not None and not bool(message.message)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_incoming(
    *,
    message: Message,
    source_chat_id: str,
    chat_title: str | None,
    chat_username: str | None,
    trader_id: str | None,
    acquisition_status: str,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        source_chat_id=source_chat_id,
        source_chat_title=chat_title,
        source_type=_resolve_source_type(chat_title, chat_username),
        source_trader_id=trader_id,
        telegram_message_id=int(message.id),
        reply_to_message_id=(
            int(message.reply_to.reply_to_msg_id)
            if message.reply_to and getattr(message.reply_to, "reply_to_msg_id", None)
            else None
        ),
        raw_text=message.message,
        message_ts=message.date or datetime.now(timezone.utc),
        acquisition_status=acquisition_status,
    )


def _resolve_source_type(chat_title: str | None, chat_username: str | None) -> str | None:
    if chat_title:
        return "channel"
    if chat_username:
        return "user"
    return None
