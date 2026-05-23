# src/runtime_v2/lifecycle/repositories.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.lifecycle.models import (
    ControlMode, ExecutionCommand, ExchangeEvent,
    LifecycleEvent, TradeChain,
)

_CONTROL_MODE_SEVERITY: dict[str, int] = {"NONE": 0, "BLOCK_NEW_ENTRIES": 1, "FULL_STOP": 2}

_CHAIN_COLS = (
    "trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
    "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
    "entry_avg_price, current_stop_price, expected_stop_price, be_protection_status, "
    "entry_timeout_at, management_plan_json, risk_snapshot_json, "
    "planned_entry_qty, filled_entry_qty, open_position_qty, closed_position_qty, "
    "last_position_sync_at, execution_mode, risk_already_realized, risk_remaining, "
    "plan_state_json, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chain_from_row(row: tuple) -> TradeChain:
    (trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id,
     trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
     entry_avg_price, current_stop_price, expected_stop_price, be_protection_status,
     entry_timeout_at, management_plan_json, risk_snapshot_json,
     planned_entry_qty, filled_entry_qty, open_position_qty, closed_position_qty,
     last_position_sync_at, execution_mode, risk_already_realized, risk_remaining,
     plan_state_json, created_at, updated_at) = row
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=source_enrichment_id,
        canonical_message_id=canonical_message_id,
        raw_message_id=raw_message_id,
        trader_id=trader_id,
        account_id=account_id,
        symbol=symbol,
        side=side,
        lifecycle_state=lifecycle_state,
        entry_mode=entry_mode,
        entry_avg_price=entry_avg_price,
        current_stop_price=current_stop_price,
        expected_stop_price=expected_stop_price,
        be_protection_status=be_protection_status,
        entry_timeout_at=datetime.fromisoformat(entry_timeout_at) if entry_timeout_at else None,
        management_plan_json=management_plan_json or "{}",
        risk_snapshot_json=risk_snapshot_json or "{}",
        planned_entry_qty=planned_entry_qty or 0.0,
        filled_entry_qty=filled_entry_qty or 0.0,
        open_position_qty=open_position_qty or 0.0,
        closed_position_qty=closed_position_qty or 0.0,
        last_position_sync_at=(
            datetime.fromisoformat(last_position_sync_at)
            if last_position_sync_at else None
        ),
        execution_mode=execution_mode or "D_POSITION_TPSL",
        risk_already_realized=risk_already_realized or 0.0,
        risk_remaining=risk_remaining or 0.0,
        plan_state_json=plan_state_json or "{}",
        created_at=datetime.fromisoformat(created_at) if created_at else None,
        updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
    )


class TradeChainRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, chain: TradeChain) -> TradeChain:
        now = _now()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                f"""
                INSERT OR IGNORE INTO ops_trade_chains (
                    source_enrichment_id, canonical_message_id, raw_message_id,
                    trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
                    entry_avg_price, current_stop_price, expected_stop_price,
                    be_protection_status, entry_timeout_at, management_plan_json,
                    risk_snapshot_json, planned_entry_qty, filled_entry_qty,
                    open_position_qty, closed_position_qty, last_position_sync_at,
                    execution_mode, risk_already_realized, risk_remaining,
                    plan_state_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chain.source_enrichment_id, chain.canonical_message_id, chain.raw_message_id,
                    chain.trader_id, chain.account_id, chain.symbol, chain.side,
                    chain.lifecycle_state, chain.entry_mode,
                    chain.entry_avg_price, chain.current_stop_price, chain.expected_stop_price,
                    chain.be_protection_status,
                    chain.entry_timeout_at.isoformat() if chain.entry_timeout_at else None,
                    chain.management_plan_json, chain.risk_snapshot_json,
                    chain.planned_entry_qty, chain.filled_entry_qty,
                    chain.open_position_qty, chain.closed_position_qty,
                    chain.last_position_sync_at.isoformat() if chain.last_position_sync_at else None,
                    chain.execution_mode, chain.risk_already_realized, chain.risk_remaining,
                    chain.plan_state_json, now, now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                row_id = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT trade_chain_id FROM ops_trade_chains WHERE source_enrichment_id=?",
                    (chain.source_enrichment_id,),
                ).fetchone()
                row_id = row[0]
        finally:
            conn.close()
        return chain.model_copy(update={"trade_chain_id": row_id})

    def get_by_id(self, trade_chain_id: int) -> TradeChain | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                f"SELECT {_CHAIN_COLS} FROM ops_trade_chains WHERE trade_chain_id=?",
                (trade_chain_id,),
            ).fetchone()
            return _chain_from_row(row) if row else None
        finally:
            conn.close()

    def get_active_by_trader(self, trader_id: str) -> list[TradeChain]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT {_CHAIN_COLS} FROM ops_trade_chains
                WHERE trader_id=? AND lifecycle_state NOT IN ('CLOSED','CANCELLED','EXPIRED')
                """,
                (trader_id,),
            ).fetchall()
            return [_chain_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_timed_out_waiting_entry(self, limit: int = 100) -> list[TradeChain]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT {_CHAIN_COLS} FROM ops_trade_chains
                WHERE lifecycle_state='WAITING_ENTRY'
                  AND entry_timeout_at IS NOT NULL
                  AND entry_timeout_at <= ?
                LIMIT ?
                """,
                (_now(), limit),
            ).fetchall()
            return [_chain_from_row(r) for r in rows]
        finally:
            conn.close()

    def update_state(
        self,
        trade_chain_id: int,
        new_state: str,
        *,
        entry_avg_price: float | None = None,
        current_stop_price: float | None = None,
        be_protection_status: str | None = None,
    ) -> None:
        now = _now()
        fields = ["lifecycle_state=?", "updated_at=?"]
        values: list = [new_state, now]
        if entry_avg_price is not None:
            fields.append("entry_avg_price=?")
            values.append(entry_avg_price)
        if current_stop_price is not None:
            fields.append("current_stop_price=?")
            values.append(current_stop_price)
        if be_protection_status is not None:
            fields.append("be_protection_status=?")
            values.append(be_protection_status)
        values.append(trade_chain_id)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                values,
            )
            conn.commit()
        finally:
            conn.close()


class LifecycleEventRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, event: LifecycleEvent) -> LifecycleEvent:
        now = _now()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO ops_lifecycle_events (
                    trade_chain_id, event_type, source_type, source_id,
                    previous_state, next_state, payload_json, idempotency_key, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.trade_chain_id, event.event_type, event.source_type, event.source_id,
                    event.previous_state, event.next_state, event.payload_json,
                    event.idempotency_key, now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                eid = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT event_id FROM ops_lifecycle_events WHERE idempotency_key=?",
                    (event.idempotency_key,),
                ).fetchone()
                eid = row[0]
        finally:
            conn.close()
        return event.model_copy(update={"event_id": eid})


class ExecutionCommandRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, cmd: ExecutionCommand) -> ExecutionCommand:
        now = _now()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO ops_execution_commands (
                    trade_chain_id, command_type, status, payload_json,
                    idempotency_key, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    cmd.trade_chain_id, cmd.command_type, cmd.status,
                    cmd.payload_json, cmd.idempotency_key, now, now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                cid = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT command_id FROM ops_execution_commands WHERE idempotency_key=?",
                    (cmd.idempotency_key,),
                ).fetchone()
                cid = row[0]
        finally:
            conn.close()
        return cmd.model_copy(update={"command_id": cid})

    def get_active_for_chain(self, trade_chain_id: int) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                """
                SELECT command_id, trade_chain_id, command_type, status, payload_json,
                       idempotency_key, created_at, updated_at
                FROM ops_execution_commands
                WHERE trade_chain_id=? AND status IN ('PENDING','SENT','ACK')
                """,
                (trade_chain_id,),
            ).fetchall()
            return [
                ExecutionCommand(
                    command_id=r[0], trade_chain_id=r[1], command_type=r[2],
                    status=r[3], payload_json=r[4], idempotency_key=r[5],
                    created_at=datetime.fromisoformat(r[6]) if r[6] else None,
                    updated_at=datetime.fromisoformat(r[7]) if r[7] else None,
                )
                for r in rows
            ]
        finally:
            conn.close()

    def get_entry_client_order_id(self, trade_chain_id: int) -> str | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT client_order_id FROM ops_execution_commands "
                "WHERE trade_chain_id=? "
                "AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL') "
                "AND client_order_id IS NOT NULL LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()


class ControlStateRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_effective_mode(
        self, account_id: str, trader_id: str, symbol: str, side: str
    ) -> ControlMode:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT scope_type, scope_value, execution_pause_mode FROM ops_control_state WHERE active=1",
            ).fetchall()
        finally:
            conn.close()

        applicable: list[str] = []
        for scope_type, scope_value, mode in rows:
            if scope_type == "GLOBAL":
                applicable.append(mode)
            elif scope_type == "ACCOUNT" and scope_value == account_id:
                applicable.append(mode)
            elif scope_type == "TRADER" and scope_value == trader_id:
                applicable.append(mode)
            elif scope_type == "SYMBOL" and scope_value == symbol:
                applicable.append(mode)
            elif scope_type == "SIDE" and scope_value == side:
                applicable.append(mode)

        if not applicable:
            return "NONE"
        return max(applicable, key=lambda m: _CONTROL_MODE_SEVERITY.get(m, 0))  # type: ignore[return-value]


class SnapshotRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save_account(self, snap, account_id: str) -> None:
        from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
        assert isinstance(snap, AccountStateSnapshot)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO ops_account_snapshots (
                    account_id, equity_usdt, available_balance_usdt,
                    total_open_risk_usdt, total_margin_used_usdt, source, captured_at, payload_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    account_id, snap.equity_usdt, snap.available_balance_usdt,
                    snap.total_open_risk_usdt, snap.total_margin_used_usdt,
                    snap.source, snap.captured_at.isoformat(), "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def save_market(self, snap, account_id: str) -> None:
        from src.runtime_v2.lifecycle.ports import SymbolMarketSnapshot
        assert isinstance(snap, SymbolMarketSnapshot)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO ops_market_snapshots (
                    account_id, symbol, mark_price, bid, ask,
                    min_order_size, price_precision, qty_precision,
                    source, captured_at, payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    account_id, snap.symbol, snap.mark_price, snap.bid, snap.ask,
                    snap.min_order_size, snap.price_precision, snap.qty_precision,
                    snap.source, snap.captured_at.isoformat(), "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()


class ExchangeEventRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_new_events(self, limit: int = 100) -> list[ExchangeEvent]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                """
                SELECT exchange_event_id, trade_chain_id, event_type, payload_json,
                       processing_status, idempotency_key, received_at, processed_at
                FROM ops_exchange_events
                WHERE processing_status='NEW'
                ORDER BY received_at
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                ExchangeEvent(
                    exchange_event_id=r[0], trade_chain_id=r[1], event_type=r[2],
                    payload_json=r[3], processing_status=r[4], idempotency_key=r[5],
                    received_at=datetime.fromisoformat(r[6]) if r[6] else None,
                    processed_at=datetime.fromisoformat(r[7]) if r[7] else None,
                )
                for r in rows
            ]
        finally:
            conn.close()

    def mark_processed(self, exchange_event_id: int) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE ops_exchange_events SET processing_status='DONE', processed_at=? WHERE exchange_event_id=?",
                (_now(), exchange_event_id),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = [
    "TradeChainRepository", "LifecycleEventRepository", "ExecutionCommandRepository",
    "ControlStateRepository", "SnapshotRepository", "ExchangeEventRepository",
]
