# src/runtime_v2/lifecycle/workers.py
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.lifecycle.event_processor import EventProcessorResult, LifecycleEventProcessor
from src.runtime_v2.lifecycle.models import (
    TERMINAL_STATES, LEGACY_BE_STATES, ExchangeEvent, ExecutionCommand, LifecycleEvent,
)
from src.runtime_v2.lifecycle.repositories import (
    ExecutionCommandRepository, ExchangeEventRepository,
    LifecycleEventRepository, TradeChainRepository,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_pending_entry_client_order_ids(conn: sqlite3.Connection, chain_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING','SENT','ACK')
          AND client_order_id IS NOT NULL
        ORDER BY command_id
        """,
        (chain_id,),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


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

    if payload.get("entry_client_order_id") or payload.get("command_id") is None:
        return exchange_event

    try:
        command_id = int(payload["command_id"])
    except (TypeError, ValueError):
        return exchange_event

    conn = sqlite3.connect(ops_db_path)
    try:
        client_order_id, command_payload = _load_entry_command_for_command(
            conn,
            int(exchange_event.trade_chain_id),
            command_id,
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
                    (chain_id, "TIMEOUT_REACHED", "timeout_worker",
                     "WAITING_ENTRY", "EXPIRED", "{}", f"timeout:{chain_id}", now),
                )
                entry_client_order_ids = _load_pending_entry_client_order_ids(conn, chain_id)
                if not entry_client_order_ids:
                    entry_client_order_ids = [""]
                for entry_client_order_id in entry_client_order_ids:
                    payload = {"symbol": chain.symbol, "side": chain.side}
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

    def _persist_result(self, chain_id: int, result: EventProcessorResult) -> None:
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
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

                for cmd in result.execution_commands:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (chain_id, cmd.command_type, cmd.status, cmd.payload_json,
                         cmd.idempotency_key, now, now),
                    )
        finally:
            conn.close()


__all__ = ["TimeoutWorker", "LifecycleEventWorker"]
