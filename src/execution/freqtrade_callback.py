"""Minimal DB callback writer for freqtrade bridge events."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Callable

from src.core.timeutils import utc_now_iso
from src.execution.freqtrade_normalizer import FreqtradeSignalContext, load_context_by_attempt_key
from src.execution.protective_orders_mode import (
    ProtectiveOrdersMode,
    resolve_protective_orders_mode,
    strategy_owns_stoploss,
    strategy_owns_take_profit,
)
from src.execution.machine_event import MachineEventAction, evaluate_rules
from src.execution.update_applier import UpdateApplyResult, apply_update_plan

_TP_ORDER_TAG_RE = re.compile(r"^(?P<attempt_key>.+):TP:(?P<idx>\d+)$")


def order_filled_callback(
    *,
    db_path: str,
    attempt_key: str,
    qty: float,
    fill_price: float,
    client_order_id: str | None = None,
    exchange_order_id: str | None = None,
    order_type: str = "LIMIT",
    execution_mode: str = "FREQTRADE",
    protective_orders_mode: str | None = None,
    order_manager: Any | None = None,
    margin_mode: str = "isolated",
    channel_id: str = "freqtrade",
    telegram_msg_id: str = "0",
    busy_retries: int = 5,
    busy_sleep_s: float = 0.05,
) -> dict[str, Any]:
    """Persist an entry fill event."""
    context = load_context_by_attempt_key(attempt_key, db_path)
    if context is None:
        return {"ok": False, "error": "missing_context"}
    if context.signal_status != "PENDING":
        return {"ok": False, "error": "signal_not_pending"}
    if context.cancel_pending_requested:
        return {"ok": False, "error": "signal_cancelled_before_fill"}

    now = utc_now_iso()
    resolved_protective_orders_mode = resolve_protective_orders_mode(
        persisted_mode=protective_orders_mode,
    )

    def _writer(conn: sqlite3.Connection) -> dict[str, Any]:
        conn.execute(
            "UPDATE signals SET status = 'ACTIVE', updated_at = ? WHERE env = ? AND attempt_key = ?",
            (now, context.env, context.attempt_key),
        )
        _upsert_trade(
            conn,
            context=context,
            execution_mode=execution_mode,
            protective_orders_mode=resolved_protective_orders_mode.value,
            opened_at=now,
        )
        _upsert_order(
            conn,
            env=context.env,
            attempt_key=context.attempt_key,
            symbol=context.symbol or "",
            side=context.signal_side or _entry_side_from_context(context),
            order_type=order_type,
            purpose="ENTRY",
            idx=0,
            qty=float(qty),
            price=float(fill_price),
            trigger_price=None,
            reduce_only=False,
            client_order_id=client_order_id or _default_client_order_id(context.attempt_key, "ENTRY", 0),
            exchange_order_id=exchange_order_id,
            status="FILLED",
            now=now,
        )
        _ensure_protective_orders(
            conn,
            context=context,
            qty=float(qty),
            protective_orders_mode=resolved_protective_orders_mode.value,
            now=now,
        )
        _upsert_position(
            conn,
            env=context.env,
            symbol=context.symbol or "",
            side=context.signal_side or _entry_side_from_context(context),
            size=float(qty),
            entry_price=float(fill_price),
            mark_price=float(fill_price),
            leverage=float(context.leverage or 1),
            margin_mode=margin_mode,
            updated_at=now,
        )
        _insert_event(
            conn,
            env=context.env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=context.trader_id,
            trader_prefix=None,
            attempt_key=context.attempt_key,
            event_type="ENTRY_FILLED",
            payload={
                "fill_price": float(fill_price),
                "protective_orders_mode": resolved_protective_orders_mode.value,
                "qty": float(qty),
                "source": "freqtrade_callback",
            },
            created_at=now,
        )
        return {"ok": True, "attempt_key": context.attempt_key}

    result = _with_sqlite_retry(
        db_path=db_path,
        writer=_writer,
        retries=busy_retries,
        sleep_s=busy_sleep_s,
    )
    if not result.get("ok"):
        return result
    if resolved_protective_orders_mode is not ProtectiveOrdersMode.EXCHANGE_MANAGER:
        return result
    if order_manager is None:
        issue = {"ok": False, "error": "exchange_order_manager_not_configured"}
        _record_exchange_manager_issue(
            db_path=db_path,
            context=context,
            code="exchange_manager_missing",
            event_type="PROTECTIVE_ORDER_MANAGER_MISSING",
            payload=issue,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            retries=busy_retries,
            sleep_s=busy_sleep_s,
        )
        result["manager_result"] = issue
        return result
    try:
        manager_result = order_manager.sync_after_entry_fill(
            attempt_key=context.attempt_key,
            fill_qty=float(qty),
            fill_price=float(fill_price),
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
        )
    except Exception as exc:  # pragma: no cover - guarded via tests with fake manager
        issue = {"ok": False, "error": str(exc)}
        _record_exchange_manager_issue(
            db_path=db_path,
            context=context,
            code="exchange_manager_sync_failed",
            event_type="PROTECTIVE_ORDER_SYNC_FAILED",
            payload=issue,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            retries=busy_retries,
            sleep_s=busy_sleep_s,
        )
        result["manager_result"] = issue
        return result
    result["manager_result"] = manager_result.as_dict() if hasattr(manager_result, "as_dict") else manager_result
    return result


def partial_exit_callback(
    *,
    db_path: str,
    attempt_key: str,
    close_fraction: float,
    remaining_qty: float,
    closed_qty: float | None = None,
    exit_price: float | None = None,
    realized_pnl: float | None = None,
    tp_idx: int | None = None,
    client_order_id: str | None = None,
    exchange_order_id: str | None = None,
    channel_id: str = "freqtrade",
    telegram_msg_id: str = "0",
    busy_retries: int = 5,
    busy_sleep_s: float = 0.05,
) -> dict[str, Any]:
    """Persist a partial exit fill without closing the trade."""
    context = load_context_by_attempt_key(attempt_key, db_path)
    if context is None:
        return {"ok": False, "error": "missing_context"}

    normalized_fraction = max(0.0, min(float(close_fraction), 1.0))
    normalized_remaining_qty = max(0.0, float(remaining_qty))
    if normalized_fraction <= 0.0:
        return {"ok": False, "error": "invalid_close_fraction"}

    now = utc_now_iso()
    event_payload: dict[str, Any] = {
        "close_fraction": normalized_fraction,
        "remaining_qty": normalized_remaining_qty,
        "source": "freqtrade_callback",
    }
    if exit_price is not None:
        event_payload["exit_price"] = float(exit_price)
    if closed_qty is not None:
        event_payload["closed_qty"] = float(closed_qty)
    if context.partial_close_update_id is not None:
        event_payload["update_op_signal_id"] = int(context.partial_close_update_id)
    if tp_idx is not None:
        event_payload["tp_idx"] = int(tp_idx)

    def _writer(conn: sqlite3.Connection) -> dict[str, Any]:
        meta_json = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
        current_position_size = _load_position_size(conn, env=context.env, symbol=context.symbol or "")
        exit_qty = float(closed_qty) if closed_qty is not None else max(0.0, current_position_size - normalized_remaining_qty)
        meta_json["close_fraction"] = normalized_fraction
        meta_json["last_partial_exit_fraction"] = normalized_fraction
        meta_json["last_partial_exit_at"] = now
        if context.partial_close_update_id is not None:
            meta_json["last_partial_exit_update_id"] = int(context.partial_close_update_id)
        if tp_idx is not None:
            _mark_take_profit_filled(
                conn,
                context=context,
                tp_idx=int(tp_idx),
                fill_price=float(exit_price) if exit_price is not None else None,
                exchange_order_id=exchange_order_id,
                now=now,
            )
            meta_json["last_tp_idx"] = int(tp_idx)
            meta_json["last_tp_fill_at"] = now
            filled = meta_json.get("tp_filled_indices")
            filled_set = {
                int(value)
                for value in filled
                if isinstance(value, (int, float))
            } if isinstance(filled, list) else set()
            filled_set.add(int(tp_idx))
            meta_json["tp_filled_indices"] = sorted(filled_set)

        trade_state = "OPEN" if normalized_remaining_qty > 0 else "CLOSED"
        if trade_state == "OPEN":
            conn.execute(
                """
                UPDATE trades
                SET state = 'OPEN',
                    meta_json = ?,
                    updated_at = ?
                WHERE env = ?
                  AND attempt_key = ?
                """,
                (json.dumps(meta_json, ensure_ascii=False, sort_keys=True), now, context.env, context.attempt_key),
            )
        else:
            conn.execute(
                """
                UPDATE trades
                SET state = 'CLOSED',
                    close_reason = 'PARTIAL_CLOSE_FILLED',
                    closed_at = ?,
                    meta_json = ?,
                    updated_at = ?
                WHERE env = ?
                  AND attempt_key = ?
                """,
                (now, json.dumps(meta_json, ensure_ascii=False, sort_keys=True), now, context.env, context.attempt_key),
            )

        _upsert_order(
            conn,
            env=context.env,
            attempt_key=context.attempt_key,
            symbol=context.symbol or "",
            side=_reduce_only_side(context.signal_side or _entry_side_from_context(context)),
            order_type="LIMIT",
            purpose="EXIT",
            idx=0,
            qty=exit_qty,
            price=float(exit_price) if exit_price is not None else None,
            trigger_price=None,
            reduce_only=True,
            client_order_id=client_order_id or _default_client_order_id(context.attempt_key, "EXIT", 0),
            exchange_order_id=exchange_order_id,
            status="FILLED",
            now=now,
        )
        _upsert_position(
            conn,
            env=context.env,
            symbol=context.symbol or "",
            side=context.signal_side or _entry_side_from_context(context),
            size=normalized_remaining_qty,
            entry_price=None,
            mark_price=float(exit_price) if exit_price is not None else None,
            leverage=float(context.leverage or 1),
            margin_mode="isolated",
            updated_at=now,
        )
        if realized_pnl is not None:
            conn.execute(
                """
                UPDATE positions
                SET realized_pnl = ?,
                    updated_at = ?
                WHERE env = ?
                  AND symbol = ?
                """,
                (float(realized_pnl), now, context.env, context.symbol),
            )
        _insert_event(
            conn,
            env=context.env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=context.trader_id,
            trader_prefix=None,
            attempt_key=context.attempt_key,
            event_type="PARTIAL_CLOSE_FILLED",
            payload=event_payload,
            created_at=now,
        )
        return {"ok": True, "attempt_key": context.attempt_key, "trade_state": trade_state}

    result = _with_sqlite_retry(
        db_path=db_path,
        writer=_writer,
        retries=busy_retries,
        sleep_s=busy_sleep_s,
    )
    if result.get("ok") and tp_idx is not None:
        _fire_tp_machine_events(
            db_path=db_path,
            context=context,
            tp_idx=tp_idx,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            busy_retries=busy_retries,
            busy_sleep_s=busy_sleep_s,
        )
    return result


def trade_exit_callback(
    *,
    db_path: str,
    attempt_key: str,
    close_reason: str = "POSITION_CLOSED",
    exit_price: float | None = None,
    realized_pnl: float | None = None,
    tp_idx: int | None = None,
    exchange_order_id: str | None = None,
    channel_id: str = "freqtrade",
    telegram_msg_id: str = "0",
    busy_retries: int = 5,
    busy_sleep_s: float = 0.05,
) -> dict[str, Any]:
    """Persist a full trade close."""
    context = load_context_by_attempt_key(attempt_key, db_path)
    if context is None:
        return {"ok": False, "error": "missing_context"}

    update_result = _apply_update_plan_with_retry(
        plan={
            "message_type": "CALLBACK",
            "actions": ["ACT_MARK_POSITION_CLOSED"],
            "events": ["POSITION_CLOSED"],
            "target_refs": [],
        },
        db_path=db_path,
        env=context.env,
        trader_id=context.trader_id,
        channel_id=channel_id,
        telegram_msg_id=telegram_msg_id,
        target_attempt_keys=[context.attempt_key],
        retries=busy_retries,
        sleep_s=busy_sleep_s,
    )
    if update_result.errors:
        return {"ok": False, "error": update_result.errors[0], "update_result": update_result.as_dict()}

    now = utc_now_iso()

    def _writer(conn: sqlite3.Connection) -> dict[str, Any]:
        if tp_idx is not None:
            _mark_take_profit_filled(
                conn,
                context=context,
                tp_idx=int(tp_idx),
                fill_price=float(exit_price) if exit_price is not None else None,
                exchange_order_id=exchange_order_id,
                now=now,
            )
            meta_json = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
            filled = meta_json.get("tp_filled_indices")
            filled_set = {
                int(value)
                for value in filled
                if isinstance(value, (int, float))
            } if isinstance(filled, list) else set()
            filled_set.add(int(tp_idx))
            meta_json["last_tp_idx"] = int(tp_idx)
            meta_json["last_tp_fill_at"] = now
            meta_json["tp_filled_indices"] = sorted(filled_set)
            conn.execute(
                """
                UPDATE trades
                SET meta_json = ?,
                    updated_at = ?
                WHERE env = ?
                  AND attempt_key = ?
                """,
                (json.dumps(meta_json, ensure_ascii=False, sort_keys=True), now, context.env, context.attempt_key),
            )
        if close_reason != "POSITION_CLOSED":
            conn.execute(
                "UPDATE trades SET close_reason = ?, closed_at = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
                (close_reason, now, now, context.env, context.attempt_key),
            )
        if exit_price is not None or realized_pnl is not None:
            conn.execute(
                """
                UPDATE positions
                SET mark_price = COALESCE(?, mark_price),
                    realized_pnl = COALESCE(?, realized_pnl),
                    updated_at = ?
                WHERE env = ?
                  AND symbol = ?
                """,
                (exit_price, realized_pnl, now, context.env, context.symbol),
            )
        return {"ok": True, "attempt_key": context.attempt_key, "update_result": update_result.as_dict()}

    return _with_sqlite_retry(
        db_path=db_path,
        writer=_writer,
        retries=busy_retries,
        sleep_s=busy_sleep_s,
    )


def stoploss_callback(
    *,
    db_path: str,
    attempt_key: str,
    qty: float,
    stop_price: float,
    client_order_id: str | None = None,
    exchange_order_id: str | None = None,
    channel_id: str = "freqtrade",
    telegram_msg_id: str = "0",
    busy_retries: int = 5,
    busy_sleep_s: float = 0.05,
) -> dict[str, Any]:
    """Persist a stoploss hit."""
    context = load_context_by_attempt_key(attempt_key, db_path)
    if context is None:
        return {"ok": False, "error": "missing_context"}

    now = utc_now_iso()

    def _prewrite(conn: sqlite3.Connection) -> dict[str, Any]:
        _upsert_order(
            conn,
            env=context.env,
            attempt_key=context.attempt_key,
            symbol=context.symbol or "",
            side=_reduce_only_side(context.signal_side or _entry_side_from_context(context)),
            order_type="STOP",
            purpose="SL",
            idx=0,
            qty=float(qty),
            price=None,
            trigger_price=float(stop_price),
            reduce_only=True,
            client_order_id=client_order_id or _default_client_order_id(context.attempt_key, "SL", 0),
            exchange_order_id=exchange_order_id,
            status="FILLED",
            now=now,
        )
        return {"ok": True}

    _with_sqlite_retry(
        db_path=db_path,
        writer=_prewrite,
        retries=busy_retries,
        sleep_s=busy_sleep_s,
    )

    update_result = _apply_update_plan_with_retry(
        plan={
            "message_type": "CALLBACK",
            "actions": ["ACT_MARK_STOP_HIT"],
            "events": ["STOP_HIT"],
            "target_refs": [],
        },
        db_path=db_path,
        env=context.env,
        trader_id=context.trader_id,
        channel_id=channel_id,
        telegram_msg_id=telegram_msg_id,
        target_attempt_keys=[context.attempt_key],
        retries=busy_retries,
        sleep_s=busy_sleep_s,
    )
    if update_result.errors:
        return {"ok": False, "error": update_result.errors[0], "update_result": update_result.as_dict()}

    def _writer(conn: sqlite3.Connection) -> dict[str, Any]:
        _upsert_position(
            conn,
            env=context.env,
            symbol=context.symbol or "",
            side=context.signal_side or _entry_side_from_context(context),
            size=0.0,
            entry_price=None,
            mark_price=float(stop_price),
            leverage=float(context.leverage or 1),
            margin_mode="isolated",
            updated_at=now,
        )
        return {"ok": True, "attempt_key": context.attempt_key, "update_result": update_result.as_dict()}

    result = _with_sqlite_retry(
        db_path=db_path,
        writer=_writer,
        retries=busy_retries,
        sleep_s=busy_sleep_s,
    )
    if result.get("ok"):
        _fire_sl_machine_events(
            db_path=db_path,
            context=context,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            busy_retries=busy_retries,
            busy_sleep_s=busy_sleep_s,
        )
    return result


def _with_sqlite_retry(
    *,
    db_path: str,
    writer: Callable[[sqlite3.Connection], dict[str, Any]],
    retries: int,
    sleep_s: float,
) -> dict[str, Any]:
    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            with sqlite3.connect(db_path) as conn:
                result = writer(conn)
                conn.commit()
                return result
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "busy" not in message and "locked" not in message:
                return {"ok": False, "error": str(exc)}
            last_error = str(exc)
            if attempt >= retries:
                break
            time.sleep(sleep_s)
    return {"ok": False, "error": last_error or "sqlite_busy_retry_exhausted"}


def _apply_update_plan_with_retry(
    *,
    plan: dict[str, Any],
    db_path: str,
    env: str,
    trader_id: str,
    channel_id: str,
    telegram_msg_id: str,
    target_attempt_keys: list[str],
    retries: int,
    sleep_s: float,
) -> UpdateApplyResult:
    last_result = UpdateApplyResult(errors=["apply_update_plan_not_run"])
    for attempt in range(retries + 1):
        result = apply_update_plan(
            plan,
            db_path,
            env=env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=trader_id,
            target_attempt_keys=target_attempt_keys,
        )
        if not result.errors:
            return result
        last_result = result
        joined_errors = " ".join(result.errors).lower()
        if "busy" not in joined_errors and "locked" not in joined_errors:
            return result
        if attempt >= retries:
            break
        time.sleep(sleep_s)
    return last_result


def _record_exchange_manager_issue(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    code: str,
    event_type: str,
    payload: dict[str, Any],
    channel_id: str,
    telegram_msg_id: str,
    retries: int,
    sleep_s: float,
) -> None:
    now = utc_now_iso()

    def _writer(conn: sqlite3.Connection) -> dict[str, Any]:
        _insert_warning(
            conn,
            env=context.env,
            attempt_key=context.attempt_key,
            trader_id=context.trader_id,
            code=code,
            severity="WARN",
            detail=payload,
            created_at=now,
        )
        _insert_event(
            conn,
            env=context.env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=context.trader_id,
            trader_prefix=None,
            attempt_key=context.attempt_key,
            event_type=event_type,
            payload=payload,
            created_at=now,
        )
        return {"ok": True}

    _with_sqlite_retry(
        db_path=db_path,
        writer=_writer,
        retries=retries,
        sleep_s=sleep_s,
    )


def _take_profit_idx_from_order_tag(order_tag: str | None, attempt_key: str) -> int | None:
    if not isinstance(order_tag, str):
        return None
    match = _TP_ORDER_TAG_RE.match(order_tag.strip())
    if match is None or match.group("attempt_key") != attempt_key:
        return None
    return int(match.group("idx"))


def _upsert_trade(
    conn: sqlite3.Connection,
    *,
    context: FreqtradeSignalContext,
    execution_mode: str,
    protective_orders_mode: str,
    opened_at: str,
) -> None:
    existing = conn.execute(
        "SELECT trade_id, protective_orders_mode FROM trades WHERE env = ? AND attempt_key = ? LIMIT 1",
        (context.env, context.attempt_key),
    ).fetchone()
    effective_protective_orders_mode = (
        str(existing[1])
        if existing and existing[1] is not None
        else protective_orders_mode
    )
    meta_json = {
        "entry_tag": context.entry_tag,
        "protective_orders_mode": effective_protective_orders_mode,
        "source": "freqtrade_callback",
    }
    if existing:
        conn.execute(
            """
            UPDATE trades
            SET state = 'OPEN',
                opened_at = COALESCE(opened_at, ?),
                protective_orders_mode = COALESCE(protective_orders_mode, ?),
                meta_json = ?,
                updated_at = ?
            WHERE env = ?
              AND attempt_key = ?
            """,
            (
                opened_at,
                protective_orders_mode,
                json.dumps(meta_json, ensure_ascii=False, sort_keys=True),
                opened_at,
                context.env,
                context.attempt_key,
            ),
        )
        return

    conn.execute(
        """
        INSERT INTO trades(
          env, attempt_key, trader_id, symbol, side, execution_mode, state, protective_orders_mode,
          entry_zone_policy, non_chase_policy, opened_at, meta_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, 'Z1', 'NI3', ?, ?, ?, ?)
        """,
        (
            context.env,
            context.attempt_key,
            context.trader_id,
            context.symbol,
            context.signal_side or _entry_side_from_context(context),
            execution_mode,
            protective_orders_mode,
            opened_at,
            json.dumps(meta_json, ensure_ascii=False, sort_keys=True),
            opened_at,
            opened_at,
        ),
    )


def _ensure_protective_orders(
    conn: sqlite3.Connection,
    *,
    context: FreqtradeSignalContext,
    qty: float,
    protective_orders_mode: str,
    now: str,
) -> None:
    if not context.symbol:
        return
    if not strategy_owns_stoploss(persisted_mode=protective_orders_mode) and not strategy_owns_take_profit(
        persisted_mode=protective_orders_mode
    ):
        return

    reduce_only_side = _reduce_only_side(context.signal_side or _entry_side_from_context(context))
    if context.stoploss_ref is not None:
        _upsert_order(
            conn,
            env=context.env,
            attempt_key=context.attempt_key,
            symbol=context.symbol,
            side=reduce_only_side,
            order_type="STOP",
            purpose="SL",
            idx=0,
            qty=qty,
            price=None,
            trigger_price=float(context.stoploss_ref),
            reduce_only=True,
            client_order_id=_default_client_order_id(context.attempt_key, "SL", 0),
            exchange_order_id=None,
            status="NEW",
            now=now,
        )

    for idx, take_profit in enumerate(context.take_profit_refs):
        _upsert_order(
            conn,
            env=context.env,
            attempt_key=context.attempt_key,
            symbol=context.symbol,
            side=reduce_only_side,
            order_type="LIMIT",
            purpose="TP",
            idx=idx,
            qty=qty,
            price=float(take_profit),
            trigger_price=None,
            reduce_only=True,
            client_order_id=_default_client_order_id(context.attempt_key, "TP", idx),
            exchange_order_id=None,
            status="NEW",
            now=now,
        )


def _mark_take_profit_filled(
    conn: sqlite3.Connection,
    *,
    context: FreqtradeSignalContext,
    tp_idx: int,
    fill_price: float | None,
    exchange_order_id: str | None,
    now: str,
) -> None:
    if tp_idx < 0:
        return
    conn.execute(
        """
        UPDATE orders
        SET status = 'FILLED',
            price = COALESCE(?, price),
            exchange_order_id = COALESCE(?, exchange_order_id),
            updated_at = ?
        WHERE env = ?
          AND attempt_key = ?
          AND purpose = 'TP'
          AND idx = ?
        """,
        (fill_price, exchange_order_id, now, context.env, context.attempt_key, int(tp_idx)),
    )


def _upsert_order(
    conn: sqlite3.Connection,
    *,
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
    now: str,
) -> None:
    existing = conn.execute(
        "SELECT order_pk FROM orders WHERE env = ? AND client_order_id = ? LIMIT 1",
        (env, client_order_id),
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
                updated_at = ?
            WHERE env = ?
              AND client_order_id = ?
            """,
            (exchange_order_id, status, qty, price, trigger_price, now, env, client_order_id),
        )
        return

    conn.execute(
        """
        INSERT INTO orders(
          env, attempt_key, symbol, side, order_type, purpose, idx, qty, price, trigger_price,
          reduce_only, client_order_id, exchange_order_id, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            now,
            now,
        ),
    )


def _upsert_position(
    conn: sqlite3.Connection,
    *,
    env: str,
    symbol: str,
    side: str,
    size: float,
    entry_price: float | None,
    mark_price: float | None,
    leverage: float,
    margin_mode: str,
    updated_at: str,
) -> None:
    existing = conn.execute(
        "SELECT position_pk FROM positions WHERE env = ? AND symbol = ? LIMIT 1",
        (env, symbol),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE positions
            SET side = ?,
                size = ?,
                entry_price = COALESCE(?, entry_price),
                mark_price = COALESCE(?, mark_price),
                leverage = ?,
                margin_mode = ?,
                updated_at = ?
            WHERE env = ?
              AND symbol = ?
            """,
            (side, size, entry_price, mark_price, leverage, margin_mode, updated_at, env, symbol),
        )
        return

    conn.execute(
        """
        INSERT INTO positions(
          env, symbol, side, size, entry_price, mark_price, unrealized_pnl, realized_pnl, leverage, margin_mode, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?)
        """,
        (env, symbol, side, size, entry_price, mark_price, leverage, margin_mode, updated_at),
    )


def _insert_event(
    conn: sqlite3.Connection,
    *,
    env: str,
    channel_id: str,
    telegram_msg_id: str,
    trader_id: str,
    trader_prefix: str | None,
    attempt_key: str,
    event_type: str,
    payload: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO events(
          env, channel_id, telegram_msg_id, trader_id, trader_prefix,
          attempt_key, event_type, payload_json, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            env,
            channel_id,
            telegram_msg_id,
            trader_id,
            trader_prefix,
            attempt_key,
            event_type,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            1.0,
            created_at,
        ),
    )


def _insert_warning(
    conn: sqlite3.Connection,
    *,
    env: str,
    attempt_key: str,
    trader_id: str,
    code: str,
    severity: str,
    detail: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO warnings(env, attempt_key, trader_id, code, severity, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            env,
            attempt_key,
            trader_id,
            code,
            severity,
            json.dumps(detail, ensure_ascii=False, sort_keys=True),
            created_at,
        ),
    )


def _default_client_order_id(attempt_key: str, purpose: str, idx: int) -> str:
    return f"{attempt_key}:{purpose}:{idx}"


def _load_trade_meta(conn: sqlite3.Connection, *, env: str, attempt_key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT meta_json FROM trades WHERE env = ? AND attempt_key = ? LIMIT 1",
        (env, attempt_key),
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_position_size(conn: sqlite3.Connection, *, env: str, symbol: str) -> float:
    row = conn.execute(
        "SELECT size FROM positions WHERE env = ? AND symbol = ? LIMIT 1",
        (env, symbol),
    ).fetchone()
    if not row or not isinstance(row[0], (int, float)):
        return 0.0
    return float(row[0])


def _entry_side_from_context(context: FreqtradeSignalContext) -> str:
    if context.signal_side:
        return context.signal_side
    return "BUY" if context.side == "long" else "SELL"


def _reduce_only_side(entry_side: str) -> str:
    return "SELL" if entry_side.upper() == "BUY" else "BUY"


# ---------------------------------------------------------------------------
# Machine event helpers
# ---------------------------------------------------------------------------

def _fire_tp_machine_events(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    tp_idx: int,
    channel_id: str,
    telegram_msg_id: str,
    busy_retries: int,
    busy_sleep_s: float,
) -> None:
    """Evaluate machine_event rules for a TP fill and dispatch resulting actions."""
    if context.management_rules is None:
        return
    # tp_idx is 0-based; rules use 1-based tp_level (TP1 = level 1, TP2 = level 2, …)
    actions = evaluate_rules(
        event_type="TP_EXECUTED",
        event_context={"tp_level": tp_idx + 1},
        management_rules=context.management_rules,
    )
    if not actions:
        return
    _apply_machine_actions(
        db_path=db_path,
        context=context,
        actions=actions,
        channel_id=channel_id,
        telegram_msg_id=telegram_msg_id,
        busy_retries=busy_retries,
        busy_sleep_s=busy_sleep_s,
    )


def _fire_sl_machine_events(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    channel_id: str,
    telegram_msg_id: str,
    busy_retries: int,
    busy_sleep_s: float,
) -> None:
    """Evaluate machine_event rules for a stop hit — fires EXIT_BE when applicable."""
    if context.management_rules is None:
        return
    # Check whether the stop that fired was a breakeven stop (flagged during MOVE_STOP_TO_BE)
    with sqlite3.connect(db_path) as conn:
        trade_meta = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
    if not trade_meta.get("be_stop_active"):
        return
    actions = evaluate_rules(
        event_type="EXIT_BE",
        event_context={},
        management_rules=context.management_rules,
    )
    if not actions:
        return
    _apply_machine_actions(
        db_path=db_path,
        context=context,
        actions=actions,
        channel_id=channel_id,
        telegram_msg_id=telegram_msg_id,
        busy_retries=busy_retries,
        busy_sleep_s=busy_sleep_s,
    )


def _apply_machine_actions(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    actions: list[MachineEventAction],
    channel_id: str,
    telegram_msg_id: str,
    busy_retries: int,
    busy_sleep_s: float,
) -> None:
    for action in actions:
        if action.action_type == "MOVE_STOP_TO_BE":
            _apply_move_stop_to_be(
                db_path=db_path,
                context=context,
                channel_id=channel_id,
                telegram_msg_id=telegram_msg_id,
                busy_retries=busy_retries,
                busy_sleep_s=busy_sleep_s,
            )
        elif action.action_type == "MARK_EXIT_BE":
            _apply_mark_exit_be(
                db_path=db_path,
                context=context,
                busy_retries=busy_retries,
                busy_sleep_s=busy_sleep_s,
            )


def _apply_move_stop_to_be(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    channel_id: str,
    telegram_msg_id: str,
    busy_retries: int,
    busy_sleep_s: float,
) -> None:
    """Move the stop-loss to the entry fill price (breakeven) and flag the trade."""

    def _writer(conn: sqlite3.Connection) -> dict[str, Any]:
        # Load entry price from the positions table (set by order_filled_callback)
        entry_price = _load_position_entry_price(conn, env=context.env, symbol=context.symbol or "")
        if entry_price is None:
            return {"ok": False, "error": "machine_event_move_stop_to_be_missing_entry_price"}

        now = utc_now_iso()

        # Update signal stop level
        conn.execute(
            "UPDATE signals SET sl = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (entry_price, now, context.env, context.attempt_key),
        )

        # Update active SL order trigger price (audit trail + exchange_manager future use)
        conn.execute(
            """
            UPDATE orders
            SET trigger_price = ?, updated_at = ?
            WHERE env = ? AND attempt_key = ? AND purpose IN ('SL', 'STOP_LOSS')
              AND status NOT IN ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED')
            """,
            (entry_price, now, context.env, context.attempt_key),
        )

        # Flag the trade so EXIT_BE can be detected when the stop fires
        meta_json = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
        meta_json["be_stop_active"] = True
        meta_json["be_entry_price"] = entry_price
        conn.execute(
            "UPDATE trades SET meta_json = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (json.dumps(meta_json, ensure_ascii=False, sort_keys=True), now, context.env, context.attempt_key),
        )

        _insert_event(
            conn,
            env=context.env,
            channel_id=channel_id,
            telegram_msg_id=telegram_msg_id,
            trader_id=context.trader_id,
            trader_prefix=None,
            attempt_key=context.attempt_key,
            event_type="MACHINE_EVENT_MOVE_STOP_TO_BE",
            payload={"entry_price": entry_price, "source": "machine_event"},
            created_at=now,
        )
        return {"ok": True, "entry_price": entry_price}

    _with_sqlite_retry(db_path=db_path, writer=_writer, retries=busy_retries, sleep_s=busy_sleep_s)


def _apply_mark_exit_be(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    busy_retries: int,
    busy_sleep_s: float,
) -> None:
    """Record a breakeven exit in trade metadata."""

    def _writer(conn: sqlite3.Connection) -> dict[str, Any]:
        now = utc_now_iso()
        meta_json = _load_trade_meta(conn, env=context.env, attempt_key=context.attempt_key)
        meta_json["breakeven_exit"] = True
        conn.execute(
            "UPDATE trades SET meta_json = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (json.dumps(meta_json, ensure_ascii=False, sort_keys=True), now, context.env, context.attempt_key),
        )
        _insert_event(
            conn,
            env=context.env,
            channel_id="system",
            telegram_msg_id="0",
            trader_id=context.trader_id,
            trader_prefix=None,
            attempt_key=context.attempt_key,
            event_type="MACHINE_EVENT_EXIT_BE",
            payload={"source": "machine_event"},
            created_at=now,
        )
        return {"ok": True}

    _with_sqlite_retry(db_path=db_path, writer=_writer, retries=busy_retries, sleep_s=busy_sleep_s)


def _load_position_entry_price(conn: sqlite3.Connection, *, env: str, symbol: str) -> float | None:
    row = conn.execute(
        "SELECT entry_price FROM positions WHERE env = ? AND symbol = ? LIMIT 1",
        (env, symbol),
    ).fetchone()
    if not row or not isinstance(row[0], (int, float)):
        return None
    return float(row[0])

