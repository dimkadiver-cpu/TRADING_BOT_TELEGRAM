# src/runtime_v2/lifecycle/workers.py
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.lifecycle.cancel_expander import (
    expand_cancel_pending_commands,
    load_pending_entry_client_order_ids,
)
from src.runtime_v2.lifecycle.event_processor import EventProcessorResult, LifecycleEventProcessor
from src.runtime_v2.lifecycle.models import (
    TERMINAL_STATES, LEGACY_BE_STATES, ExchangeEvent, ExecutionCommand, LifecycleEvent,
)
from src.runtime_v2.lifecycle.repositories import (
    ExecutionCommandRepository, ExchangeEventRepository,
    LifecycleEventRepository, TradeChainRepository,
)

logger = logging.getLogger(__name__)

from src.runtime_v2.control_plane.outbox_writer import (
    project_clean_log_for_chain,
    write_engine_rule_update_clean_log,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_PNL_EVENT_TYPES = {
    "TP_FILLED",
    "SL_FILLED",
    "CLOSE_FULL_FILLED",
    "CLOSE_PARTIAL_FILLED",
}

_ENTRY_FEE_EVENT_TYPES = {
    "ENTRY_FILLED",
    "ENTRY_UPDATED",
}


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_peak_margin_update(
    *,
    chain_row: tuple,
    result: EventProcessorResult,
) -> float | None:
    current_entry_avg, current_open_qty, risk_snapshot_json, existing_peak = chain_row
    effective_entry_avg = (
        result.entry_avg_price if result.entry_avg_price is not None else _safe_float(current_entry_avg)
    )
    effective_open_qty = (
        result.new_open_position_qty if result.new_open_position_qty is not None else _safe_float(current_open_qty)
    )
    effective_risk_snapshot_json = (
        result.new_risk_snapshot_json
        if result.new_risk_snapshot_json is not None
        else risk_snapshot_json
    )
    try:
        risk_snapshot = json.loads(effective_risk_snapshot_json or "{}")
    except Exception:
        risk_snapshot = {}
    leverage = _safe_float(risk_snapshot.get("leverage"))

    existing_peak_value = _safe_float(existing_peak)
    if (
        effective_entry_avg is None
        or effective_open_qty is None
        or leverage is None
        or leverage <= 0.0
    ):
        return existing_peak_value
    if effective_open_qty <= 0.0:
        return existing_peak_value

    current_margin_used = effective_open_qty * effective_entry_avg / leverage
    base_peak = existing_peak_value or 0.0
    return max(base_peak, current_margin_used)


def _accumulate_pnl_for_events(
    conn: sqlite3.Connection,
    *,
    chain_id: int,
    events: list,
) -> None:
    row = conn.execute(
        "SELECT side, entry_avg_price FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    if row is None:
        return
    side = str(row[0] or "").upper()
    entry_avg_price = row[1]
    side_sign = 1.0 if side == "LONG" else -1.0
    gross_total = 0.0
    fee_total = 0.0
    for event in events:
        try:
            payload = json.loads(event.payload_json or "{}")
        except json.JSONDecodeError:
            continue
        if event.event_type in _PNL_EVENT_TYPES:
            fee_total += float(payload.get("exec_fee") or 0.0)
            if entry_avg_price is not None:
                fill_price = payload.get("fill_price")
                closed_qty = payload.get("closed_size", payload.get("filled_qty"))
                if fill_price is not None and closed_qty is not None:
                    gross_total += float(closed_qty) * (float(fill_price) - float(entry_avg_price)) * side_sign
        elif event.event_type in _ENTRY_FEE_EVENT_TYPES:
            fee_total += float(payload.get("exec_fee") or 0.0)
    if gross_total != 0.0 or fee_total != 0.0:
        conn.execute(
            """
            UPDATE ops_trade_chains
            SET cumulative_gross_pnl = COALESCE(cumulative_gross_pnl, 0.0) + ?,
                cumulative_fees = COALESCE(cumulative_fees, 0.0) + ?
            WHERE trade_chain_id=?
            """,
            (gross_total, fee_total, chain_id),
        )


def _load_entry_command_for_command(
    conn: sqlite3.Connection,
    chain_id: int,
    command_id: int,
) -> tuple[str | None, dict | None]:
    row = conn.execute(
        """
        SELECT client_order_id, payload_json
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND client_order_id IS NOT NULL
        LIMIT 1
        """,
        (chain_id, command_id),
    ).fetchone()
    if not row:
        return None, None
    payload: dict | None
    try:
        payload = json.loads(row[1] or "{}")
    except Exception:
        payload = None
    return (str(row[0]) if row[0] else None), payload


def _with_entry_client_order_id_from_command(
    ops_db_path: str,
    exchange_event: ExchangeEvent,
) -> ExchangeEvent:
    if exchange_event.event_type != "ENTRY_FILLED" or exchange_event.trade_chain_id is None:
        return exchange_event

    try:
        payload = json.loads(exchange_event.payload_json or "{}")
    except Exception:
        return exchange_event

    if payload.get("entry_client_order_id") or payload.get("entry_command_payload"):
        return exchange_event  # already enriched

    chain_id = int(exchange_event.trade_chain_id)
    command_id: int | None = None

    # Path 1: REST reconciliation — command_id explicitly set in payload
    if payload.get("command_id") is not None:
        try:
            command_id = int(payload["command_id"])
        except (TypeError, ValueError):
            pass

    # Path 2: WS fills — parse command_id from order_link_id (format: tsb:{chain}:{cmd}:entry:{seq})
    if command_id is None:
        order_link_id = payload.get("order_link_id")
        if order_link_id:
            try:
                parsed = coid_mod.parse(order_link_id)
                if parsed.role == "entry":
                    command_id = parsed.command_id
            except (ValueError, Exception):
                pass

    if command_id is None:
        return exchange_event

    conn = sqlite3.connect(ops_db_path)
    try:
        client_order_id, command_payload = _load_entry_command_for_command(
            conn, chain_id, command_id,
        )
    finally:
        conn.close()
    if not client_order_id and not command_payload:
        return exchange_event

    if client_order_id:
        payload["entry_client_order_id"] = client_order_id
    if command_payload:
        payload["entry_command_payload"] = command_payload
    return exchange_event.model_copy(update={"payload_json": json.dumps(payload)})


class TimeoutWorker:
    def __init__(self, ops_db_path: str, chain_repo: TradeChainRepository) -> None:
        self._ops_db = ops_db_path
        self._chain_repo = chain_repo

    def run_once(self, batch_size: int = 100) -> int:
        chains = self._chain_repo.get_timed_out_waiting_entry(batch_size)
        processed = 0
        for chain in chains:
            try:
                self._process_timeout(chain)
                processed += 1
            except Exception:
                logger.exception("timeout error for chain %s", chain.trade_chain_id)
        return processed

    def _process_timeout(self, chain) -> None:
        chain_id = chain.trade_chain_id
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                conn.execute(
                    "UPDATE ops_trade_chains SET lifecycle_state='EXPIRED', updated_at=? WHERE trade_chain_id=?",
                    (now, chain_id),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (chain_id, "PENDING_TIMEOUT", "timeout_worker",
                     "WAITING_ENTRY", "EXPIRED", "{}", f"timeout:{chain_id}", now),
                )
                entry_client_order_ids = load_pending_entry_client_order_ids(conn, chain_id)
                if not entry_client_order_ids:
                    entry_client_order_ids = [""]
                for entry_client_order_id in entry_client_order_ids:
                    payload = {"symbol": chain.symbol, "side": chain.side, "cancel_origin": "timeout_worker"}
                    idempotency_key = f"cancel_timeout:{chain_id}"
                    if entry_client_order_id:
                        payload["entry_client_order_id"] = entry_client_order_id
                        idempotency_key = f"{idempotency_key}:{entry_client_order_id}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, "CANCEL_PENDING_ENTRY", "PENDING",
                            json.dumps(payload), idempotency_key, now, now,
                        ),
                    )
        finally:
            conn.close()


class LifecycleEventWorker:
    def __init__(
        self,
        ops_db_path: str,
        processor: LifecycleEventProcessor,
        chain_repo: TradeChainRepository,
        event_repo: LifecycleEventRepository,
        command_repo: ExecutionCommandRepository,
        exchange_event_repo: ExchangeEventRepository,
    ) -> None:
        self._ops_db = ops_db_path
        self._processor = processor
        self._chain_repo = chain_repo
        self._event_repo = event_repo
        self._command_repo = command_repo
        self._exchange_event_repo = exchange_event_repo

    def run_once(self, batch_size: int = 100) -> int:
        events = self._exchange_event_repo.get_new_events(batch_size)
        processed = 0
        for exchange_event in events:
            try:
                if exchange_event.event_type == "FUNDING_SETTLED":
                    self._handle_funding_settled(exchange_event)
                    self._exchange_event_repo.mark_processed(exchange_event.exchange_event_id)
                    processed += 1
                    continue

                if exchange_event.trade_chain_id is None:
                    self._exchange_event_repo.mark_processed(exchange_event.exchange_event_id)
                    processed += 1
                    continue

                chain = self._chain_repo.get_by_id(exchange_event.trade_chain_id)
                if chain is None or chain.lifecycle_state in TERMINAL_STATES or chain.lifecycle_state in LEGACY_BE_STATES:
                    self._exchange_event_repo.mark_processed(exchange_event.exchange_event_id)
                    processed += 1
                    continue

                active_commands = self._command_repo.get_active_for_chain(chain.trade_chain_id)
                exchange_event = _with_entry_client_order_id_from_command(
                    self._ops_db,
                    exchange_event,
                )
                result = self._processor.process(exchange_event, chain, active_commands)
                self._persist_result(chain.trade_chain_id, result)
                self._exchange_event_repo.mark_processed(exchange_event.exchange_event_id)
                processed += 1
            except Exception:
                logger.exception("error processing exchange_event %s", exchange_event.exchange_event_id)
        return processed

    def _handle_funding_settled(self, exchange_event) -> None:
        if exchange_event.trade_chain_id is None:
            return
        try:
            payload = json.loads(exchange_event.payload_json or "{}")
        except Exception:
            return
        funding_amount = float(payload.get("exec_fee") or 0.0)
        if funding_amount == 0.0:
            return
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE ops_trade_chains
                    SET cumulative_funding = COALESCE(cumulative_funding, 0.0) + ?
                    WHERE trade_chain_id=?
                    """,
                    (funding_amount, exchange_event.trade_chain_id),
                )
        finally:
            conn.close()

    def _persist_result(self, chain_id: int, result: EventProcessorResult) -> None:
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                chain_row = conn.execute(
                    "SELECT entry_avg_price, open_position_qty, risk_snapshot_json, peak_margin_used "
                    "FROM ops_trade_chains WHERE trade_chain_id=?",
                    (chain_id,),
                ).fetchone()
                new_peak_margin_used = (
                    _compute_peak_margin_update(chain_row=chain_row, result=result)
                    if chain_row is not None
                    else None
                )
                has_chain_update = (
                    result.new_lifecycle_state is not None
                    or result.new_be_protection_status is not None
                    or result.entry_avg_price is not None
                    or result.current_stop_price is not None
                    or result.new_filled_entry_qty is not None
                    or result.new_open_position_qty is not None
                    or result.new_closed_position_qty is not None
                    or result.new_risk_already_realized is not None
                    or result.new_risk_remaining is not None
                    or result.new_plan_state_json is not None
                    or result.new_risk_snapshot_json is not None
                )
                if has_chain_update:
                    fields = ["updated_at=?"]
                    vals: list = [now]
                    if result.new_lifecycle_state is not None:
                        fields.append("lifecycle_state=?")
                        vals.append(result.new_lifecycle_state)
                    if result.new_be_protection_status is not None:
                        fields.append("be_protection_status=?")
                        vals.append(result.new_be_protection_status)
                    if result.entry_avg_price is not None:
                        fields.append("entry_avg_price=?")
                        vals.append(result.entry_avg_price)
                    if result.current_stop_price is not None:
                        fields.append("current_stop_price=?")
                        vals.append(result.current_stop_price)
                    if result.new_filled_entry_qty is not None:
                        fields.append("filled_entry_qty=?")
                        vals.append(result.new_filled_entry_qty)
                    if result.new_open_position_qty is not None:
                        fields.append("open_position_qty=?")
                        vals.append(result.new_open_position_qty)
                    if result.new_closed_position_qty is not None:
                        fields.append("closed_position_qty=?")
                        vals.append(result.new_closed_position_qty)
                    if result.new_risk_already_realized is not None:
                        fields.append("risk_already_realized=?")
                        vals.append(result.new_risk_already_realized)
                    if result.new_risk_remaining is not None:
                        fields.append("risk_remaining=?")
                        vals.append(result.new_risk_remaining)
                    if result.new_plan_state_json is not None:
                        fields.append("plan_state_json=?")
                        vals.append(result.new_plan_state_json)
                    if result.new_risk_snapshot_json is not None:
                        fields.append("risk_snapshot_json=?")
                        vals.append(result.new_risk_snapshot_json)
                    if new_peak_margin_used is not None:
                        fields.append("peak_margin_used=?")
                        vals.append(new_peak_margin_used)
                    vals.append(chain_id)
                    conn.execute(
                        f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                        vals,
                    )

                if result.release_waiting_position:
                    conn.execute(
                        "UPDATE ops_execution_commands SET status='PENDING', updated_at=? "
                        "WHERE trade_chain_id=? AND status='WAITING_POSITION'",
                        (now, chain_id),
                    )

                for event in result.lifecycle_events:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_lifecycle_events (
                            trade_chain_id, event_type, source_type, source_id,
                            previous_state, next_state, payload_json, idempotency_key, created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, event.event_type, event.source_type, event.source_id,
                            event.previous_state, event.next_state, event.payload_json,
                            event.idempotency_key, now,
                        ),
                    )

                _accumulate_pnl_for_events(conn, chain_id=chain_id, events=result.lifecycle_events)

                for cmd in result.execution_commands:
                    for payload_json_exp, idempotency_key_exp in expand_cancel_pending_commands(
                        conn,
                        trade_chain_id=chain_id,
                        command_type=cmd.command_type,
                        payload_json=cmd.payload_json,
                        idempotency_key=cmd.idempotency_key,
                    ):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO ops_execution_commands (
                                trade_chain_id, command_type, status, payload_json,
                                idempotency_key, created_at, updated_at
                            ) VALUES (?,?,?,?,?,?,?)
                            """,
                            (chain_id, cmd.command_type, cmd.status, payload_json_exp,
                             idempotency_key_exp, now, now),
                        )

                engine_rule_events = [
                    e for e in result.lifecycle_events
                    if e.event_type == "ENGINE_RULE_UPDATE_ACCEPTED"
                ]
                if engine_rule_events:
                    try:
                        write_engine_rule_update_clean_log(conn, chain_id, engine_rule_events)
                    except Exception:
                        logger.exception("engine_rule update_clean_log failed for chain %s", chain_id)

                try:
                    project_clean_log_for_chain(conn, chain_id)
                except Exception:
                    logger.exception("clean_log projection failed for chain %s", chain_id)
        finally:
            conn.close()


__all__ = ["TimeoutWorker", "LifecycleEventWorker"]
