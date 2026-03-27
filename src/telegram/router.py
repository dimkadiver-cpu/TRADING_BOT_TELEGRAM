"""Message router that prepares parser input and owns processing lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from src.operation_rules.engine import OperationRulesEngine
from src.parser.models.operational import OperationalSignal, ResolvedTarget
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.registry import get_profile_parser
from src.storage.operational_signals_store import (
    OperationalSignalRecord,
    OperationalSignalsStore,
)
from src.storage.parse_results import ParseResultRecord, ParseResultStore
from src.storage.processing_status import ProcessingStatusStore
from src.storage.raw_messages import RawMessageStore
from src.storage.review_queue import ReviewQueueStore
from src.storage.signals_store import SignalRecord, SignalsStore
from src.target_resolver.resolver import TargetResolver
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

_ENV_DEFAULT = "T"


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
        # Layer 4+5 — optional; if None, Phase 4 pipeline is skipped
        db_path: str | None = None,
        operation_rules_engine: OperationRulesEngine | None = None,
        target_resolver: TargetResolver | None = None,
        signals_store: SignalsStore | None = None,
        operational_signals_store: OperationalSignalsStore | None = None,
    ) -> None:
        self._trader_resolver = effective_trader_resolver
        self._eligibility = eligibility_evaluator
        self._parse_results = parse_results_store
        self._status_store = processing_status_store
        self._review_queue = review_queue_store
        self._raw_store = raw_message_store
        self._logger = logger
        self._config = channels_config

        # Layer 4+5
        self._db_path = db_path
        self._engine = operation_rules_engine
        self._resolver = target_resolver
        self._signals_store = signals_store
        self._op_signals_store = operational_signals_store

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

        # ── Layer 4+5 — only when validation is VALID and all stores wired ──
        if validation.status == "VALID" and self._engine is not None and self._db_path is not None:
            self._apply_phase4(
                item=item,
                result=result,
                trader_id=trader_resolution.trader_id,
                now_ts=now_ts,
            )

        self._status_store.update(item.raw_message_id, "done")
        self._logger.info(
            "parse result persisted | raw_message_id=%s type=%s executable=%s validation=%s mode=%s",
            item.raw_message_id,
            parse_record.message_type,
            parse_record.is_executable,
            validation.status,
            item.acquisition_mode,
        )

    # ------------------------------------------------------------------
    # Phase 4 integration
    # ------------------------------------------------------------------

    def _apply_phase4(
        self,
        *,
        item: QueueItem,
        result: TraderParseResult,
        trader_id: str,
        now_ts: str,
    ) -> None:
        """Apply operation rules + target resolver and persist output."""
        assert self._engine is not None
        assert self._db_path is not None

        db_path = self._db_path

        # Step 1 — Apply operation rules
        op_signal = self._engine.apply(result, trader_id, db_path=db_path)

        # Step 2 — If NEW_SIGNAL and not blocked: INSERT into signals
        attempt_key: str | None = None
        if result.message_type == "NEW_SIGNAL" and not op_signal.is_blocked:
            attempt_key = _build_attempt_key(
                env=_ENV_DEFAULT,
                channel_id=item.source_chat_id,
                telegram_msg_id=item.telegram_message_id,
                trader_id=trader_id,
            )
            signal_rec = _build_signal_record(
                op_signal=op_signal,
                attempt_key=attempt_key,
                item=item,
                trader_id=trader_id,
                now_ts=now_ts,
            )
            if self._signals_store is not None:
                self._signals_store.insert(signal_rec)

        # Step 3 — Resolve target
        resolved: ResolvedTarget | None = None
        if self._resolver is not None:
            resolved = self._resolver.resolve(op_signal, db_path=db_path)

        # Step 4 — INSERT into operational_signals
        parse_result_id = None
        if self._op_signals_store is not None:
            parse_result_id = self._op_signals_store.get_parse_result_id(item.raw_message_id)
            if parse_result_id is not None:
                op_rec = _build_op_signal_record(
                    op_signal=op_signal,
                    parse_result_id=parse_result_id,
                    attempt_key=attempt_key,
                    trader_id=trader_id,
                    resolved=resolved,
                    now_ts=now_ts,
                )
                op_signal_id = self._op_signals_store.insert(op_rec)
                self._logger.debug(
                    "operational_signal persisted | op_signal_id=%s attempt_key=%s is_blocked=%s"
                    " target_eligibility=%s",
                    op_signal_id,
                    attempt_key,
                    op_signal.is_blocked,
                    op_rec.target_eligibility,
                )

        self._logger.info(
            "phase4 complete | raw_message_id=%s type=%s is_blocked=%s block_reason=%s"
            " attempt_key=%s target_eligibility=%s",
            item.raw_message_id,
            result.message_type,
            op_signal.is_blocked,
            op_signal.block_reason,
            attempt_key,
            resolved.eligibility if resolved else None,
        )

    # ------------------------------------------------------------------
    # Existing helpers
    # ------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Phase 4 builders
# ---------------------------------------------------------------------------


def _build_attempt_key(
    *,
    env: str,
    channel_id: str,
    telegram_msg_id: int,
    trader_id: str,
) -> str:
    return f"{env}_{channel_id}_{telegram_msg_id}_{trader_id}"


def _build_signal_record(
    *,
    op_signal: OperationalSignal,
    attempt_key: str,
    item: QueueItem,
    trader_id: str,
    now_ts: str,
) -> SignalRecord:
    entities: dict[str, Any] = (
        op_signal.parse_result.entities
        if isinstance(op_signal.parse_result.entities, dict)
        else {}
    )
    symbol = str(entities.get("symbol") or "").strip().upper() or None
    side = str(entities.get("side") or entities.get("direction") or "").strip().upper() or None

    # Build entry_json from entry_split weights and extracted prices
    entry_prices = _extract_entry_prices_from_entities(entities)
    entry_json = json.dumps(
        [{"price": p, "type": "LIMIT"} for p in entry_prices] or [{"price": None, "type": "MARKET"}]
    )

    # SL
    sl = _extract_sl_float(entities)

    # TP list
    tp_prices = _extract_tp_prices(entities)
    tp_json = json.dumps([{"price": p} for p in tp_prices])

    return SignalRecord(
        attempt_key=attempt_key,
        env=_ENV_DEFAULT,
        channel_id=item.source_chat_id,
        root_telegram_id=str(item.telegram_message_id),
        trader_id=trader_id,
        trader_prefix=trader_id.upper()[:4],
        symbol=symbol,
        side=side,
        entry_json=entry_json,
        sl=sl,
        tp_json=tp_json,
        status="PENDING",
        confidence=op_signal.parse_result.confidence,
        raw_text=item.raw_text,
        created_at=now_ts,
        updated_at=now_ts,
    )


def _build_op_signal_record(
    *,
    op_signal: OperationalSignal,
    parse_result_id: int,
    attempt_key: str | None,
    trader_id: str,
    resolved: ResolvedTarget | None,
    now_ts: str,
) -> OperationalSignalRecord:
    return OperationalSignalRecord(
        parse_result_id=parse_result_id,
        attempt_key=attempt_key,
        trader_id=trader_id,
        message_type=op_signal.parse_result.message_type,
        is_blocked=op_signal.is_blocked,
        block_reason=op_signal.block_reason,
        position_size_pct=op_signal.position_size_pct,
        position_size_usdt=op_signal.position_size_usdt,
        entry_split_json=json.dumps(op_signal.entry_split) if op_signal.entry_split else None,
        leverage=op_signal.leverage,
        risk_hint_used=op_signal.risk_hint_used,
        management_rules_json=(
            json.dumps(op_signal.management_rules) if op_signal.management_rules else None
        ),
        price_corrections_json=None,
        applied_rules_json=json.dumps(op_signal.applied_rules),
        warnings_json=json.dumps(op_signal.warnings) if op_signal.warnings else None,
        resolved_target_ids=(
            json.dumps(resolved.position_ids) if resolved is not None else None
        ),
        target_eligibility=resolved.eligibility if resolved is not None else None,
        target_reason=resolved.reason if resolved is not None else None,
        created_at=now_ts,
    )


# ---------------------------------------------------------------------------
# Entity extraction helpers (best-effort, used for signal INSERT)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[\d]+(?:[.,][\d]+)?")


def _parse_first_float(s: str | None) -> float | None:
    if not s:
        return None
    cleaned = str(s).replace(" ", "").replace("\xa0", "")
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _parse_all_floats(s: str | None) -> list[float]:
    if not s:
        return []
    cleaned = str(s).replace(" ", "").replace("\xa0", "")
    results: list[float] = []
    for m in _NUMBER_RE.finditer(cleaned):
        try:
            results.append(float(m.group(0).replace(",", ".")))
        except ValueError:
            pass
    return results


def _extract_entry_prices_from_entities(entities: dict[str, Any]) -> list[float]:
    for key in ("entry_plan_entries", "entries"):
        entries_raw = entities.get(key)
        if isinstance(entries_raw, list) and entries_raw:
            prices: list[float] = []
            for e in entries_raw:
                if isinstance(e, dict):
                    p = e.get("price")
                    if p is not None:
                        try:
                            prices.append(float(p))
                        except (TypeError, ValueError):
                            pass
                elif isinstance(e, (int, float)):
                    prices.append(float(e))
            if prices:
                return prices
    entry_raw = entities.get("entry_raw") or entities.get("entry")
    return _parse_all_floats(str(entry_raw) if entry_raw is not None else None)


def _extract_sl_float(entities: dict[str, Any]) -> float | None:
    sl_obj = entities.get("stop_loss") or entities.get("sl")
    if isinstance(sl_obj, dict):
        p = sl_obj.get("price") or sl_obj.get("value")
        if p is not None:
            try:
                return float(p)
            except (TypeError, ValueError):
                pass
    stop_raw = entities.get("stop_raw") or entities.get("stop")
    return _parse_first_float(str(stop_raw) if stop_raw is not None else None)


def _extract_tp_prices(entities: dict[str, Any]) -> list[float]:
    tps = entities.get("take_profits") or entities.get("tp") or entities.get("targets")
    if isinstance(tps, list):
        prices: list[float] = []
        for t in tps:
            if isinstance(t, dict):
                p = t.get("price") or t.get("value")
                if p is not None:
                    try:
                        prices.append(float(p))
                    except (TypeError, ValueError):
                        pass
            elif isinstance(t, (int, float)):
                prices.append(float(t))
        if prices:
            return prices
    tp_raw = entities.get("target_raw") or entities.get("tp_raw")
    return _parse_all_floats(str(tp_raw) if tp_raw is not None else None)


# ---------------------------------------------------------------------------
# Existing utility functions (unchanged)
# ---------------------------------------------------------------------------


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
