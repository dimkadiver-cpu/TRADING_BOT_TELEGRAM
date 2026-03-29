"""Tests for SignalBridgeBacktestStrategy.

Uses importlib to load the strategy file directly (same pattern as
test_freqtrade_bridge.py) so it can run without freqtrade installed.

Covers:
- test_normalize_pair / test_denormalize_pair
- test_entry_signal_at_correct_candle
- test_no_entry_before_open_ts
- test_custom_entry_price_forced
- test_custom_stoploss_with_move_stop_delayed
- test_custom_stoploss_sl_to_be_after_tp2
- test_custom_exit_tp_ladder_partial
- test_custom_exit_close_full_from_chain
- test_adjust_trade_position_averaging
- test_signals_only_no_updates
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.backtesting.models import BacktestReadyChain, ChainedMessage, SignalChain
from src.parser.models.canonical import Price
from src.parser.models.update import UpdateEntities

# ── Load strategy module ──────────────────────────────────────────────────────

def _load_strategy_module():
    path = (Path(__file__).resolve().parents[3]
            / "freqtrade" / "user_data" / "strategies"
            / "SignalBridgeBacktestStrategy.py")
    spec = importlib.util.spec_from_file_location("signal_bridge_backtest", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_mod = _load_strategy_module()
SignalBridgeBacktestStrategy = _mod.SignalBridgeBacktestStrategy
_normalize_pair = _mod._normalize_pair
_denormalize_pair = _mod._denormalize_pair


# ── Minimal DataFrame mock ────────────────────────────────────────────────────

class _AtAccessor:
    def __init__(self, frame: "_MiniDataFrame") -> None:
        self._frame = frame

    def __setitem__(self, key: tuple[int, str], value: object) -> None:
        row_idx, col = key
        self._frame._data.setdefault(col, [None] * len(self._frame.index))[row_idx] = value


class _MiniDataFrame:
    """Minimal pandas-compatible DataFrame mock for strategy hook testing."""

    def __init__(self, dates: list[datetime]) -> None:
        n = len(dates)
        self.index = list(range(n))
        self._data: dict[str, list[Any]] = {"date": list(dates)}
        self.at = _AtAccessor(self)

    def __setitem__(self, key: str, value: Any) -> None:
        if isinstance(value, list):
            self._data[key] = list(value)
        else:
            self._data[key] = [value] * len(self.index)

    def __getitem__(self, key: str) -> list[Any]:
        return self._data.get(key, [None] * len(self.index))

    def __contains__(self, key: str) -> bool:
        return key in self._data


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_T0 = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_dates(n: int = 10, start: datetime = _T0) -> list[datetime]:
    return [start + timedelta(minutes=5 * i) for i in range(n)]


def _make_df(n: int = 10, start: datetime = _T0) -> _MiniDataFrame:
    return _MiniDataFrame(dates=_make_dates(n, start))


def _make_chained_msg(
    *,
    tg_id: int = 100,
    message_ts: datetime = _T0,
    message_type: str = "NEW_SIGNAL",
    intents: list[str] | None = None,
    entities: Any = None,
    is_blocked: bool = False,
) -> ChainedMessage:
    return ChainedMessage(
        raw_message_id=tg_id,
        parse_result_id=tg_id,
        telegram_message_id=tg_id,
        message_ts=message_ts,
        message_type=message_type,  # type: ignore[arg-type]
        intents=intents or [],
        entities=entities,
        op_signal_id=tg_id,
        attempt_key=f"trader_3:{tg_id}" if message_type == "NEW_SIGNAL" else None,
        is_blocked=is_blocked,
        block_reason=None,
        risk_budget_usdt=100.0,
        position_size_usdt=1000.0,
        entry_split={"E1": 1.0},
        management_rules=None,
    )


def _make_signal_chain(
    *,
    chain_id: str = "trader_3:100",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    open_ts: datetime = _T0,
    entry_prices: list[float] | None = None,
    sl_price: float = 85000.0,
    tp_prices: list[float] | None = None,
    updates: list[ChainedMessage] | None = None,
    is_blocked: bool = False,
) -> SignalChain:
    new_sig = _make_chained_msg(
        tg_id=100,
        message_ts=open_ts,
        message_type="NEW_SIGNAL",
        is_blocked=is_blocked,
    )
    return SignalChain(
        chain_id=chain_id,
        trader_id="trader_3",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        new_signal=new_sig,
        updates=updates or [],
        entry_prices=entry_prices or [90000.0],
        sl_price=sl_price,
        tp_prices=tp_prices or [95000.0, 100000.0],
        open_ts=open_ts,
        close_ts=None,
    )


def _make_ready_chain(
    *,
    chain: SignalChain | None = None,
    scenario_name: str = "follow_full_chain",
    applied_updates: list[ChainedMessage] | None = None,
    effective_sl_price: float = 85000.0,
    effective_tp_prices: list[float] | None = None,
    effective_entry_prices: list[float] | None = None,
    effective_entry_split: dict[str, float] | None = None,
    effective_position_size_usdt: float = 1000.0,
    include_blocked: bool = False,
) -> BacktestReadyChain:
    if chain is None:
        chain = _make_signal_chain()
    return BacktestReadyChain(
        chain=chain,
        scenario_name=scenario_name,
        applied_updates=applied_updates or [],
        effective_sl_price=effective_sl_price,
        effective_tp_prices=effective_tp_prices or [95000.0, 100000.0],
        effective_entry_prices=effective_entry_prices or [90000.0],
        effective_entry_split=effective_entry_split or {"E1": 1.0},
        effective_position_size_usdt=effective_position_size_usdt,
        effective_risk_pct=None,
        include_blocked=include_blocked,
    )


def _make_strategy_with_chains(
    chains: list[BacktestReadyChain],
) -> SignalBridgeBacktestStrategy:
    """Create a strategy instance with pre-loaded chains (no bot_start needed)."""
    strat = SignalBridgeBacktestStrategy.__new__(SignalBridgeBacktestStrategy)
    strat.config = {}
    strat._trade_state = {}
    strat._active_chains = {}
    strat._chain_by_id = {}
    strat._index_chains(chains)
    return strat


class _MockTrade:
    """Minimal trade mock for strategy hook testing."""

    def __init__(
        self,
        *,
        trade_id: int = 1,
        enter_tag: str = "trader_3:100",
        pair: str = "BTC/USDT:USDT",
        open_rate: float = 90000.0,
        is_short: bool = False,
        stake_amount: float = 1000.0,
    ) -> None:
        self.id = trade_id
        self.enter_tag = enter_tag
        self.pair = pair
        self.open_rate = open_rate
        self.is_short = is_short
        self.stake_amount = stake_amount
        self.amount = stake_amount / open_rate


# ── Tests: normalize/denormalize ─────────────────────────────────────────────

def test_normalize_pair() -> None:
    assert _normalize_pair("BTCUSDT") == "BTC/USDT:USDT"
    assert _normalize_pair("ETHUSDT") == "ETH/USDT:USDT"
    assert _normalize_pair("SOLUSDT") == "SOL/USDT:USDT"
    # Pass-through when already in freqtrade format
    assert _normalize_pair("BTC/USDT:USDT") == "BTC/USDT:USDT"
    assert _normalize_pair("btcusdt") == "BTC/USDT:USDT"


def test_denormalize_pair() -> None:
    assert _denormalize_pair("BTC/USDT:USDT") == "BTCUSDT"
    assert _denormalize_pair("ETH/USDT:USDT") == "ETHUSDT"
    assert _denormalize_pair("BTCUSDT") == "BTCUSDT"   # pass-through


# ── Tests: entry signals ──────────────────────────────────────────────────────

def test_entry_signal_at_correct_candle() -> None:
    """enter_long=1 only at the first candle >= chain.open_ts."""
    open_ts = _T0 + timedelta(minutes=15)  # 4th candle (index 3)
    chain = _make_ready_chain(
        chain=_make_signal_chain(open_ts=open_ts),
    )
    strat = _make_strategy_with_chains([chain])
    df = _make_df(n=10)

    result = strat.populate_indicators(df, {"pair": "BTC/USDT:USDT"})

    enter_long = result["enter_long"]
    assert enter_long[3] == 1, "Expected enter_long=1 at index 3"
    assert all(v == 0 for i, v in enumerate(enter_long) if i != 3), \
        "No other row should have enter_long=1"
    assert result["enter_tag"][3] == "trader_3:100"


def test_no_entry_before_open_ts() -> None:
    """No entry signal set if open_ts is after all candle dates."""
    open_ts = _T0 + timedelta(hours=10)  # beyond the 10-candle window
    chain = _make_ready_chain(chain=_make_signal_chain(open_ts=open_ts))
    strat = _make_strategy_with_chains([chain])
    df = _make_df(n=10)

    result = strat.populate_indicators(df, {"pair": "BTC/USDT:USDT"})

    assert all(v == 0 for v in result["enter_long"]), "No entry should be set"


def test_entry_signal_short_side() -> None:
    """enter_short=1 is set for SELL chains."""
    chain = _make_ready_chain(
        chain=_make_signal_chain(side="SELL"),
    )
    strat = _make_strategy_with_chains([chain])
    df = _make_df(n=5)

    result = strat.populate_indicators(df, {"pair": "BTC/USDT:USDT"})

    assert result["enter_short"][0] == 1
    assert result["enter_long"][0] == 0


# ── Tests: custom entry price ─────────────────────────────────────────────────

def test_custom_entry_price_forced() -> None:
    """custom_entry_price returns effective_entry_prices[0]."""
    chain = _make_ready_chain(effective_entry_prices=[90500.0])
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade()

    price = strat.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=_T0,
        proposed_rate=89000.0,
        entry_tag="trader_3:100",
        side="long",
    )
    assert price == 90500.0


def test_custom_entry_price_unknown_tag_returns_proposed() -> None:
    strat = _make_strategy_with_chains([])
    trade = _MockTrade()
    price = strat.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=_T0,
        proposed_rate=89000.0,
        entry_tag="unknown_chain",
        side="long",
    )
    assert price == 89000.0


# ── Tests: custom stoploss ────────────────────────────────────────────────────

def test_custom_stoploss_baseline() -> None:
    """SL is set from effective_sl_price before any updates."""
    chain = _make_ready_chain(effective_sl_price=85000.0)
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade(open_rate=90000.0)

    ratio = strat.custom_stoploss(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=_T0,
        current_rate=90000.0,
        current_profit=0.0,
    )
    # Expected: (85000 - 90000) / 90000 ≈ -0.0556
    assert ratio < 0
    assert abs(ratio - (-5000 / 90000)) < 1e-6


def test_custom_stoploss_with_move_stop_delayed() -> None:
    """U_MOVE_STOP only activates after +1 candle (5 min) delay."""
    move_ts = _T0 + timedelta(hours=1)
    new_sl_price = Price(raw="87000", value=87000.0)
    upd = _make_chained_msg(
        tg_id=101,
        message_ts=move_ts,
        message_type="UPDATE",
        intents=["U_MOVE_STOP"],
        entities=UpdateEntities(new_sl_level=new_sl_price),
    )
    chain = _make_ready_chain(
        effective_sl_price=85000.0,
        applied_updates=[upd],
    )
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade(open_rate=90000.0)

    # Before delay: SL should still be 85000
    before_delay = move_ts + timedelta(minutes=4)
    ratio_before = strat.custom_stoploss(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=before_delay,
        current_rate=90000.0,
        current_profit=0.01,
    )
    state = strat._trade_state[trade.id]
    assert state["sl_level"] == 85000.0, "SL should not have moved yet"

    # After delay: SL should move to 87000
    # Reset state to simulate new candle
    del strat._trade_state[trade.id]
    after_delay = move_ts + timedelta(minutes=5)
    strat.custom_stoploss(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=after_delay,
        current_rate=90000.0,
        current_profit=0.01,
    )
    state = strat._trade_state[trade.id]
    assert state["sl_level"] == 87000.0, "SL should have moved to 87000"


def test_custom_stoploss_sl_to_be_after_tp2() -> None:
    """SL moves to breakeven (open_rate) when TP2 has been hit."""
    chain = _make_ready_chain(effective_sl_price=85000.0)
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade(open_rate=90000.0)

    # Simulate TP2 already hit by pre-populating _trade_state
    strat._trade_state[trade.id] = {
        "sl_level": 85000.0,
        "tps_hit": {1, 2},
        "entries_filled": set(),
        "exits_done": set(),
    }

    strat.custom_stoploss(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=_T0 + timedelta(hours=2),
        current_rate=90000.0,
        current_profit=0.05,
    )
    state = strat._trade_state[trade.id]
    assert state["sl_level"] == 90000.0, "SL should be at breakeven (open_rate)"


# ── Tests: custom exit ────────────────────────────────────────────────────────

def test_custom_exit_last_tp_triggers_full_exit() -> None:
    """When the last TP is hit, custom_exit returns 'tp2'."""
    chain = _make_ready_chain(
        effective_tp_prices=[95000.0, 100000.0],
    )
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade()

    # TP2 hit (last)
    result = strat.custom_exit(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=_T0 + timedelta(hours=2),
        current_rate=101000.0,   # above TP2
        current_profit=0.12,
    )
    assert result == "tp2"


def test_custom_exit_tp_ladder_partial() -> None:
    """Intermediate TP hit does NOT trigger full exit via custom_exit
    (handled by adjust_trade_position instead)."""
    chain = _make_ready_chain(
        effective_tp_prices=[95000.0, 100000.0],
    )
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade()

    # TP1 hit (intermediate) → should NOT exit via custom_exit
    result = strat.custom_exit(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=_T0 + timedelta(hours=1),
        current_rate=96000.0,   # between TP1 and TP2
        current_profit=0.067,
    )
    assert result is None, "Intermediate TP should not trigger full exit"

    # adjust_trade_position should return negative stake for partial exit
    sell = strat.adjust_trade_position(
        trade=trade,
        current_time=_T0 + timedelta(hours=1),
        current_rate=96000.0,
        current_profit=0.067,
        min_stake=-50.0,
        max_stake=1000.0,
        current_entry_rate=90000.0,
        current_exit_rate=96000.0,
        current_entry_profit=0.067,
    )
    assert sell is not None and sell < 0, "Should return negative stake for partial TP1 exit"


def test_custom_exit_close_full_from_chain() -> None:
    """U_CLOSE_FULL from applied_updates triggers 'close_full' exit."""
    close_ts = _T0 + timedelta(hours=2)
    upd = _make_chained_msg(
        tg_id=201,
        message_ts=close_ts,
        message_type="UPDATE",
        intents=["U_CLOSE_FULL"],
        entities=UpdateEntities(),
    )
    chain = _make_ready_chain(applied_updates=[upd])
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade()

    # Before delay: no exit
    result_before = strat.custom_exit(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=close_ts + timedelta(minutes=4),
        current_rate=91000.0,
        current_profit=0.01,
    )
    assert result_before is None

    # After delay: should exit
    result_after = strat.custom_exit(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=close_ts + timedelta(minutes=5),
        current_rate=91000.0,
        current_profit=0.01,
    )
    assert result_after == "close_full"


# ── Tests: adjust_trade_position ─────────────────────────────────────────────

def test_adjust_trade_position_averaging() -> None:
    """When price drops to E2, adjust_trade_position returns positive stake."""
    e2_price = 87000.0
    chain = _make_ready_chain(
        chain=_make_signal_chain(entry_prices=[90000.0, e2_price]),
        effective_entry_prices=[90000.0, e2_price],
        effective_entry_split={"E1": 0.5, "E2": 0.5},
        effective_position_size_usdt=1000.0,
    )
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade()

    # Price above E2: no additional entry
    result_above = strat.adjust_trade_position(
        trade=trade,
        current_time=_T0 + timedelta(hours=1),
        current_rate=88000.0,
        current_profit=-0.02,
        min_stake=10.0,
        max_stake=1000.0,
        current_entry_rate=90000.0,
        current_exit_rate=88000.0,
        current_entry_profit=-0.02,
    )
    assert result_above is None

    # Price at E2 level: should add to position
    result_e2 = strat.adjust_trade_position(
        trade=trade,
        current_time=_T0 + timedelta(hours=2),
        current_rate=86500.0,   # below E2
        current_profit=-0.04,
        min_stake=10.0,
        max_stake=1000.0,
        current_entry_rate=90000.0,
        current_exit_rate=86500.0,
        current_entry_profit=-0.04,
    )
    assert result_e2 is not None and result_e2 > 0, "Should add stake at E2"
    assert result_e2 == pytest.approx(500.0)  # 1000 * 0.5


def test_signals_only_no_updates() -> None:
    """signals_only scenario: applied_updates=[], no dynamic moves."""
    upd = _make_chained_msg(
        tg_id=301,
        message_ts=_T0 + timedelta(hours=1),
        message_type="UPDATE",
        intents=["U_MOVE_STOP"],
        entities=UpdateEntities(new_sl_level=Price(raw="87000", value=87000.0)),
    )
    # signals_only: applied_updates is EMPTY regardless of chain.updates
    chain = _make_ready_chain(
        applied_updates=[],   # <-- signals_only empties this
    )
    strat = _make_strategy_with_chains([chain])
    trade = _MockTrade(open_rate=90000.0)

    strat.custom_stoploss(
        pair="BTC/USDT:USDT",
        trade=trade,
        current_time=_T0 + timedelta(hours=2),
        current_rate=90000.0,
        current_profit=0.0,
    )
    state = strat._trade_state[trade.id]
    # SL should remain at effective_sl_price (85000), no U_MOVE_STOP applied
    assert state["sl_level"] == 85000.0, "SL should not move in signals_only mode"


# ── Tests: bot_start ──────────────────────────────────────────────────────────

def test_bot_start_loads_chains(tmp_path: Path) -> None:
    """bot_start reads signal_chains.json and indexes chains."""
    chain = _make_ready_chain()
    chains_path = tmp_path / "signal_chains.json"
    chains_path.write_text(
        json.dumps([chain.model_dump(mode="json")]),
        encoding="utf-8",
    )

    strat = SignalBridgeBacktestStrategy.__new__(SignalBridgeBacktestStrategy)
    strat.config = {"strategy_params": {"signal_chains_path": str(chains_path)}}
    strat._trade_state = {}
    strat._active_chains = {}
    strat._chain_by_id = {}

    strat.bot_start()

    assert "BTC/USDT:USDT" in strat._active_chains
    assert len(strat._active_chains["BTC/USDT:USDT"]) == 1
    assert "trader_3:100" in strat._chain_by_id


def test_bot_start_missing_path_does_not_crash(tmp_path: Path) -> None:
    """bot_start with missing path logs a warning but doesn't raise."""
    strat = SignalBridgeBacktestStrategy.__new__(SignalBridgeBacktestStrategy)
    strat.config = {}
    strat._trade_state = {}
    strat._active_chains = {}
    strat._chain_by_id = {}

    strat.bot_start()  # should not raise

    assert strat._active_chains == {}
