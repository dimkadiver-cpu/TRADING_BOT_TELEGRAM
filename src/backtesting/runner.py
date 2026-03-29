"""Backtest Runner — orchestrates the full backtesting pipeline.

For each scenario in a backtest_scenarios.yaml config file:
  1. Build signal chains from DB (SignalChainBuilder)
  2. Apply scenario conditions (ScenarioApplier)
  3. Write signal_chains.json sidecar
  4. Generate a minimal freqtrade_config.json
  5. Run freqtrade backtesting subprocess
  6. Persist run metadata and trade results (BacktestRunStore / BacktestTradeStore)

Usage (programmatic):
    runner = BacktestRunner(db_path="db/backtest.sqlite3")
    results = await runner.run("config/backtest_scenarios.yaml")

Usage (CLI):
    python -m src.backtesting.run_backtest --scenario-config config/backtest_scenarios.yaml
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from src.backtesting.chain_builder import SignalChainBuilder
from src.backtesting.models import BacktestReadyChain
from src.backtesting.scenario import BacktestScenario, BacktestSettings, ScenarioLoader
from src.backtesting.storage import BacktestRunStore, BacktestTradeStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class BacktestRunResult(BaseModel):
    """Summary of a single scenario run returned by BacktestRunner.run()."""

    run_id: int
    scenario_name: str
    status: Literal["COMPLETED", "FAILED"]
    error: str | None = None
    output_dir: str
    chains_count: int
    trades_count: int
    freqtrade_results_path: str | None = None


# ---------------------------------------------------------------------------
# Pair normalisation (local — no import from execution layer)
# ---------------------------------------------------------------------------

def _normalize_pair(symbol: str) -> str:
    """Convert canonical symbol to freqtrade perpetual futures pair format.

    Examples:
        BTCUSDT  → BTC/USDT:USDT
        SOLUSDT  → SOL/USDT:USDT
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


def _collect_pairs(chains: list[BacktestReadyChain]) -> list[str]:
    """Return a sorted, unique list of freqtrade pairs from the given chains."""
    seen: set[str] = set()
    pairs: list[str] = []
    for chain in chains:
        pair = _normalize_pair(chain.chain.symbol)
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return sorted(pairs)


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

def _generate_freqtrade_config(
    *,
    chains: list[BacktestReadyChain],
    signal_chains_path: str,
    results_path: str,
    settings: BacktestSettings,
) -> dict[str, Any]:
    """Return a minimal freqtrade backtesting config dict."""
    pairs = _collect_pairs(chains)
    # Resolve absolute datadir so freqtrade can find OHLCV files regardless of cwd
    _project_root = Path(__file__).resolve().parents[2]
    _datadir = str(_project_root / "freqtrade" / "user_data" / "data" / "bybit")
    _userdir = str(_project_root / "freqtrade" / "user_data")
    return {
        "exchange": {"name": settings.exchange},
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "stake_currency": "USDT",
        "dry_run": True,
        "datadir": _datadir,
        "user_data_dir": _userdir,
        "strategy_params": {
            "signal_chains_path": signal_chains_path,
        },
        "max_open_trades": settings.max_open_trades,
        "stake_amount": "unlimited",
        "pairs": pairs,
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {"price_side": "other", "ask_last_balance": 0.0},
        "exit_pricing": {"price_side": "other", "bid_ask_gap": 0.0},
        "export": "trades",
        "exportfilename": results_path,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class BacktestRunner:
    """Orchestrates the backtesting pipeline for all scenarios in a config file."""

    def __init__(self, db_path: str, output_base: str = "backtest_reports") -> None:
        self._db_path = db_path
        self._output_base = Path(output_base)

    async def run(self, scenario_config_path: str) -> list[BacktestRunResult]:
        """Run all scenarios defined in *scenario_config_path*.

        Returns a list of BacktestRunResult (one per scenario).
        """
        config = ScenarioLoader.load(scenario_config_path)
        settings: BacktestSettings = config.backtest_settings
        results: list[BacktestRunResult] = []

        # Build chains once — shared across all scenarios
        logger.info("Building signal chains from %s …", self._db_path)
        all_chains = await SignalChainBuilder.build_all_async(
            db_path=self._db_path,
            trader_id=settings.trader_filter,
            date_from=settings.date_from,
            date_to=settings.date_to,
        )
        logger.info("Loaded %d signal chains", len(all_chains))

        for scenario in config.scenarios:
            result = await self._run_scenario(
                scenario=scenario,
                all_chains=all_chains,
                settings=settings,
            )
            results.append(result)

        return results

    async def _run_scenario(
        self,
        *,
        scenario: BacktestScenario,
        all_chains: Any,
        settings: BacktestSettings,
    ) -> BacktestRunResult:
        """Execute a single scenario and return its result."""
        from src.backtesting.scenario import ScenarioApplier

        logger.info("Running scenario: %s", scenario.name)

        # ── Apply scenario to chains ─────────────────────────────────────────
        ready_chains = ScenarioApplier.apply_all(
            all_chains,
            scenario,
            capital_base_usdt=settings.capital_base_usdt,
        )
        logger.info(
            "Scenario %s: %d chains after filtering",
            scenario.name,
            len(ready_chains),
        )

        # ── Create output directory ──────────────────────────────────────────
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        output_dir = self._output_base / f"run_{scenario.name}_{ts}"
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Write signal_chains.json ─────────────────────────────────────────
        chains_path = output_dir / "signal_chains.json"
        chains_path.write_text(
            json.dumps(
                [c.model_dump(mode="json") for c in ready_chains],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Wrote %d chains to %s", len(ready_chains), chains_path)

        # ── Write freqtrade_config.json ──────────────────────────────────────
        results_json_path = str((output_dir / "freqtrade_results.json").resolve())
        config_path = output_dir / "freqtrade_config.json"
        ft_config = _generate_freqtrade_config(
            chains=ready_chains,
            signal_chains_path=str(chains_path.resolve()),
            results_path=results_json_path,
            settings=settings,
        )
        config_path.write_text(
            json.dumps(ft_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ── Count blocked chains ─────────────────────────────────────────────
        chains_blocked = sum(1 for c in ready_chains if c.chain.new_signal.is_blocked)

        # ── Persist run record ───────────────────────────────────────────────
        run_store = BacktestRunStore(self._db_path)
        run_id = await run_store.insert_run(
            scenario_name=scenario.name,
            scenario_conditions_json=scenario.conditions.model_dump_json(),
            trader_filter=settings.trader_filter,
            date_from=settings.date_from,
            date_to=settings.date_to,
            chains_count=len(ready_chains),
            chains_blocked=chains_blocked,
            output_dir=str(output_dir.resolve()),
            status="RUNNING",
        )

        # ── Call freqtrade subprocess ────────────────────────────────────────
        error: str | None = None
        trades_count = 0
        freqtrade_results_path: str | None = None

        try:
            returncode, stdout, stderr = self._run_freqtrade(
                config_path=str(config_path.resolve()),
                timeframe=settings.timeframe,
            )
            if returncode != 0:
                error = f"freqtrade exited with code {returncode}: {stderr[:500]}"
                logger.error("Scenario %s: %s", scenario.name, error)
                await run_store.update_status(run_id, "FAILED", error=error)
                return BacktestRunResult(
                    run_id=run_id,
                    scenario_name=scenario.name,
                    status="FAILED",
                    error=error,
                    output_dir=str(output_dir.resolve()),
                    chains_count=len(ready_chains),
                    trades_count=0,
                    freqtrade_results_path=None,
                )
            logger.info("Scenario %s: freqtrade finished OK", scenario.name)

            # ── Import trade results ─────────────────────────────────────────
            results_file = self._find_results_file(output_dir, results_json_path)
            if results_file is not None:
                freqtrade_results_path = str(results_file)
                trade_store = BacktestTradeStore(self._db_path)
                trades_count = await trade_store.import_from_freqtrade_json(
                    run_id, freqtrade_results_path
                )
                logger.info("Scenario %s: imported %d trades", scenario.name, trades_count)
            else:
                logger.warning("Scenario %s: no results file found at %s", scenario.name, results_json_path)

        except Exception as exc:
            error = str(exc)
            logger.exception("Scenario %s: unexpected error", scenario.name)
            await run_store.update_status(run_id, "FAILED", error=error)
            return BacktestRunResult(
                run_id=run_id,
                scenario_name=scenario.name,
                status="FAILED",
                error=error,
                output_dir=str(output_dir.resolve()),
                chains_count=len(ready_chains),
                trades_count=0,
                freqtrade_results_path=None,
            )

        await run_store.update_status(run_id, "COMPLETED")
        return BacktestRunResult(
            run_id=run_id,
            scenario_name=scenario.name,
            status="COMPLETED",
            output_dir=str(output_dir.resolve()),
            chains_count=len(ready_chains),
            trades_count=trades_count,
            freqtrade_results_path=freqtrade_results_path,
        )

    # ── Subprocess helper ────────────────────────────────────────────────────

    def _run_freqtrade(
        self,
        *,
        config_path: str,
        timeframe: str,
    ) -> tuple[int, str, str]:
        """Run the freqtrade backtesting subprocess.

        Returns (returncode, stdout, stderr).
        On Windows, uses `python -m freqtrade`; on other platforms uses `freqtrade`.
        """
        if sys.platform == "win32":
            # Prefer the dedicated freqtrade venv executable when present
            _ft_exe = Path(__file__).resolve().parents[2] / ".venv-freqtrade" / "Scripts" / "freqtrade.exe"
            if _ft_exe.exists():
                base_cmd = [str(_ft_exe)]
            else:
                base_cmd = ["python", "-m", "freqtrade"]
        else:
            base_cmd = ["freqtrade"]

        cmd = base_cmd + [
            "backtesting",
            "--config", config_path,
            "--strategy", "SignalBridgeBacktestStrategy",
            "--timeframe", timeframe,
            "--export", "trades",
        ]

        logger.info("Running: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return proc.returncode, proc.stdout, proc.stderr

    @staticmethod
    def _find_results_file(output_dir: Path, expected_path: str) -> Path | None:
        """Locate the freqtrade results JSON.

        Checks the expected path first; if not present, looks for any
        .json file in output_dir that isn't the config or signal_chains files.
        """
        expected = Path(expected_path)
        if expected.exists():
            return expected
        # Fallback: newest .json in output_dir (excluding config / chains files)
        skip = {"freqtrade_config.json", "signal_chains.json"}
        candidates = sorted(
            (f for f in output_dir.glob("*.json") if f.name not in skip),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None
