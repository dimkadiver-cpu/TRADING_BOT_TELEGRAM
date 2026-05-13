"""Shadow sidecar: runs runtime_v2 pipeline alongside the legacy router."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate, ResolvedTraderContext


class RuntimeV2ListenerSidecar:
    """Processes a QueueItem through the runtime_v2 parser pipeline in shadow mode.

    Runs after the legacy router in _process_item(). Activated by USE_RUNTIME_V2=1.
    Never raises — all exceptions are caught and logged.
    """

    def __init__(
        self,
        *,
        db_path: str,
        channels_config_path: str,
        logger: logging.Logger,
    ) -> None:
        self._logger = logger
        self._channel_resolver = ChannelConfigResolver(config_path=channels_config_path)
        self._raw_repo = RawMessageRepository(db_path=db_path)
        self._processor = ParserPipelineProcessor(
            canonical_repo=CanonicalMessageRepository(db_path=db_path)
        )

    def reload_config(self) -> None:
        self._channel_resolver.reload()

    def process_queue_item(self, item: object) -> None:
        try:
            self._process(item)
        except Exception:
            self._logger.exception(
                "runtime_v2 sidecar error | raw_message_id=%s",
                getattr(item, "raw_message_id", "?"),
            )

    def _process(self, item: object) -> None:
        source_chat_id: str = getattr(item, "source_chat_id", "")
        source_topic_id: int | None = getattr(item, "source_topic_id", None)
        raw_message_id: int = getattr(item, "raw_message_id", 0)

        entry = self._channel_resolver.lookup(source_chat_id, source_topic_id)
        if entry is None or not entry.active:
            return

        envelope = self._raw_repo.get_by_id(raw_message_id)

        resolved = ResolvedTraderContext(
            raw_message_id=raw_message_id,
            trader_id=entry.trader_id,
            method="source_chat_id",
            detail=None,
            is_ambiguous=False,
            resolved_at=datetime.now(timezone.utc),
        )

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
            parser_profile=entry.parser_profile,
            parser_context=parser_context,
        )

        result = self._processor.process(candidate)
        if isinstance(result, ParserJobStatus):
            self._logger.warning(
                "runtime_v2: parse failed | raw_message_id=%s reason=%s",
                raw_message_id,
                result.reason,
            )
        else:
            self._logger.info(
                "runtime_v2: parsed | raw_message_id=%s profile=%s class=%s status=%s canonical_id=%s",
                raw_message_id,
                result.parser_profile,
                result.primary_class,
                result.parse_status,
                result.canonical_message_id,
            )
