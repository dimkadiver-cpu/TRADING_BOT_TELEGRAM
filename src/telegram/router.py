"""Message router that prepares parser input and owns processing lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
import sqlite3
from typing import Any

from src.execution.dynamic_pairlist import DynamicPairlistManager
from src.execution.update_applier import apply_update_plan
from src.execution.update_planner import build_update_plan
from src.operation_rules.engine import OperationRulesEngine
from src.parser.intent_action_map import map_intents_to_actions
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
    source_topic_id: int | None = None


def is_blacklisted_text(
    config: ChannelsConfig,
    raw_text: str,
    chat_id: int | None,
    topic_id: int | None = None,
) -> bool:
    """Return True if raw_text matches blacklist_global or the scope-matched entry blacklist.

    Rule: blacklist_global + blacklist_scope_matchato (no implicit merge between topic and forum-wide).
    """
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
        dynamic_pairlist_manager: DynamicPairlistManager | None = None,
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
        self._dynamic_pairlist = dynamic_pairlist_manager

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

        if is_blacklisted_text(self._config, item.raw_text, chat_id_int, item.source_topic_id):
            self._status_store.update(item.raw_message_id, "blacklisted")
            self._logger.info(
                "blacklisted | chat_id=%s topic_id=%s telegram_message_id=%s raw_message_id=%s",
                item.source_chat_id,
                item.source_topic_id,
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

        if self._is_inactive_channel(chat_id_int, item.source_topic_id):
            self._status_store.update(item.raw_message_id, "done")
            self._logger.info(
                "trader_inactive | trader_id=%s chat_id=%s topic_id=%s telegram_message_id=%s",
                trader_resolution.trader_id,
                item.source_chat_id,
                item.source_topic_id,
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
                source_topic_id=item.source_topic_id,
            )
            if self._signals_store is not None:
                self._signals_store.insert(signal_rec)
            if self._dynamic_pairlist is not None:
                pair = self._dynamic_pairlist.ensure_symbol(signal_rec.symbol)
                if pair:
                    self._logger.info(
                        "dynamic pairlist updated | attempt_key=%s symbol=%s pair=%s path=%s",
                        attempt_key,
                        signal_rec.symbol,
                        pair,
                        self._dynamic_pairlist.path,
                    )

        # Step 3 — Resolve target
        resolved: ResolvedTarget | None = None
        if self._resolver is not None:
            resolved = self._resolver.resolve(op_signal, db_path=db_path)

        # Step 3b — Route UNRESOLVED UPDATE to review queue
        if (
            resolved is not None
            and resolved.eligibility == "UNRESOLVED"
            and result.message_type == "UPDATE"
        ):
            self._review_queue.insert(
                item.raw_message_id,
                f"update_target_unresolved:{resolved.reason or 'unknown'}",
            )
            self._logger.warning(
                "UPDATE target unresolved → review_queue | raw_message_id=%s reason=%s",
                item.raw_message_id,
                resolved.reason,
            )

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
                    source_topic_id=item.source_topic_id,
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
                self._apply_update_runtime(
                    item=item,
                    result=result,
                    trader_id=trader_id,
                    op_signal=op_signal,
                    resolved=resolved,
                    op_signal_id=op_signal_id,
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

    def _apply_update_runtime(
        self,
        *,
        item: QueueItem,
        result: TraderParseResult,
        trader_id: str,
        op_signal: OperationalSignal,
        resolved: ResolvedTarget | None,
        op_signal_id: int,
    ) -> None:
        """Apply eligible UPDATE messages to the live trade state DB."""
        if result.message_type != "UPDATE" or op_signal.is_blocked or resolved is None:
            return
        if resolved.eligibility != "ELIGIBLE" or not resolved.position_ids:
            return

        actions = map_intents_to_actions(result.intents, result.entities)
        if not actions:
            return

        target_attempt_keys = self._resolve_attempt_keys_from_position_ids(resolved.position_ids)
        if not target_attempt_keys:
            self._logger.warning(
                "update runtime skipped | raw_message_id=%s op_signal_id=%s reason=missing_target_attempt_keys"
                " position_ids=%s",
                item.raw_message_id,
                op_signal_id,
                resolved.position_ids,
            )
            return

        plan = build_update_plan(
            {
                "message_type": result.message_type,
                "intents": result.intents,
                "actions": actions,
                "entities": result.entities,
                "reported_results": result.reported_results,
                "target_refs": resolved.position_ids,
            }
        )
        apply_result = apply_update_plan(
            plan,
            self._db_path,
            env=_ENV_DEFAULT,
            channel_id=item.source_chat_id,
            telegram_msg_id=str(item.telegram_message_id),
            trader_id=trader_id,
            target_attempt_keys=target_attempt_keys,
        )
        if apply_result.errors:
            self._logger.warning(
                "update runtime apply failed | raw_message_id=%s op_signal_id=%s errors=%s warnings=%s",
                item.raw_message_id,
                op_signal_id,
                apply_result.errors,
                apply_result.warnings,
            )
            return
        self._logger.info(
            "update runtime applied | raw_message_id=%s op_signal_id=%s target_attempt_keys=%s"
            " warnings=%s",
            item.raw_message_id,
            op_signal_id,
            apply_result.target_attempt_keys,
            apply_result.warnings,
        )

    def _resolve_attempt_keys_from_position_ids(self, position_ids: list[int]) -> list[str]:
        if not position_ids or self._db_path is None:
            return []
        placeholders = ",".join("?" for _ in position_ids)
        query = f"""
            SELECT attempt_key
            FROM operational_signals
            WHERE op_signal_id IN ({placeholders})
              AND attempt_key IS NOT NULL
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query, tuple(position_ids)).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]

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

        entry = self._config.match_entry(_safe_int(item.source_chat_id), item.source_topic_id)
        if entry is not None and entry.trader_id:
            return EffectiveTraderResult(
                trader_id=entry.trader_id,
                method="channels_yaml",
                detail=entry.label,
            )
        return trader_resolution

    def _is_inactive_channel(self, chat_id: int | None, topic_id: int | None = None) -> bool:
        if chat_id is None:
            return False
        entry = self._config.match_entry(chat_id, topic_id)
        if entry is None:
            return False
        return not entry.active

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
    source_topic_id: int | None = None,
) -> SignalRecord:
    entities: dict[str, Any] = (
        op_signal.parse_result.entities
        if isinstance(op_signal.parse_result.entities, dict)
        else {}
    )
    symbol = str(entities.get("symbol") or "").strip().upper() or None
    side = str(entities.get("side") or entities.get("direction") or "").strip().upper() or None

    # Build entry_json preserving the real order_type for each entry
    entry_json = json.dumps(_build_entry_json(entities))

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
        source_topic_id=source_topic_id,
    )


def _build_op_signal_record(
    *,
    op_signal: OperationalSignal,
    parse_result_id: int,
    attempt_key: str | None,
    trader_id: str,
    resolved: ResolvedTarget | None,
    now_ts: str,
    source_topic_id: int | None = None,
) -> OperationalSignalRecord:
    return OperationalSignalRecord(
        parse_result_id=parse_result_id,
        attempt_key=attempt_key,
        trader_id=trader_id,
        message_type=op_signal.parse_result.message_type,
        is_blocked=op_signal.is_blocked,
        block_reason=op_signal.block_reason,
        risk_mode=op_signal.risk_mode,
        risk_pct_of_capital=op_signal.risk_pct_of_capital,
        risk_usdt_fixed=op_signal.risk_usdt_fixed,
        capital_base_usdt=op_signal.capital_base_usdt,
        risk_budget_usdt=op_signal.risk_budget_usdt,
        sl_distance_pct=op_signal.sl_distance_pct,
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
        source_topic_id=source_topic_id,
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


def _build_entry_json(entities: dict[str, Any]) -> list[dict[str, Any]]:
    """Build entry_json preserving the real order_type from parsed entities.

    Falls back to MARKET when no prices are present (market-entry signal).
    Falls back to LIMIT for individual entries that have no order_type set.
    """
    for key in ("entry_plan_entries", "entries"):
        entries_raw = entities.get(key)
        if isinstance(entries_raw, list) and entries_raw:
            result: list[dict[str, Any]] = []
            for e in entries_raw:
                if isinstance(e, dict):
                    p = e.get("price")
                    order_type = str(e.get("order_type") or "LIMIT").upper()
                    if p is None:
                        # Keep MARKET legs even without a price — they must survive
                        # into entry_json so MarketEntryDispatcher can pick them up.
                        if order_type == "MARKET":
                            result.append({"price": None, "type": "MARKET"})
                        continue
                    try:
                        price_val = float(p)
                    except (TypeError, ValueError):
                        continue
                    result.append({"price": price_val, "type": order_type})
                elif isinstance(e, (int, float)):
                    result.append({"price": float(e), "type": "LIMIT"})
            if result:
                return result

    # Fallback: raw entry string — no order_type info available
    prices = _extract_entry_prices_from_entities(entities)
    if prices:
        # Infer MARKET when entry_mode / order_type signals it
        entry_mode = str(entities.get("entry_mode") or entities.get("order_type") or "").upper()
        order_type = "MARKET" if "MARKET" in entry_mode else "LIMIT"
        return [{"price": p, "type": order_type} for p in prices]

    return [{"price": None, "type": "MARKET"}]


def _extract_sl_float(entities: dict[str, Any]) -> float | None:
    sl_obj = entities.get("stop_loss") or entities.get("sl")
    if isinstance(sl_obj, (int, float)):
        return float(sl_obj)
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
