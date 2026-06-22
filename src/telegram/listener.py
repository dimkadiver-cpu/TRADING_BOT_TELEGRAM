"""Telegram listener with fast-path ingestion, recovery, and worker delegation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from typing import TYPE_CHECKING, Iterable

try:
    from telethon import TelegramClient, events
    from telethon.tl.custom.message import Message
except ModuleNotFoundError:  # pragma: no cover - test fallback when Telethon is unavailable
    TelegramClient = object  # type: ignore[assignment]

    class _EventsModule:
        class NewMessage:
            class Event:
                pass

        class MessageEdited:
            class Event:
                pass

        class MessageDeleted:
            class Event:
                pass

    events = _EventsModule()  # type: ignore[assignment]
    Message = object  # type: ignore[assignment]

from dataclasses import dataclass

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate, ResolvedTraderContext
from src.storage.processing_status import ProcessingStatusStore
from src.storage.raw_message_revisions import RawMessageRevisionStore
from src.storage.raw_messages import RawMessageStore
from src.telegram.channel_config import ChannelsConfig
from src.telegram.topic_utils import extract_message_topic_id, extract_real_reply_to_message_id
from src.telegram.ingestion import RawMessageIngestionService, TelegramIncomingMessage

if TYPE_CHECKING:
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor
    from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
    from src.telegram.trader_resolver import TraderResolver


@dataclass(slots=True)
class _QueueItem:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    raw_text: str
    source_trader_id: str | None
    reply_to_message_id: int | None
    acquisition_mode: str
    source_topic_id: int | None = None
    run_context: str = "live"


def _is_blacklisted_text(
    config: ChannelsConfig,
    raw_text: str,
    chat_id: int | None,
    topic_id: int | None = None,
) -> bool:
    text_lower = raw_text.lower()
    for tag in config.blacklist_global:
        if tag.lower() in text_lower:
            return True
    if chat_id is not None:
        entry = config.match_entry(chat_id, topic_id)
        if entry is not None:
            for tag in entry.blacklist:
                if tag.lower() in text_lower:
                    return True
    return False


def build_ingestion_service(db_path: str, logger: logging.Logger) -> RawMessageIngestionService:
    return RawMessageIngestionService(
        store=RawMessageStore(db_path=db_path),
        revision_store=RawMessageRevisionStore(db_path=db_path),
        logger=logger,
    )


def build_processing_status_store(db_path: str) -> ProcessingStatusStore:
    return ProcessingStatusStore(db_path=db_path)


class TelegramListener:
    """Producer/consumer listener with recovery and hot reload support."""

    def __init__(
        self,
        *,
        ingestion_service: RawMessageIngestionService,
        processing_status_store: ProcessingStatusStore,
        raw_repo: RawMessageRepository,
        channel_resolver: ChannelConfigResolver,
        parser_pipeline: ParserPipelineProcessor,
        enrichment_processor: SignalEnrichmentProcessor,
        trader_resolver: TraderResolver,
        logger: logging.Logger,
        channels_config: ChannelsConfig,
        fallback_allowed_chat_ids: Iterable[int] | None = None,
        chain_exists_for_raw: Callable[[int], bool] | None = None,
        notify_edit_skipped: Callable[[dict], None] | None = None,
        revision_store: RawMessageRevisionStore | None = None,
    ) -> None:
        self._ingestion = ingestion_service
        self._status_store = processing_status_store
        self._raw_repo = raw_repo
        self._channel_resolver = channel_resolver
        self._parser_pipeline = parser_pipeline
        self._enrichment_processor = enrichment_processor
        self._trader_resolver = trader_resolver
        self._logger = logger
        self._config = channels_config
        self._fallback_ids: set[int] = set(fallback_allowed_chat_ids or [])
        self._chain_exists_for_raw = chain_exists_for_raw
        self._notify_edit_skipped = notify_edit_skipped
        self._revision_store = revision_store
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue()

    def update_config(self, new_config: ChannelsConfig) -> None:
        self._config = new_config
        self._channel_resolver.reload()
        self._logger.info(
            "listener config updated | active_channels=%d",
            len(new_config.active_channels),
        )

    def register_handlers(self, client: TelegramClient) -> None:
        @client.on(events.NewMessage)
        async def _on_message(event: events.NewMessage.Event) -> None:
            await self._handle_new_message(event, acquisition_mode="live")

        @client.on(events.MessageEdited)
        async def _on_message_edited(event: events.MessageEdited.Event) -> None:
            await self._handle_edited_message(event)

        @client.on(events.MessageDeleted)
        async def _on_message_deleted(event: events.MessageDeleted.Event) -> None:
            await self._handle_deleted_message(event)

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
                    source_topic_id=msg.source_topic_id,
                )
            )

    async def _catchup_from_telegram(self, client: TelegramClient) -> None:
        active = self._config.active_channels
        if not active and not self._fallback_ids:
            return
        if self._config.recovery_max_hours <= 0:
            self._logger.info(
                "recovery catchup skipped | recovery_max_hours=%s",
                self._config.recovery_max_hours,
            )
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._config.recovery_max_hours)

        if active:
            # Build per-chat map: chat_id → list of active entries
            chat_entry_map: dict[int, list] = {}
            for ch in active:
                chat_entry_map.setdefault(ch.chat_id, []).append(ch)

            for chat_id, entries in chat_entry_map.items():
                # Per-entry topic-aware checkpoint; use min to not miss any messages
                per_entry_last = [
                    self._status_store.get_last_telegram_message_id(str(chat_id), e.topic_id)
                    for e in entries
                ]
                # If any active topic has no checkpoint yet, recover from 0 for the whole chat
                # and let topic filtering trim the set. Otherwise a previously active topic can
                # hide messages for a newly enabled topic in the same forum chat.
                if any(x is None for x in per_entry_last):
                    min_last_id = None
                else:
                    min_last_id = min((x for x in per_entry_last if x is not None), default=None)
                try:
                    messages = await client.get_messages(
                        chat_id, min_id=min_last_id or 0, limit=200
                    )
                except Exception:
                    self._logger.exception("recovery: failed to fetch messages | chat=%s", chat_id)
                    continue

                catchup_messages = [
                    msg
                    for msg in messages
                    if isinstance(msg, Message)
                    and msg.date is not None
                    and _as_utc(msg.date) >= cutoff
                ]
                catchup_messages.sort(key=lambda msg: msg.id)
                if catchup_messages:
                    self._logger.info(
                        "recovery: %d catchup messages | chat=%s since_id=%s",
                        len(catchup_messages),
                        chat_id,
                        min_last_id,
                    )
                for msg in catchup_messages:
                    topic_id = extract_message_topic_id(
                        msg,
                        known_topic_ids=_known_topic_ids(entries),
                    )
                    if not self._is_allowed_message(chat_id, topic_id):
                        self._logger.info(
                            "catchup skipped by topic scope | chat=%s topic=%s msg_id=%s",
                            chat_id,
                            topic_id,
                            msg.id,
                        )
                        continue
                    await self._ingest_and_enqueue(
                        message=msg,
                        chat_id=chat_id,
                        chat_title=None,
                        chat_username=None,
                        acquisition_mode="catchup",
                        source_topic_id=topic_id,
                    )
        else:
            # Fallback IDs path: no config entries, use chat-level checkpoint
            for chat_id in self._fallback_ids:
                last_id = self._status_store.get_last_telegram_message_id(str(chat_id))
                try:
                    messages = await client.get_messages(
                        chat_id, min_id=last_id or 0, limit=200
                    )
                except Exception:
                    self._logger.exception("recovery: failed to fetch messages | chat=%s", chat_id)
                    continue

                catchup_messages = [
                    msg
                    for msg in messages
                    if isinstance(msg, Message)
                    and msg.date is not None
                    and _as_utc(msg.date) >= cutoff
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
                    topic_id = extract_message_topic_id(msg)
                    await self._ingest_and_enqueue(
                        message=msg,
                        chat_id=chat_id,
                        chat_title=None,
                        chat_username=None,
                        acquisition_mode="catchup",
                        source_topic_id=topic_id,
                    )

    async def run_worker(self) -> None:
        self._logger.info("listener worker started")
        while True:
            item = await self._queue.get()
            try:
                self._status_store.update(item.raw_message_id, "processing")
                self._process_item(item)
                self._status_store.update(item.raw_message_id, "done")
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
        topic_id = self._extract_topic_id(chat_id_raw, message)

        if not self._is_allowed_message(chat_id_raw, topic_id):
            if chat_id_raw is not None and self._config.entries_for_chat(chat_id_raw):
                self._logger.info(
                    "message skipped by topic scope | chat=%s topic=%s msg_id=%s",
                    chat_id_raw,
                    topic_id,
                    message.id,
                )
            return

        if _is_media_only(message):
            self._logger.info(
                "media_only_skipped | chat=%s topic=%s msg_id=%s",
                chat_id_raw,
                topic_id,
                message.id,
            )
            return

        chat_title = getattr(event.chat, "title", None) or getattr(event.chat, "username", None)
        chat_username = getattr(event.chat, "username", None)

        await self._ingest_and_enqueue(
            message=message,
            chat_id=chat_id_raw,
            chat_title=chat_title,
            chat_username=chat_username,
            acquisition_mode=acquisition_mode,
            source_topic_id=topic_id,
        )

    async def _handle_edited_message(self, event: events.MessageEdited.Event) -> None:
        """Edited messages: ingest if never acquired (e.g. caption added to a media
        post), re-process if the text changed and no trade chain was created yet
        (e.g. trader corrects a rejected signal). Edits of messages that already
        produced a trade chain are never re-executed."""
        message: Message = event.message
        chat_id_raw = int(event.chat_id) if event.chat_id is not None else None
        topic_id = self._extract_topic_id(chat_id_raw, message)

        if not self._is_allowed_message(chat_id_raw, topic_id):
            return

        new_text = message.message or ""
        if not new_text:
            return

        if self._config.recovery_max_hours > 0 and message.date is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self._config.recovery_max_hours)
            if _as_utc(message.date) < cutoff:
                self._logger.info(
                    "edit_too_old_skipped | chat=%s topic=%s msg_id=%s",
                    chat_id_raw,
                    topic_id,
                    message.id,
                )
                return

        source_chat_id = str(chat_id_raw)
        existing = self._raw_repo.get_id_and_text(source_chat_id, int(message.id))

        if existing is None:
            # Never acquired live (es. foto senza caption) → ingest come nuovo.
            chat_title = getattr(event.chat, "title", None) or getattr(event.chat, "username", None)
            chat_username = getattr(event.chat, "username", None)
            await self._ingest_and_enqueue(
                message=message,
                chat_id=chat_id_raw,
                chat_title=chat_title,
                chat_username=chat_username,
                acquisition_mode="edit",
                source_topic_id=topic_id,
            )
            return

        raw_message_id, old_text = existing
        if (old_text or "") == new_text:
            self._logger.info(
                "edit_no_text_change_skipped | chat=%s topic=%s msg_id=%s raw_message_id=%s",
                source_chat_id,
                topic_id,
                message.id,
                raw_message_id,
            )
            return

        run_context = self._edit_run_context(message)

        if self._chain_exists_for_message(raw_message_id):
            self._append_edit_revision(
                raw_message_id=raw_message_id,
                message=message,
                source_chat_id=source_chat_id,
                topic_id=topic_id,
                new_text=new_text,
                run_context=run_context,
                applied_to_current=False,
            )
            self._logger.warning(
                "edit_of_executed_signal_skipped | chat=%s topic=%s msg_id=%s raw_message_id=%s "
                "— una trade chain esiste già, correzione da gestire manualmente",
                source_chat_id,
                topic_id,
                message.id,
                raw_message_id,
            )
            self._emit_edit_skipped_notification(
                message=message,
                source_chat_id=source_chat_id,
                topic_id=topic_id,
                raw_message_id=raw_message_id,
                new_text=new_text,
            )
            return

        if self._is_blacklisted(new_text, chat_id_raw, topic_id):
            self._logger.info(
                "edit_blacklisted_skipped | chat=%s topic=%s msg_id=%s",
                source_chat_id,
                topic_id,
                message.id,
            )
            return

        self._append_edit_revision(
            raw_message_id=raw_message_id,
            message=message,
            source_chat_id=source_chat_id,
            topic_id=topic_id,
            new_text=new_text,
            run_context=run_context,
            applied_to_current=True,
        )
        self._raw_repo.update_message_content(
            raw_message_id,
            raw_text=new_text,
            message_presentation_type=_resolve_message_presentation_type(message),
        )
        self._status_store.update(raw_message_id, "pending")
        reply_to_message_id = extract_real_reply_to_message_id(
            message,
            source_topic_id=topic_id,
        )
        await self._queue.put(
            _QueueItem(
                raw_message_id=raw_message_id,
                source_chat_id=source_chat_id,
                telegram_message_id=int(message.id),
                raw_text=new_text,
                source_trader_id=None,
                reply_to_message_id=reply_to_message_id,
                acquisition_mode="edit",
                source_topic_id=topic_id,
                run_context=run_context,
            )
        )
        self._logger.info(
            "edited message re-enqueued | chat=%s topic=%s msg_id=%s raw_message_id=%s run_context=%s",
            source_chat_id,
            topic_id,
            message.id,
            raw_message_id,
            run_context,
        )

    async def _handle_deleted_message(self, event: events.MessageDeleted.Event) -> None:
        chat_id_raw = int(event.chat_id) if getattr(event, "chat_id", None) is not None else None
        if chat_id_raw is None:
            return

        deleted_ids = list(getattr(event, "deleted_ids", []) or [])
        if not deleted_ids:
            return

        source_chat_id = str(chat_id_raw)
        deleted_at = getattr(event, "deleted_at", None) or datetime.now(timezone.utc)
        run_context = self._delete_run_context(deleted_at)

        for telegram_message_id in deleted_ids:
            existing = self._raw_repo.get_id_and_text(source_chat_id, int(telegram_message_id))
            if existing is None:
                continue

            raw_message_id, old_text = existing
            envelope = self._raw_repo.get_by_id(raw_message_id)
            self._append_deleted_revision(
                raw_message_id=raw_message_id,
                source_chat_id=source_chat_id,
                telegram_message_id=int(telegram_message_id),
                raw_text=old_text,
                message_ts=envelope.message_ts.isoformat(),
                run_context=run_context,
                acquisition_status=envelope.acquisition_status,
                reply_to_message_id=envelope.reply_to_message_id,
                source_topic_id=envelope.source_topic_id,
                has_media=envelope.has_media,
                media_kind=envelope.media_kind,
                media_mime_type=envelope.media_mime_type,
                media_filename=envelope.media_filename,
            )
            self._logger.info(
                "deleted message observed | chat=%s topic=%s msg_id=%s raw_message_id=%s run_context=%s",
                source_chat_id,
                envelope.source_topic_id,
                telegram_message_id,
                raw_message_id,
                run_context,
            )

    def _emit_edit_skipped_notification(
        self,
        *,
        message: Message,
        source_chat_id: str,
        topic_id: int | None,
        raw_message_id: int,
        new_text: str,
    ) -> None:
        if self._notify_edit_skipped is None:
            return
        edit_ts = getattr(message, "edit_date", None)
        try:
            self._notify_edit_skipped(
                {
                    "chat": source_chat_id,
                    "topic": topic_id,
                    "msg_id": int(message.id),
                    "raw_message_id": raw_message_id,
                    "edit_ts": int(_as_utc(edit_ts).timestamp()) if edit_ts else None,
                    "new_text_preview": new_text[:120],
                }
            )
        except Exception:
            self._logger.exception(
                "edit skipped notification failed | raw_message_id=%s", raw_message_id
            )

    def _chain_exists_for_message(self, raw_message_id: int) -> bool:
        if self._chain_exists_for_raw is None:
            # Senza accesso alle chain non possiamo escludere un'esecuzione già
            # avvenuta: fail-safe, non si riprocessa.
            return True
        try:
            return self._chain_exists_for_raw(raw_message_id)
        except Exception:
            self._logger.exception(
                "chain lookup failed | raw_message_id=%s — edit non riprocessato (fail-safe)",
                raw_message_id,
            )
            return True

    def _append_edit_revision(
        self,
        *,
        raw_message_id: int,
        message: Message,
        source_chat_id: str,
        topic_id: int | None,
        new_text: str,
        run_context: str,
        applied_to_current: bool,
    ) -> None:
        if self._revision_store is None:
            return
        edit_ts = getattr(message, "edit_date", None)
        self._revision_store.append_edit(
            raw_message_id=raw_message_id,
            source_chat_id=source_chat_id,
            telegram_message_id=int(message.id),
            raw_text=new_text,
            message_ts=_as_utc(message.date or datetime.now(timezone.utc)).isoformat(),
            run_context=run_context,
            telegram_edit_ts=_as_utc(edit_ts).isoformat() if edit_ts else None,
            acquisition_status="ACQUIRED_ELIGIBLE",
            reply_to_message_id=extract_real_reply_to_message_id(
                message,
                source_topic_id=topic_id,
            ),
            source_topic_id=topic_id,
            has_media=bool(getattr(message, "media", None)),
            media_kind=None,
            media_mime_type=None,
            media_filename=None,
            applied_to_current=applied_to_current,
        )

    def _append_deleted_revision(
        self,
        *,
        raw_message_id: int,
        source_chat_id: str,
        telegram_message_id: int,
        raw_text: str | None,
        message_ts: str,
        run_context: str,
        acquisition_status: str | None,
        reply_to_message_id: int | None,
        source_topic_id: int | None,
        has_media: bool,
        media_kind: str | None,
        media_mime_type: str | None,
        media_filename: str | None,
    ) -> None:
        if self._revision_store is None:
            return
        self._revision_store.append_deleted(
            raw_message_id=raw_message_id,
            source_chat_id=source_chat_id,
            telegram_message_id=telegram_message_id,
            raw_text=raw_text,
            message_ts=message_ts,
            run_context=run_context,
            acquisition_status=acquisition_status,
            reply_to_message_id=reply_to_message_id,
            source_topic_id=source_topic_id,
            has_media=has_media,
            media_kind=media_kind,
            media_mime_type=media_mime_type,
            media_filename=media_filename,
            applied_to_current=False,
        )

    def _edit_run_context(self, message: Message) -> str:
        edit_ts = getattr(message, "edit_date", None) or datetime.now(timezone.utc)
        return f"edit:{int(_as_utc(edit_ts).timestamp())}"

    def _delete_run_context(self, deleted_at: datetime) -> str:
        return f"delete:{int(_as_utc(deleted_at).timestamp())}"

    async def _ingest_and_enqueue(
        self,
        *,
        message: Message,
        chat_id: int | None,
        chat_title: str | None,
        chat_username: str | None,
        acquisition_mode: str,
        source_topic_id: int | None = None,
    ) -> None:
        source_chat_id = str(chat_id) if chat_id is not None else "unknown"
        raw_text = message.message or ""

        if self._is_blacklisted(raw_text, chat_id, source_topic_id):
            self._logger.info(
                "blacklisted | chat=%s topic=%s msg_id=%s text_start=%.80r",
                source_chat_id,
                source_topic_id,
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
                    source_topic_id=source_topic_id,
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
                source_topic_id=source_topic_id,
            )
        )
        if not ingestion.saved and ingestion.raw_message_id is not None:
            self._logger.info(
                "duplicate skipped | chat=%s topic=%s msg_id=%s",
                source_chat_id,
                source_topic_id,
                message.id,
            )
            return
        if ingestion.raw_message_id is None:
            self._logger.warning(
                "ingest failed | chat=%s topic=%s msg_id=%s",
                source_chat_id,
                source_topic_id,
                message.id,
            )
            return

        reply_to_message_id = extract_real_reply_to_message_id(
            message,
            source_topic_id=source_topic_id,
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
                source_topic_id=source_topic_id,
            )
        )
        self._logger.info(
            "raw acquired | chat=%s topic=%s msg_id=%s mode=%s raw_message_id=%s",
            source_chat_id,
            source_topic_id,
            message.id,
            acquisition_mode,
            ingestion.raw_message_id,
        )

    def _process_item(self, item: _QueueItem) -> None:
        entry = self._channel_resolver.lookup(item.source_chat_id, item.source_topic_id)
        if entry is None or not entry.active:
            self._logger.debug(
                "no active channel entry | raw_message_id=%s chat=%s topic=%s",
                item.raw_message_id,
                item.source_chat_id,
                item.source_topic_id,
            )
            return

        envelope = self._raw_repo.get_by_id(item.raw_message_id)

        resolved = self._trader_resolver.resolve(envelope)
        resolved = resolved.model_copy(update={"raw_message_id": item.raw_message_id})

        self._raw_repo.update_trader_resolution(item.raw_message_id, resolved)

        if resolved.is_ambiguous or resolved.trader_id is None:
            self._logger.info(
                "trader unresolved | raw_message_id=%s method=%s",
                item.raw_message_id,
                resolved.method,
            )
            self._raw_repo.update_processing_status(item.raw_message_id, "review")
            return

        parser_profile = entry.parser_profile if entry.parser_profile else resolved.trader_id

        raw_context = RawContext(
            raw_text=envelope.raw_text or "",
            message_id=envelope.telegram_message_id,
            reply_to_message_id=envelope.reply_to_message_id,
            source_chat_id=envelope.source_chat_id,
            source_topic_id=envelope.source_topic_id,
        )
        parser_context = ParserContext(
            raw_context=raw_context,
            message_id=envelope.telegram_message_id,
            reply_to_message_id=envelope.reply_to_message_id,
            source_chat_id=envelope.source_chat_id,
            source_topic_id=envelope.source_topic_id,
        )
        candidate = ParserDispatchCandidate(
            raw_message=envelope,
            resolved_trader=resolved,
            parser_profile=parser_profile,
            parser_context=parser_context,
        )

        result = self._parser_pipeline.process(candidate, run_context=item.run_context)
        if isinstance(result, ParserJobStatus):
            self._logger.warning(
                "parse failed | raw_message_id=%s reason=%s",
                item.raw_message_id,
                result.reason,
            )
        else:
            self._logger.info(
                "parsed | raw_message_id=%s canonical_id=%s class=%s status=%s trader=%s",
                item.raw_message_id,
                result.canonical_message_id,
                result.primary_class,
                result.parse_status,
                resolved.trader_id,
            )
            enriched = self._enrichment_processor.process(result)
            self._logger.info(
                "enriched | canonical_id=%s decision=%s reason=%s lifecycle_processed=%s",
                enriched.canonical_message_id,
                enriched.enrichment_decision,
                enriched.reason_code,
                enriched.lifecycle_processed,
            )

    def _is_allowed_message(self, chat_id: int | None, topic_id: int | None) -> bool:
        if chat_id is None:
            return False
        if self._fallback_ids:
            return chat_id in self._fallback_ids
        if not self._config.channels:
            return True  # no channels configured at all → open mode
        entry = self._config.match_entry(chat_id, topic_id)
        return entry is not None and entry.active

    def _is_blacklisted(self, raw_text: str, chat_id: int | None, topic_id: int | None = None) -> bool:
        return _is_blacklisted_text(self._config, raw_text, chat_id, topic_id)

    def _extract_topic_id(self, chat_id: int | None, message: Message) -> int | None:
        if chat_id is None:
            return extract_message_topic_id(message)
        entries = [entry for entry in self._config.active_channels if entry.chat_id == chat_id]
        return extract_message_topic_id(message, known_topic_ids=_known_topic_ids(entries))


def _is_media_only(message: Message) -> bool:
    return message.media is not None and not bool(message.message)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_message_presentation_type(message: Message) -> str:
    return "INLINE_BUTTONS" if getattr(message, "reply_markup", None) is not None else "PLAIN"


def _build_incoming(
    *,
    message: Message,
    source_chat_id: str,
    chat_title: str | None,
    chat_username: str | None,
    trader_id: str | None,
    acquisition_status: str,
    source_topic_id: int | None = None,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        source_chat_id=source_chat_id,
        source_chat_title=chat_title,
        source_type=_resolve_source_type(chat_title, chat_username),
        source_trader_id=trader_id,
        telegram_message_id=int(message.id),
        reply_to_message_id=extract_real_reply_to_message_id(
            message,
            source_topic_id=source_topic_id,
        ),
        raw_text=message.message,
        message_ts=message.date or datetime.now(timezone.utc),
        acquisition_status=acquisition_status,
        source_topic_id=source_topic_id,
        message_presentation_type=_resolve_message_presentation_type(message),
    )


def _resolve_source_type(chat_title: str | None, chat_username: str | None) -> str | None:
    if chat_title:
        return "channel"
    if chat_username:
        return "user"
    return None


def _known_topic_ids(entries: Iterable[object]) -> set[int]:
    return {
        topic_id
        for entry in entries
        for topic_id in [getattr(entry, "topic_id", None)]
        if isinstance(topic_id, int)
    }
