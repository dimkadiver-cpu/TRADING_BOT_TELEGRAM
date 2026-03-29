"""Backtest Report Generator — Fase 7 Step 22.

Generates comparative metrics, CSV tables, HTML reports, and per-scenario
trade/equity-curve files from completed BacktestRunResult objects.

Usage:
    from src.backtesting.report import ReportGenerator
    generator = ReportGenerator(db_path="db/backtest.sqlite3")
    report = await generator.generate(run_results, output_dir="backtest_reports/run_001")
"""

from __future__ import annotations

import csv
import json
import logging
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.backtesting.runner import BacktestRunResult
from src.backtesting.storage import BacktestRunStore, BacktestTradeStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScenarioMetrics(BaseModel):
    """Aggregated performance metrics for a single scenario run."""

    scenario_name: str
    total_trades: int
    win_rate_pct: float
    total_profit_pct: float
    max_drawdown_pct: float
    profit_factor: float
    sharpe_ratio: float
    avg_trade_duration_hours: float
    sl_moved_to_be_count: int
    chains_blocked_count: int


class MonthlyMetrics(BaseModel):
    """Per-month breakdown of a scenario's performance."""

    scenario_name: str
    month: str          # YYYY-MM
    total_trades: int
    win_rate_pct: float
    total_profit_pct: float
    max_drawdown_pct: float


class BacktestSummaryReport(BaseModel):
    """Top-level summary produced by ReportGenerator.generate()."""

    run_ts: str
    scenarios: list[ScenarioMetrics]
    monthly: list[MonthlyMetrics]


# ---------------------------------------------------------------------------
# Pure calculation helpers (exposed for testing)
# ---------------------------------------------------------------------------

def _compute_max_drawdown(profits_pct: list[float]) -> float:
    """Maximum drawdown (as positive pct) on a cumulative equity curve."""
    if not profits_pct:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in profits_pct:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_sharpe(trades: list[dict[str, Any]]) -> float:
    """Approximate annualised Sharpe ratio (risk-free rate = 0).

    Groups profit_pct by close_date calendar day, then computes
    mean / std * sqrt(365).  Returns 0.0 when there are fewer than 2 days.
    """
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        date_str = (t.get("close_date") or t.get("open_date") or "")[:10]
        if date_str:
            daily[date_str] += t.get("profit_pct") or 0.0

    returns = list(daily.values())
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = variance ** 0.5
    if std == 0.0:
        return 0.0
    return mean / std * (365.0 ** 0.5)


def _compute_scenario_metrics(
    scenario_name: str,
    trades: list[dict[str, Any]],
    chains_blocked: int = 0,
) -> ScenarioMetrics:
    """Compute ScenarioMetrics from a flat list of trade dicts."""
    if not trades:
        return ScenarioMetrics(
            scenario_name=scenario_name,
            total_trades=0,
            win_rate_pct=0.0,
            total_profit_pct=0.0,
            max_drawdown_pct=0.0,
            profit_factor=0.0,
            sharpe_ratio=0.0,
            avg_trade_duration_hours=0.0,
            sl_moved_to_be_count=0,
            chains_blocked_count=chains_blocked,
        )

    profits = [t.get("profit_pct") or 0.0 for t in trades]
    wins  = [p for p in profits if p > 0.0]
    losses = [p for p in profits if p < 0.0]

    win_rate = round(len(wins) / len(trades) * 100.0, 2)
    total_profit = round(sum(profits), 4)
    max_dd = round(_compute_max_drawdown(profits), 4)

    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    if sum_losses == 0.0:
        # No losing trades: profit factor is theoretically infinite; cap at 999
        profit_factor = 999.0 if sum_wins > 0.0 else 0.0
    else:
        profit_factor = round(sum_wins / sum_losses, 4)

    sharpe = round(_compute_sharpe(trades), 4)

    durations_sec = [
        t["duration_seconds"]
        for t in trades
        if t.get("duration_seconds") is not None
    ]
    avg_dur_h = round(
        (sum(durations_sec) / len(durations_sec) / 3600.0) if durations_sec else 0.0,
        2,
    )

    sl_moved = sum(1 for t in trades if t.get("sl_moved_to_be"))

    return ScenarioMetrics(
        scenario_name=scenario_name,
        total_trades=len(trades),
        win_rate_pct=win_rate,
        total_profit_pct=total_profit,
        max_drawdown_pct=max_dd,
        profit_factor=profit_factor,
        sharpe_ratio=sharpe,
        avg_trade_duration_hours=avg_dur_h,
        sl_moved_to_be_count=sl_moved,
        chains_blocked_count=chains_blocked,
    )


def _compute_monthly_metrics(
    scenario_name: str,
    trades: list[dict[str, Any]],
) -> list[MonthlyMetrics]:
    """Group trades by YYYY-MM of open_date and compute per-month metrics."""
    monthly: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        month = (t.get("open_date") or "")[:7]   # YYYY-MM
        if month:
            monthly[month].append(t)

    result: list[MonthlyMetrics] = []
    for month in sorted(monthly):
        m_trades = monthly[month]
        profits = [t.get("profit_pct") or 0.0 for t in m_trades]
        wins = [p for p in profits if p > 0.0]
        win_rate = round(len(wins) / len(m_trades) * 100.0, 2) if m_trades else 0.0
        result.append(
            MonthlyMetrics(
                scenario_name=scenario_name,
                month=month,
                total_trades=len(m_trades),
                win_rate_pct=win_rate,
                total_profit_pct=round(sum(profits), 4),
                max_drawdown_pct=round(_compute_max_drawdown(profits), 4),
            )
        )
    return result


# ---------------------------------------------------------------------------
# CSV / HTML writers (module-level, exposed for testing)
# ---------------------------------------------------------------------------

def _write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    """Write a UTF-8-sig CSV (LibreOffice-compatible)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)


def _write_comparison_csv(metrics: list[ScenarioMetrics], path: Path) -> None:
    headers = [
        "scenario", "trades", "win_rate", "profit_pct", "max_dd",
        "profit_factor", "sharpe", "avg_duration_h", "sl_moved", "blocked",
    ]
    rows = [
        [
            m.scenario_name, m.total_trades, m.win_rate_pct, m.total_profit_pct,
            m.max_drawdown_pct, m.profit_factor, m.sharpe_ratio,
            m.avg_trade_duration_hours, m.sl_moved_to_be_count, m.chains_blocked_count,
        ]
        for m in metrics
    ]
    _write_csv(path, headers, rows)


def _write_comparison_monthly_csv(monthly: list[MonthlyMetrics], path: Path) -> None:
    headers = ["scenario", "month", "trades", "win_rate_pct", "profit_pct", "max_dd_pct"]
    rows = [
        [m.scenario_name, m.month, m.total_trades, m.win_rate_pct,
         m.total_profit_pct, m.max_drawdown_pct]
        for m in monthly
    ]
    _write_csv(path, headers, rows)


def _write_trades_csv(trades: list[dict[str, Any]], path: Path) -> None:
    headers = [
        "chain_id", "trader_id", "pair", "side",
        "open_date", "close_date",
        "entry_price", "close_price", "profit_usdt", "profit_pct",
        "exit_reason", "duration_hours",
    ]
    rows = [
        [
            t.get("chain_id", ""),
            t.get("trader_id", ""),
            t.get("pair", ""),
            t.get("side", ""),
            t.get("open_date", ""),
            t.get("close_date", ""),
            t.get("entry_price", ""),
            t.get("close_price", ""),
            t.get("profit_usdt", ""),
            t.get("profit_pct", ""),
            t.get("exit_reason", ""),
            round((t.get("duration_seconds") or 0) / 3600.0, 2),
        ]
        for t in trades
    ]
    _write_csv(path, headers, rows)


def _write_equity_curve_csv(trades: list[dict[str, Any]], path: Path) -> None:
    sorted_trades = sorted(
        (t for t in trades if t.get("close_date")),
        key=lambda t: t["close_date"],
    )
    cumulative = 0.0
    rows: list[list[Any]] = []
    for t in sorted_trades:
        cumulative += t.get("profit_pct") or 0.0
        rows.append([t["close_date"][:10], round(cumulative, 4)])
    _write_csv(path, ["date", "cumulative_profit_pct"], rows)


def _write_signal_coverage_csv(
    all_trades_by_run: dict[int, list[dict[str, Any]]],
    path: Path,
) -> None:
    """Per-trader: total distinct chains, chains with a close_date, coverage_pct."""
    all_trades = [t for trades in all_trades_by_run.values() for t in trades]

    chains_per_trader: dict[str, set[str]] = defaultdict(set)
    complete_per_trader: dict[str, set[str]] = defaultdict(set)

    for t in all_trades:
        trader_id = t.get("trader_id") or "unknown"
        chain_id = t.get("chain_id") or ""
        chains_per_trader[trader_id].add(chain_id)
        if t.get("close_date"):
            complete_per_trader[trader_id].add(chain_id)

    rows: list[list[Any]] = []
    for trader_id in sorted(chains_per_trader):
        total = len(chains_per_trader[trader_id])
        complete = len(complete_per_trader.get(trader_id, set()))
        coverage = round(complete / total * 100.0, 2) if total else 0.0
        rows.append([trader_id, total, complete, coverage])

    _write_csv(path, ["trader_id", "total_chains", "complete_chains", "coverage_pct"], rows)


def _write_update_chain_stats_csv(
    run_results: list[BacktestRunResult],
    path: Path,
) -> None:
    """Per-trader: avg_updates_per_chain, intent_breakdown from signal_chains.json sidecar."""
    from collections import Counter

    chains_data: list[dict[str, Any]] | None = None
    for run_result in run_results:
        if run_result.status == "COMPLETED":
            chains_file = Path(run_result.output_dir) / "signal_chains.json"
            if chains_file.exists():
                chains_data = json.loads(chains_file.read_text(encoding="utf-8"))
                break

    if not chains_data:
        _write_csv(path, ["trader_id", "avg_updates_per_chain", "intent_breakdown"], [])
        return

    updates_count: dict[str, list[int]] = defaultdict(list)
    intents_count: dict[str, Counter[str]] = defaultdict(Counter)

    for chain_dict in chains_data:
        chain = chain_dict.get("chain", {})
        trader_id = chain.get("trader_id") or "unknown"
        applied_updates = chain_dict.get("applied_updates", [])
        updates_count[trader_id].append(len(applied_updates))
        for upd in applied_updates:
            for intent in upd.get("intents", []):
                intents_count[trader_id][intent] += 1

    rows: list[list[Any]] = []
    for trader_id in sorted(updates_count):
        counts = updates_count[trader_id]
        avg = round(sum(counts) / len(counts), 2) if counts else 0.0
        breakdown = "; ".join(
            f"{k}:{v}"
            for k, v in sorted(intents_count[trader_id].items())
        )
        rows.append([trader_id, avg, breakdown])

    _write_csv(path, ["trader_id", "avg_updates_per_chain", "intent_breakdown"], rows)


def _generate_html_table(metrics: list[ScenarioMetrics]) -> str:
    """Return a minimal inline-CSS HTML comparison table."""
    th_style = (
        "padding:6px 10px; text-align:left; background:#2c3e50; color:#ecf0f1;"
        "border:1px solid #1a252f; white-space:nowrap;"
    )
    td_style = "padding:5px 9px; border:1px solid #bdc3c7;"
    tr_even = "background:#f9f9f9;"
    tr_odd  = "background:#ffffff;"

    columns = [
        ("Scenario",       "scenario_name"),
        ("Trades",         "total_trades"),
        ("Win %",          "win_rate_pct"),
        ("Profit %",       "total_profit_pct"),
        ("Max DD %",       "max_drawdown_pct"),
        ("Profit Factor",  "profit_factor"),
        ("Sharpe",         "sharpe_ratio"),
        ("Avg Dur (h)",    "avg_trade_duration_hours"),
        ("SL→BE",          "sl_moved_to_be_count"),
        ("Blocked",        "chains_blocked_count"),
    ]

    header_html = "".join(f"<th style='{th_style}'>{label}</th>" for label, _ in columns)

    rows_html = ""
    for i, m in enumerate(metrics):
        style = tr_even if i % 2 == 0 else tr_odd
        cells = "".join(
            f"<td style='{td_style}'>{getattr(m, field)}</td>"
            for _, field in columns
        )
        rows_html += f"<tr style='{style}'>{cells}</tr>\n"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        "  <title>Backtest Comparison</title>\n"
        "</head>\n"
        "<body style='font-family:monospace; font-size:13px; padding:16px;'>\n"
        "<h2 style='color:#2c3e50;'>Backtest Scenario Comparison</h2>\n"
        "<table style='border-collapse:collapse; width:100%;'>\n"
        f"  <thead><tr>{header_html}</tr></thead>\n"
        f"  <tbody>\n{rows_html}  </tbody>\n"
        "</table>\n"
        "</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# freqtrade plot-profit (best-effort)
# ---------------------------------------------------------------------------

def _try_plot_profit(run_result: BacktestRunResult) -> None:
    """Call freqtrade plot-profit for a completed run.  Failures are logged, not raised."""
    if not run_result.freqtrade_results_path:
        return
    config_path = Path(run_result.output_dir) / "freqtrade_config.json"
    if not config_path.exists():
        return
    base_cmd = ["python", "-m", "freqtrade"] if sys.platform == "win32" else ["freqtrade"]
    cmd = base_cmd + [
        "plot-profit",
        "--config", str(config_path),
        "--export-filename", run_result.freqtrade_results_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning(
                "plot-profit exited %d for %s: %s",
                result.returncode,
                run_result.scenario_name,
                result.stderr[:200],
            )
    except Exception as exc:
        logger.warning("plot-profit failed for %s: %s", run_result.scenario_name, exc)


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Generates comparative backtest reports from completed run results."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def generate(
        self,
        run_results: list[BacktestRunResult],
        output_dir: str,
    ) -> BacktestSummaryReport:
        """Generate all reports and return the BacktestSummaryReport."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        run_store   = BacktestRunStore(self._db_path)
        trade_store = BacktestTradeStore(self._db_path)

        all_scenario_metrics: list[ScenarioMetrics] = []
        all_monthly_metrics:  list[MonthlyMetrics]  = []
        all_trades_by_run:    dict[int, list[dict[str, Any]]] = {}

        for run_result in run_results:
            if run_result.status != "COMPLETED":
                continue

            # ── Load trades and run metadata ─────────────────────────────
            trades   = await trade_store.get_trades_by_run(run_result.run_id)
            run_meta = await run_store.get_run(run_result.run_id)
            chains_blocked = (run_meta.get("chains_blocked") or 0) if run_meta else 0

            all_trades_by_run[run_result.run_id] = trades

            # ── Scenario-level metrics ────────────────────────────────────
            s_metrics = _compute_scenario_metrics(
                scenario_name=run_result.scenario_name,
                trades=trades,
                chains_blocked=chains_blocked,
            )
            all_scenario_metrics.append(s_metrics)

            # ── Monthly metrics ───────────────────────────────────────────
            monthly = _compute_monthly_metrics(run_result.scenario_name, trades)
            all_monthly_metrics.extend(monthly)

            # ── Per-scenario directory ────────────────────────────────────
            scenario_dir = output_path / "per_scenario" / run_result.scenario_name
            scenario_dir.mkdir(parents=True, exist_ok=True)
            _write_trades_csv(trades, scenario_dir / "trades.csv")
            _write_equity_curve_csv(trades, scenario_dir / "equity_curve.csv")

            logger.info(
                "Scenario %s: %d trades, win_rate=%.1f%%",
                run_result.scenario_name,
                s_metrics.total_trades,
                s_metrics.win_rate_pct,
            )

        # ── Comparison files ─────────────────────────────────────────────
        _write_comparison_csv(all_scenario_metrics, output_path / "comparison_table.csv")
        _write_comparison_monthly_csv(all_monthly_metrics, output_path / "comparison_table_monthly.csv")
        (output_path / "comparison_table.html").write_text(
            _generate_html_table(all_scenario_metrics),
            encoding="utf-8-sig",
        )

        # ── Parser quality ───────────────────────────────────────────────
        parser_quality_dir = output_path / "parser_quality"
        parser_quality_dir.mkdir(parents=True, exist_ok=True)
        _write_signal_coverage_csv(all_trades_by_run, parser_quality_dir / "signal_coverage.csv")
        _write_update_chain_stats_csv(run_results, parser_quality_dir / "update_chain_stats.csv")

        # ── freqtrade plot-profit (best-effort) ──────────────────────────
        for run_result in run_results:
            if run_result.status == "COMPLETED":
                _try_plot_profit(run_result)

        # ── Summary JSON ─────────────────────────────────────────────────
        report = BacktestSummaryReport(
            run_ts=datetime.now(timezone.utc).isoformat(),
            scenarios=all_scenario_metrics,
            monthly=all_monthly_metrics,
        )
        (output_path / "summary.json").write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("Report written to %s", output_path)
        return report
