"""Bootstrap and watchdog reconciliation for exchange-managed open trades."""

from __future__ import annotations

from dataclasses import dataclass, field
import sqlite3
from typing import Any

from src.core.timeutils import utc_now_iso
from src.execution.exchange_gateway import ExchangeGateway, ExchangeOrder, ExchangePosition
from src.execution.exchange_order_manager import (
    ExchangeOrderManager,
    ManagerOperationResult,
    _insert_event_row,
    _insert_warning_row,
    _load_trade_meta,
    _upsert_order_record,
)
from src.execution.freqtrade_normalizer import load_context_by_attempt_key

_EPSILON = 1e-9


@dataclass(slots=True)
class TradeReconciliationResult:
    attempt_key: str
    imported_orders: int = 0
    updated_orders: int = 0
    recreated_orders: int = 0
    warnings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_key": self.attempt_key,
            "imported_orders": self.imported_orders,
            "updated_orders": self.updated_orders,
            "recreated_orders": self.recreated_orders,
            "warnings": list(self.warnings),
            "actions": list(self.actions),
        }


@dataclass(slots=True)
class BootstrapSyncResult:
    processed_attempt_keys: list[str] = field(default_factory=list)
    trade_results: list[TradeReconciliationResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed_attempt_keys": list(self.processed_attempt_keys),
            "trade_results": [item.as_dict() for item in self.trade_results],
        }


def bootstrap_sync_open_trades(
    *,
    db_path: str,
    gateway: ExchangeGateway,
    order_manager: ExchangeOrderManager | None = None,
    env: str = "T",
    channel_id: str = "system",
    telegram_msg_id: str = "0",
    reason: str = "bootstrap",
) -> BootstrapSyncResult:
    manager = order_manager or ExchangeOrderManager(db_path=db_path, gateway=gateway)
    result = BootstrapSyncResult()
    open_trades = _load_open_exchange_managed_trades(db_path=db_path, env=env)
    for attempt_key in open_trades:
        result.processed_attempt_keys.append(attempt_key)
        trade_result = _reconcile_single_trade(
            db_path=db_path,
            manager=manager,
            attempt_key=attempt_key,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            reason=reason,
        )
        result.trade_results.append(trade_result)
    return result


def _reconcile_single_trade(
    *,
    db_path: str,
    manager: ExchangeOrderManager,
    attempt_key: str,
    channel_id: str,
    telegram_msg_id: str,
    reason: str,
) -> TradeReconciliationResult:
    context = manager._load_exchange_managed_context(attempt_key)
    now = utc_now_iso()
    trade_result = TradeReconciliationResult(attempt_key=attempt_key)
    exchange_orders = manager.gateway.fetch_open_orders(symbol=context.symbol or "")
    exchange_position = manager.gateway.fetch_position(symbol=context.symbol or "")

    with sqlite3.connect(db_path) as conn:
        if exchange_position is not None:
            _sync_position_from_exchange(conn, context=context, position=exchange_position, now=now)

        recognized_exchange_orders: list[tuple[ExchangeOrder, str, int]] = []
        unknown_exchange_orders: list[ExchangeOrder] = []
        for order in exchange_orders:
            parsed = _parse_client_order_id(order.client_order_id, attempt_key=attempt_key)
            if parsed is None:
                unknown_exchange_orders.append(order)
                continue
            recognized_exchange_orders.append((order, parsed[0], parsed[1]))

        for order, purpose, idx in recognized_exchange_orders:
            if _import_or_update_exchange_order(
                conn=conn,
                context=context,
                order=order,
                purpose=purpose,
                idx=idx,
                now=now,
            ) == "imported":
                trade_result.imported_orders += 1
            else:
                trade_result.updated_orders += 1

        db_active_orders = manager._load_active_protective_orders(conn, context=context)
        recognized_client_ids = {
            order.client_order_id
            for order, _, _ in recognized_exchange_orders
            if order.client_order_id
        }
        residual_size = exchange_position.size if exchange_position is not None else manager._load_position_size(conn, env=context.env, symbol=context.symbol or "")
        no_recognized_protectives = not any(purpose in {"SL", "TP"} for _, purpose, _ in recognized_exchange_orders)

        if unknown_exchange_orders:
            _warn_ambiguous(
                conn=conn,
                context=context,
                trade_result=trade_result,
                code="exchange_reconciliation_ambiguous",
                payload={"reason": "unknown_exchange_orders", "count": len(unknown_exchange_orders), "mode": reason},
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
            conn.commit()
            return trade_result

        if residual_size > _EPSILON and no_recognized_protectives:
            # Watchdog mode runs frequently. In some backends (notably dry-run),
            # fetch_open_orders may not expose protective orders reliably.
            # If we already have active protective rows in DB, avoid recreate loops.
            if reason == "watchdog" and db_active_orders:
                trade_result.actions.append("skipped_recreate_existing_db_protectives")
            else:
                _mark_missing_exchange_orders_cancelled(conn=conn, orders=db_active_orders, now=now)
                recreated = _rebuild_protectives(
                    conn=conn,
                    manager=manager,
                    context=context,
                    residual_qty=residual_size,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
                trade_result.recreated_orders += recreated
                trade_result.actions.append("recreated_missing_protectives")
        elif _has_qty_mismatch(recognized_exchange_orders, residual_size):
            cancel_result = ManagerOperationResult(attempt_key=attempt_key, action="reconciliation_cancel_incompatible")
            for order_row in manager._load_active_protective_orders(conn, context=context):
                manager._cancel_existing_order(
                    conn=conn,
                    context=context,
                    order_row=order_row,
                    result=cancel_result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
            trade_result.actions.append("cancelled_incompatible_protectives")
            trade_result.warnings.extend(failure.error for failure in cancel_result.failures)
            recreated = _rebuild_protectives(
                conn=conn,
                manager=manager,
                context=context,
                residual_qty=residual_size,
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
            trade_result.recreated_orders += recreated
            trade_result.actions.append("recreated_incompatible_protectives")
        elif db_active_orders and recognized_client_ids:
            missing_on_exchange = [
                order_row
                for order_row in db_active_orders
                if order_row.client_order_id not in recognized_client_ids
            ]
            if missing_on_exchange:
                _warn_ambiguous(
                    conn=conn,
                    context=context,
                    trade_result=trade_result,
                    code="exchange_reconciliation_ambiguous",
                    payload={"reason": "partial_exchange_mismatch", "missing_client_order_ids": [row.client_order_id for row in missing_on_exchange], "mode": reason},
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )

        _insert_event_row(
            conn,
            env=context.env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=context.trader_id,
            attempt_key=context.attempt_key,
            event_type="RECONCILIATION_COMPLETED",
            payload=trade_result.as_dict(),
            created_at=now,
        )
        conn.commit()
    return trade_result


def _load_open_exchange_managed_trades(*, db_path: str, env: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT attempt_key
            FROM trades
            WHERE env = ?
              AND protective_orders_mode = 'exchange_manager'
              AND state IN ('OPEN', 'PARTIAL_CLOSE_REQUESTED', 'CLOSE_REQUESTED')
            ORDER BY trade_id ASC
            """,
            (env,),
        ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _sync_position_from_exchange(
    conn: sqlite3.Connection,
    *,
    context: Any,
    position: ExchangePosition,
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE positions
        SET size = ?,
            entry_price = COALESCE(?, entry_price),
            updated_at = ?
        WHERE env = ?
          AND symbol = ?
        """,
        (float(position.size), position.entry_price, now, context.env, context.symbol),
    )


def _parse_client_order_id(client_order_id: str | None, *, attempt_key: str) -> tuple[str, int] | None:
    if not isinstance(client_order_id, str) or not client_order_id.startswith(f"{attempt_key}:"):
        return None
    suffix = client_order_id[len(attempt_key) + 1 :]
    parts = suffix.split(":")
    if len(parts) < 2:
        return None
    purpose = parts[0].strip().upper()
    if purpose not in {"SL", "TP", "EXIT", "ENTRY"}:
        return None
    try:
        idx = int(parts[1])
    except ValueError:
        return None
    return purpose, idx


def _import_or_update_exchange_order(
    *,
    conn: sqlite3.Connection,
    context: Any,
    order: ExchangeOrder,
    purpose: str,
    idx: int,
    now: str,
) -> str:
    existing = conn.execute(
        """
        SELECT order_pk, exchange_order_id, status
        FROM orders
        WHERE env = ?
          AND client_order_id = ?
        LIMIT 1
        """,
        (context.env, order.client_order_id),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE orders
            SET exchange_order_id = COALESCE(?, exchange_order_id),
                status = ?,
                qty = ?,
                price = ?,
                trigger_price = ?,
                venue_status_raw = ?,
                last_exchange_sync_at = ?,
                updated_at = ?
            WHERE order_pk = ?
            """,
            (order.exchange_order_id, order.status, order.qty, order.price, order.trigger_price, order.venue_status_raw, now, now, int(existing[0])),
        )
        return "updated"

    _upsert_order_record(
        conn=conn,
        env=context.env,
        attempt_key=context.attempt_key,
        symbol=context.symbol or "",
        side=order.side,
        order_type=order.order_type,
        purpose=purpose,
        idx=idx,
        qty=order.qty,
        price=order.price,
        trigger_price=order.trigger_price,
        reduce_only=order.reduce_only,
        client_order_id=order.client_order_id or f"{context.attempt_key}:{purpose}:{idx}",
        exchange_order_id=order.exchange_order_id,
        status=order.status,
        venue_status_raw=order.venue_status_raw,
        last_exchange_sync_at=now,
        created_at=now,
        updated_at=now,
    )
    return "imported"


def _has_qty_mismatch(recognized_exchange_orders: list[tuple[ExchangeOrder, str, int]], residual_size: float) -> bool:
    if residual_size <= _EPSILON:
        return False
    stop_orders = [order for order, purpose, _ in recognized_exchange_orders if purpose == "SL"]
    tp_orders = [order for order, purpose, _ in recognized_exchange_orders if purpose == "TP"]
    if not stop_orders and not tp_orders:
        return False
    if len(stop_orders) != 1:
        return True
    if abs(stop_orders[0].qty - residual_size) > _EPSILON:
        return True
    if any(order.qty - residual_size > _EPSILON for order in tp_orders):
        return True
    return sum(order.qty for order in tp_orders) - residual_size > _EPSILON


def _mark_missing_exchange_orders_cancelled(
    *,
    conn: sqlite3.Connection,
    orders: list[Any],
    now: str,
) -> None:
    for order_row in orders:
        conn.execute(
            "UPDATE orders SET status = 'CANCELLED', last_exchange_sync_at = ?, updated_at = ? WHERE order_pk = ?",
            (now, now, int(order_row.order_pk)),
        )


def _rebuild_protectives(
    *,
    conn: sqlite3.Connection,
    manager: ExchangeOrderManager,
    context: Any,
    residual_qty: float,
    now: str,
    channel_id: str,
    telegram_msg_id: str,
) -> int:
    op_result = ManagerOperationResult(attempt_key=context.attempt_key, action="reconciliation_rebuild")
    manager._rebuild_residual_protective_orders(
        conn=conn,
        context=context,
        residual_qty=float(residual_qty),
        result=op_result,
        now=now,
        channel_id=channel_id,
        telegram_msg_id=telegram_msg_id,
    )
    return len(op_result.created_orders)


def _warn_ambiguous(
    *,
    conn: sqlite3.Connection,
    context: Any,
    trade_result: TradeReconciliationResult,
    code: str,
    payload: dict[str, Any],
    now: str,
    channel_id: str,
    telegram_msg_id: str,
) -> None:
    trade_result.warnings.append(code)
    _insert_warning_row(
        conn,
        env=context.env,
        attempt_key=context.attempt_key,
        trader_id=context.trader_id,
        code=code,
        detail=payload,
        created_at=now,
    )
    _insert_event_row(
        conn,
        env=context.env,
        channel_id=channel_id,
        telegram_msg_id=telegram_msg_id,
        trader_id=context.trader_id,
        attempt_key=context.attempt_key,
        event_type="RECONCILIATION_WARNING",
        payload=payload,
        created_at=now,
    )
