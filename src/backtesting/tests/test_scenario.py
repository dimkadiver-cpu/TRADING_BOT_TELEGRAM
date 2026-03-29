"""Tests for ScenarioLoader and ScenarioApplier.

No DB required — all SignalChain instances are built in-memory.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.backtesting.models import ChainedMessage, SignalChain
from src.backtesting.scenario import (
    BacktestScenario,
    BacktestSettings,
    ScenarioApplier,
    ScenarioConditions,
    ScenarioConfig,
    ScenarioLoader,
)
from src.parser.models.canonical import Price
from src.parser.models.update import UpdateEntities

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
_UPDATE_TS = datetime(2025, 6, 1, 11, 0, 0, tzinfo=timezone.utc)

_COUNTER = 0


def _next_id() -> int:
    global _COUNTER
    _COUNTER += 1
    return _COUNTER


def _make_chained_message(
    *,
    message_type: str = "NEW_SIGNAL",
    intents: list[str] | None = None,
    entities=None,
    is_blocked: bool = False,
    block_reason: str | None = None,
    position_size_usdt: float | None = 100.0,
    entry_split: dict | None = None,
    message_ts: datetime | None = None,
) -> ChainedMessage:
    return ChainedMessage(
        raw_message_id=_next_id(),
        parse_result_id=_next_id(),
        telegram_message_id=_next_id(),
        message_ts=message_ts or _BASE_TS,
        message_type=message_type,  # type: ignore[arg-type]
        intents=intents or [],
        entities=entities,
        op_signal_id=_next_id(),
        attempt_key=f"T:chat_001:tg{_next_id()}:trader_3" if message_type == "NEW_SIGNAL" else None,
        is_blocked=is_blocked,
        block_reason=block_reason,
        risk_budget_usdt=10.0,
        position_size_usdt=position_size_usdt,
        entry_split=entry_split,
        management_rules=None,
    )


def _make_update(
    *,
    intents: list[str] | None = None,
    tp_hit_number: int | None = None,
    new_sl_level: float | None = None,
    message_ts: datetime | None = None,
) -> ChainedMessage:
    entities = UpdateEntities(
        tp_hit_number=tp_hit_number,
        new_sl_level=Price.from_float(new_sl_level) if new_sl_level is not None else None,
    )
    return _make_chained_message(
        message_type="UPDATE",
        intents=intents or [],
        entities=entities,
        message_ts=message_ts or _UPDATE_TS,
    )


def _make_chain(
    *,
    entry_prices: list[float] | None = None,
    sl_price: float = 85000.0,
    tp_prices: list[float] | None = None,
    updates: list[ChainedMessage] | None = None,
    is_blocked: bool = False,
    entry_split: dict | None = None,
    position_size_usdt: float | None = 100.0,
) -> SignalChain:
    if entry_prices is None:
        entry_prices = [90000.0]
    if tp_prices is None:
        tp_prices = [95000.0, 100000.0]

    new_signal = _make_chained_message(
        message_type="NEW_SIGNAL",
        is_blocked=is_blocked,
        entry_split=entry_split,
        position_size_usdt=position_size_usdt,
    )
    return SignalChain(
        chain_id=f"trader_3:{new_signal.attempt_key}",
        trader_id="trader_3",
        symbol="BTCUSDT",
        side="BUY",
        new_signal=new_signal,
        updates=updates or [],
        entry_prices=entry_prices,
        sl_price=sl_price,
        tp_prices=tp_prices,
        open_ts=_BASE_TS,
        close_ts=None,
    )


def _scenario(
    name: str = "test",
    *,
    follow_full_chain: bool = True,
    signals_only: bool = False,
    sl_to_be_after_tp2: bool = False,
    vary_entry_pct: float | None = None,
    risk_pct_variant: float | None = None,
    gate_mode_variant: str | None = None,
) -> BacktestScenario:
    return BacktestScenario(
        name=name,
        description="test scenario",
        conditions=ScenarioConditions(
            follow_full_chain=follow_full_chain,
            signals_only=signals_only,
            sl_to_be_after_tp2=sl_to_be_after_tp2,
            vary_entry_pct=vary_entry_pct,
            risk_pct_variant=risk_pct_variant,
            gate_mode_variant=gate_mode_variant,  # type: ignore[arg-type]
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_scenario_conditions_defaults() -> None:
    """Default ScenarioConditions flags match the PRD spec."""
    cond = ScenarioConditions()
    assert cond.follow_full_chain is True
    assert cond.signals_only is False
    assert cond.sl_to_be_after_tp2 is False
    assert cond.vary_entry_pct is None
    assert cond.risk_pct_variant is None
    assert cond.gate_mode_variant is None


def test_load_scenario_config_from_yaml() -> None:
    """ScenarioLoader.load() parses backtest_scenarios.yaml correctly."""
    yaml_path = str(
        Path(__file__).parent.parent.parent.parent / "config" / "backtest_scenarios.yaml"
    )
    assert os.path.exists(yaml_path), f"YAML not found at {yaml_path}"

    config = ScenarioLoader.load(yaml_path)

    assert isinstance(config, ScenarioConfig)
    assert len(config.scenarios) == 6

    names = [s.name for s in config.scenarios]
    assert "follow_full_chain" in names
    assert "signals_only" in names
    assert "sl_to_be_after_tp2" in names
    assert "aggressive_averaging" in names
    assert "double_risk" in names
    assert "gate_warn" in names

    assert isinstance(config.backtest_settings, BacktestSettings)
    assert config.backtest_settings.capital_base_usdt == 1000.0
    assert config.backtest_settings.timeframe == "5m"
    assert config.backtest_settings.exchange == "bybit"
    assert config.backtest_settings.max_open_trades == 10

    # aggressive_averaging has vary_entry_pct=0.50
    agg = next(s for s in config.scenarios if s.name == "aggressive_averaging")
    assert agg.conditions.vary_entry_pct == 0.50

    # double_risk has risk_pct_variant=2.0
    dr = next(s for s in config.scenarios if s.name == "double_risk")
    assert dr.conditions.risk_pct_variant == 2.0

    # gate_warn has gate_mode_variant=warn
    gw = next(s for s in config.scenarios if s.name == "gate_warn")
    assert gw.conditions.gate_mode_variant == "warn"


def test_signals_only_no_updates_applied() -> None:
    """signals_only=True → applied_updates is empty, originals unchanged."""
    updates = [
        _make_update(intents=["U_MOVE_STOP"], new_sl_level=87000.0),
        _make_update(intents=["U_CLOSE_FULL"]),
    ]
    chain = _make_chain(updates=updates)
    sc = _scenario("signals_only", follow_full_chain=False, signals_only=True)

    ready = ScenarioApplier.apply(chain, sc)

    assert ready.applied_updates == []
    assert ready.effective_sl_price == chain.sl_price
    assert ready.effective_tp_prices == chain.tp_prices
    assert ready.effective_entry_prices == chain.entry_prices
    assert ready.scenario_name == "signals_only"


def test_follow_full_chain_all_updates_applied() -> None:
    """follow_full_chain=True → all updates included."""
    updates = [
        _make_update(intents=["U_MOVE_STOP"], new_sl_level=87000.0),
        _make_update(intents=["U_TP_HIT"], tp_hit_number=1),
        _make_update(intents=["U_CLOSE_FULL"]),
    ]
    chain = _make_chain(updates=updates)
    sc = _scenario("full", follow_full_chain=True)

    ready = ScenarioApplier.apply(chain, sc)

    assert len(ready.applied_updates) == 3
    assert ready.effective_sl_price == chain.sl_price  # no sl_to_be_after_tp2


def test_sl_to_be_after_tp2_activates() -> None:
    """sl_to_be_after_tp2=True + U_TP_HIT(tp_hit_number=2) → SL = entry_prices[0]."""
    updates = [
        _make_update(intents=["U_TP_HIT"], tp_hit_number=1),
        _make_update(intents=["U_TP_HIT"], tp_hit_number=2),
    ]
    chain = _make_chain(
        entry_prices=[90000.0],
        sl_price=85000.0,
        updates=updates,
    )
    sc = _scenario("be", follow_full_chain=True, sl_to_be_after_tp2=True)

    ready = ScenarioApplier.apply(chain, sc)

    assert ready.effective_sl_price == 90000.0  # = entry_prices[0]


def test_sl_to_be_after_tp2_not_activated_without_tp2() -> None:
    """sl_to_be_after_tp2=True but only TP1 hit → SL unchanged."""
    updates = [
        _make_update(intents=["U_TP_HIT"], tp_hit_number=1),
    ]
    chain = _make_chain(sl_price=85000.0, updates=updates)
    sc = _scenario("be", follow_full_chain=True, sl_to_be_after_tp2=True)

    ready = ScenarioApplier.apply(chain, sc)

    assert ready.effective_sl_price == 85000.0


def test_vary_entry_pct_redistribution() -> None:
    """vary_entry_pct=0.5 + 3 entries → {"E1": 0.5, "E2": 0.25, "E3": 0.25}."""
    chain = _make_chain(entry_prices=[90000.0, 88000.0, 86000.0])
    sc = _scenario("avg", vary_entry_pct=0.5)

    ready = ScenarioApplier.apply(chain, sc)

    split = ready.effective_entry_split
    assert split is not None
    assert split["E1"] == pytest.approx(0.5)
    assert split["E2"] == pytest.approx(0.25)
    assert split["E3"] == pytest.approx(0.25)


def test_vary_entry_pct_single_entry_is_full() -> None:
    """vary_entry_pct with only 1 entry → E1=1.0 regardless."""
    chain = _make_chain(entry_prices=[90000.0])
    sc = _scenario("avg", vary_entry_pct=0.7)

    ready = ScenarioApplier.apply(chain, sc)

    assert ready.effective_entry_split == {"E1": 1.0}


def test_risk_pct_variant_recalculates_sizing() -> None:
    """risk_pct_variant=2.0 → position_size_usdt recalculated from risk."""
    # entry=90000, sl=85000 → sl_distance=(90000-85000)/90000 ≈ 5.55%
    # risk_budget = 1000 * 2.0 / 100 = 20 USDT
    # position_size = 20 / (0.0556 * 1) ≈ 360 USDT
    chain = _make_chain(
        entry_prices=[90000.0],
        sl_price=85000.0,
        position_size_usdt=100.0,  # original (will be overridden)
    )
    sc = _scenario("dr", risk_pct_variant=2.0)

    ready = ScenarioApplier.apply(chain, sc, capital_base_usdt=1000.0, leverage=1)

    assert ready.effective_risk_pct == 2.0
    # position_size should be much larger than original 100 USDT
    assert ready.effective_position_size_usdt is not None
    assert ready.effective_position_size_usdt > 100.0
    # rough sanity: risk_budget=20, sl_dist≈5.56% → position≈360
    assert ready.effective_position_size_usdt == pytest.approx(
        20.0 / (5000.0 / 90000.0), rel=0.01
    )


def test_gate_mode_warn_includes_blocked() -> None:
    """gate_mode_variant=warn → include_blocked=True for a blocked chain."""
    chain = _make_chain(is_blocked=True)
    sc = _scenario("gw", gate_mode_variant="warn")

    ready = ScenarioApplier.apply(chain, sc)

    assert ready.include_blocked is True


def test_gate_mode_default_does_not_include_blocked() -> None:
    """Without gate_mode_variant=warn, blocked chains have include_blocked=False."""
    chain = _make_chain(is_blocked=True)
    sc = _scenario("full")

    ready = ScenarioApplier.apply(chain, sc)

    assert ready.include_blocked is False


def test_apply_all_returns_correct_count() -> None:
    """apply_all filters blocked chains unless gate_mode=warn."""
    chains = [
        _make_chain(),                          # normal
        _make_chain(is_blocked=True),           # blocked — should be skipped
        _make_chain(),                          # normal
    ]

    sc_default = _scenario("full")
    result_default = ScenarioApplier.apply_all(chains, sc_default)
    assert len(result_default) == 2

    sc_warn = _scenario("gw", gate_mode_variant="warn")
    result_warn = ScenarioApplier.apply_all(chains, sc_warn)
    assert len(result_warn) == 3
    blocked_ones = [r for r in result_warn if r.include_blocked]
    assert len(blocked_ones) == 1
