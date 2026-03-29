"""Scenario Condition System for Fase 7 backtesting.

Provides:
  - ScenarioConditions  — flag model per scenario
  - BacktestScenario    — nome + descrizione + condizioni
  - BacktestSettings    — impostazioni globali del run
  - ScenarioConfig      — lista scenari + settings (da YAML)
  - ScenarioLoader      — carica e valida backtest_scenarios.yaml
  - ScenarioApplier     — applica uno scenario a una SignalChain → BacktestReadyChain

Usage:
    config = ScenarioLoader.load("config/backtest_scenarios.yaml")
    ready = ScenarioApplier.apply(chain, config.scenarios[0])
"""

from __future__ import annotations

import logging
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from src.backtesting.models import BacktestReadyChain, ChainedMessage, SignalChain
from src.operation_rules.risk_calculator import compute_position_size_from_risk
from src.parser.models.update import UpdateEntities

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScenarioConditions(BaseModel):
    """Flag set that defines the behaviour of a single backtest scenario."""

    follow_full_chain: bool = True
    """Apply all UPDATE messages in chronological order."""

    signals_only: bool = False
    """Ignore all UPDATE messages; exit via original SL + TP prices only."""

    sl_to_be_after_tp2: bool = False
    """After U_TP_HIT with tp_hit_number >= 2, move effective_sl_price to breakeven
    (entry_prices[0])."""

    vary_entry_pct: float | None = None
    """Redistribute entry_split so that E1 receives this fraction and the rest is
    split equally among the remaining entries.
    E.g. vary_entry_pct=0.5 + 3 entries → {"E1": 0.5, "E2": 0.25, "E3": 0.25}."""

    risk_pct_variant: float | None = None
    """Override risk_pct_of_capital and recompute position_size_usdt via
    risk_calculator.compute_position_size_from_risk()."""

    gate_mode_variant: Literal["warn"] | None = None
    """When "warn", include blocked chains in the output (sets include_blocked=True)."""

    @model_validator(mode="after")
    def _check_mutual_exclusion(self) -> ScenarioConditions:
        if self.signals_only and self.follow_full_chain:
            raise ValueError(
                "signals_only and follow_full_chain are mutually exclusive"
            )
        return self


class BacktestScenario(BaseModel):
    """A named scenario with a description and its condition flags."""

    name: str
    description: str
    conditions: ScenarioConditions


class BacktestSettings(BaseModel):
    """Global run settings shared across all scenarios in a config file."""

    trader_filter: str | None = None
    """Filter by trader_id. None = all traders."""

    date_from: str | None = None
    """Inclusive start date, ISO format YYYY-MM-DD."""

    date_to: str | None = None
    """Inclusive end date, ISO format YYYY-MM-DD."""

    ohlcv_source: str = "bybit_api"
    """OHLCV data source: "bybit_api" | "local_files"."""

    timeframe: str = "5m"
    capital_base_usdt: float = 1000.0
    exchange: str = "bybit"
    max_open_trades: int = 10


class ScenarioConfig(BaseModel):
    """Parsed representation of backtest_scenarios.yaml."""

    scenarios: list[BacktestScenario]
    backtest_settings: BacktestSettings


# ---------------------------------------------------------------------------
# ScenarioLoader
# ---------------------------------------------------------------------------

class ScenarioLoader:
    """Loads and validates a backtest_scenarios.yaml file."""

    @staticmethod
    def load(path: str) -> ScenarioConfig:
        """Read *path* as YAML and validate against ScenarioConfig.

        Args:
            path: Filesystem path to the YAML file.

        Returns:
            A validated ScenarioConfig instance.

        Raises:
            FileNotFoundError: If *path* does not exist.
            yaml.YAMLError: If the file is not valid YAML.
            pydantic.ValidationError: If the structure doesn't match the schema.
        """
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return ScenarioConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _redistribute_entry_split(
    n_entries: int,
    vary_entry_pct: float,
) -> dict[str, float]:
    """Compute a new entry_split with E1 getting vary_entry_pct and the
    remaining entries sharing the rest equally.

    Edge cases:
    - n_entries == 0 → empty dict
    - n_entries == 1 → {"E1": 1.0} (ignore vary_entry_pct)
    """
    if n_entries == 0:
        return {}
    if n_entries == 1:
        return {"E1": 1.0}

    remaining_per_entry = (1.0 - vary_entry_pct) / (n_entries - 1)
    split = {f"E{i + 1}": remaining_per_entry for i in range(n_entries)}
    split["E1"] = vary_entry_pct
    return split


def _find_tp2_hit(updates: list[ChainedMessage]) -> bool:
    """Return True if any update contains U_TP_HIT with tp_hit_number >= 2."""
    for upd in updates:
        if "U_TP_HIT" not in upd.intents:
            continue
        if isinstance(upd.entities, UpdateEntities):
            tp_num = upd.entities.tp_hit_number
            if tp_num is not None and tp_num >= 2:
                return True
    return False


# ---------------------------------------------------------------------------
# ScenarioApplier
# ---------------------------------------------------------------------------

class ScenarioApplier:
    """Applies a BacktestScenario to a SignalChain to produce a BacktestReadyChain."""

    @classmethod
    def apply(
        cls,
        chain: SignalChain,
        scenario: BacktestScenario,
        *,
        capital_base_usdt: float = 1000.0,
        leverage: int = 1,
    ) -> BacktestReadyChain:
        """Apply *scenario* conditions to *chain*.

        Args:
            chain:             The raw signal chain from SignalChainBuilder.
            scenario:          The scenario to apply.
            capital_base_usdt: Capital base used for risk_pct_variant recalculation.
                               Caller should pass this from BacktestSettings.
            leverage:          Leverage assumed for position sizing. Defaults to 1.

        Returns:
            A BacktestReadyChain ready for the BacktestRunner.
        """
        cond = scenario.conditions

        # ── Step 1: determine which updates are applied ───────────────────────
        if cond.signals_only:
            applied_updates: list[ChainedMessage] = []
        else:
            # follow_full_chain (or neither flag set): include all updates
            applied_updates = list(chain.updates)

        # ── Step 2: baseline effective values (copy from chain) ───────────────
        effective_sl_price = chain.sl_price
        effective_tp_prices = list(chain.tp_prices)
        effective_entry_prices = list(chain.entry_prices)
        effective_entry_split: dict[str, float] | None = chain.new_signal.entry_split
        effective_position_size_usdt = chain.new_signal.position_size_usdt
        effective_risk_pct: float | None = None

        # ── Step 3: sl_to_be_after_tp2 ───────────────────────────────────────
        if cond.sl_to_be_after_tp2 and applied_updates:
            if _find_tp2_hit(applied_updates):
                if effective_entry_prices:
                    effective_sl_price = effective_entry_prices[0]
                    logger.debug(
                        "chain=%s sl_to_be_after_tp2: SL moved to breakeven %.6f",
                        chain.chain_id,
                        effective_sl_price,
                    )

        # ── Step 4: vary_entry_pct ────────────────────────────────────────────
        if cond.vary_entry_pct is not None:
            n = len(effective_entry_prices)
            effective_entry_split = _redistribute_entry_split(n, cond.vary_entry_pct)

        # ── Step 5: risk_pct_variant ──────────────────────────────────────────
        if cond.risk_pct_variant is not None:
            effective_risk_pct = cond.risk_pct_variant
            risk_budget_usdt = capital_base_usdt * cond.risk_pct_variant / 100.0

            if effective_entry_prices and effective_sl_price > 0:
                try:
                    position_size_usdt, _, _ = compute_position_size_from_risk(
                        entry_prices=effective_entry_prices,
                        sl_price=effective_sl_price,
                        risk_budget_usdt=risk_budget_usdt,
                        leverage=leverage,
                        capital_base_usdt=capital_base_usdt,
                    )
                    effective_position_size_usdt = position_size_usdt
                except ValueError as exc:
                    logger.warning(
                        "chain=%s risk_pct_variant: could not compute position size: %s",
                        chain.chain_id,
                        exc,
                    )
            else:
                logger.warning(
                    "chain=%s risk_pct_variant: no entry_prices or sl_price=0, skipping recalc",
                    chain.chain_id,
                )

        # ── Step 6: gate_mode_variant ─────────────────────────────────────────
        include_blocked = (
            cond.gate_mode_variant == "warn" and chain.new_signal.is_blocked
        )

        return BacktestReadyChain(
            chain=chain,
            scenario_name=scenario.name,
            applied_updates=applied_updates,
            effective_sl_price=effective_sl_price,
            effective_tp_prices=effective_tp_prices,
            effective_entry_prices=effective_entry_prices,
            effective_entry_split=effective_entry_split,
            effective_position_size_usdt=effective_position_size_usdt,
            effective_risk_pct=effective_risk_pct,
            include_blocked=include_blocked,
        )

    @classmethod
    def apply_all(
        cls,
        chains: list[SignalChain],
        scenario: BacktestScenario,
        *,
        capital_base_usdt: float = 1000.0,
        leverage: int = 1,
    ) -> list[BacktestReadyChain]:
        """Apply *scenario* to every chain in *chains*.

        Blocked chains are skipped unless gate_mode_variant="warn", in which
        case they are included with include_blocked=True.

        Args:
            chains:            All chains from SignalChainBuilder.
            scenario:          The scenario to apply.
            capital_base_usdt: Passed through to apply().
            leverage:          Passed through to apply().

        Returns:
            List of BacktestReadyChain, one per included chain.
        """
        result: list[BacktestReadyChain] = []
        gate_warn = scenario.conditions.gate_mode_variant == "warn"

        for chain in chains:
            if chain.new_signal.is_blocked and not gate_warn:
                logger.debug(
                    "chain=%s skipped: is_blocked=True and gate_mode != warn",
                    chain.chain_id,
                )
                continue

            ready = cls.apply(
                chain,
                scenario,
                capital_base_usdt=capital_base_usdt,
                leverage=leverage,
            )
            result.append(ready)

        return result
