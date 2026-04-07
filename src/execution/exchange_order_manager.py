"""Exchange-backed protective order manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
import json
import sqlite3
from typing import Any

from src.core.timeutils import utc_now_iso
from src.execution.exchange_gateway import ExchangeGateway, ExchangeOrder
from src.execution.freqtrade_normalizer import (
    FreqtradeSignalContext,
    FreqtradeUpdateDirective,
    load_context_by_attempt_key,
)
from src.execution.freqtrade_ui_mirror import mirror_trade_stoploss
from src.execution.protective_orders_mode import ProtectiveOrdersMode

ACTIVE_ORDER_STATUSES = {"NEW", "OPEN", "PARTIALLY_FILLED"}
TERMINAL_ORDER_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}


@dataclass(frozen=True, slots=True)
class ProtectiveOrderPlanItem:
    symbol: str
    purpose: str
    idx: int
    order_type: str
    side: str
    qty: float
    price: float | None
    trigger_price: float | None
    client_order_id: str


@dataclass(frozen=True, slots=True)
class EntryProtectiveOrderPlan:
    stop_order: ProtectiveOrderPlanItem | None
    take_profit_orders: tuple[ProtectiveOrderPlanItem, ...]

    @property
    def all_orders(self) -> tuple[ProtectiveOrderPlanItem, ...]:
        items: list[ProtectiveOrderPlanItem] = []
        if self.stop_order is not None:
            items.append(self.stop_order)
        items.extend(self.take_profit_orders)
        return tuple(items)


@dataclass(frozen=True, slots=True)
class ProtectiveOrderFailure:
    purpose: str
    idx: int
    client_order_id: str
    error: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "idx": self.idx,
            "client_order_id": self.client_order_id,
            "error": self.error,
        }


@dataclass(slots=True)
class ManagerOperationResult:
    attempt_key: str
    action: str
    created_orders: list[dict[str, Any]] = field(default_factory=list)
    cancelled_order_ids: list[str] = field(default_factory=list)
    failures: list[ProtectiveOrderFailure] = field(default_factory=list)

    @property
    def partial_failure(self) -> bool:
        return bool(self.failures) and bool(self.created_orders or self.cancelled_order_ids)

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "attempt_key": self.attempt_key,
            "action": self.action,
            "created_orders": list(self.created_orders),
            "cancelled_order_ids": list(self.cancelled_order_ids),
            "failures": [failure.as_dict() for failure in self.failures],
            "partial_failure": self.partial_failure,
        }


@dataclass(frozen=True, slots=True)
class _OrderRow:
    order_pk: int
    env: str
    attempt_key: str
    symbol: str
    side: str
    order_type: str
    purpose: str
    idx: int
    qty: float
    price: float | None
    trigger_price: float | None
    client_order_id: str
    exchange_order_id: str | None
    status: str


class ExchangeOrderManager:
    def __init__(self, *, db_path: str, gateway: ExchangeGateway) -> None:
        self._db_path = db_path
        self._gateway = gateway

    @property
    def gateway(self) -> ExchangeGateway:
        return self._gateway

    def sync_after_entry_fill(
        self,
        *,
        attempt_key: str,
        fill_qty: float,
        fill_price: float,
        channel_id: str = "freqtrade",
        telegram_msg_id: str = "0",
    ) -> ManagerOperationResult:
        context = self._load_exchange_managed_context(attempt_key)
        plan = build_entry_protective_order_plan(
            context=context,
            fill_qty=float(fill_qty),
            fill_price=float(fill_price),
        )
        result = ManagerOperationResult(attempt_key=attempt_key, action="sync_after_entry_fill")
        now = utc_now_iso()

        with sqlite3.connect(self._db_path) as conn:
            for item in plan.all_orders:
                self._create_plan_order(
                    conn=conn,
                    context=context,
                    plan_item=item,
                    result=result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="PROTECTIVE_ORDERS_SYNCED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        return result

    def apply_update(
        self,
        *,
        attempt_key: str,
        update_context: FreqtradeUpdateDirective | dict[str, Any],
        channel_id: str = "system",
        telegram_msg_id: str = "0",
    ) -> ManagerOperationResult:
        context = self._load_exchange_managed_context(attempt_key)
        directive = _coerce_update_context(update_context)
        if directive.intent == "U_MOVE_STOP":
            return self._apply_move_stop(
                context=context,
                directive=directive,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
        if directive.intent == "U_CLOSE_FULL":
            return self._apply_close_full(
                context=context,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
        if directive.intent == "U_CLOSE_PARTIAL":
            return self._apply_close_partial(
                context=context,
                directive=directive,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
        if directive.intent == "U_CANCEL_PENDING":
            return self._apply_cancel_pending(
                context=context,
                directive=directive,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
        raise ValueError(f"unsupported_update_intent:{directive.intent}")

    def cancel_all_protective_orders(
        self,
        *,
        attempt_key: str,
        channel_id: str = "system",
        telegram_msg_id: str = "0",
    ) -> ManagerOperationResult:
        context = self._load_exchange_managed_context(attempt_key)
        result = ManagerOperationResult(attempt_key=attempt_key, action="cancel_all_protective_orders")
        now = utc_now_iso()
        with sqlite3.connect(self._db_path) as conn:
            for order_row in self._load_active_protective_orders(conn, context=context):
                self._cancel_existing_order(
                    conn=conn,
                    context=context,
                    order_row=order_row,
                    result=result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="PROTECTIVE_ORDERS_CANCELLED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        return result

    def sync_after_tp_fill(
        self,
        *,
        attempt_key: str,
        tp_idx: int,
        closed_qty: float,
        fill_price: float,
        exchange_order_id: str | None = None,
        channel_id: str = "exchange",
        telegram_msg_id: str = "0",
    ) -> ManagerOperationResult:
        context = self._load_exchange_managed_context(attempt_key)
        result = ManagerOperationResult(attempt_key=attempt_key, action="sync_after_tp_fill")
        now = utc_now_iso()
        with sqlite3.connect(self._db_path) as conn:
            self._mark_take_profit_filled(
                conn=conn,
                context=context,
                tp_idx=int(tp_idx),
                fill_price=float(fill_price),
                exchange_order_id=exchange_order_id,
                now=now,
            )
            residual_size = max(0.0, self._load_position_size(conn, env=context.env, symbol=context.symbol or "") - float(closed_qty))
            self._update_position_size(
                conn=conn,
                env=context.env,
                symbol=context.symbol or "",
                size=residual_size,
                mark_price=float(fill_price),
                now=now,
            )
            self._update_trade_meta_after_tp(
                conn=conn,
                context=context,
                tp_idx=int(tp_idx),
                now=now,
            )
            if residual_size <= 0:
                self.cancel_all_protective_orders(
                    attempt_key=attempt_key,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
                self._close_trade(
                    conn=conn,
                    context=context,
                    state="CLOSED",
                    close_reason=f"TP{int(tp_idx) + 1}_HIT",
                    now=now,
                )
            else:
                self._rebuild_residual_protective_orders(
                    conn=conn,
                    context=context,
                    residual_qty=residual_size,
                    result=result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="TP_FILL_SYNCED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        return result

    def sync_after_stop_fill(
        self,
        *,
        attempt_key: str,
        closed_qty: float,
        fill_price: float,
        exchange_order_id: str | None = None,
        channel_id: str = "exchange",
        telegram_msg_id: str = "0",
    ) -> ManagerOperationResult:
        context = self._load_exchange_managed_context(attempt_key)
        result = ManagerOperationResult(attempt_key=attempt_key, action="sync_after_stop_fill")
        now = utc_now_iso()
        with sqlite3.connect(self._db_path) as conn:
            stop_order = self._load_active_stop_order(conn, context=context)
            if stop_order is not None:
                self._mark_order_status(
                    conn=conn,
                    order_pk=stop_order.order_pk,
                    status="FILLED",
                    exchange_order_id=exchange_order_id,
                    price=None,
                    trigger_price=float(fill_price),
                    now=now,
                )
            self._update_position_size(
                conn=conn,
                env=context.env,
                symbol=context.symbol or "",
                size=max(0.0, self._load_position_size(conn, env=context.env, symbol=context.symbol or "") - float(closed_qty)),
                mark_price=float(fill_price),
                now=now,
            )
            for order_row in self._load_active_protective_orders(conn, context=context):
                if order_row.purpose == "TP":
                    self._cancel_existing_order(
                        conn=conn,
                        context=context,
                        order_row=order_row,
                        result=result,
                        now=now,
                        channel_id=channel_id,
                        telegram_msg_id=telegram_msg_id,
                    )
            self._close_trade(conn=conn, context=context, state="CLOSED", close_reason="STOP_HIT", now=now)
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="STOP_FILL_SYNCED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        return result

    def _apply_move_stop(
        self,
        *,
        context: FreqtradeSignalContext,
        directive: FreqtradeUpdateDirective,
        channel_id: str,
        telegram_msg_id: str,
    ) -> ManagerOperationResult:
        result = ManagerOperationResult(attempt_key=context.attempt_key, action="apply_update:U_MOVE_STOP")
        now = utc_now_iso()
        with sqlite3.connect(self._db_path) as conn:
            new_stop = self._resolve_new_stop(conn, context=context, directive=directive)
            self._update_signal_stop(conn=conn, context=context, stop_level=new_stop, now=now)
            stop_order = self._load_active_stop_order(conn, context=context)
            plan_item = ProtectiveOrderPlanItem(
                symbol=context.symbol or "",
                purpose="SL",
                idx=0,
                order_type="STOP",
                side=_reduce_only_side(context.signal_side or "BUY"),
                qty=self._load_position_size(conn, env=context.env, symbol=context.symbol or ""),
                price=None,
                trigger_price=new_stop,
                client_order_id=_next_client_order_id(conn, env=context.env, attempt_key=context.attempt_key, purpose="SL", idx=0),
            )
            self._replace_order_conservatively(
                conn=conn,
                context=context,
                existing_order=stop_order,
                replacement=plan_item,
                result=result,
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="STOP_REPLACED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        mirror_trade_stoploss(
            attempt_key=context.attempt_key,
            stoploss_ref=new_stop,
            bot_db_path=self._db_path,
        )
        return result

    def _apply_close_full(
        self,
        *,
        context: FreqtradeSignalContext,
        channel_id: str,
        telegram_msg_id: str,
    ) -> ManagerOperationResult:
        result = ManagerOperationResult(attempt_key=context.attempt_key, action="apply_update:U_CLOSE_FULL")
        now = utc_now_iso()
        with sqlite3.connect(self._db_path) as conn:
            for order_row in self._load_active_protective_orders(conn, context=context):
                self._cancel_existing_order(
                    conn=conn,
                    context=context,
                    order_row=order_row,
                    result=result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
            size = self._load_position_size(conn, env=context.env, symbol=context.symbol or "")
            if size > 0:
                exit_order = self._gateway.create_reduce_only_market_order(
                    symbol=context.symbol or "",
                    side=_reduce_only_side(context.signal_side or "BUY"),
                    qty=size,
                    client_order_id=_next_client_order_id(conn, env=context.env, attempt_key=context.attempt_key, purpose="EXIT", idx=0),
                )
                self._persist_created_order(
                    conn=conn,
                    context=context,
                    purpose="EXIT",
                    idx=0,
                    requested_qty=size,
                    requested_price=None,
                    requested_trigger_price=None,
                    client_order_id=exit_order.client_order_id or _default_client_order_id(context.attempt_key, "EXIT", 0),
                    exchange_order=exit_order,
                    now=now,
                )
                result.created_orders.append(_exchange_order_to_dict(exit_order))
                if exit_order.status == "FILLED":
                    self._update_position_size(conn=conn, env=context.env, symbol=context.symbol or "", size=0.0, mark_price=exit_order.price, now=now)
                    self._close_trade(conn=conn, context=context, state="CLOSED", close_reason="FULL_CLOSE_REQUESTED", now=now)
                else:
                    self._close_trade(conn=conn, context=context, state="CLOSE_REQUESTED", close_reason="FULL_CLOSE_REQUESTED", now=now)
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="FULL_CLOSE_APPLIED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        return result

    def _apply_close_partial(
        self,
        *,
        context: FreqtradeSignalContext,
        directive: FreqtradeUpdateDirective,
        channel_id: str,
        telegram_msg_id: str,
    ) -> ManagerOperationResult:
        result = ManagerOperationResult(attempt_key=context.attempt_key, action="apply_update:U_CLOSE_PARTIAL")
        close_fraction = float(directive.close_fraction or 0.0)
        if close_fraction <= 0:
            raise ValueError("invalid_close_fraction")
        now = utc_now_iso()
        with sqlite3.connect(self._db_path) as conn:
            current_size = self._load_position_size(conn, env=context.env, symbol=context.symbol or "")
            close_qty = min(current_size, current_size * close_fraction)
            if close_qty <= 0:
                raise ValueError("missing_open_position")
            for order_row in self._load_active_protective_orders(conn, context=context):
                self._cancel_existing_order(
                    conn=conn,
                    context=context,
                    order_row=order_row,
                    result=result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
            exit_order = self._gateway.create_reduce_only_market_order(
                symbol=context.symbol or "",
                side=_reduce_only_side(context.signal_side or "BUY"),
                qty=close_qty,
                client_order_id=_next_client_order_id(conn, env=context.env, attempt_key=context.attempt_key, purpose="EXIT", idx=0),
            )
            self._persist_created_order(
                conn=conn,
                context=context,
                purpose="EXIT",
                idx=0,
                requested_qty=close_qty,
                requested_price=None,
                requested_trigger_price=None,
                client_order_id=exit_order.client_order_id or _default_client_order_id(context.attempt_key, "EXIT", 0),
                exchange_order=exit_order,
                now=now,
            )
            result.created_orders.append(_exchange_order_to_dict(exit_order))
            residual_size = max(0.0, current_size - close_qty if exit_order.status == "FILLED" else current_size)
            self._update_position_size(conn=conn, env=context.env, symbol=context.symbol or "", size=residual_size, mark_price=exit_order.price, now=now)
            self._update_trade_partial_close_meta(conn=conn, context=context, close_fraction=close_fraction, now=now)
            if residual_size > 0:
                self._rebuild_residual_protective_orders(
                    conn=conn,
                    context=context,
                    residual_qty=residual_size,
                    result=result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
                self._close_trade(conn=conn, context=context, state="OPEN", close_reason=None, now=now)
            else:
                self._close_trade(conn=conn, context=context, state="CLOSED", close_reason="PARTIAL_CLOSE_FILLED", now=now)
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="PARTIAL_CLOSE_APPLIED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        return result

    def _apply_cancel_pending(
        self,
        *,
        context: FreqtradeSignalContext,
        directive: FreqtradeUpdateDirective,
        channel_id: str,
        telegram_msg_id: str,
    ) -> ManagerOperationResult:
        del directive
        result = ManagerOperationResult(attempt_key=context.attempt_key, action="apply_update:U_CANCEL_PENDING")
        now = utc_now_iso()
        with sqlite3.connect(self._db_path) as conn:
            entry_order = self._load_entry_order(conn, context=context)
            if entry_order is None or context.trade_state == "OPEN":
                self._record_failure(
                    conn=conn,
                    context=context,
                    result=result,
                    failure=ProtectiveOrderFailure(purpose="ENTRY", idx=0, client_order_id=_default_client_order_id(context.attempt_key, "ENTRY", 0), error="cancel_pending_not_applicable"),
                    warning_code="exchange_manager_action_failed",
                    event_type="EXCHANGE_ORDER_ACTION_FAILED",
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
            else:
                self._cancel_existing_order(
                    conn=conn,
                    context=context,
                    order_row=entry_order,
                    result=result,
                    now=now,
                    channel_id=channel_id,
                    telegram_msg_id=telegram_msg_id,
                )
                conn.execute(
                    "UPDATE signals SET status = 'INVALID', updated_at = ? WHERE env = ? AND attempt_key = ?",
                    (now, context.env, context.attempt_key),
                )
            self._record_summary(
                conn=conn,
                context=context,
                result=result,
                success_event="PENDING_ENTRY_CANCELLED",
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                now=now,
            )
            conn.commit()
        return result

    def _replace_order_conservatively(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        existing_order: _OrderRow | None,
        replacement: ProtectiveOrderPlanItem,
        result: ManagerOperationResult,
        now: str,
        channel_id: str,
        telegram_msg_id: str,
    ) -> None:
        if existing_order is not None:
            self._cancel_existing_order(
                conn=conn,
                context=context,
                order_row=existing_order,
                result=result,
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
        self._create_plan_order(
            conn=conn,
            context=context,
            plan_item=replacement,
            result=result,
            now=now,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
        )

    def _rebuild_residual_protective_orders(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        residual_qty: float,
        result: ManagerOperationResult,
        now: str,
        channel_id: str,
        telegram_msg_id: str,
    ) -> None:
        for order_row in self._load_active_protective_orders(conn, context=context):
            self._cancel_existing_order(
                conn=conn,
                context=context,
                order_row=order_row,
                result=result,
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )
        remaining_tp_indices = self._remaining_take_profit_indices(conn, context=context)
        plan = build_entry_protective_order_plan(
            context=context,
            fill_qty=float(residual_qty),
            fill_price=self._load_position_entry_price(conn, env=context.env, symbol=context.symbol or "") or 0.0,
            include_tp_indices=remaining_tp_indices,
            conn=conn,
        )
        for item in plan.all_orders:
            self._create_plan_order(
                conn=conn,
                context=context,
                plan_item=item,
                result=result,
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )

    def _create_plan_order(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        plan_item: ProtectiveOrderPlanItem,
        result: ManagerOperationResult,
        now: str,
        channel_id: str,
        telegram_msg_id: str,
    ) -> None:
        try:
            exchange_order = self._create_exchange_order(plan_item)
            self._persist_created_order(
                conn=conn,
                context=context,
                purpose=plan_item.purpose,
                idx=plan_item.idx,
                requested_qty=plan_item.qty,
                requested_price=plan_item.price,
                requested_trigger_price=plan_item.trigger_price,
                client_order_id=plan_item.client_order_id,
                exchange_order=exchange_order,
                now=now,
            )
            result.created_orders.append(_exchange_order_to_dict(exchange_order))
            _insert_event_row(
                conn,
                env=context.env,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                trader_id=context.trader_id,
                attempt_key=context.attempt_key,
                event_type="EXCHANGE_ORDER_CREATED",
                payload={
                    "purpose": plan_item.purpose,
                    "idx": plan_item.idx,
                    "exchange_order_id": exchange_order.exchange_order_id,
                },
                created_at=now,
            )
        except Exception as exc:
            self._record_failure(
                conn=conn,
                context=context,
                result=result,
                failure=ProtectiveOrderFailure(
                    purpose=plan_item.purpose,
                    idx=plan_item.idx,
                    client_order_id=plan_item.client_order_id,
                    error=str(exc),
                ),
                warning_code="exchange_manager_order_create_failed",
                event_type="PROTECTIVE_ORDER_CREATE_FAILED",
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )

    def _create_exchange_order(self, plan_item: ProtectiveOrderPlanItem) -> ExchangeOrder:
        if plan_item.order_type == "STOP":
            return self._gateway.create_reduce_only_stop_order(
                symbol=plan_item.symbol,
                side=plan_item.side,
                qty=plan_item.qty,
                trigger_price=float(plan_item.trigger_price or 0.0),
                client_order_id=plan_item.client_order_id,
            )
        if plan_item.order_type == "LIMIT":
            return self._gateway.create_reduce_only_limit_order(
                symbol=plan_item.symbol,
                side=plan_item.side,
                qty=plan_item.qty,
                price=float(plan_item.price or 0.0),
                client_order_id=plan_item.client_order_id,
            )
        raise ValueError(f"unsupported_plan_item_type:{plan_item.order_type}")

    def _persist_created_order(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        purpose: str,
        idx: int,
        requested_qty: float,
        requested_price: float | None,
        requested_trigger_price: float | None,
        client_order_id: str,
        exchange_order: ExchangeOrder,
        now: str,
    ) -> None:
        _upsert_order_record(
            conn=conn,
            env=context.env,
            attempt_key=context.attempt_key,
            symbol=context.symbol or "",
            side=exchange_order.side or _reduce_only_side(context.signal_side or "BUY"),
            order_type=exchange_order.order_type,
            purpose=purpose,
            idx=idx,
            qty=float(exchange_order.qty or requested_qty),
            price=exchange_order.price if exchange_order.price is not None else requested_price,
            trigger_price=exchange_order.trigger_price if exchange_order.trigger_price is not None else requested_trigger_price,
            reduce_only=True,
            client_order_id=exchange_order.client_order_id or client_order_id,
            exchange_order_id=exchange_order.exchange_order_id,
            status=exchange_order.status,
            venue_status_raw=exchange_order.venue_status_raw,
            last_exchange_sync_at=now,
            created_at=now,
            updated_at=now,
        )

    def _cancel_existing_order(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        order_row: _OrderRow,
        result: ManagerOperationResult,
        now: str,
        channel_id: str,
        telegram_msg_id: str,
    ) -> None:
        try:
            if order_row.exchange_order_id:
                self._gateway.cancel_order(exchange_order_id=order_row.exchange_order_id, symbol=order_row.symbol)
            self._mark_order_status(conn=conn, order_pk=order_row.order_pk, status="CANCELLED", now=now)
            if order_row.exchange_order_id:
                result.cancelled_order_ids.append(order_row.exchange_order_id)
            _insert_event_row(
                conn,
                env=context.env,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                trader_id=context.trader_id,
                attempt_key=context.attempt_key,
                event_type="PROTECTIVE_ORDER_CANCELLED",
                payload={"purpose": order_row.purpose, "idx": order_row.idx, "exchange_order_id": order_row.exchange_order_id},
                created_at=now,
            )
        except Exception as exc:
            self._record_failure(
                conn=conn,
                context=context,
                result=result,
                failure=ProtectiveOrderFailure(
                    purpose=order_row.purpose,
                    idx=order_row.idx,
                    client_order_id=order_row.client_order_id,
                    error=str(exc),
                ),
                warning_code="exchange_manager_order_cancel_failed",
                event_type="PROTECTIVE_ORDER_CANCEL_FAILED",
                now=now,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
            )

    def _record_failure(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        result: ManagerOperationResult,
        failure: ProtectiveOrderFailure,
        warning_code: str,
        event_type: str,
        now: str,
        channel_id: str,
        telegram_msg_id: str,
    ) -> None:
        result.failures.append(failure)
        _insert_warning_row(
            conn,
            env=context.env,
            attempt_key=context.attempt_key,
            trader_id=context.trader_id,
            code=warning_code,
            detail=failure.as_dict(),
            created_at=now,
        )
        _insert_event_row(
            conn,
            env=context.env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=context.trader_id,
            attempt_key=context.attempt_key,
            event_type=event_type,
            payload=failure.as_dict(),
            created_at=now,
        )

    def _record_summary(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        result: ManagerOperationResult,
        success_event: str,
        channel_id: str,
        telegram_msg_id: str,
        now: str,
    ) -> None:
        if result.failures:
            _insert_warning_row(
                conn,
                env=context.env,
                attempt_key=context.attempt_key,
                trader_id=context.trader_id,
                code="exchange_manager_partial_failure",
                detail=result.as_dict(),
                created_at=now,
            )
        _insert_event_row(
            conn,
            env=context.env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=context.trader_id,
            attempt_key=context.attempt_key,
            event_type=success_event,
            payload=result.as_dict(),
            created_at=now,
        )

    def _load_exchange_managed_context(self, attempt_key: str) -> FreqtradeSignalContext:
        context = load_context_by_attempt_key(attempt_key, self._db_path)
        if context is None:
            raise ValueError("missing_context")
        if context.protective_orders_mode != ProtectiveOrdersMode.EXCHANGE_MANAGER.value:
            raise ValueError("protective_orders_mode_not_exchange_manager")
        return context

    def _load_active_protective_orders(self, conn: sqlite3.Connection, *, context: FreqtradeSignalContext) -> list[_OrderRow]:
        rows = conn.execute(
            """
            SELECT order_pk, env, attempt_key, symbol, side, order_type, purpose, idx, qty, price,
                   trigger_price, client_order_id, exchange_order_id, status
            FROM orders
            WHERE env = ?
              AND attempt_key = ?
              AND purpose IN ('SL', 'TP')
              AND status IN ('NEW', 'OPEN', 'PARTIALLY_FILLED')
            ORDER BY order_pk ASC
            """,
            (context.env, context.attempt_key),
        ).fetchall()
        return [_OrderRow(*row) for row in rows]

    def _load_active_stop_order(self, conn: sqlite3.Connection, *, context: FreqtradeSignalContext) -> _OrderRow | None:
        rows = [row for row in self._load_active_protective_orders(conn, context=context) if row.purpose == "SL"]
        return rows[-1] if rows else None

    def _load_entry_order(self, conn: sqlite3.Connection, *, context: FreqtradeSignalContext) -> _OrderRow | None:
        row = conn.execute(
            """
            SELECT order_pk, env, attempt_key, symbol, side, order_type, purpose, idx, qty, price,
                   trigger_price, client_order_id, exchange_order_id, status
            FROM orders
            WHERE env = ?
              AND attempt_key = ?
              AND purpose = 'ENTRY'
              AND status IN ('NEW', 'OPEN', 'PARTIALLY_FILLED')
            ORDER BY order_pk DESC
            LIMIT 1
            """,
            (context.env, context.attempt_key),
        ).fetchone()
        return _OrderRow(*row) if row else None

    def _remaining_take_profit_indices(self, conn: sqlite3.Connection, *, context: FreqtradeSignalContext) -> tuple[int, ...]:
        levels = _effective_take_profit_levels(context)
        filled = set()
        meta = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
        if isinstance(meta.get("tp_filled_indices"), list):
            filled = {int(value) for value in meta["tp_filled_indices"] if isinstance(value, (int, float))}
        return tuple(idx for idx in range(len(levels)) if idx not in filled)

    def _load_position_size(self, conn: sqlite3.Connection, *, env: str, symbol: str) -> float:
        row = conn.execute("SELECT size FROM positions WHERE env = ? AND symbol = ? LIMIT 1", (env, symbol)).fetchone()
        return float(row[0]) if row and isinstance(row[0], (int, float)) else 0.0

    def _load_position_entry_price(self, conn: sqlite3.Connection, *, env: str, symbol: str) -> float | None:
        row = conn.execute("SELECT entry_price FROM positions WHERE env = ? AND symbol = ? LIMIT 1", (env, symbol)).fetchone()
        return float(row[0]) if row and isinstance(row[0], (int, float)) else None

    def _resolve_new_stop(
        self,
        conn: sqlite3.Connection,
        *,
        context: FreqtradeSignalContext,
        directive: FreqtradeUpdateDirective,
    ) -> float:
        value = directive.new_stop_level
        if isinstance(value, (int, float)):
            return float(value)
        normalized = str(value or "").strip().upper()
        if normalized in {"ENTRY", "BE", "BREAKEVEN"}:
            entry_price = self._load_position_entry_price(conn, env=context.env, symbol=context.symbol or "")
            if entry_price is not None:
                return entry_price
        if context.stoploss_ref is not None:
            return float(context.stoploss_ref)
        raise ValueError("missing_stop_level")

    def _update_signal_stop(self, *, conn: sqlite3.Connection, context: FreqtradeSignalContext, stop_level: float, now: str) -> None:
        conn.execute(
            "UPDATE signals SET sl = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (float(stop_level), now, context.env, context.attempt_key),
        )

    def _update_position_size(self, *, conn: sqlite3.Connection, env: str, symbol: str, size: float, mark_price: float | None, now: str) -> None:
        conn.execute(
            "UPDATE positions SET size = ?, mark_price = COALESCE(?, mark_price), updated_at = ? WHERE env = ? AND symbol = ?",
            (float(size), mark_price, now, env, symbol),
        )

    def _update_trade_meta_after_tp(self, *, conn: sqlite3.Connection, context: FreqtradeSignalContext, tp_idx: int, now: str) -> None:
        meta = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
        current = meta.get("tp_filled_indices")
        filled = {int(value) for value in current if isinstance(value, (int, float))} if isinstance(current, list) else set()
        filled.add(int(tp_idx))
        meta["tp_filled_indices"] = sorted(filled)
        meta["last_tp_idx"] = int(tp_idx)
        meta["last_tp_fill_at"] = now
        conn.execute(
            "UPDATE trades SET meta_json = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (json.dumps(meta, ensure_ascii=False, sort_keys=True), now, context.env, context.attempt_key),
        )

    def _update_trade_partial_close_meta(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        close_fraction: float,
        now: str,
    ) -> None:
        meta = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
        meta["close_fraction"] = float(close_fraction)
        meta["last_partial_exit_fraction"] = float(close_fraction)
        meta["last_partial_exit_at"] = now
        conn.execute(
            "UPDATE trades SET meta_json = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (json.dumps(meta, ensure_ascii=False, sort_keys=True), now, context.env, context.attempt_key),
        )

    def _close_trade(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        state: str,
        close_reason: str | None,
        now: str,
    ) -> None:
        if state == "CLOSED":
            conn.execute(
                "UPDATE trades SET state = ?, close_reason = ?, closed_at = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
                (state, close_reason, now, now, context.env, context.attempt_key),
            )
            return
        conn.execute(
            "UPDATE trades SET state = ?, close_reason = COALESCE(?, close_reason), updated_at = ? WHERE env = ? AND attempt_key = ?",
            (state, close_reason, now, context.env, context.attempt_key),
        )

    def _mark_order_status(
        self,
        *,
        conn: sqlite3.Connection,
        order_pk: int,
        status: str,
        exchange_order_id: str | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE orders
            SET status = ?,
                exchange_order_id = COALESCE(?, exchange_order_id),
                price = COALESCE(?, price),
                trigger_price = COALESCE(?, trigger_price),
                last_exchange_sync_at = ?,
                updated_at = ?
            WHERE order_pk = ?
            """,
            (status, exchange_order_id, price, trigger_price, now, now, int(order_pk)),
        )

    def _mark_take_profit_filled(
        self,
        *,
        conn: sqlite3.Connection,
        context: FreqtradeSignalContext,
        tp_idx: int,
        fill_price: float,
        exchange_order_id: str | None,
        now: str,
    ) -> None:
        row = conn.execute(
            """
            SELECT order_pk
            FROM orders
            WHERE env = ?
              AND attempt_key = ?
              AND purpose = 'TP'
              AND idx = ?
            ORDER BY order_pk DESC
            LIMIT 1
            """,
            (context.env, context.attempt_key, int(tp_idx)),
        ).fetchone()
        if row is not None:
            self._mark_order_status(
                conn=conn,
                order_pk=int(row[0]),
                status="FILLED",
                exchange_order_id=exchange_order_id,
                price=float(fill_price),
                now=now,
            )


def build_entry_protective_order_plan(
    *,
    context: FreqtradeSignalContext,
    fill_qty: float,
    fill_price: float,
    include_tp_indices: tuple[int, ...] | None = None,
    conn: sqlite3.Connection | None = None,
) -> EntryProtectiveOrderPlan:
    del fill_price
    qty = max(0.0, float(fill_qty))
    stop_order: ProtectiveOrderPlanItem | None = None
    if context.symbol and context.stoploss_ref is not None and qty > 0:
        stop_order = ProtectiveOrderPlanItem(
            symbol=context.symbol,
            purpose="SL",
            idx=0,
            order_type="STOP",
            side=_reduce_only_side(context.signal_side or "BUY"),
            qty=qty,
            price=None,
            trigger_price=float(context.stoploss_ref),
            client_order_id=_default_client_order_id(context.attempt_key, "SL", 0) if conn is None else _next_client_order_id(conn, env=context.env, attempt_key=context.attempt_key, purpose="SL", idx=0),
        )

    levels = _effective_take_profit_levels(context)
    selected_indices = include_tp_indices if include_tp_indices is not None else tuple(range(len(levels)))
    tp_qtys = _distributed_take_profit_quantities(
        context=context,
        fill_qty=qty,
        all_level_count=len(levels),
        selected_indices=selected_indices,
    )
    tp_orders: list[ProtectiveOrderPlanItem] = []
    for idx, tp_qty in zip(selected_indices, tp_qtys):
        if idx < 0 or idx >= len(levels) or tp_qty <= 0 or not context.symbol:
            continue
        tp_orders.append(
            ProtectiveOrderPlanItem(
                symbol=context.symbol,
                purpose="TP",
                idx=int(idx),
                order_type="LIMIT",
                side=_reduce_only_side(context.signal_side or "BUY"),
                qty=float(tp_qty),
                price=float(levels[idx]),
                trigger_price=None,
                client_order_id=_default_client_order_id(context.attempt_key, "TP", int(idx)) if conn is None else _next_client_order_id(conn, env=context.env, attempt_key=context.attempt_key, purpose="TP", idx=int(idx)),
            )
        )
    return EntryProtectiveOrderPlan(stop_order=stop_order, take_profit_orders=tuple(tp_orders))


def _effective_take_profit_levels(context: FreqtradeSignalContext) -> tuple[float, ...]:
    levels = tuple(float(level) for level in context.take_profit_refs if isinstance(level, (int, float)))
    if not levels:
        return ()
    rules = _tp_handling_rules(context)
    mode = str(rules.get("tp_handling_mode") or "").strip().lower()
    if mode == "limit_to_max_levels":
        max_levels = rules.get("max_tp_levels")
        if isinstance(max_levels, (int, float)) and int(max_levels) > 0:
            return levels[: int(max_levels)]
    return levels


def _distributed_take_profit_quantities(
    *,
    context: FreqtradeSignalContext,
    fill_qty: float,
    all_level_count: int,
    selected_indices: tuple[int, ...],
) -> tuple[float, ...]:
    if fill_qty <= 0 or all_level_count <= 0 or not selected_indices:
        return ()
    fractions = _take_profit_close_fractions(context, all_level_count)
    selected_fractions = [fractions[idx] for idx in selected_indices if 0 <= idx < len(fractions)]
    residual_fraction = sum(selected_fractions)
    if residual_fraction <= 0:
        return ()
    qty_decimal = Decimal(str(float(fill_qty)))
    quantum = _quantity_quantum(fill_qty)
    normalized_fractions = [Decimal(str(fraction / residual_fraction)) for fraction in selected_fractions]

    distributed: list[float] = []
    allocated = Decimal("0")
    for idx, fraction in enumerate(normalized_fractions):
        if idx == len(normalized_fractions) - 1:
            qty = qty_decimal - allocated
        else:
            qty = (qty_decimal * fraction).quantize(quantum, rounding=ROUND_DOWN)
            allocated += qty
        distributed.append(float(max(qty, Decimal("0"))))
    return tuple(distributed)


def _take_profit_close_fractions(context: FreqtradeSignalContext, level_count: int) -> tuple[float, ...]:
    if level_count <= 0:
        return ()
    rules = _tp_handling_rules(context)
    distribution = rules.get("tp_close_distribution")
    raw_values: list[float] | None = None
    if isinstance(distribution, dict):
        candidate = distribution.get(level_count)
        if candidate is None:
            candidate = distribution.get(str(level_count))
        if isinstance(candidate, list) and len(candidate) == level_count:
            parsed = [float(value) for value in candidate if isinstance(value, (int, float))]
            if len(parsed) == level_count and sum(parsed) > 0:
                raw_values = parsed
    if raw_values is None:
        equal_share = 1.0 / level_count
        return tuple(equal_share for _ in range(level_count))
    total = sum(raw_values)
    return tuple(value / total for value in raw_values)


def _tp_handling_rules(context: FreqtradeSignalContext) -> dict[str, Any]:
    rules = context.management_rules if isinstance(context.management_rules, dict) else {}
    tp_rules = rules.get("tp_handling")
    return tp_rules if isinstance(tp_rules, dict) else {}


def _quantity_quantum(fill_qty: float) -> Decimal:
    text = format(float(fill_qty), ".12f").rstrip("0").rstrip(".")
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    decimals = max(decimals, 3)
    return Decimal("1").scaleb(-decimals)


def _upsert_order_record(
    *,
    conn: sqlite3.Connection,
    env: str,
    attempt_key: str,
    symbol: str,
    side: str,
    order_type: str,
    purpose: str,
    idx: int,
    qty: float,
    price: float | None,
    trigger_price: float | None,
    reduce_only: bool,
    client_order_id: str,
    exchange_order_id: str | None,
    status: str,
    venue_status_raw: str | None,
    last_exchange_sync_at: str | None,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO orders(
          env, attempt_key, symbol, side, order_type, purpose, idx, qty, price, trigger_price,
          reduce_only, client_order_id, exchange_order_id, status, venue_status_raw,
          last_exchange_sync_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            env,
            attempt_key,
            symbol,
            side,
            order_type,
            purpose,
            idx,
            qty,
            price,
            trigger_price,
            1 if reduce_only else 0,
            client_order_id,
            exchange_order_id,
            status,
            venue_status_raw,
            last_exchange_sync_at,
            created_at,
            updated_at,
        ),
    )


def _insert_event_row(
    conn: sqlite3.Connection,
    *,
    env: str,
    channel_id: str,
    telegram_msg_id: str,
    trader_id: str,
    attempt_key: str,
    event_type: str,
    payload: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO events(
          env, channel_id, telegram_msg_id, trader_id, trader_prefix, attempt_key, event_type, payload_json, confidence, created_at
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 1.0, ?)
        """,
        (env, channel_id, telegram_msg_id, trader_id, attempt_key, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), created_at),
    )


def _insert_warning_row(
    conn: sqlite3.Connection,
    *,
    env: str,
    attempt_key: str,
    trader_id: str,
    code: str,
    detail: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        "INSERT INTO warnings(env, attempt_key, trader_id, code, severity, detail_json, created_at) VALUES (?, ?, ?, ?, 'WARN', ?, ?)",
        (env, attempt_key, trader_id, code, json.dumps(detail, ensure_ascii=False, sort_keys=True), created_at),
    )


def _load_trade_meta(conn: sqlite3.Connection, *, env: str, attempt_key: str) -> dict[str, Any]:
    row = conn.execute("SELECT meta_json FROM trades WHERE env = ? AND attempt_key = ? LIMIT 1", (env, attempt_key)).fetchone()
    if not row or not row[0]:
        return {}
    try:
        parsed = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _default_client_order_id(attempt_key: str, purpose: str, idx: int) -> str:
    return f"{attempt_key}:{purpose}:{idx}"


def _next_client_order_id(conn: sqlite3.Connection, *, env: str, attempt_key: str, purpose: str, idx: int) -> str:
    base = _default_client_order_id(attempt_key, purpose, idx)
    row = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE env = ? AND attempt_key = ? AND purpose = ? AND idx = ?",
        (env, attempt_key, purpose, int(idx)),
    ).fetchone()
    count = int(row[0]) if row and isinstance(row[0], (int, float)) else 0
    return base if count <= 0 else f"{base}:R{count}"


def _reduce_only_side(entry_side: str) -> str:
    normalized = str(entry_side).strip().upper()
    if normalized in {"BUY", "LONG"}:
        return "SELL"
    if normalized in {"SELL", "SHORT"}:
        return "BUY"
    return "SELL"


def _exchange_order_to_dict(order: ExchangeOrder) -> dict[str, Any]:
    return {
        "exchange_order_id": order.exchange_order_id,
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "order_type": order.order_type,
        "qty": order.qty,
        "price": order.price,
        "trigger_price": order.trigger_price,
        "reduce_only": order.reduce_only,
        "status": order.status,
    }


def _coerce_update_context(update_context: FreqtradeUpdateDirective | dict[str, Any]) -> FreqtradeUpdateDirective:
    if isinstance(update_context, FreqtradeUpdateDirective):
        return update_context
    if not isinstance(update_context, dict):
        raise TypeError("update_context must be FreqtradeUpdateDirective or dict")
    intent = str(update_context.get("intent") or "").strip().upper()
    return FreqtradeUpdateDirective(
        update_op_signal_id=int(update_context.get("update_op_signal_id") or 0),
        intent=intent,
        eligibility=str(update_context.get("eligibility") or "ELIGIBLE"),
        close_fraction=float(update_context["close_fraction"]) if isinstance(update_context.get("close_fraction"), (int, float)) else None,
        cancel_scope=str(update_context["cancel_scope"]) if isinstance(update_context.get("cancel_scope"), str) else None,
        new_stop_level=update_context.get("new_stop_level"),
    )
