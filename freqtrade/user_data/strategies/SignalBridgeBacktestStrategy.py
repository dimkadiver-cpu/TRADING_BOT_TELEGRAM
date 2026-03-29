"""Freqtrade IStrategy for backtesting signal chains.

Replays historical signal chains (BacktestReadyChain) against OHLCV data.
Each chain represents a NEW_SIGNAL plus its UPDATE messages, processed by
ScenarioApplier before being handed to this strategy.

Handoff protocol:
    config["strategy_params"]["signal_chains_path"] → path to signal_chains.json
    signal_chains.json → list of BacktestReadyChain (serialised via model_dump)

Architectural constraints:
    - COMPLETELY separate from SignalBridgeStrategy (live).
    - NO imports from src/execution/.
    - Symbol normalisation is local (_normalize_pair, _denormalize_pair).
    - Targeting Bybit perpetual futures: BTCUSDT ↔ BTC/USDT:USDT.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ── freqtrade compat ──────────────────────────────────────────────────────────

try:
    from freqtrade.strategy import IStrategy
    from freqtrade.persistence import Trade
except ImportError:  # pragma: no cover - exercised indirectly in unit tests
    class IStrategy:  # type: ignore[override]
        INTERFACE_VERSION = 3
        minimal_roi: dict[str, float] = {}
        stoploss = -1.0
        timeframe = "1m"
        can_short = True
        process_only_new_candles = False
        startup_candle_count = 0
        position_adjustment_enable = True
        use_custom_stoploss = True

        def __init__(self, config: dict[str, Any] | None = None) -> None:
            self.config: dict[str, Any] = config or {}

    class Trade:  # type: ignore[override]
        pass

# ── project path ──────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.backtesting.models import BacktestReadyChain
from src.parser.models.update import UpdateEntities

# ── Constants ─────────────────────────────────────────────────────────────────

_CANDLE_DELAY = timedelta(minutes=5)   # 1 candle delay for UPDATE application


# ── Pair normalisation (local, Bybit perpetual futures) ──────────────────────

def _normalize_pair(symbol: str) -> str:
    """Convert canonical symbol to freqtrade pair format.

    Examples:
        BTCUSDT  → BTC/USDT:USDT
        ETHUSDT  → ETH/USDT:USDT
        BTC/USDT:USDT → BTC/USDT:USDT  (pass-through)
    """
    s = symbol.upper().strip()
    if "/" in s:
        return s
    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base}/USDT:USDT"
    if s.endswith("USD"):
        base = s[:-3]
        return f"{base}/USD:USD"
    return s


def _denormalize_pair(pair: str) -> str:
    """Convert freqtrade pair format to canonical symbol.

    Examples:
        BTC/USDT:USDT → BTCUSDT
        ETH/USDT:USDT → ETHUSDT
    """
    if "/" not in pair:
        return pair
    base, rest = pair.split("/", 1)
    quote = rest.split(":")[0]
    return f"{base}{quote}"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure_utc(dt: datetime) -> datetime:
    """Return *dt* as a timezone-aware UTC datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _find_entry_row_idx(dates: Any, open_ts: datetime) -> int | None:
    """Return the index of the first date >= open_ts, or None.

    Works with both pandas DatetimeSeries (iterable) and plain lists.
    """
    ts = _ensure_utc(open_ts)
    for i, d in enumerate(dates):
        try:
            d_utc = _ensure_utc(d.to_pydatetime()) if hasattr(d, "to_pydatetime") else _ensure_utc(d)
        except Exception:
            continue
        if d_utc >= ts:
            return i
    return None


def _tp_reached(current_rate: float, tp_price: float, is_short: bool) -> bool:
    """Check whether *current_rate* has reached the TP target."""
    if is_short:
        return current_rate <= tp_price
    return current_rate >= tp_price


# ── Strategy ──────────────────────────────────────────────────────────────────


class SignalBridgeBacktestStrategy(IStrategy):
    """Freqtrade strategy that replays BacktestReadyChain signal chains."""

    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = True
    use_custom_stoploss = True
    minimal_roi = {"0": 100.0}      # effectively disabled
    stoploss = -0.99                 # effectively disabled
    position_adjustment_enable = True
    startup_candle_count = 0

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)    # type: ignore[call-arg]
        self._active_chains: dict[str, list[BacktestReadyChain]] = {}
        """pair (freqtrade format) → list of BacktestReadyChain."""

        self._chain_by_id: dict[str, BacktestReadyChain] = {}
        """chain_id → BacktestReadyChain for O(1) lookup in hooks."""

        self._trade_state: dict[int, dict[str, Any]] = {}
        """trade.id → per-trade mutable state (sl_level, tps_hit, …)."""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def bot_start(self, **kwargs: Any) -> None:
        """Load signal_chains.json and build lookup indexes."""
        path_str: str | None = None
        try:
            params: dict[str, Any] = self.config.get("strategy_params") or {}
            path_str = params.get("signal_chains_path")
            if not path_str:
                _log.warning("strategy_params.signal_chains_path not set — no chains loaded")
                return
            chains_path = Path(path_str)
            if not chains_path.is_absolute():
                chains_path = _PROJECT_ROOT / chains_path
            raw: list[dict[str, Any]] = json.loads(chains_path.read_text(encoding="utf-8"))
            chains = [BacktestReadyChain.model_validate(item) for item in raw]
            self._index_chains(chains)
            _log.info("Loaded %d chains from %s", len(chains), chains_path)
        except Exception as exc:
            _log.error("bot_start: failed to load chains from %s: %s", path_str, exc)

    def _index_chains(self, chains: list[BacktestReadyChain]) -> None:
        """Build pair → chains and chain_id → chain indexes."""
        self._active_chains = {}
        self._chain_by_id = {}
        for chain in chains:
            pair = _normalize_pair(chain.chain.symbol)
            self._active_chains.setdefault(pair, []).append(chain)
            self._chain_by_id[chain.chain.chain_id] = chain

    # ── Entry signals ─────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: Any, metadata: dict[str, Any]) -> Any:
        """Set enter_long / enter_short / enter_tag at the first candle >= open_ts."""
        pair = str((metadata or {}).get("pair") or "").strip()
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""

        chains = self._active_chains.get(pair, [])
        if not chains:
            return dataframe

        dates = dataframe["date"]
        for bchain in chains:
            if bchain.chain.new_signal.is_blocked and not bchain.include_blocked:
                continue
            row_idx = _find_entry_row_idx(dates, bchain.chain.open_ts)
            if row_idx is None:
                continue
            if bchain.chain.side == "BUY":
                dataframe.at[row_idx, "enter_long"] = 1
            else:
                dataframe.at[row_idx, "enter_short"] = 1
            dataframe.at[row_idx, "enter_tag"] = bchain.chain.chain_id

        return dataframe

    def populate_entry_trend(self, dataframe: Any, metadata: dict[str, Any]) -> Any:
        """Entry signals were set in populate_indicators; pass through."""
        return dataframe

    def populate_exit_trend(self, dataframe: Any, metadata: dict[str, Any]) -> Any:
        """Exits are driven by custom_stoploss and custom_exit; pass through."""
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    # ── Entry price ───────────────────────────────────────────────────────────

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
        """Force entry at effective_entry_prices[0] (E1 limit order)."""
        bchain = self._chain_by_id.get(entry_tag or "")
        if bchain is None or not bchain.effective_entry_prices:
            return float(proposed_rate)
        return float(bchain.effective_entry_prices[0])

    # ── Position adjustment (averaging + partial TP exits) ───────────────────

    def adjust_trade_position(
        self,
        trade: Any,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        **kwargs: Any,
    ) -> float | None:
        """Handle averaging entries (E2/E3) and partial TP exits."""
        bchain = self._chain_by_id.get(getattr(trade, "enter_tag", "") or "")
        if bchain is None:
            return None

        state = self._get_or_init_state(trade, bchain)
        ct = _ensure_utc(current_time) if isinstance(current_time, datetime) else current_time
        is_short: bool = bool(getattr(trade, "is_short", False))
        stake_amount: float = float(getattr(trade, "stake_amount", 0.0))

        # ── Partial TP exits (non-last TP candles already hit) ──────────────
        tp_prices = bchain.effective_tp_prices
        n_tps = len(tp_prices)

        for i, tp_price in enumerate(tp_prices[:-1] if n_tps > 1 else []):
            tp_num = i + 1
            exit_key = f"tp_exit_{tp_num}"
            if exit_key in state["exits_done"]:
                continue
            if tp_num not in state["tps_hit"]:
                # Check if TP reached now
                if _tp_reached(current_rate, tp_price, is_short):
                    state["tps_hit"].add(tp_num)
                else:
                    continue
            # TP hit: do partial exit
            state["exits_done"].add(exit_key)
            partial_stake = -(stake_amount / n_tps)
            return partial_stake

        # ── Averaging entries E2, E3, … from effective_entry_prices ─────────
        for i, ep in enumerate(bchain.effective_entry_prices[1:], start=2):
            entry_key = f"E{i}"
            if entry_key in state["entries_filled"]:
                continue
            if (not is_short and current_rate <= ep) or (is_short and current_rate >= ep):
                state["entries_filled"].add(entry_key)
                split = (bchain.effective_entry_split or {}).get(entry_key, 0.0)
                if bchain.effective_position_size_usdt and split > 0:
                    return float(bchain.effective_position_size_usdt * split)

        # ── U_REENTER / U_ADD_ENTRY from applied_updates ─────────────────────
        for upd in bchain.applied_updates:
            has_reenter = "U_REENTER" in upd.intents or "U_ADD_ENTRY" in upd.intents
            if not has_reenter:
                continue
            upd_key = f"reenter_{upd.telegram_message_id}"
            if upd_key in state["entries_filled"]:
                continue
            upd_ts = _ensure_utc(upd.message_ts)
            if isinstance(ct, datetime) and ct < upd_ts + _CANDLE_DELAY:
                continue
            # Extract new entry price from entities
            entry_price: float | None = None
            if isinstance(upd.entities, UpdateEntities):
                if upd.entities.new_entry_price is not None and upd.entities.new_entry_price.value is not None:
                    entry_price = float(upd.entities.new_entry_price.value)
                elif upd.entities.reenter_entries:
                    first = upd.entities.reenter_entries[0]
                    if first.price is not None and first.price.value is not None:
                        entry_price = float(first.price.value)
            if entry_price is None:
                state["entries_filled"].add(upd_key)
                continue
            if (not is_short and current_rate <= entry_price) or (is_short and current_rate >= entry_price):
                state["entries_filled"].add(upd_key)
                if bchain.effective_position_size_usdt:
                    return float(bchain.effective_position_size_usdt * 0.5)

        return None

    # ── Dynamic stoploss ──────────────────────────────────────────────────────

    def custom_stoploss(
        self,
        pair: str,
        trade: Any,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> float:
        """Apply U_MOVE_STOP from applied_updates with +1 candle delay.

        Also applies sl_to_be when TP2 was hit (tracked in _trade_state).
        """
        bchain = self._chain_by_id.get(getattr(trade, "enter_tag", "") or "")
        if bchain is None:
            return self.stoploss

        state = self._get_or_init_state(trade, bchain)
        ct = _ensure_utc(current_time) if isinstance(current_time, datetime) else current_time
        is_short: bool = bool(getattr(trade, "is_short", False))
        open_rate: float = float(getattr(trade, "open_rate", current_rate))

        sl_price: float = state["sl_level"]

        # ── Apply U_MOVE_STOP / U_MOVE_STOP_TO_BE from applied_updates ───────
        for upd in bchain.applied_updates:
            has_move = "U_MOVE_STOP" in upd.intents or "U_MOVE_STOP_TO_BE" in upd.intents
            if not has_move:
                continue
            upd_ts = _ensure_utc(upd.message_ts)
            if isinstance(ct, datetime) and ct < upd_ts + _CANDLE_DELAY:
                continue
            # Move to explicit level or breakeven
            if isinstance(upd.entities, UpdateEntities):
                if upd.entities.new_sl_level is not None and upd.entities.new_sl_level.value is not None:
                    sl_price = float(upd.entities.new_sl_level.value)
                else:
                    # U_MOVE_STOP_TO_BE or explicit breakeven intent
                    sl_price = open_rate
            elif "U_MOVE_STOP_TO_BE" in upd.intents:
                sl_price = open_rate

        # ── sl_to_be_after_tp2: override if TP2 was hit ───────────────────────
        tps_hit: set[int] = state["tps_hit"]
        if tps_hit and any(t >= 2 for t in tps_hit):
            sl_price = open_rate

        # Persist updated level
        state["sl_level"] = sl_price

        # ── Convert to freqtrade stoploss ratio ───────────────────────────────
        if current_rate <= 0:
            return self.stoploss
        if is_short:
            ratio = (current_rate - sl_price) / current_rate
        else:
            ratio = (sl_price - current_rate) / current_rate

        # Clamp: freqtrade requires stoploss < 0 and > -1
        return float(max(-0.999, min(-1e-4, ratio)))

    # ── Exit (last TP + U_CLOSE_FULL) ─────────────────────────────────────────

    def custom_exit(
        self,
        pair: str,
        trade: Any,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> str | None:
        """Return exit reason on last TP hit or U_CLOSE_FULL.

        Intermediate TP partial exits are handled in adjust_trade_position.
        """
        bchain = self._chain_by_id.get(getattr(trade, "enter_tag", "") or "")
        if bchain is None:
            return None

        state = self._get_or_init_state(trade, bchain)
        ct = _ensure_utc(current_time) if isinstance(current_time, datetime) else current_time
        is_short: bool = bool(getattr(trade, "is_short", False))

        # ── U_CLOSE_FULL ──────────────────────────────────────────────────────
        for upd in bchain.applied_updates:
            if "U_CLOSE_FULL" not in upd.intents:
                continue
            upd_ts = _ensure_utc(upd.message_ts)
            if isinstance(ct, datetime) and ct >= upd_ts + _CANDLE_DELAY:
                return "close_full"

        # ── Last TP → full exit ───────────────────────────────────────────────
        tp_prices = bchain.effective_tp_prices
        if not tp_prices:
            return None

        last_tp_num = len(tp_prices)
        last_tp_price = tp_prices[-1]

        if last_tp_num not in state["tps_hit"] and _tp_reached(current_rate, last_tp_price, is_short):
            state["tps_hit"].add(last_tp_num)
            return f"tp{last_tp_num}"

        return None

    # ── State management ─────────────────────────────────────────────────────

    def _get_or_init_state(
        self,
        trade: Any,
        bchain: BacktestReadyChain,
    ) -> dict[str, Any]:
        trade_id: int = int(getattr(trade, "id", id(trade)))
        if trade_id not in self._trade_state:
            self._trade_state[trade_id] = {
                "sl_level": bchain.effective_sl_price,
                "tps_hit": set(),
                "entries_filled": set(),
                "exits_done": set(),
            }
        return self._trade_state[trade_id]
