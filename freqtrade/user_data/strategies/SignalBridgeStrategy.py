"""Thin freqtrade strategy that bridges canonical DB signals to entry hooks."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

try:
    from freqtrade.persistence import Order, Trade
    from freqtrade.strategy import IStrategy
except ImportError:  # pragma: no cover - exercised indirectly in unit tests
    class IStrategy:  # type: ignore[override]
        INTERFACE_VERSION = 3
        minimal_roi: dict[str, float] = {}
        stoploss = -1.0
        timeframe = "1m"
        can_short = True
        process_only_new_candles = False
        startup_candle_count = 1

        def __init__(self, config: dict[str, Any] | None = None) -> None:
            self.config = config or {}

    class Trade:  # type: ignore[override]
        pass

    class Order:  # type: ignore[override]
        pass

from src.execution.freqtrade_callback import (
    entry_order_open_callback,
    order_filled_callback,
    partial_exit_callback,
    stoploss_callback,
    trade_exit_callback,
)
from src.execution.freqtrade_normalizer import (
    EntryPricePolicy,
    FreqtradeSignalContext,
    check_entry_rate,
    load_active_contexts_for_pair,
    load_context_by_attempt_key,
    load_pending_contexts_for_pair,
    persist_entry_rejected_event,
    persist_entry_price_rejected_event,
    resolve_entry_price_policy,
)
from src.execution.market_entry_dispatcher import MarketEntryDispatcher
from src.execution.order_reconciliation import bootstrap_sync_open_trades
from src.execution.protective_orders_mode import (
    ProtectiveOrderOwner,
    ProtectiveOrderOwnership,
    ProtectiveOrdersMode,
    resolve_protective_order_ownership,
)


class SignalBridgeStrategy(IStrategy):
    """Signal bridge for Step 16.

    All symbol/side conversion is delegated to the normalizer.
    """

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    process_only_new_candles = False
    startup_candle_count = 1
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    use_custom_stoploss = True
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    bot_db_path: str | None = os.getenv("TELESIGNALBOT_DB_PATH")
    _execution_bootstrap_done: bool = False
    _last_execution_reconciliation_at: float = 0.0
    _last_market_dispatch_at: float = 0.0
    _last_stoploss_dedupe_at: float = 0.0
    _last_entry_cancel_sync_at: float = 0.0

    # ------------------------------------------------------------------
    # FreqUI plot configuration — observability only, no trading logic
    # ------------------------------------------------------------------
    plot_config = {
        "main_plot": {
            "bridge_sl": {"color": "#e74c3c", "type": "line"},
            "bridge_tp1": {"color": "#27ae60", "type": "line"},
            "bridge_tp2": {"color": "#2ecc71", "type": "line"},
            "bridge_tp3": {"color": "#a3d977", "type": "line"},
            "bridge_live_entry_open": {"color": "#f39c12", "type": "line"},
            "bridge_live_sl_open": {"color": "#c0392b", "type": "line"},
            "bridge_live_tp_open_1": {"color": "#16a085", "type": "line"},
            "bridge_live_tp_open_2": {"color": "#1abc9c", "type": "line"},
            "bridge_live_tp_open_3": {"color": "#48c9b0", "type": "line"},
            "bridge_entry_price": {"color": "#3498db", "type": "line"},
            "bridge_entry_avg": {"color": "#1f78b4", "type": "line"},
            "bridge_entry_pending_e1": {"color": "#7fb3d5", "type": "line"},
            "bridge_entry_pending_e2": {"color": "#85c1e9", "type": "line"},
            "bridge_entry_pending_e3": {"color": "#aed6f1", "type": "line"},
            "bridge_entry_filled_e1": {"color": "#1f618d", "type": "line"},
            "bridge_entry_filled_e2": {"color": "#2874a6", "type": "line"},
            "bridge_entry_filled_e3": {"color": "#2e86c1", "type": "line"},
            "bridge_tp1_hit": {"color": "#1e8449", "type": "line"},
            "bridge_tp2_hit": {"color": "#239b56", "type": "line"},
            "bridge_tp3_hit": {"color": "#52be80", "type": "line"},
        },
        "subplots": {
            "Bridge Events": {
                "bridge_event_entry": {"color": "#3498db", "type": "bar"},
                "bridge_event_entry_e1": {"color": "#1f618d", "type": "bar"},
                "bridge_event_entry_e2": {"color": "#2874a6", "type": "bar"},
                "bridge_event_entry_e3": {"color": "#2e86c1", "type": "bar"},
                "bridge_event_partial_exit": {"color": "#f39c12", "type": "bar"},
                "bridge_event_tp_hit": {"color": "#27ae60", "type": "bar"},
                "bridge_event_tp1_hit": {"color": "#1e8449", "type": "bar"},
                "bridge_event_tp2_hit": {"color": "#239b56", "type": "bar"},
                "bridge_event_tp3_hit": {"color": "#52be80", "type": "bar"},
                "bridge_event_sl_hit": {"color": "#e74c3c", "type": "bar"},
                "bridge_event_close": {"color": "#8e44ad", "type": "bar"},
            },
        },
    }

    def populate_indicators(self, dataframe: Any, metadata: dict[str, Any]) -> Any:
        self._maybe_run_execution_reconciliation()
        self._maybe_dispatch_market_entries()
        self._maybe_sync_cancelled_entry_orders()
        self._maybe_cleanup_duplicate_stoploss_orders()
        pair = str((metadata or {}).get("pair") or "").strip()
        self._populate_bridge_columns(dataframe, pair)
        return dataframe

    def populate_entry_trend(self, dataframe: Any, metadata: dict[str, Any]) -> Any:
        self._reset_entry_columns(dataframe)
        pair = str((metadata or {}).get("pair") or "").strip()
        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return dataframe

        context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None or not context.is_executable:
            return dataframe
        if self._has_active_entry_runtime(db_path=db_path, attempt_key=context.attempt_key):
            return dataframe

        # Strategy path only emits pending LIMIT entries.
        # MARKET first legs are dispatched by _maybe_dispatch_market_entries() so they
        # fill immediately at market and can bootstrap protections before any
        # averaging LIMIT legs are placed.
        next_limit_leg = context.next_pending_entry_leg
        if next_limit_leg is None or next_limit_leg.order_type != "LIMIT" or next_limit_leg.sequence != 1:
            return dataframe

        column = "enter_long" if context.side == "long" else "enter_short"
        self._set_last_row_value(dataframe, column, 1)
        self._set_last_row_value(dataframe, "enter_tag", f"{context.attempt_key}:ENTRY:{next_limit_leg.sequence - 1}")
        return dataframe

    def populate_exit_trend(self, dataframe: Any, metadata: dict[str, Any]) -> Any:
        self._reset_exit_columns(dataframe)
        pair = str((metadata or {}).get("pair") or "").strip()
        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return dataframe

        context = self._select_exit_context(pair=pair, db_path=db_path)
        if context is None:
            return dataframe

        column = "exit_long" if context.side == "long" else "exit_short"
        self._set_last_row_value(dataframe, column, 1)
        self._set_last_row_value(dataframe, "exit_tag", context.entry_tag)
        return dataframe

    def check_entry_timeout(
        self,
        pair: str,
        trade: Trade,
        order: Order,
        current_time: Any,
        **kwargs: Any,
    ) -> bool:
        del current_time, kwargs

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return False

        attempt_key = self._order_attempt_key(order=order, trade=trade)
        context = load_context_by_attempt_key(attempt_key, db_path) if attempt_key else self._select_pending_context(pair=pair, db_path=db_path)
        if context is None:
            return False
        if not context.is_pair_mappable or context.pair != pair:
            return False
        return context.signal_status == "PENDING" and context.cancel_pending_requested

    def custom_stake_amount(
        self,
        pair: str,
        current_time: Any,
        current_rate: float,
        proposed_stake: float,
        *args: Any,
        **kwargs: Any,
    ) -> float:
        del current_time, current_rate, args, kwargs

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return float(proposed_stake)

        context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None or context.stake_amount is None or context.stake_amount <= 0:
            return float(proposed_stake)
        # For multi-leg plans, scale stake by the first leg's split so that
        # adjust_trade_position can add subsequent legs without over-sizing.
        first_leg = context.first_entry_leg
        split = float(first_leg.split) if first_leg is not None and first_leg.split else 1.0
        return float(context.stake_amount) * split

    def custom_entry_price(
        self,
        pair: str,
        trade: Any,
        current_time: Any,
        proposed_rate: float,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float:
        """Return the canonical signal entry price for limit orders.

        Entry model: single-entry, policy = first_in_plan.

        The policy uses entry_prices[0] (E1 in the operation_rules split plan):
        - SINGLE_LIMIT            → E1: the one limit level
        - LIMIT_WITH_AVERAGING    → E1: first (typically more conservative) limit
        - ZONE endpoints          → E1: lower endpoint for long / higher for short
        - MARKET / missing price  → falls back to proposed_rate (no override)

        This hook is only called by freqtrade when a limit entry order is being
        placed. MARKET-type signals remain unaffected.
        """
        del trade, current_time, side, kwargs

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return float(proposed_rate)

        attempt_key = self._attempt_key_from_tag(entry_tag)
        context = load_context_by_attempt_key(attempt_key, db_path) if attempt_key else self._select_pending_context(pair=pair, db_path=db_path)
        if context is None:
            return float(proposed_rate)

        entry_leg = self._resolve_entry_leg(context=context, entry_tag=entry_tag)
        if entry_leg is None or entry_leg.order_type != "LIMIT":
            return float(proposed_rate)
        if entry_leg.price is None or entry_leg.price <= 0:
            return float(proposed_rate)

        return float(entry_leg.price)

    def adjust_entry_price(
        self,
        trade: Any,
        order: Any,
        pair: str,
        current_time: Any,
        proposed_rate: float,
        current_order_rate: float,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float | None:
        """Keep open LIMIT entry orders anchored to the canonical plan price.

        This only affects replacement/repricing of already-open entry orders.
        MARKET signals and entries without an explicit LIMIT price continue to
        use Freqtrade's default repricing behavior.
        """
        del trade, order, current_time, proposed_rate, side, kwargs

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return float(current_order_rate)

        attempt_key = self._attempt_key_from_tag(entry_tag)
        context = load_context_by_attempt_key(attempt_key, db_path) if attempt_key else None
        if context is None:
            context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None:
            return float(current_order_rate)
        if not context.is_pair_mappable or context.pair != pair:
            return float(current_order_rate)
        if context.signal_status not in {"PENDING", "ACTIVE"}:
            return float(current_order_rate)

        entry_leg = self._resolve_entry_leg(context=context, entry_tag=entry_tag)
        if entry_leg is None or entry_leg.order_type != "LIMIT":
            return float(current_order_rate)
        if entry_leg.price is None or entry_leg.price <= 0:
            return float(current_order_rate)

        return float(entry_leg.price)

    def leverage(
        self,
        pair: str,
        current_time: Any,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        *args: Any,
        **kwargs: Any,
    ) -> float:
        del current_time, current_rate, proposed_leverage, args, kwargs

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return 1.0

        context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None or context.leverage is None or context.leverage <= 0:
            return 1.0

        target = float(context.leverage)
        if isinstance(max_leverage, (int, float)) and max_leverage > 0:
            target = min(target, float(max_leverage))
        return max(1.0, target)

    def order_filled(
        self,
        pair: str,
        trade: Trade,
        order: Order,
        current_time: Any,
        **kwargs: Any,
    ) -> None:
        del current_time, kwargs

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return

        attempt_key = self._trade_attempt_key(trade) or self._order_attempt_key(order, trade)
        if not attempt_key:
            return
        context = load_context_by_attempt_key(attempt_key, db_path)
        ownership = self._resolve_protective_order_ownership(context=context)
        order_manager = getattr(self, "exchange_order_manager", None)

        order_side = str(getattr(order, "ft_order_side", "") or "").lower()
        trade_entry_side = self._normalize_order_side(getattr(trade, "entry_side", None))
        trade_exit_side = self._normalize_order_side(getattr(trade, "exit_side", None))
        if trade_entry_side is None:
            is_short = bool(getattr(trade, "is_short", False))
            if context is not None and context.side in {"long", "short"}:
                is_short = context.side == "short"
            trade_entry_side = "sell" if is_short else "buy"
        if trade_exit_side is None:
            trade_exit_side = "sell" if trade_entry_side == "buy" else "buy"
        filled_qty = float(getattr(order, "safe_filled", 0.0) or 0.0)
        fill_price = float(getattr(order, "safe_price", 0.0) or 0.0)
        if filled_qty <= 0 or fill_price <= 0:
            return

        order_id = getattr(order, "order_id", None)
        order_type = str(getattr(order, "order_type", "") or "LIMIT").upper()
        client_order_id = str(order_id) if order_id else None
        order_tag = str(getattr(order, "ft_order_tag", None) or getattr(order, "tag", None) or "")
        tp_idx = self._take_profit_idx_from_tag(order_tag, attempt_key)

        if order_side in {"buy", "sell"} and order_side == trade_entry_side:
            entry_idx = self._entry_idx_from_tag(order_tag or client_order_id, attempt_key=attempt_key)
            order_filled_callback(
                db_path=db_path,
                attempt_key=attempt_key,
                qty=filled_qty,
                fill_price=fill_price,
                client_order_id=client_order_id,
                exchange_order_id=client_order_id,
                order_type=order_type,
                margin_mode=self._resolve_margin_mode(),
                protective_orders_mode=ownership.mode.value,
                order_manager=order_manager,
                entry_idx=entry_idx,
            )
            return

        if order_side == "stoploss":
            if ownership.mode is ProtectiveOrdersMode.EXCHANGE_MANAGER and order_manager is not None:
                trigger_price = float(getattr(order, "stop_price", 0.0) or fill_price)
                order_manager.sync_after_stop_fill(
                    attempt_key=attempt_key,
                    closed_qty=filled_qty,
                    fill_price=trigger_price,
                    exchange_order_id=client_order_id,
                )
                return
            trigger_price = float(getattr(order, "stop_price", 0.0) or fill_price)
            stoploss_callback(
                db_path=db_path,
                attempt_key=attempt_key,
                qty=filled_qty,
                stop_price=trigger_price,
                client_order_id=client_order_id,
                exchange_order_id=client_order_id,
            )
            return

        if order_side not in {"buy", "sell"} or order_side != trade_exit_side:
            return

        closed_qty = float(getattr(order, "safe_amount_after_fee", 0.0) or filled_qty)
        remaining_qty = float(getattr(trade, "amount", 0.0) or 0.0)
        if ownership.mode is ProtectiveOrdersMode.EXCHANGE_MANAGER and order_manager is not None and tp_idx is not None:
            order_manager.sync_after_tp_fill(
                attempt_key=attempt_key,
                tp_idx=tp_idx,
                closed_qty=closed_qty,
                fill_price=fill_price,
                exchange_order_id=client_order_id,
            )
            return

        if bool(getattr(trade, "is_open", False)):
            total_qty = max(closed_qty, closed_qty + remaining_qty)
            close_fraction = min(1.0, max(0.0, closed_qty / total_qty)) if total_qty > 0 else 1.0
            partial_exit_callback(
                db_path=db_path,
                attempt_key=attempt_key,
                close_fraction=close_fraction,
                remaining_qty=remaining_qty,
                closed_qty=closed_qty,
                exit_price=fill_price,
                tp_idx=tp_idx,
                client_order_id=client_order_id,
                exchange_order_id=client_order_id,
            )
            return

        close_reason = str(getattr(trade, "exit_reason", None) or "POSITION_CLOSED")
        if tp_idx is not None:
            close_reason = f"TP{tp_idx + 1}_HIT"
        trade_exit_callback(
            db_path=db_path,
            attempt_key=attempt_key,
            close_reason=close_reason,
            exit_price=fill_price,
            tp_idx=tp_idx,
            exchange_order_id=client_order_id,
        )

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> float:
        del current_time, current_profit, kwargs

        db_path = self._resolve_db_path()
        if not pair or not db_path or current_rate <= 0:
            return float(self.stoploss)

        context = self._load_trade_context(pair=pair, trade=trade, db_path=db_path)
        if context is None or context.stoploss_ref is None or context.side not in {"long", "short"}:
            return float(self.stoploss)
        ownership = self._resolve_protective_order_ownership(context=context)
        if ownership.stoploss_owner is not ProtectiveOrderOwner.STRATEGY:
            return float(self.stoploss)

        relative_stop = self._absolute_stop_to_relative(
            side=context.side,
            stop_price=float(context.stoploss_ref),
            current_rate=float(current_rate),
        )
        if relative_stop is None:
            return float(self.stoploss)
        return float(relative_stop)

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float | None = None,
        current_exit_rate: float | None = None,
        current_entry_profit: float | None = None,
        current_exit_profit: float | None = None,
        **kwargs: Any,
    ) -> float | tuple[float, str] | None:
        del current_time, current_profit
        del current_entry_rate, current_exit_rate, current_entry_profit, current_exit_profit, kwargs

        db_path = self._resolve_db_path()
        pair = str(getattr(trade, "pair", "") or "").strip()
        if not pair or not db_path:
            return None

        context = self._load_trade_context(pair=pair, trade=trade, db_path=db_path)
        if context is None:
            return None

        if bool(getattr(trade, "has_open_orders", False)):
            return None

        next_limit_leg = context.next_pending_entry_leg
        if (
            context.signal_status == "ACTIVE"
            and next_limit_leg is not None
            and next_limit_leg.sequence > 1
            and next_limit_leg.order_type == "LIMIT"
            and context.stake_amount is not None
            and context.stake_amount > 0
        ):
            increase = float(context.stake_amount) * float(next_limit_leg.split)
            if isinstance(max_stake, (int, float)) and float(max_stake) > 0:
                increase = min(increase, float(max_stake))
            if isinstance(min_stake, (int, float)) and float(min_stake) > 0 and increase < float(min_stake):
                return None
            if increase > 0:
                return increase, f"{context.attempt_key}:ENTRY:{next_limit_leg.sequence - 1}"

        ownership = self._resolve_protective_order_ownership(context=context)
        if ownership.take_profit_owner is not ProtectiveOrderOwner.STRATEGY:
            return None

        trade_stake = self._trade_stake_amount(trade)
        if trade_stake is None or trade_stake <= 0:
            return None

        if context.partial_close_fraction is not None:
            reduction = -min(trade_stake, trade_stake * float(context.partial_close_fraction))
            if reduction < 0:
                return reduction, f"signal_close_partial:{context.attempt_key}"

        tp_action = self._next_take_profit_action(
            context=context,
            current_rate=float(current_rate),
            db_path=db_path,
        )
        if tp_action is None or tp_action["close_full"]:
            return None

        reduction = -min(trade_stake, trade_stake * float(tp_action["close_fraction_current"]))
        if reduction >= 0:
            return None
        return reduction, str(tp_action["order_tag"])

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> str | bool | None:
        del current_time, current_profit, kwargs

        if bool(getattr(trade, "has_open_orders", False)):
            return None

        db_path = self._resolve_db_path()
        if not pair or not db_path or current_rate <= 0:
            return None

        context = self._load_trade_context(pair=pair, trade=trade, db_path=db_path)
        if context is None:
            return None
        ownership = self._resolve_protective_order_ownership(context=context)
        if ownership.take_profit_owner is not ProtectiveOrderOwner.STRATEGY:
            return None

        tp_action = self._next_take_profit_action(
            context=context,
            current_rate=float(current_rate),
            db_path=db_path,
        )
        if tp_action is None or not tp_action["close_full"]:
            return None
        return str(tp_action["order_tag"])

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: Any,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        del time_in_force, current_time

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return False

        entry_tag, side = self._resolve_confirm_args(args=args, kwargs=kwargs)
        entry_idx = self._entry_idx_from_tag(entry_tag, attempt_key=self._attempt_key_from_tag(entry_tag) or "") if entry_tag else 0
        attempt_key = self._attempt_key_from_tag(entry_tag)
        context = load_context_by_attempt_key(attempt_key, db_path) if attempt_key else None
        if context is None:
            context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None:
            return False
        if not context.is_pair_mappable or context.pair != pair:
            return False
        if context.signal_status not in {"PENDING", "ACTIVE"}:
            return False
        if context.signal_status == "PENDING" and not context.is_executable:
            return False
        if context.signal_status == "ACTIVE" and entry_idx <= 0:
            return False
        if attempt_key and context.attempt_key != attempt_key:
            return False
        if side and context.side and side != context.side:
            return False

        configured_order_type = str(order_type).upper()
        ownership = self._resolve_protective_order_ownership(context=context)
        entry_leg = self._resolve_entry_leg(context=context, entry_tag=entry_tag)
        if entry_leg is None:
            return False
        signal_entry_order_type = entry_leg.order_type
        if signal_entry_order_type not in ("LIMIT", "MARKET"):
            _log.warning(
                "confirm_trade_entry REJECTED | pair=%s attempt_key=%s reason=%s"
                " configured_order_type=%s signal_entry_order_type=%s",
                pair,
                context.attempt_key,
                "unsupported_entry_order_type",
                configured_order_type,
                signal_entry_order_type,
            )
            return False
        if signal_entry_order_type == "MARKET" and configured_order_type != "MARKET":
            _log.info(
                "confirm_trade_entry REJECTED | pair=%s attempt_key=%s reason=%s"
                " configured_order_type=%s signal_entry_order_type=%s",
                pair,
                context.attempt_key,
                "market_leg_reserved_for_dispatcher",
                configured_order_type,
                signal_entry_order_type,
            )
            return False

        # Entry price policy — reject if rate diverges from signal entry plan.
        policy = resolve_entry_price_policy(
            context.management_rules,
            getattr(self, "config", None),
        )
        effective_rate = float(rate)
        if configured_order_type == "LIMIT" and entry_leg.order_type == "LIMIT" and entry_leg.price is not None and entry_leg.price > 0:
            effective_rate = float(entry_leg.price)

        rejection = check_entry_rate(
            entry_prices=({"price": entry_leg.price, "type": entry_leg.order_type},),
            rate=effective_rate,
            order_type=str(order_type),
            policy=policy,
        )
        if rejection is not None:
            _log.warning(
                "confirm_trade_entry REJECTED | pair=%s attempt_key=%s reason=%s"
                " rate=%.6f e1=%s e2=%s deviation_pct=%.4f policy_pct=%.4f",
                pair,
                context.attempt_key,
                rejection["reason"],
                rejection["rate"],
                rejection.get("e1"),
                rejection.get("e2"),
                rejection.get("deviation_pct", 0.0),
                rejection.get("policy_pct", 0.0),
            )
            persist_entry_price_rejected_event(
                db_path=db_path,
                attempt_key=context.attempt_key,
                rejection_info={
                    **rejection,
                    "pair": pair,
                    "order_type": order_type,
                    "requested_rate": float(rate),
                    "effective_rate": effective_rate,
                },
            )
            return False

        entry_order_open_callback(
            db_path=db_path,
            attempt_key=context.attempt_key,
            qty=float(amount),
            price=float(effective_rate) if configured_order_type == "LIMIT" else None,
            order_type=configured_order_type,
            protective_orders_mode=ownership.mode.value,
            entry_idx=entry_idx,
        )
        return True

    def bot_start(self, **kwargs: Any) -> None:
        """Initialize exchange_order_manager for watchdog reconciliation.

        Called once by freqtrade after the exchange is ready.  Sets
        ``self.exchange_order_manager`` so that
        ``_maybe_run_execution_reconciliation`` can perform bootstrap sync
        and periodic watchdog checks.  No-ops silently when db_path or
        the exchange DataProvider are not available (e.g. unit tests).
        """
        del kwargs
        db_path = self._resolve_db_path()
        if not db_path:
            return
        try:
            exchange = getattr(self.dp, "_exchange", None) if hasattr(self, "dp") and self.dp is not None else None
            if exchange is None:
                return
            from src.execution.exchange_gateway import ExchangeGateway
            from src.execution.exchange_order_manager import ExchangeOrderManager
            from src.execution.freqtrade_exchange_backend import FreqtradeExchangeBackend
            backend = FreqtradeExchangeBackend(exchange)
            gateway = ExchangeGateway(backend)
            self.exchange_order_manager = ExchangeOrderManager(db_path=db_path, gateway=gateway)
            _log.info("exchange_order_manager initialized for watchdog reconciliation")
        except Exception as exc:
            _log.warning("failed to initialize exchange_order_manager: %s", exc)

    def _resolve_db_path(self) -> str | None:
        if self.bot_db_path:
            return self.bot_db_path

        config = getattr(self, "config", None)
        if isinstance(config, dict):
            candidate = config.get("bot_db_path") or config.get("te_signal_bot_db_path")
            if candidate:
                return str(candidate)

        env_path = os.getenv("TELESIGNALBOT_DB_PATH")
        if env_path:
            return env_path

        # Final fallback for live/runtime cases where config/env are not
        # propagated to the strategy instance but the standard repo layout is used.
        repo_db = Path(__file__).resolve().parents[3] / "db" / "tele_signal_bot.sqlite3"
        if repo_db.exists():
            return str(repo_db)

        return None

    def _resolve_margin_mode(self) -> str:
        config = getattr(self, "config", None)
        if isinstance(config, dict):
            candidate = config.get("margin_mode")
            if candidate:
                return str(candidate)
        return "isolated"

    def _maybe_dispatch_market_entries(self) -> None:
        # Safety guard: MARKET dispatch is only enabled in dry-run.
        # In live mode the full FT-Trade integration is not yet complete and
        # dispatching would open real positions without SL/TP managed by Freqtrade.
        config = getattr(self, "config", None)
        if not (isinstance(config, dict) and config.get("dry_run", False)):
            return

        db_path = self._resolve_db_path()
        if not db_path:
            return

        now = time.monotonic()
        interval = self._market_dispatch_interval_s()
        if interval <= 0:
            return
        if now - getattr(self, "_last_market_dispatch_at", 0.0) < interval:
            return
        self._last_market_dispatch_at = now

        order_manager = getattr(self, "exchange_order_manager", None)
        gateway = order_manager.gateway if order_manager is not None and hasattr(order_manager, "gateway") else None
        ownership = self._resolve_protective_order_ownership(context=None)

        try:
            dispatcher = MarketEntryDispatcher(
                db_path=db_path,
                gateway=gateway,
                protective_orders_mode=ownership.mode.value,
                order_manager=order_manager,
            )
            results = dispatcher.dispatch_pending_market_entries()
            for r in results:
                _log.info(
                    "market_dispatch | attempt_key=%s ok=%s action=%s error=%s",
                    r.get("attempt_key"), r.get("ok"), r.get("action"), r.get("error"),
                )
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            _log.warning("market_dispatch error: %s", exc)

    def _maybe_run_execution_reconciliation(self) -> None:
        db_path = self._resolve_db_path()
        order_manager = getattr(self, "exchange_order_manager", None)
        if not db_path or order_manager is None or not hasattr(order_manager, "gateway"):
            return

        now = time.monotonic()
        if not getattr(self, "_execution_bootstrap_done", False):
            try:
                bootstrap_sync_open_trades(
                    db_path=db_path,
                    gateway=order_manager.gateway,
                    order_manager=order_manager,
                    reason="startup",
                )
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                setattr(self, "_last_execution_reconciliation_error", str(exc))
                return
            self._execution_bootstrap_done = True
            self._last_execution_reconciliation_at = now
            return

        interval = self._reconciliation_watchdog_interval_s()
        if interval <= 0 or now - getattr(self, "_last_execution_reconciliation_at", 0.0) < interval:
            return
        try:
            bootstrap_sync_open_trades(
                db_path=db_path,
                gateway=order_manager.gateway,
                order_manager=order_manager,
                reason="watchdog",
            )
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            setattr(self, "_last_execution_reconciliation_error", str(exc))
            return
        self._last_execution_reconciliation_at = now

    def _reconciliation_watchdog_interval_s(self) -> float:
        config = getattr(self, "config", None)
        if isinstance(config, dict):
            execution = config.get("execution")
            if isinstance(execution, dict):
                value = execution.get("reconciliation_watchdog_interval_s")
                if isinstance(value, (int, float)) and float(value) > 0:
                    return float(value)
        return 0.0

    def _market_dispatch_interval_s(self) -> float:
        config = getattr(self, "config", None)
        if isinstance(config, dict):
            execution = config.get("execution")
            if isinstance(execution, dict):
                value = execution.get("market_dispatch_interval_s")
                if isinstance(value, (int, float)) and float(value) > 0:
                    return float(value)
        return 10.0

    def _maybe_cleanup_duplicate_stoploss_orders(self) -> None:
        interval = self._stoploss_dedupe_interval_s()
        if interval <= 0:
            return

        now = time.monotonic()
        if now - getattr(self, "_last_stoploss_dedupe_at", 0.0) < interval:
            return
        self._last_stoploss_dedupe_at = now

        trades_db_path = self._resolve_freqtrade_trades_db_path()
        if not trades_db_path:
            return

        try:
            summary = self._dedupe_open_stoploss_orders(trades_db_path)
        except Exception:
            _log.debug("stoploss_dedupe failed", exc_info=True)
            return

        if summary.get("duplicates_resolved", 0) > 0:
            _log.warning(
                "stoploss_dedupe | db=%s duplicates=%s canceled=%s",
                trades_db_path,
                summary.get("duplicates_resolved"),
                summary.get("canceled_orders"),
            )

    def _maybe_sync_cancelled_entry_orders(self) -> None:
        db_path = self._resolve_db_path()
        trades_db_path = self._resolve_freqtrade_trades_db_path()
        if not db_path or not trades_db_path:
            return

        now = time.monotonic()
        if now - getattr(self, "_last_entry_cancel_sync_at", 0.0) < 10.0:
            return
        self._last_entry_cancel_sync_at = now

        try:
            summary = self._sync_cancelled_entry_orders(
                trades_db_path=trades_db_path,
                bot_db_path=db_path,
            )
        except Exception:
            _log.debug("entry_cancel_sync failed", exc_info=True)
            return

        if summary.get("signals_cancelled", 0) > 0:
            _log.warning(
                "entry_cancel_sync | trades_db=%s bot_db=%s scanned=%s cancelled=%s",
                trades_db_path,
                db_path,
                summary.get("candidates_scanned", 0),
                summary.get("signals_cancelled", 0),
            )

    @staticmethod
    def _sync_cancelled_entry_orders(*, trades_db_path: str, bot_db_path: str) -> dict[str, int]:
        now = datetime.now(timezone.utc).isoformat()
        candidate_attempt_keys: set[str] = set()
        processed_attempt_keys = 0
        signals_cancelled = 0
        orders_cancelled = 0
        trades_closed = 0
        events_inserted = 0

        with sqlite3.connect(trades_db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT o.ft_order_tag
                FROM orders o
                WHERE LOWER(COALESCE(o.status, '')) IN ('canceled', 'cancelled', 'expired', 'rejected')
                  AND COALESCE(o.ft_order_tag, '') <> ''
                ORDER BY o.id DESC
                """
            ).fetchall()

            for row in rows:
                tag = str(row[0] or "").strip()
                if not tag:
                    continue
                upper_tag = tag.upper()
                if ":TP:" in upper_tag or ":SL:" in upper_tag or ":EXIT:" in upper_tag:
                    continue
                attempt_key = SignalBridgeStrategy._attempt_key_from_tag(tag)
                if not attempt_key:
                    continue

                # If we observed a canceled/rejected/expired ENTRY tag for a still-PENDING
                # bridge signal, we must stop re-dispatch loops even when Freqtrade has already
                # spawned a replacement technical order.
                candidate_attempt_keys.add(attempt_key)

        with sqlite3.connect(bot_db_path) as conn:
            for attempt_key in sorted(candidate_attempt_keys):
                processed_attempt_keys += 1
                signal_row = conn.execute(
                    """
                    SELECT env, trader_id
                    FROM signals
                    WHERE attempt_key = ?
                      AND status = 'PENDING'
                    LIMIT 1
                    """,
                    (attempt_key,),
                ).fetchone()
                if signal_row is None:
                    continue

                env = str(signal_row[0] or "T")
                trader_id = str(signal_row[1] or "")

                signal_result = conn.execute(
                    """
                    UPDATE signals
                    SET status = 'CANCELLED',
                        updated_at = ?
                    WHERE attempt_key = ?
                      AND status = 'PENDING'
                    """,
                    (now, attempt_key),
                )
                signals_cancelled += int(signal_result.rowcount)

                trade_result = conn.execute(
                    """
                    UPDATE trades
                    SET state = 'CLOSED',
                        close_reason = COALESCE(close_reason, 'ENTRY_CANCELLED_IN_FREQTRADE'),
                        closed_at = COALESCE(closed_at, ?),
                        updated_at = ?
                    WHERE attempt_key = ?
                      AND state = 'ENTRY_PENDING'
                    """,
                    (now, now, attempt_key),
                )
                trades_closed += int(trade_result.rowcount)

                order_result = conn.execute(
                    """
                    UPDATE orders
                    SET status = 'CANCELLED',
                        updated_at = ?
                    WHERE attempt_key = ?
                      AND purpose = 'ENTRY'
                      AND status NOT IN ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED')
                    """,
                    (now, attempt_key),
                )
                orders_cancelled += int(order_result.rowcount)

                conn.execute(
                    """
                    INSERT INTO events(
                      env, channel_id, telegram_msg_id, trader_id, trader_prefix,
                      attempt_key, event_type, payload_json, confidence, created_at
                    ) VALUES (?, 'freqtrade_sync', '0', ?, NULL, ?, 'ENTRY_CANCELLED_EXTERNALLY', ?, 1.0, ?)
                    """,
                    (
                        env,
                        trader_id,
                        attempt_key,
                        json.dumps(
                            {
                                "source": "freqtrade_orders_sync",
                                "reason": "entry_order_cancelled_in_freqtrade",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                    ),
                )
                events_inserted += 1

            conn.commit()

        return {
            "candidates_scanned": len(candidate_attempt_keys),
            "attempt_keys_processed": processed_attempt_keys,
            "signals_cancelled": signals_cancelled,
            "trades_closed": trades_closed,
            "orders_cancelled": orders_cancelled,
            "events_inserted": events_inserted,
        }

    def _stoploss_dedupe_interval_s(self) -> float:
        config = getattr(self, "config", None)
        if isinstance(config, dict):
            execution = config.get("execution")
            if isinstance(execution, dict):
                value = execution.get("stoploss_dedupe_interval_s")
                if isinstance(value, (int, float)):
                    return max(0.0, float(value))
        return 30.0

    def _resolve_freqtrade_trades_db_path(self) -> str | None:
        config = getattr(self, "config", None)
        if isinstance(config, dict):
            db_url = config.get("db_url")
            if isinstance(db_url, str) and db_url.startswith("sqlite:///"):
                raw = db_url[len("sqlite:///") :]
                if raw:
                    candidate = Path(raw)
                    if candidate.exists():
                        return str(candidate)

        repo_freqtrade_dir = Path(__file__).resolve().parents[2]
        candidates = (
            repo_freqtrade_dir / "tradesv3.dryrun.sqlite",
            repo_freqtrade_dir / "tradesv3.sqlite",
            repo_freqtrade_dir / "user_data" / "tradesv3.dryrun.sqlite",
            repo_freqtrade_dir / "user_data" / "tradesv3.sqlite",
        )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    @staticmethod
    def _dedupe_open_stoploss_orders(trades_db_path: str) -> dict[str, int]:
        duplicates_resolved = 0
        canceled_orders = 0
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

        with sqlite3.connect(trades_db_path) as conn:
            rows = conn.execute(
                """
                SELECT o.ft_trade_id, MAX(o.id) AS keep_id, COUNT(*) AS open_count
                FROM orders o
                JOIN trades t ON t.id = o.ft_trade_id
                WHERE t.is_open = 1
                  AND LOWER(COALESCE(o.ft_order_side, '')) = 'stoploss'
                  AND LOWER(COALESCE(o.status, '')) IN ('open', 'new')
                  AND COALESCE(o.ft_is_open, 1) = 1
                GROUP BY o.ft_trade_id
                HAVING COUNT(*) > 1
                """
            ).fetchall()

            for ft_trade_id, keep_id, _ in rows:
                result = conn.execute(
                    """
                    UPDATE orders
                    SET status = 'canceled',
                        ft_is_open = 0,
                        order_update_date = ?
                    WHERE ft_trade_id = ?
                      AND id <> ?
                      AND LOWER(COALESCE(ft_order_side, '')) = 'stoploss'
                      AND LOWER(COALESCE(status, '')) IN ('open', 'new')
                      AND COALESCE(ft_is_open, 1) = 1
                    """,
                    (now, int(ft_trade_id), int(keep_id)),
                )
                if result.rowcount > 0:
                    duplicates_resolved += 1
                    canceled_orders += int(result.rowcount)

            conn.commit()

        return {
            "duplicates_resolved": duplicates_resolved,
            "canceled_orders": canceled_orders,
        }

    def _resolve_protective_order_ownership(
        self,
        *,
        context: FreqtradeSignalContext | None,
    ) -> ProtectiveOrderOwnership:
        config = getattr(self, "config", None)
        config_mapping = config if isinstance(config, dict) else None
        return resolve_protective_order_ownership(
            config=config_mapping,
            persisted_mode=context.protective_orders_mode if context is not None else None,
        )

    @staticmethod
    def _resolve_confirm_args(*, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str | None, str | None]:
        entry_tag = kwargs.get("entry_tag")
        side = kwargs.get("side")

        if len(args) >= 2:
            entry_tag = entry_tag or args[0]
            side = side or args[1]
        elif len(args) == 1:
            value = args[0]
            if isinstance(value, str) and value.lower() in {"long", "short"}:
                side = side or value
            else:
                entry_tag = entry_tag or value

        entry_tag_value = str(entry_tag) if entry_tag not in (None, "") else None
        side_value = str(side).lower() if side not in (None, "") else None
        return entry_tag_value, side_value

    @staticmethod
    def _attempt_key_from_tag(tag: str | None) -> str | None:
        if not isinstance(tag, str) or not tag.strip():
            return None
        normalized = tag.strip()
        if ":ENTRY:" in normalized:
            return normalized.split(":ENTRY:", 1)[0]
        return normalized

    @staticmethod
    def _entry_idx_from_tag(tag: str | None, *, attempt_key: str) -> int:
        if not isinstance(tag, str) or not tag.strip():
            return 0
        normalized = tag.strip()
        if normalized == attempt_key:
            return 0
        prefix = f"{attempt_key}:ENTRY:"
        if not normalized.startswith(prefix):
            return 0
        try:
            return max(0, int(normalized[len(prefix):].split(":", 1)[0]))
        except ValueError:
            return 0

    @staticmethod
    def _resolve_entry_leg(context: FreqtradeSignalContext, entry_tag: str | None) -> Any:
        if not context.entry_legs:
            return None
        idx = SignalBridgeStrategy._entry_idx_from_tag(entry_tag, attempt_key=context.attempt_key)
        if idx < 0 or idx >= len(context.entry_legs):
            idx = 0
        return context.entry_legs[idx]

    @staticmethod
    def _select_pending_context(pair: str, db_path: str) -> FreqtradeSignalContext | None:
        for context in load_pending_contexts_for_pair(pair, db_path):
            if context.is_executable:
                return context
        return None

    @staticmethod
    def _has_active_entry_runtime(*, db_path: str, attempt_key: str) -> bool:
        if not attempt_key:
            return False
        with sqlite3.connect(db_path) as conn:
            order_row = conn.execute(
                """
                SELECT 1
                FROM orders
                WHERE attempt_key = ?
                  AND purpose = 'ENTRY'
                  AND status NOT IN ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED')
                LIMIT 1
                """,
                (attempt_key,),
            ).fetchone()
            if order_row is not None:
                return True
            trade_row = conn.execute(
                """
                SELECT 1
                FROM trades
                WHERE attempt_key = ?
                  AND state = 'ENTRY_PENDING'
                LIMIT 1
                """,
                (attempt_key,),
            ).fetchone()
        return trade_row is not None

    @staticmethod
    def _select_active_context(pair: str, db_path: str) -> FreqtradeSignalContext | None:
        for context in load_active_contexts_for_pair(pair, db_path):
            if not (context.is_pair_mappable and context.side in {"long", "short"}):
                continue
            if context.trade_state == "OPEN":
                return context
            size, _ = SignalBridgeStrategy._load_position_snapshot(
                db_path=db_path,
                env=context.env,
                symbol=context.symbol,
            )
            if size is not None and size > 0:
                return context
        return None

    @staticmethod
    def _select_exit_context(pair: str, db_path: str) -> FreqtradeSignalContext | None:
        for context in load_active_contexts_for_pair(pair, db_path):
            if context.side not in {"long", "short"}:
                continue
            if context.close_full_requested:
                return context
        return None

    @staticmethod
    def _load_trade_context(pair: str, trade: Trade, db_path: str) -> FreqtradeSignalContext | None:
        attempt_key = SignalBridgeStrategy._trade_attempt_key(trade)
        if attempt_key:
            context = load_context_by_attempt_key(attempt_key, db_path)
            if context is not None:
                return context
        return SignalBridgeStrategy._select_active_context(pair=pair, db_path=db_path)

    @staticmethod
    def _trade_attempt_key(trade: Trade) -> str | None:
        for attr_name in ("enter_tag", "entry_tag", "open_tag"):
            value = getattr(trade, attr_name, None)
            if isinstance(value, str) and value.strip():
                return SignalBridgeStrategy._attempt_key_from_tag(value)
        return None

    @staticmethod
    def _order_attempt_key(order: Order, trade: Trade) -> str | None:
        for attr_name in ("ft_order_tag", "tag", "client_order_id"):
            value = getattr(order, attr_name, None)
            if isinstance(value, str) and value.strip():
                tag = value.strip()
                if ":" in tag:
                    return tag.split(":", 1)[0]
                return tag
        return SignalBridgeStrategy._trade_attempt_key(trade)

    @staticmethod
    def _take_profit_idx_from_tag(order_tag: str | None, attempt_key: str) -> int | None:
        if not isinstance(order_tag, str) or not order_tag.startswith(f"{attempt_key}:TP:"):
            return None
        try:
            return int(order_tag.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _normalize_order_side(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        if normalized in {"buy", "long"}:
            return "buy"
        if normalized in {"sell", "short"}:
            return "sell"
        return None

    @staticmethod
    def _trade_stake_amount(trade: Trade) -> float | None:
        for attr_name in ("stake_amount", "amount_requested"):
            value = getattr(trade, attr_name, None)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        return None

    @staticmethod
    def _absolute_stop_to_relative(*, side: str, stop_price: float, current_rate: float) -> float | None:
        if current_rate <= 0:
            return None
        if side == "long":
            return min(0.0, (stop_price / current_rate) - 1.0)
        if side == "short":
            return min(0.0, 1.0 - (stop_price / current_rate))
        return None

    @staticmethod
    def _reset_entry_columns(dataframe: Any) -> None:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None

    @staticmethod
    def _reset_exit_columns(dataframe: Any) -> None:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = None

    @staticmethod
    def _set_last_row_value(dataframe: Any, column: str, value: Any) -> None:
        row_index = SignalBridgeStrategy._last_row_index(dataframe)
        if row_index is None:
            return
        dataframe.at[row_index, column] = value

    @staticmethod
    def _last_row_index(dataframe: Any) -> Any:
        index = getattr(dataframe, "index", None)
        if index is None:
            return None
        try:
            return index[-1]
        except (IndexError, TypeError):
            return None

    @staticmethod
    def _next_take_profit_action(
        *,
        context: FreqtradeSignalContext,
        current_rate: float,
        db_path: str,
    ) -> dict[str, Any] | None:
        if current_rate <= 0 or context.side not in {"long", "short"}:
            return None

        tp_levels = SignalBridgeStrategy._effective_take_profit_levels(context)
        if not tp_levels:
            return None

        filled_indices = SignalBridgeStrategy._filled_take_profit_indices(
            attempt_key=context.attempt_key,
            db_path=db_path,
        )
        next_idx = next((idx for idx in range(len(tp_levels)) if idx not in filled_indices), None)
        if next_idx is None:
            return None

        target_price = float(tp_levels[next_idx])
        if not SignalBridgeStrategy._is_take_profit_hit(
            side=context.side,
            current_rate=float(current_rate),
            target_price=target_price,
        ):
            return None

        fractions = SignalBridgeStrategy._take_profit_close_fractions(context, len(tp_levels))
        filled_fraction = sum(
            fractions[idx]
            for idx in range(len(fractions))
            if idx in filled_indices
        )
        remaining_fraction = max(0.0, 1.0 - filled_fraction)
        if remaining_fraction <= 0:
            return None

        requested_fraction = float(fractions[next_idx])
        close_fraction_current = min(1.0, requested_fraction / remaining_fraction)
        return {
            "idx": next_idx,
            "target_price": target_price,
            "close_fraction_current": close_fraction_current,
            "close_full": close_fraction_current >= 0.999999,
            "order_tag": f"{context.attempt_key}:TP:{next_idx}",
        }

    @staticmethod
    def _effective_take_profit_levels(context: FreqtradeSignalContext) -> tuple[float, ...]:
        levels = tuple(float(level) for level in context.take_profit_refs if isinstance(level, (int, float)))
        if not levels:
            return ()

        tp_rules = SignalBridgeStrategy._tp_handling_rules(context)
        mode = str(tp_rules.get("tp_handling_mode") or "").strip().lower()
        if mode == "limit_to_max_levels":
            max_levels = tp_rules.get("max_tp_levels")
            if isinstance(max_levels, (int, float)) and int(max_levels) > 0:
                return levels[: int(max_levels)]
        return levels

    @staticmethod
    def _take_profit_close_fractions(context: FreqtradeSignalContext, level_count: int) -> tuple[float, ...]:
        if level_count <= 0:
            return ()

        tp_rules = SignalBridgeStrategy._tp_handling_rules(context)
        distribution = tp_rules.get("tp_close_distribution")
        raw_values: list[float] | None = None
        if isinstance(distribution, dict):
            candidate = distribution.get(level_count)
            if candidate is None:
                candidate = distribution.get(str(level_count))
            if isinstance(candidate, list) and len(candidate) == level_count:
                parsed = [
                    float(value)
                    for value in candidate
                    if isinstance(value, (int, float))
                ]
                if len(parsed) == level_count and sum(parsed) > 0:
                    raw_values = parsed

        if raw_values is None:
            equal_share = 1.0 / level_count
            return tuple(equal_share for _ in range(level_count))

        total = sum(raw_values)
        return tuple(value / total for value in raw_values)

    @staticmethod
    def _tp_handling_rules(context: FreqtradeSignalContext) -> dict[str, Any]:
        management_rules = context.management_rules if isinstance(context.management_rules, dict) else {}
        tp_rules = management_rules.get("tp_handling")
        return tp_rules if isinstance(tp_rules, dict) else {}

    @staticmethod
    def _filled_take_profit_indices(*, attempt_key: str, db_path: str) -> set[int]:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT idx
                FROM orders
                WHERE attempt_key = ?
                  AND purpose = 'TP'
                  AND status = 'FILLED'
                """,
                (attempt_key,),
            ).fetchall()
        return {
            int(row[0])
            for row in rows
            if row and isinstance(row[0], (int, float))
        }

    @staticmethod
    def _is_take_profit_hit(*, side: str, current_rate: float, target_price: float) -> bool:
        if current_rate <= 0 or target_price <= 0:
            return False
        if side == "long":
            return current_rate >= target_price
        if side == "short":
            return current_rate <= target_price
        return False

    # ------------------------------------------------------------------
    # Bridge plotting helpers — read-only, no trading logic
    # ------------------------------------------------------------------

    _BRIDGE_CONTEXT_COLUMNS = (
        "bridge_sl", "bridge_tp1", "bridge_tp2", "bridge_tp3", "bridge_entry_price",
        "bridge_live_entry_open", "bridge_live_sl_open",
        "bridge_live_tp_open_1", "bridge_live_tp_open_2", "bridge_live_tp_open_3",
        "bridge_entry_avg",
        "bridge_entry_pending_e1", "bridge_entry_pending_e2", "bridge_entry_pending_e3",
        "bridge_entry_filled_e1", "bridge_entry_filled_e2", "bridge_entry_filled_e3",
        "bridge_tp1_hit", "bridge_tp2_hit", "bridge_tp3_hit",
    )
    _BRIDGE_EVENT_COLUMNS = (
        "bridge_event_entry", "bridge_event_partial_exit",
        "bridge_event_tp_hit", "bridge_event_sl_hit", "bridge_event_close",
        "bridge_event_entry_e1", "bridge_event_entry_e2", "bridge_event_entry_e3",
        "bridge_event_tp1_hit", "bridge_event_tp2_hit", "bridge_event_tp3_hit",
    )

    def _populate_bridge_columns(self, dataframe: Any, pair: str) -> None:
        """Inject bridge context and event columns into the dataframe for FreqUI plotting.

        This is purely observational - it never changes trading logic.
        Errors are caught and logged so plotting failures cannot break the strategy.
        """
        try:
            import numpy as np
            nan_value: Any = np.nan
        except ImportError:  # pragma: no cover - test env may lack numpy
            nan_value = None

        for col in self._BRIDGE_CONTEXT_COLUMNS:
            dataframe[col] = nan_value
        for col in self._BRIDGE_EVENT_COLUMNS:
            dataframe[col] = 0

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return

        try:
            self._inject_bridge_context(dataframe, pair=pair, db_path=db_path)
            self._inject_bridge_events(dataframe, pair=pair, db_path=db_path)
        except Exception:  # pragma: no cover - defensive; never break trading
            _log.debug("bridge plotting columns failed for %s", pair, exc_info=True)

    def _inject_bridge_context(self, dataframe: Any, *, pair: str, db_path: str) -> None:
        """Fill entry/SL/TP reference lines from active or pending bridge context."""
        context = self._select_active_context(pair=pair, db_path=db_path)
        if context is None:
            context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None:
            return

        runtime_legs = tuple(context.runtime_entry_legs)
        filled_legs = tuple(
            leg for leg in runtime_legs
            if leg.status == "FILLED" and isinstance(leg.price, (int, float)) and float(leg.price) > 0
        )
        position_size, position_entry_price = self._load_position_snapshot(
            db_path=db_path,
            env=context.env,
            symbol=context.symbol,
        )
        has_filled_entry = bool(filled_legs) or (position_size is not None and position_size > 0) or context.signal_status == "ACTIVE"

        for idx, leg in enumerate(runtime_legs[:3], start=1):
            if not isinstance(leg.price, (int, float)) or float(leg.price) <= 0:
                continue
            column_prefix = "bridge_entry_filled" if leg.status == "FILLED" else "bridge_entry_pending"
            dataframe[f"{column_prefix}_e{idx}"] = float(leg.price)

        weighted_avg = self._weighted_average_entry_price(filled_legs)
        if weighted_avg is not None and len(filled_legs) >= 2:
            dataframe["bridge_entry_avg"] = float(weighted_avg)

        entry_price = self._primary_entry_plot_price(context=context, filled_legs=filled_legs, position_entry_price=position_entry_price)
        if entry_price is not None and entry_price > 0:
            dataframe["bridge_entry_price"] = float(entry_price)

        self._inject_live_freqtrade_open_order_lines(
            dataframe,
            attempt_key=context.attempt_key,
        )

        if not has_filled_entry:
            return

        if context.stoploss_ref is not None and context.stoploss_ref > 0:
            dataframe["bridge_sl"] = float(context.stoploss_ref)

        filled_tp_indices = self._filled_take_profit_indices(
            attempt_key=context.attempt_key,
            db_path=db_path,
        )
        tp_levels = self._effective_take_profit_levels(context)
        for idx, level in enumerate(tp_levels[:3]):
            column = f"bridge_tp{idx + 1}_hit" if idx in filled_tp_indices else f"bridge_tp{idx + 1}"
            dataframe[column] = float(level)

    def _inject_bridge_events(self, dataframe: Any, *, pair: str, db_path: str) -> None:
        """Overlay bridge fill events as markers on the dataframe.

        Reads from the ``events`` table and maps event timestamps to the
        closest candle. This is a best-effort read-only overlay.
        """
        symbol = self._pair_to_symbol(pair)
        if not symbol:
            return

        event_map = {
            "ENTRY_FILLED": "bridge_event_entry",
            "PARTIAL_CLOSE_FILLED": "bridge_event_partial_exit",
            "POSITION_CLOSED": "bridge_event_close",
        }

        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA query_only = ON")
                rows = conn.execute(
                    """
                    SELECT e.event_type, e.created_at, e.payload_json
                    FROM events e
                    JOIN signals s ON s.attempt_key = e.attempt_key
                    WHERE s.symbol = ?
                      AND e.event_type IN ('ENTRY_FILLED', 'PARTIAL_CLOSE_FILLED',
                                           'POSITION_CLOSED', 'STOP_HIT')
                    ORDER BY e.created_at ASC
                    """,
                    (symbol,),
                ).fetchall()
        except sqlite3.Error:
            return

        if not rows:
            return

        try:
            import pandas as pd
            df_dates = pd.to_datetime(dataframe["date"], utc=True)
            min_df_time = df_dates.min()
            max_df_time = df_dates.max()
        except Exception:
            return

        for event_type, created_at, payload_json in rows:
            col = event_map.get(event_type)
            payload: dict[str, Any] | None = None
            if payload_json:
                try:
                    parsed_payload = json.loads(payload_json)
                    payload = parsed_payload if isinstance(parsed_payload, dict) else None
                except (json.JSONDecodeError, TypeError):
                    payload = None

            if event_type == "ENTRY_FILLED" and payload:
                entry_idx = payload.get("entry_idx")
                if isinstance(entry_idx, (int, float)) and 0 <= int(entry_idx) <= 2:
                    col = f"bridge_event_entry_e{int(entry_idx) + 1}"
            elif event_type == "PARTIAL_CLOSE_FILLED" and payload:
                tp_idx = payload.get("tp_idx")
                if isinstance(tp_idx, (int, float)):
                    col = "bridge_event_tp_hit"
                    if 0 <= int(tp_idx) <= 2:
                        col = f"bridge_event_tp{int(tp_idx) + 1}_hit"
            elif event_type == "STOP_HIT":
                col = "bridge_event_sl_hit"
            elif event_type == "POSITION_CLOSED" and payload:
                close_reason = str(payload.get("close_reason", ""))
                if "TP" in close_reason:
                    col = "bridge_event_tp_hit"
                    tp_idx = payload.get("tp_idx")
                    if isinstance(tp_idx, (int, float)) and 0 <= int(tp_idx) <= 2:
                        col = f"bridge_event_tp{int(tp_idx) + 1}_hit"

            if col is None:
                continue

            try:
                import pandas as pd
                event_time = pd.Timestamp(created_at, tz="UTC")
                # Ignore events outside the visible candle range to avoid
                # collapsing old markers on the chart borders.
                if event_time < min_df_time or event_time > max_df_time:
                    continue
                idx = (df_dates - event_time).abs().idxmin()
                dataframe.at[idx, col] = 1
            except Exception:
                continue

    @staticmethod
    def _weighted_average_entry_price(entry_legs: tuple[Any, ...]) -> float | None:
        total_weight = 0.0
        weighted_sum = 0.0
        for leg in entry_legs:
            try:
                price = float(getattr(leg, "price", 0.0) or 0.0)
                split = float(getattr(leg, "split", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or split <= 0:
                continue
            weighted_sum += price * split
            total_weight += split
        if total_weight <= 0:
            return None
        return weighted_sum / total_weight

    @staticmethod
    def _primary_entry_plot_price(
        *,
        context: FreqtradeSignalContext,
        filled_legs: tuple[Any, ...],
        position_entry_price: float | None,
    ) -> float | None:
        weighted_avg = SignalBridgeStrategy._weighted_average_entry_price(filled_legs)
        if weighted_avg is not None:
            return float(weighted_avg)
        if isinstance(position_entry_price, (int, float)) and float(position_entry_price) > 0:
            return float(position_entry_price)
        entry_price = context.first_entry_price
        if entry_price is None:
            return None
        try:
            value = float(entry_price)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _load_position_snapshot(*, db_path: str, env: str, symbol: str | None) -> tuple[float | None, float | None]:
        if not symbol:
            return None, None
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT size, entry_price FROM positions WHERE env = ? AND symbol = ? LIMIT 1",
                    (env, symbol),
                ).fetchone()
        except sqlite3.Error:
            return None, None
        if not row:
            return None, None

        size_raw, entry_raw = row[0], row[1]
        try:
            size_value = float(size_raw) if isinstance(size_raw, (int, float)) else None
        except (TypeError, ValueError):
            size_value = None
        try:
            entry_value = float(entry_raw) if isinstance(entry_raw, (int, float)) else None
        except (TypeError, ValueError):
            entry_value = None
        return size_value, entry_value

    def _inject_live_freqtrade_open_order_lines(self, dataframe: Any, *, attempt_key: str) -> None:
        trades_db_path = self._resolve_freqtrade_trades_db_path()
        if not trades_db_path or not attempt_key:
            return

        orders = self._load_live_open_orders_for_attempt(
            trades_db_path=trades_db_path,
            attempt_key=attempt_key,
        )
        if not orders:
            return

        entry_open: float | None = None
        sl_open: float | None = None
        tp_open: dict[int, float] = {}

        for row in orders:
            side = str(row.get("ft_order_side") or "").strip().lower()
            tag = str(row.get("ft_order_tag") or "").strip()
            price_value = self._coerce_positive_float(row.get("stop_price" if side == "stoploss" else "price"))
            if price_value is None:
                continue

            if side == "stoploss":
                if sl_open is None:
                    sl_open = price_value
                continue

            tp_idx = self._tp_idx_from_live_order_tag(tag=tag, attempt_key=attempt_key)
            if tp_idx is not None and tp_idx not in tp_open and tp_idx < 3:
                tp_open[tp_idx] = price_value
                continue

            if entry_open is None:
                if tag == attempt_key or f"{attempt_key}:ENTRY:" in tag or side in {"buy", "sell"}:
                    entry_open = price_value

        if entry_open is not None:
            dataframe["bridge_live_entry_open"] = float(entry_open)
        if sl_open is not None:
            dataframe["bridge_live_sl_open"] = float(sl_open)
        for idx, value in sorted(tp_open.items()):
            dataframe[f"bridge_live_tp_open_{idx + 1}"] = float(value)

    @staticmethod
    def _load_live_open_orders_for_attempt(*, trades_db_path: str, attempt_key: str) -> list[dict[str, Any]]:
        tag_like = f"{attempt_key}:%"
        try:
            with sqlite3.connect(trades_db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT o.id, o.ft_order_side, o.status, o.price, o.stop_price, o.ft_order_tag
                    FROM orders o
                    LEFT JOIN trades t ON t.id = o.ft_trade_id
                    WHERE LOWER(COALESCE(o.status, '')) IN ('open', 'new')
                      AND COALESCE(o.ft_is_open, 1) = 1
                      AND (
                            (COALESCE(t.is_open, 1) = 1 AND t.enter_tag = ?)
                         OR o.ft_order_tag = ?
                         OR o.ft_order_tag LIKE ?
                      )
                    ORDER BY o.id DESC
                    """,
                    (attempt_key, attempt_key, tag_like),
                ).fetchall()
        except sqlite3.Error:
            return []

        return [
            {
                "id": row[0],
                "ft_order_side": row[1],
                "status": row[2],
                "price": row[3],
                "stop_price": row[4],
                "ft_order_tag": row[5],
            }
            for row in rows
        ]

    @staticmethod
    def _tp_idx_from_live_order_tag(*, tag: str, attempt_key: str) -> int | None:
        if not tag or not tag.startswith(f"{attempt_key}:TP:"):
            return None
        try:
            idx = int(tag.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return None
        return idx if idx >= 0 else None

    @staticmethod
    def _coerce_positive_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _pair_to_symbol(pair: str) -> str | None:
        """Reverse-map a freqtrade pair like 'BTC/USDT:USDT' to canonical symbol 'BTCUSDT'."""
        if not pair:
            return None
        normalized = pair.strip().upper()
        # Strip futures suffix
        if ":" in normalized:
            normalized = normalized.split(":")[0]
        # Remove slash
        normalized = normalized.replace("/", "")
        return normalized if normalized else None














