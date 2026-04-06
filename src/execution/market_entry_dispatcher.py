"""Dispatcher that executes the first MARKET entry leg for pending signals."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.execution.exchange_gateway import ExchangeGateway
from src.execution.freqtrade_callback import order_filled_callback
from src.execution.freqtrade_normalizer import (
    FreqtradeSignalContext,
    load_context_by_attempt_key,
)


class MarketEntryDispatcher:
    """Reads PENDING signals whose first entry leg is MARKET and dispatches them.

    The dispatcher is intentionally decoupled from FreqtradeBot internals.
    It relies only on the normalizer, the callback layer, and the exchange gateway.
    """

    def __init__(
        self,
        *,
        db_path: str,
        gateway: ExchangeGateway | None = None,
        protective_orders_mode: str | None = None,
        order_manager: Any | None = None,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._protective_orders_mode = protective_orders_mode
        self._order_manager = order_manager

    def dispatch_pending_market_entries(self) -> list[dict[str, Any]]:
        """Scan PENDING signals with a MARKET first leg and dispatch each one.

        Returns a list of per-signal result dicts with keys:
            attempt_key, ok, action, error (str | None).
        """
        results: list[dict[str, Any]] = []

        for attempt_key in _load_pending_attempt_keys(self._db_path):
            context = load_context_by_attempt_key(attempt_key, self._db_path)
            if context is None:
                continue

            # Only handle MARKET first-leg signals
            if not context.market_entry_required:
                continue

            result = self._process_candidate(context)
            results.append(result)

        return results

    def _process_candidate(self, context: FreqtradeSignalContext) -> dict[str, Any]:
        attempt_key = context.attempt_key
        entry_id = context.first_entry_leg.entry_id if context.first_entry_leg else "E1"

        if not context.is_executable:
            return _result(attempt_key, ok=False, action="SKIP_NOT_EXECUTABLE", error="context_not_executable")

        # Idempotency guard 1: trade row already exists
        if _trade_exists(self._db_path, attempt_key):
            return _result(attempt_key, ok=False, action="SKIP_TRADE_EXISTS", error="trade_already_exists")

        # Idempotency guard 2: active ENTRY order already present
        if _active_entry_order_exists(self._db_path, attempt_key):
            return _result(attempt_key, ok=False, action="SKIP_ORDER_EXISTS", error="entry_order_already_active")

        # Idempotency guard 3: MARKET_ENTRY_DISPATCHED event already recorded
        if _dispatched_event_exists(self._db_path, attempt_key, entry_id):
            return _result(attempt_key, ok=False, action="SKIP_ALREADY_DISPATCHED", error="already_dispatched")

        if self._gateway is None:
            return _result(attempt_key, ok=False, action="SKIP_NO_GATEWAY", error="no_gateway_configured")

        return self._dispatch_one(context, entry_id)

    def _dispatch_one(self, context: FreqtradeSignalContext, entry_id: str) -> dict[str, Any]:
        attempt_key = context.attempt_key
        pair = context.pair or ""
        symbol = context.symbol or ""
        side = context.side or "long"
        entry_side = "buy" if side == "long" else "sell"
        client_order_id = f"{attempt_key}:MARKET:{entry_id}"
        first_leg = context.first_entry_leg
        if first_leg is None:
            return _result(attempt_key, ok=False, action="SKIP_NO_ENTRY_LEG", error="missing_first_entry_leg")

        allocated_stake = float(context.stake_amount or 0.0) * float(first_leg.split or 0.0)
        leverage = int(context.leverage or 1)
        reference_price = _resolve_market_reference_price(context)
        if allocated_stake <= 0:
            return _result(attempt_key, ok=False, action="SKIP_INVALID_STAKE", error="invalid_stake_amount")
        if reference_price is None or reference_price <= 0:
            _persist_dispatch_failed_event(
                db_path=self._db_path,
                context=context,
                entry_id=entry_id,
                error="missing_reference_price",
            )
            return _result(attempt_key, ok=False, action="DISPATCH_FAILED", error="missing_reference_price")

        # Prefer live market price for accurate qty sizing.
        # reference_price (midpoint SL/TP) is kept as fallback only.
        live_price = self._gateway.fetch_current_price(symbol=symbol)
        qty_price = live_price if live_price is not None and live_price > 0 else reference_price
        qty_price_source = "live" if live_price is not None and live_price > 0 else "fallback"
        qty = allocated_stake / qty_price

        assert self._gateway is not None
        try:
            order = self._gateway.create_entry_market_order(
                symbol=symbol,
                side=entry_side,
                qty=qty,
                client_order_id=client_order_id,
            )
        except Exception as exc:
            _persist_dispatch_failed_event(
                db_path=self._db_path,
                context=context,
                entry_id=entry_id,
                error=str(exc),
            )
            return _result(attempt_key, ok=False, action="DISPATCH_FAILED", error=str(exc))

        # Record dispatch audit trail
        _persist_dispatch_event(
            db_path=self._db_path,
            context=context,
            event_type="MARKET_ENTRY_DISPATCHED",
            payload={
                "entry_id": entry_id,
                "pair": pair,
                "symbol": symbol,
                "side": entry_side,
                "stake_amount": allocated_stake,
                "leverage": leverage,
                "reference_price": reference_price,
                "qty_price": qty_price,
                "qty_price_source": qty_price_source,
                "qty": order.qty,
                "exchange_order_id": order.exchange_order_id,
                "status": order.status,
            },
        )

        # MARKET orders are treated as immediately filled: prefer the exchange fill
        # price, then the live ticker used for qty sizing, and only fall back to the
        # synthetic reference price when no better runtime price is available.
        if order.status in ("FILLED", "OPEN"):
            fill_price = (
                order.average_fill_price
                if order.average_fill_price is not None and order.average_fill_price > 0
                else order.price
                if order.price is not None and order.price > 0
                else live_price
                if live_price is not None and live_price > 0
                else reference_price
            )
            callback_result = order_filled_callback(
                db_path=self._db_path,
                attempt_key=attempt_key,
                qty=order.qty,
                fill_price=fill_price,
                client_order_id=order.client_order_id or client_order_id,
                exchange_order_id=order.exchange_order_id,
                order_type="MARKET",
                protective_orders_mode=self._protective_orders_mode,
                order_manager=self._order_manager,
            )
            return _result(
                attempt_key,
                ok=callback_result.get("ok", False),
                action="ENTRY_FILLED",
                error=callback_result.get("error"),
            )

        return _result(attempt_key, ok=True, action="MARKET_ENTRY_DISPATCHED")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_pending_attempt_keys(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT attempt_key FROM signals WHERE status = 'PENDING' ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    return [row["attempt_key"] for row in rows]


def _resolve_market_reference_price(context: FreqtradeSignalContext) -> float | None:
    first_leg = context.first_entry_leg
    # For MARKET first-leg signals, ignore any explicit entry price from the
    # source message. MARKET execution must not anchor on a textual price.
    if (
        first_leg is not None
        and str(first_leg.order_type or "").upper() != "MARKET"
        and isinstance(first_leg.price, (int, float))
        and float(first_leg.price) > 0
    ):
        return float(first_leg.price)

    for leg in context.entry_legs[1:]:
        if isinstance(leg.price, (int, float)) and float(leg.price) > 0:
            return float(leg.price)

    stoploss = float(context.stoploss_ref) if isinstance(context.stoploss_ref, (int, float)) and float(context.stoploss_ref) > 0 else None
    first_tp = None
    for take_profit in context.take_profit_refs:
        if isinstance(take_profit, (int, float)) and float(take_profit) > 0:
            first_tp = float(take_profit)
            break

    if stoploss is not None and first_tp is not None:
        return (stoploss + first_tp) / 2.0
    if first_tp is not None:
        return first_tp
    return stoploss


def _trade_exists(db_path: str, attempt_key: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM trades WHERE attempt_key = ? LIMIT 1",
            (attempt_key,),
        ).fetchone()
    return row is not None


def _active_entry_order_exists(db_path: str, attempt_key: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM orders
            WHERE attempt_key = ? AND purpose = 'ENTRY'
              AND status NOT IN ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED')
            LIMIT 1
            """,
            (attempt_key,),
        ).fetchone()
    return row is not None


def _dispatched_event_exists(db_path: str, attempt_key: str, entry_id: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM events
            WHERE attempt_key = ? AND event_type = 'MARKET_ENTRY_DISPATCHED'
              AND json_extract(payload_json, '$.entry_id') = ?
            LIMIT 1
            """,
            (attempt_key, entry_id),
        ).fetchone()
    return row is not None


def _persist_event(
    *,
    db_path: str,
    attempt_key: str,
    event_type: str,
    payload: dict[str, Any],
    env: str,
    trader_id: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO events
                  (env, channel_id, telegram_msg_id, trader_id, trader_prefix,
                   attempt_key, event_type, payload_json, confidence, created_at)
                VALUES (?, 'market_dispatcher', '0', ?, ?, ?, ?, ?, 1.0, ?)
                """,
                (
                    env,
                    trader_id,
                    trader_id[:4].upper() if trader_id else None,
                    attempt_key,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
    except sqlite3.Error:
        pass  # Best-effort audit trail; dispatch logic is not blocked by event write failure.


def _persist_dispatch_event(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    _persist_event(
        db_path=db_path,
        attempt_key=context.attempt_key,
        event_type=event_type,
        payload=payload,
        env=context.env,
        trader_id=context.trader_id,
    )


def _persist_dispatch_failed_event(
    *,
    db_path: str,
    context: FreqtradeSignalContext,
    entry_id: str,
    error: str,
) -> None:
    _persist_dispatch_event(
        db_path=db_path,
        context=context,
        event_type="MARKET_ENTRY_DISPATCH_FAILED",
        payload={"entry_id": entry_id, "error": error},
    )


def _result(
    attempt_key: str,
    *,
    ok: bool,
    action: str,
    error: str | None = None,
) -> dict[str, Any]:
    return {"attempt_key": attempt_key, "ok": ok, "action": action, "error": error}
