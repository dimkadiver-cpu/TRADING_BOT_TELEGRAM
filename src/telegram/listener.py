"""Telegram listener.

Receives Telegram messages and sends them to raw ingestion service.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Iterable

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.registry import get_profile_parser
from src.storage.parse_results import ParseResultRecord, ParseResultStore
from src.storage.raw_messages import RawMessageStore
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.telegram.ingestion import (
    RawMessageIngestionService,
    TelegramIncomingMessage,
)
from src.telegram.trader_mapping import TelegramSourceTraderMapper

_SIGNAL_ID_RE = re.compile(r"\bSIGNAL\s*ID\s*:\s*#?\s*(?P<id>\d+)\b", re.IGNORECASE)


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


def register_message_listener(
    client: TelegramClient,
    ingestion_service: RawMessageIngestionService,
    effective_trader_resolver: EffectiveTraderResolver,
    eligibility_evaluator: MessageEligibilityEvaluator,
    parse_results_store: ParseResultStore,
    logger: logging.Logger,
    allowed_chat_ids: Iterable[int] | None = None,
) -> None:
    allowed = set(allowed_chat_ids or [])

    @client.on(events.NewMessage)
    async def _on_message(event: events.NewMessage.Event) -> None:
        chat_id = int(event.chat_id) if event.chat_id is not None else None
        if allowed and (chat_id is None or chat_id not in allowed):
            return

        message: Message = event.message
        source_chat_id = str(chat_id) if chat_id is not None else "unknown"
        source_chat_title = getattr(event.chat, "title", None) or getattr(event.chat, "username", None)
        source_chat_username = getattr(event.chat, "username", None)
        source_type = _resolve_source_type(event)
        reply_to_message_id = None
        if message.reply_to and getattr(message.reply_to, "reply_to_msg_id", None):
            reply_to_message_id = int(message.reply_to.reply_to_msg_id)
        trader_resolution = effective_trader_resolver.resolve(
            EffectiveTraderContext(
                source_chat_id=source_chat_id,
                source_chat_username=source_chat_username,
                source_chat_title=source_chat_title,
                raw_text=message.message,
                reply_to_message_id=reply_to_message_id,
            )
        )
        if trader_resolution.trader_id is None:
            logger.warning(
                "effective trader unresolved | chat=%s username=%s title=%s msg_id=%s method=%s",
                source_chat_id,
                source_chat_username,
                source_chat_title,
                message.id,
                trader_resolution.method,
            )
        else:
            logger.info(
                "effective trader resolved | chat=%s trader=%s method=%s detail=%s",
                source_chat_id,
                trader_resolution.trader_id,
                trader_resolution.method,
                trader_resolution.detail,
            )
        eligibility = eligibility_evaluator.evaluate(
            source_chat_id=source_chat_id,
            raw_text=message.message,
            reply_to_message_id=reply_to_message_id,
        )
        if not eligibility.is_eligible:
            logger.warning(
                "message not eligible for auto-apply | chat=%s msg_id=%s reason=%s",
                source_chat_id,
                message.id,
                eligibility.reason,
            )
        elif eligibility.strong_link_method:
            logger.info(
                "message strong-link detected | chat=%s msg_id=%s method=%s ref=%s",
                source_chat_id,
                message.id,
                eligibility.strong_link_method,
                eligibility.referenced_message_id,
            )

        acquisition_status = eligibility.status
        eligibility_reason = eligibility.reason
        if trader_resolution.trader_id is None:
            acquisition_status = "ACQUIRED_UNKNOWN_TRADER"
            eligibility_reason = f"{eligibility_reason}; unresolved_trader"

        incoming = TelegramIncomingMessage(
            source_chat_id=source_chat_id,
            source_chat_title=source_chat_title,
            source_type=source_type,
            source_trader_id=trader_resolution.trader_id,
            telegram_message_id=int(message.id),
            reply_to_message_id=reply_to_message_id,
            raw_text=message.message,
            message_ts=message.date or datetime.now(timezone.utc),
            acquisition_status=acquisition_status,
        )
        ingestion = ingestion_service.ingest(incoming)
        if ingestion.saved:
            logger.info(
                "raw message acquired | chat=%s msg_id=%s",
                source_chat_id,
                message.id,
            )
        if ingestion.raw_message_id is None:
            logger.warning(
                "parse skipped: raw_message_id missing | chat=%s msg_id=%s",
                source_chat_id,
                message.id,
            )
            return

        reply_raw_text = None
        if reply_to_message_id is not None:
            parent = ingestion_service.store.get_by_source_and_message_id(source_chat_id, reply_to_message_id)
            if parent is not None:
                reply_raw_text = parent.raw_text
        if reply_raw_text is None:
            signal_id = _extract_signal_id(message.message or "")
            if signal_id is not None:
                reply_raw_text = parse_results_store.get_raw_text_by_signal_id(
                    resolved_trader_id=trader_resolution.trader_id or "",
                    signal_id=signal_id,
                )

        now_ts = datetime.now(timezone.utc).isoformat()
        raw_text = message.message or ""
        trader_code = trader_resolution.trader_id or ""
        profile_parser = get_profile_parser(trader_code)

        if profile_parser is None:
            parse_record = _build_skipped_record(
                raw_message_id=ingestion.raw_message_id,
                resolved_trader_id=trader_resolution.trader_id,
                trader_resolution_method=trader_resolution.method,
                acquisition_status=acquisition_status,
                eligibility_reason=eligibility_reason,
                linkage_method=eligibility.strong_link_method,
                now_ts=now_ts,
            )
        else:
            context = ParserContext(
                trader_code=trader_code,
                message_id=int(message.id),
                reply_to_message_id=reply_to_message_id,
                channel_id=source_chat_id,
                raw_text=raw_text,
                reply_raw_text=reply_raw_text,
                extracted_links=[],
                hashtags=[],
            )
            result = profile_parser.parse_message(text=raw_text, context=context)
            parse_record = _build_parse_result_record(
                result=result,
                raw_message_id=ingestion.raw_message_id,
                resolved_trader_id=trader_resolution.trader_id,
                trader_resolution_method=trader_resolution.method,
                acquisition_status=acquisition_status,
                eligibility_reason=eligibility_reason,
                linkage_method=eligibility.strong_link_method,
                now_ts=now_ts,
            )

        parse_results_store.upsert(parse_record)
        logger.info(
            "parse result persisted | raw_message_id=%s type=%s executable=%s",
            ingestion.raw_message_id,
            parse_record.message_type,
            parse_record.is_executable,
        )


def _build_parse_result_record(
    *,
    result: TraderParseResult,
    raw_message_id: int,
    resolved_trader_id: str | None,
    trader_resolution_method: str,
    acquisition_status: str,
    eligibility_reason: str,
    linkage_method: str | None,
    now_ts: str,
) -> ParseResultRecord:
    entities = result.entities or {}
    completeness = "INCOMPLETE" if result.message_type == "SETUP_INCOMPLETE" else "COMPLETE"
    normalized_json = json.dumps(
        {
            "message_type": result.message_type,
            "intents": result.intents,
            "entities": result.entities,
            "target_refs": result.target_refs,
            "actions_structured": result.actions_structured,
            "warnings": result.warnings,
            "confidence": result.confidence,
        },
        ensure_ascii=False,
        default=str,
    )
    return ParseResultRecord(
        raw_message_id=raw_message_id,
        eligibility_status=acquisition_status,
        eligibility_reason=eligibility_reason,
        declared_trader_tag=None,
        resolved_trader_id=resolved_trader_id,
        trader_resolution_method=trader_resolution_method,
        message_type=result.message_type,
        parse_status="PARSED",
        completeness=completeness,
        is_executable=result.message_type == "NEW_SIGNAL" and completeness == "COMPLETE",
        symbol=entities.get("symbol") or None,
        direction=entities.get("side") or None,
        entry_raw=entities.get("entry_raw") or None,
        stop_raw=entities.get("stop_raw") or None,
        target_raw_list=None,
        leverage_hint=None,
        risk_hint=None,
        risky_flag=False,
        linkage_method=linkage_method,
        linkage_status=None,
        warning_text=" | ".join(result.warnings) if result.warnings else None,
        notes=None,
        parse_result_normalized_json=normalized_json,
        created_at=now_ts,
        updated_at=now_ts,
    )


def _build_skipped_record(
    *,
    raw_message_id: int,
    resolved_trader_id: str | None,
    trader_resolution_method: str,
    acquisition_status: str,
    eligibility_reason: str,
    linkage_method: str | None,
    now_ts: str,
) -> ParseResultRecord:
    return ParseResultRecord(
        raw_message_id=raw_message_id,
        eligibility_status=acquisition_status,
        eligibility_reason=eligibility_reason,
        declared_trader_tag=None,
        resolved_trader_id=resolved_trader_id,
        trader_resolution_method=trader_resolution_method,
        message_type="UNCLASSIFIED",
        parse_status="SKIPPED",
        completeness="COMPLETE",
        is_executable=False,
        symbol=None,
        direction=None,
        entry_raw=None,
        stop_raw=None,
        target_raw_list=None,
        leverage_hint=None,
        risk_hint=None,
        risky_flag=False,
        linkage_method=linkage_method,
        linkage_status=None,
        warning_text="no_profile_parser",
        notes=None,
        parse_result_normalized_json=None,
        created_at=now_ts,
        updated_at=now_ts,
    )


def _resolve_source_type(event: events.NewMessage.Event) -> str | None:
    chat = event.chat
    if chat is None:
        return None
    if getattr(chat, "broadcast", False):
        return "channel"
    if getattr(chat, "megagroup", False):
        return "supergroup"
    if getattr(chat, "username", None) is not None and getattr(chat, "broadcast", None) is None:
        return "user"
    return chat.__class__.__name__.lower()


def _extract_signal_id(raw_text: str) -> int | None:
    match = _SIGNAL_ID_RE.search(raw_text or "")
    if not match:
        return None
    try:
        return int(match.group("id"))
    except ValueError:
        return None
