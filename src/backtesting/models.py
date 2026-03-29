"""Pydantic models for the backtesting signal chain system.

These models represent the data structures used to reconstruct and replay
historical signal chains (NEW_SIGNAL + UPDATE messages) against OHLCV data.

Usage:
    from src.backtesting.models import ChainedMessage, SignalChain, BacktestReadyChain
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from src.parser.models.new_signal import NewSignalEntities
from src.parser.models.update import UpdateEntities


class ChainedMessage(BaseModel):
    """A single message in a signal chain (NEW_SIGNAL or UPDATE).

    Combines data from raw_messages, parse_results, and operational_signals
    into a unified view used by the backtesting pipeline.
    """

    raw_message_id: int
    parse_result_id: int
    telegram_message_id: int
    message_ts: datetime
    """UTC timestamp — critical for backtest realism (no lookahead bias)."""

    message_type: Literal["NEW_SIGNAL", "UPDATE"]
    intents: list[str]
    """Intent names as strings, e.g. ["U_MOVE_STOP", "U_TP_HIT"]."""

    entities: NewSignalEntities | UpdateEntities | None
    """Deserialised from parse_result_normalized_json. Discriminated by message_type."""

    op_signal_id: int | None
    """From operational_signals.op_signal_id."""

    attempt_key: str | None
    """From signals.attempt_key (only set for NEW_SIGNAL rows)."""

    is_blocked: bool
    block_reason: str | None

    risk_budget_usdt: float | None
    """Maximum loss in USDT budgeted for this signal by the operation rules engine."""

    position_size_usdt: float | None
    """Historical position size. ScenarioApplier overwrites this when risk_pct_variant is set."""

    entry_split: dict[str, float] | None
    """Fractional allocation per entry level, e.g. {"E1": 0.3, "E2": 0.7}."""

    management_rules: dict[str, Any] | None
    """Snapshot of operation rules config at signal time."""


class SignalChain(BaseModel):
    """A NEW_SIGNAL message plus all its linked UPDATE messages, ordered by time.

    chain_id is the stable identifier used throughout the backtesting pipeline.
    """

    chain_id: str
    """Stable identifier: f"{trader_id}:{attempt_key}"."""

    trader_id: str
    symbol: str
    side: Literal["BUY", "SELL"]

    new_signal: ChainedMessage
    updates: list[ChainedMessage]
    """All UPDATE messages linked to this chain, sorted by message_ts ASC."""

    entry_prices: list[float]
    """Extracted from NewSignalEntities.entries[*].price.value."""

    sl_price: float
    """Extracted from NewSignalEntities.stop_loss.price.value."""

    tp_prices: list[float]
    """Extracted from NewSignalEntities.take_profits[*].price.value."""

    open_ts: datetime
    """Timestamp of the NEW_SIGNAL message."""

    close_ts: datetime | None
    """Timestamp of U_CLOSE_FULL or U_SL_HIT update, if present."""


class BacktestReadyChain(BaseModel):
    """A SignalChain after a scenario has been applied by ScenarioApplier.

    Uses composition (chain field) rather than inheritance so that the original
    SignalChain is always accessible unchanged alongside the scenario overrides.
    """

    chain: SignalChain

    scenario_name: str
    """Name of the scenario that produced this instance (e.g. "follow_full_chain")."""

    applied_updates: list[ChainedMessage]
    """Subset of chain.updates actually applied under this scenario."""

    effective_sl_price: float
    """Final SL price after applying scenario rules (may differ from chain.sl_price)."""

    effective_tp_prices: list[float]
    """Final TP price list after applying scenario rules."""

    effective_entry_prices: list[float]
    """Final entry prices after applying scenario rules."""

    effective_entry_split: dict[str, float] | None
    """Final entry split allocation after applying scenario rules."""

    effective_position_size_usdt: float | None
    """Final position size in USDT (overridden by scenario if risk_pct_variant is set)."""

    effective_risk_pct: float | None
    """Risk percentage of capital used to compute position size, if applicable."""

    include_blocked: bool = False
    """Whether this chain was included despite being blocked (for analysis purposes)."""
