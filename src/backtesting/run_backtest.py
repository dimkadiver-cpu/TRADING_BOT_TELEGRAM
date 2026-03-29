"""CLI entry point for the backtesting pipeline.

Usage:
    python -m src.backtesting.run_backtest \\
        --scenario-config config/backtest_scenarios.yaml \\
        --db-path db/backtest.sqlite3 \\
        --output backtest_reports/

Optional trader filter override (applied on top of scenario settings):
    python -m src.backtesting.run_backtest \\
        --scenario-config config/backtest_scenarios.yaml \\
        --db-path db/backtest.sqlite3 \\
        --trader trader_3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.backtesting.runner import BacktestRunResult, BacktestRunner


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run TeleSignalBot backtesting pipeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--scenario-config",
        required=True,
        metavar="PATH",
        help="Path to backtest_scenarios.yaml",
    )
    parser.add_argument(
        "--db-path",
        default="db/backtest.sqlite3",
        metavar="PATH",
        help="Path to the backtest SQLite database",
    )
    parser.add_argument(
        "--trader",
        default=None,
        metavar="TRADER_ID",
        help="Filter chains to a single trader (overrides scenario settings)",
    )
    parser.add_argument(
        "--output",
        default="backtest_reports",
        metavar="DIR",
        help="Base directory for per-run output folders",
    )
    return parser.parse_args(argv)


def _print_results_table(results: list[BacktestRunResult]) -> None:
    """Print a compact summary table to stdout."""
    col_w = [30, 10, 7, 7, 8]
    headers = ["Scenario", "Status", "Chains", "Trades", "Run ID"]
    sep = "  ".join("-" * w for w in col_w)

    def _row(cells: list[str]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, col_w))

    print()
    print(_row(headers))
    print(sep)
    for r in results:
        row = [
            r.scenario_name[:col_w[0]],
            r.status,
            str(r.chains_count),
            str(r.trades_count),
            str(r.run_id),
        ]
        print(_row(row))
        if r.error:
            print(f"  ERROR: {r.error[:120]}")
    print()

    failed = sum(1 for r in results if r.status == "FAILED")
    total = len(results)
    print(f"Completed {total} scenario(s), {failed} failed.")
    print()


async def _main(args: argparse.Namespace) -> int:
    """Run the pipeline and return an exit code (0=ok, 1=any failure)."""
    db_path = str(Path(args.db_path).resolve())
    output_base = str(Path(args.output).resolve())

    runner = BacktestRunner(db_path=db_path, output_base=output_base)

    # If --trader given, patch the scenario loader to inject the filter.
    # We do this by monkey-patching the loaded config *after* loading it,
    # via a thin wrapper around BacktestRunner.run().
    if args.trader:
        results = await _run_with_trader_override(
            runner, args.scenario_config, args.trader
        )
    else:
        results = await runner.run(args.scenario_config)

    _print_results_table(results)
    return 1 if any(r.status == "FAILED" for r in results) else 0


async def _run_with_trader_override(
    runner: BacktestRunner,
    scenario_config_path: str,
    trader_id: str,
) -> list[BacktestRunResult]:
    """Load config, override trader_filter, then run each scenario manually."""
    from src.backtesting.scenario import ScenarioLoader

    config = ScenarioLoader.load(scenario_config_path)
    # Override trader filter in-place on the settings object.
    config.backtest_settings.trader_filter = trader_id

    from src.backtesting.chain_builder import SignalChainBuilder

    settings = config.backtest_settings
    all_chains = await SignalChainBuilder.build_all_async(
        db_path=runner._db_path,
        trader_id=settings.trader_filter,
        date_from=settings.date_from,
        date_to=settings.date_to,
    )

    results: list[BacktestRunResult] = []
    for scenario in config.scenarios:
        result = await runner._run_scenario(
            scenario=scenario,
            all_chains=all_chains,
            settings=settings,
        )
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    exit_code = asyncio.run(_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
