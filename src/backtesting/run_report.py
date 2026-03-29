"""CLI for regenerating backtest reports from existing DB runs.

Usage:
    # Regenerate reports for all COMPLETED runs
    python -m src.backtesting.run_report \\
        --db-path db/backtest.sqlite3 \\
        --output backtest_reports/latest/

    # Regenerate for specific run IDs only
    python -m src.backtesting.run_report \\
        --db-path db/backtest.sqlite3 \\
        --run-ids 1 3 5 \\
        --output backtest_reports/custom/
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.backtesting.report import ReportGenerator
from src.backtesting.runner import BacktestRunResult
from src.backtesting.storage import BacktestRunStore, BacktestTradeStore


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate backtest reports from an existing DB.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        default="db/backtest.sqlite3",
        metavar="PATH",
        help="Path to the backtest SQLite database",
    )
    parser.add_argument(
        "--run-ids",
        nargs="*",
        type=int,
        default=None,
        metavar="ID",
        help="Specific run IDs to include (default: all COMPLETED)",
    )
    parser.add_argument(
        "--output",
        default="backtest_reports/report",
        metavar="DIR",
        help="Output directory for generated report files",
    )
    return parser.parse_args(argv)


async def _load_run_results(
    db_path: str,
    run_ids: list[int] | None,
) -> list[BacktestRunResult]:
    """Load COMPLETED BacktestRunResult objects from the DB."""
    run_store   = BacktestRunStore(db_path)
    trade_store = BacktestTradeStore(db_path)

    if run_ids:
        raw_runs = [await run_store.get_run(rid) for rid in run_ids]
        raw_runs = [r for r in raw_runs if r is not None]
    else:
        raw_runs = await run_store.get_all_runs()

    results: list[BacktestRunResult] = []
    for run in raw_runs:
        if run.get("status") != "COMPLETED":
            continue

        trades     = await trade_store.get_trades_by_run(run["run_id"])
        output_dir = Path(run.get("output_dir", ""))

        # Locate freqtrade results JSON in the run's output directory
        ft_results: str | None = None
        candidate = output_dir / "freqtrade_results.json"
        if candidate.exists():
            ft_results = str(candidate)

        results.append(
            BacktestRunResult(
                run_id=run["run_id"],
                scenario_name=run["scenario_name"],
                status="COMPLETED",
                output_dir=str(output_dir),
                chains_count=run.get("chains_count") or 0,
                trades_count=len(trades),
                freqtrade_results_path=ft_results,
            )
        )
    return results


async def _main(args: argparse.Namespace) -> int:
    db_path    = str(Path(args.db_path).resolve())
    output_dir = str(Path(args.output).resolve())

    print(f"Loading runs from {db_path} …")
    run_results = await _load_run_results(db_path, args.run_ids)

    if not run_results:
        print("No COMPLETED runs found — nothing to report.")
        return 0

    print(f"Found {len(run_results)} completed run(s): {[r.scenario_name for r in run_results]}")

    generator = ReportGenerator(db_path=db_path)
    report    = await generator.generate(run_results, output_dir=output_dir)

    print(f"\nReport written to: {output_dir}")
    print(f"  summary.json            — {len(report.scenarios)} scenario(s)")
    print(f"  comparison_table.csv    — {sum(m.total_trades for m in report.scenarios)} total trades")
    print(f"  comparison_table.html")
    print(f"  comparison_table_monthly.csv — {len(report.monthly)} month-rows")
    print(f"  per_scenario/<name>/trades.csv + equity_curve.csv")
    print(f"  parser_quality/signal_coverage.csv + update_chain_stats.csv")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    exit_code = asyncio.run(_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
