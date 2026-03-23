"""Message router that prepares parser input and owns processing lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.registry import get_profile_parser
from src.storage.parse_results import ParseResultRecord, ParseResultStore
from src.storage.processing_status import ProcessingStatusStore
from src.storage.raw_messages import RawMessageStore
from src.storage.review_queue import ReviewQueueStore
from src.telegram.channel_config import ChannelsConfig
from src.telegram.effective_trader import (
    EffectiveTraderContext,
    EffectiveTraderResolver,
    EffectiveTraderResult,
)
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.validation.coherence import ValidationResult, validate as _validate_result

_SIGNAL_ID_RE = re.compile(r"\bSIGNAL\s*ID\s*:\s*#?\s*(?P<id>\d+)\b", re.IGNORECASE)
_HTTP_LINK_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_TELEGRAM_LINK_RE = re.compile(r"\bt\.me/[^\s]+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"(?<!\w)(#[A-Za-z0-9_]+)")


@dataclass(slots=True)
class QueueItem:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    raw_text: str
    source_trader_id: str | None
    reply_to_message_id: int | None
    acquisition_mode: str


def is_blacklisted_text(config: ChannelsConfig, raw_text: str, chat_id: int | None) -> bool:
    text_lower = raw_text.lower()
    for tag in config.blacklist_global:
        if tag.lower() in text_lower:
            return True
    if chat_id is not None:
        channel = config.channel_for(chat_id)
        if channel is not None:
            for tag in channel.blacklist:
                if tag.lower() in text_lower:
                    return True
    return False


class MessageRouter:
    def __init__(
        self,
        *,
        effective_trader_resolver: EffectiveTraderResolver,
        eligibility_evaluator: MessageEligibilityEvaluator,
        parse_results_store: ParseResultStore,
        processing_status_store: ProcessingStatusStore,
        review_queue_store: ReviewQueueStore,
        raw_message_store: RawMessageStore,
        logger: logging.Logger,
        channels_config: ChannelsConfig,
    ) -> None:
        self._trader_resolver = effective_trader_resolver
        self._eligibility = eligibility_evaluator
        self._parse_results = parse_results_store
        self._status_store = processing_status_store
        self._review_queue = review_queue_store
        self._raw_store = raw_message_store
        self._logger = logger
        self._config = channels_config

    def update_config(self, new_config: ChannelsConfig) -> None:
        self._config = new_config

    def route(self, item: QueueItem) -> None:
        self._status_store.update(item.raw_message_id, "processing")
        try:
            self._route_inner(item)
        except Exception:
            self._status_store.update(item.raw_message_id, "failed")
            self._logger.exception(
                "router_failed | raw_message_id=%s chat_id=%s telegram_message_id=%s text_start=%.200r",
                item.raw_message_id,
                item.source_chat_id,
                item.telegram_message_id,
                item.raw_text,
            )

    def _route_inner(self, item: QueueItem) -> None:
        now_ts = datetime.now(timezone.utc).isoformat()
        chat_id_int = _safe_int(item.source_chat_id)

        if is_blacklisted_text(self._config, item.raw_text, chat_id_int):
            self._status_store.update(item.raw_message_id, "blacklisted")
            self._logger.info(
                "blacklisted | chat_id=%s telegram_message_id=%s raw_message_id=%s",
                item.source_chat_id,
                item.telegram_message_id,
                item.raw_message_id,
            )
            return

        trader_resolution = self._resolve_trader(item)
        eligibility = self._eligibility.evaluate(
            source_chat_id=item.source_chat_id,
            raw_text=item.raw_text,
            reply_to_message_id=item.reply_to_message_id,
        )

        acquisition_status = eligibility.status
        eligibility_reason = eligibility.reason

        if trader_resolution.trader_id is None:
            acquisition_status = "ACQUIRED_UNKNOWN_TRADER"
            eligibility_reason = f"{eligibility_reason}; unresolved_trader"
            self._review_queue.insert(item.raw_message_id, "unresolved_trader")
            self._status_store.update(item.raw_message_id, "review")
            self._logger.warning(
                "trader_unresolved | chat_id=%s telegram_message_id=%s raw_message_id=%s method=%s",
                item.source_chat_id,
                item.telegram_message_id,
                item.raw_message_id,
                trader_resolution.method,
            )
            return

        if self._is_inactive_channel(chat_id_int):
            self._status_store.update(item.raw_message_id, "done")
            self._logger.info(
                "trader_inactive | trader_id=%s chat_id=%s telegram_message_id=%s",
                trader_resolution.trader_id,
                item.source_chat_id,
                item.telegram_message_id,
            )
            return

        reply_raw_text = self._resolve_reply_raw_text(
            source_chat_id=item.source_chat_id,
            reply_to_message_id=item.reply_to_message_id,
            raw_text=item.raw_text,
            trader_code=trader_resolution.trader_id,
        )

        profile_parser = get_profile_parser(trader_resolution.trader_id)
        if profile_parser is None:
            parse_record = _build_skipped_record(
                raw_message_id=item.raw_message_id,
                resolved_trader_id=trader_resolution.trader_id,
                trader_resolution_method=trader_resolution.method,
                acquisition_status=acquisition_status,
                eligibility_reason=eligibility_reason,
                linkage_method=eligibility.strong_link_method,
                acquisition_mode=item.acquisition_mode,
                now_ts=now_ts,
            )
            self._parse_results.upsert(parse_record)
            self._status_store.update(item.raw_message_id, "done")
            return

        context = ParserContext(
            trader_code=trader_resolution.trader_id,
            message_id=item.telegram_message_id,
            reply_to_message_id=item.reply_to_message_id,
            channel_id=item.source_chat_id,
            raw_text=item.raw_text,
            reply_raw_text=reply_raw_text,
            extracted_links=_extract_links(item.raw_text),
            hashtags=_extract_hashtags(item.raw_text),
        )
        result = profile_parser.parse_message(text=item.raw_text, context=context)
        validation = _validate_result(result)
        if validation.status == "STRUCTURAL_ERROR":
            self._logger.warning(
                "validation_structural_error | raw_message_id=%s type=%s errors=%s",
                item.raw_message_id,
                result.message_type,
                validation.errors,
            )
        parse_record = _build_parse_result_record(
            result=result,
            validation=validation,
            raw_message_id=item.raw_message_id,
            resolved_trader_id=trader_resolution.trader_id,
            trader_resolution_method=trader_resolution.method,
            acquisition_status=acquisition_status,
            eligibility_reason=eligibility_reason,
            linkage_method=eligibility.strong_link_method,
            acquisition_mode=item.acquisition_mode,
            now_ts=now_ts,
        )
        self._parse_results.upsert(parse_record)
        self._status_store.update(item.raw_message_id, "done")
        self._logger.info(
            "parse result persisted | raw_message_id=%s type=%s executable=%s validation=%s mode=%s",
            item.raw_message_id,
            parse_record.message_type,
            parse_record.is_executable,
            validation.status,
            item.acquisition_mode,
        )

    def _resolve_trader(self, item: QueueItem) -> EffectiveTraderResult:
        trader_resolution = self._trader_resolver.resolve(
            EffectiveTraderContext(
                source_chat_id=item.source_chat_id,
                source_chat_username=None,
                source_chat_title=None,
                raw_text=item.raw_text,
                reply_to_message_id=item.reply_to_message_id,
            )
        )
        if trader_resolution.trader_id is not None:
            return trader_resolution

        channel = self._config.channel_for(_safe_int(item.source_chat_id))
        if channel is not None and channel.trader_id:
            return EffectiveTraderResult(
                trader_id=channel.trader_id,
                method="channels_yaml",
                detail=channel.label,
            )
        return trader_resolution

    def _is_inactive_channel(self, chat_id: int | None) -> bool:
        if chat_id is None:
            return False
        channel = self._config.channel_for(chat_id)
        if channel is None:
            return False
        return not channel.active

    def _resolve_reply_raw_text(
        self,
        *,
        source_chat_id: str,
        reply_to_message_id: int | None,
        raw_text: str,
        trader_code: str,
    ) -> str | None:
        if reply_to_message_id is not None:
            parent = self._raw_store.get_by_source_and_message_id(source_chat_id, reply_to_message_id)
            if parent is not None:
                return parent.raw_text
        signal_id = _extract_signal_id(raw_text)
        if signal_id is None:
            return None
        return self._parse_results.get_raw_text_by_signal_id(
            resolved_trader_id=trader_code,
            signal_id=signal_id,
        )


def _safe_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _extract_links(raw_text: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for pattern in (_HTTP_LINK_RE, _TELEGRAM_LINK_RE):
        for match in pattern.finditer(raw_text):
            link = match.group(0)
            normalized = link if link.startswith("http") else f"https://{link}"
            if normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
    return values


def _extract_hashtags(raw_text: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for match in _HASHTAG_RE.finditer(raw_text):
        tag = match.group(1)
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        values.append(tag)
    return values


def _extract_signal_id(raw_text: str) -> int | None:
    match = _SIGNAL_ID_RE.search(raw_text or "")
    if not match:
        return None
    try:
        return int(match.group("id"))
    except ValueError:
        return None


def _build_parse_result_record(
    *,
    result: TraderParseResult,
    validation: ValidationResult,
    raw_message_id: int,
    resolved_trader_id: str | None,
    trader_resolution_method: str,
    acquisition_status: str,
    eligibility_reason: str,
    linkage_method: str | None,
    acquisition_mode: str,
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
            "acquisition_mode": acquisition_mode,
            **validation.to_dict(),
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
    acquisition_mode: str,
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
        parse_result_normalized_json=json.dumps({"acquisition_mode": acquisition_mode}),
        created_at=now_ts,
        updated_at=now_ts,
    )
