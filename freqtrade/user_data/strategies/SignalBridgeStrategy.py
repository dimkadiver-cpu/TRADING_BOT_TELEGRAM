"""Thin freqtrade strategy that bridges canonical DB signals to entry hooks."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
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
    persist_entry_price_rejected_event,
    resolve_entry_price_policy,
)
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
    bot_db_path: str | None = os.getenv("TELESIGNALBOT_DB_PATH")
    _execution_bootstrap_done: bool = False
    _last_execution_reconciliation_at: float = 0.0

    # ------------------------------------------------------------------
    # FreqUI plot configuration — observability only, no trading logic
    # ------------------------------------------------------------------
    plot_config = {
        "main_plot": {
            "bridge_sl": {"color": "#e74c3c", "type": "line"},
            "bridge_tp1": {"color": "#27ae60", "type": "line"},
            "bridge_tp2": {"color": "#2ecc71", "type": "line"},
            "bridge_tp3": {"color": "#a3d977", "type": "line"},
            "bridge_entry_price": {"color": "#3498db", "type": "line"},
        },
        "subplots": {
            "Bridge Events": {
                "bridge_event_entry": {"color": "#3498db", "type": "bar"},
                "bridge_event_partial_exit": {"color": "#f39c12", "type": "bar"},
                "bridge_event_tp_hit": {"color": "#27ae60", "type": "bar"},
                "bridge_event_sl_hit": {"color": "#e74c3c", "type": "bar"},
                "bridge_event_close": {"color": "#8e44ad", "type": "bar"},
            },
        },
    }

    def populate_indicators(self, dataframe: Any, metadata: dict[str, Any]) -> Any:
        self._maybe_run_execution_reconciliation()
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

        column = "enter_long" if context.side == "long" else "enter_short"
        self._set_last_row_value(dataframe, column, 1)
        self._set_last_row_value(dataframe, "enter_tag", context.entry_tag)
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
        return float(context.stake_amount)

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

        context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None:
            return float(proposed_rate)

        # Only override when the plan has an explicit LIMIT price.
        if context.first_entry_order_type != "LIMIT":
            return float(proposed_rate)

        price = context.first_entry_price
        if price is None or price <= 0:
            return float(proposed_rate)

        return float(price)

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
        trade_entry_side = str(getattr(trade, "entry_side", "") or "").lower()
        trade_exit_side = str(getattr(trade, "exit_side", "") or "").lower()
        filled_qty = float(getattr(order, "safe_filled", 0.0) or 0.0)
        fill_price = float(getattr(order, "safe_price", 0.0) or 0.0)
        if filled_qty <= 0 or fill_price <= 0:
            return

        order_id = getattr(order, "order_id", None)
        order_type = str(getattr(order, "order_type", "") or "LIMIT").upper()
        client_order_id = str(order_id) if order_id else None
        order_tag = str(getattr(order, "ft_order_tag", None) or getattr(order, "tag", None) or "")
        tp_idx = self._take_profit_idx_from_tag(order_tag, attempt_key)

        if order_side == trade_entry_side:
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

        if order_side != trade_exit_side:
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
        del current_time, current_profit, min_stake, max_stake
        del current_entry_rate, current_exit_rate, current_entry_profit, current_exit_profit, kwargs

        db_path = self._resolve_db_path()
        pair = str(getattr(trade, "pair", "") or "").strip()
        if not pair or not db_path:
            return None

        context = self._load_trade_context(pair=pair, trade=trade, db_path=db_path)
        if context is None:
            return None
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

        if bool(getattr(trade, "has_open_orders", False)):
            return None

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
        del amount, time_in_force, current_time

        db_path = self._resolve_db_path()
        if not pair or not db_path:
            return False

        entry_tag, side = self._resolve_confirm_args(args=args, kwargs=kwargs)
        context = load_context_by_attempt_key(entry_tag, db_path) if entry_tag else None
        if context is None:
            context = self._select_pending_context(pair=pair, db_path=db_path)
        if context is None:
            return False
        if not context.is_pair_mappable or context.pair != pair:
            return False
        if context.signal_status != "PENDING":
            return False
        if not context.is_executable:
            return False
        if entry_tag and context.attempt_key != entry_tag:
            return False
        if side and context.side and side != context.side:
            return False

        # Entry price policy — reject if rate diverges from signal entry plan.
        policy = resolve_entry_price_policy(
            context.management_rules,
            getattr(self, "config", None),
        )
        rejection = check_entry_rate(
            entry_prices=context.entry_prices,
            rate=float(rate),
            order_type=str(order_type),
            policy=policy,
        )
        if rejection is not None:
            import logging
            _log = logging.getLogger(__name__)
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
                rejection_info={**rejection, "pair": pair, "order_type": order_type},
            )
            return False

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

        return None

    def _resolve_margin_mode(self) -> str:
        config = getattr(self, "config", None)
        if isinstance(config, dict):
            candidate = config.get("margin_mode")
            if candidate:
                return str(candidate)
        return "isolated"

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
    def _select_pending_context(pair: str, db_path: str) -> FreqtradeSignalContext | None:
        for context in load_pending_contexts_for_pair(pair, db_path):
            if context.is_executable:
                return context
        return None

    @staticmethod
    def _select_active_context(pair: str, db_path: str) -> FreqtradeSignalContext | None:
        for context in load_active_contexts_for_pair(pair, db_path):
            if context.is_pair_mappable and context.side in {"long", "short"}:
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
                return value.strip()
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
    )
    _BRIDGE_EVENT_COLUMNS = (
        "bridge_event_entry", "bridge_event_partial_exit",
        "bridge_event_tp_hit", "bridge_event_sl_hit", "bridge_event_close",
    )

    def _populate_bridge_columns(self, dataframe: Any, pair: str) -> None:
        """Inject bridge context and event columns into the dataframe for FreqUI plotting.

        This is purely observational — it never changes trading logic.
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
        except Exception:  # pragma: no cover — defensive; never break trading
            _log.debug("bridge plotting columns failed for %s", pair, exc_info=True)

    def _inject_bridge_context(self, dataframe: Any, *, pair: str, db_path: str) -> None:
        """Fill SL/TP/entry price reference lines from the active trade context."""
        context = self._select_active_context(pair=pair, db_path=db_path)
        if context is None:
            return

        if context.stoploss_ref is not None and context.stoploss_ref > 0:
            dataframe["bridge_sl"] = float(context.stoploss_ref)

        tp_levels = self._effective_take_profit_levels(context)
        for idx, level in enumerate(tp_levels[:3]):
            dataframe[f"bridge_tp{idx + 1}"] = float(level)

        entry_price = context.first_entry_price
        if entry_price is not None and entry_price > 0:
            dataframe["bridge_entry_price"] = float(entry_price)

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
        except Exception:
            return

        for event_type, created_at, payload_json in rows:
            col = event_map.get(event_type)

            # TP hit vs SL hit: check payload for tp_idx or event_type
            if event_type == "PARTIAL_CLOSE_FILLED" and payload_json:
                try:
                    payload = json.loads(payload_json)
                    if isinstance(payload, dict) and payload.get("tp_idx") is not None:
                        col = "bridge_event_tp_hit"
                except (json.JSONDecodeError, TypeError):
                    pass
            elif event_type == "STOP_HIT":
                col = "bridge_event_sl_hit"
            elif event_type == "POSITION_CLOSED" and payload_json:
                try:
                    payload = json.loads(payload_json)
                    if isinstance(payload, dict) and "TP" in str(payload.get("close_reason", "")):
                        col = "bridge_event_tp_hit"
                except (json.JSONDecodeError, TypeError):
                    pass

            if col is None:
                continue

            try:
                import pandas as pd
                event_time = pd.Timestamp(created_at, tz="UTC")
                idx = (df_dates - event_time).abs().idxmin()
                dataframe.at[idx, col] = 1
            except Exception:
                continue

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













