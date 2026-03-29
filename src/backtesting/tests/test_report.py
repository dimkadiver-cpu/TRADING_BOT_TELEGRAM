"""Tests for src/backtesting/report.py — ScenarioMetrics, MonthlyMetrics, and ReportGenerator."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.backtesting.report import (
    BacktestSummaryReport,
    MonthlyMetrics,
    ReportGenerator,
    ScenarioMetrics,
    _compute_max_drawdown,
    _compute_monthly_metrics,
    _compute_scenario_metrics,
    _compute_sharpe,
    _generate_html_table,
    _write_comparison_csv,
    _write_comparison_monthly_csv,
)
from src.backtesting.runner import BacktestRunResult


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _make_trade(
    *,
    profit_pct: float = 0.05,
    profit_usdt: float = 50.0,
    open_date: str = "2025-03-01 10:00:00",
    close_date: str = "2025-03-02 10:00:00",
    duration_seconds: int = 86400,
    sl_moved_to_be: int = 0,
    chain_id: str = "trader_3:BTC",
    trader_id: str = "trader_3",
    pair: str = "BTC/USDT:USDT",
    side: str = "LONG",
    entry_price: float = 90000.0,
    close_price: float = 95000.0,
    exit_reason: str = "tp1",
) -> dict[str, Any]:
    return {
        "profit_pct": profit_pct,
        "profit_usdt": profit_usdt,
        "open_date": open_date,
        "close_date": close_date,
        "duration_seconds": duration_seconds,
        "sl_moved_to_be": sl_moved_to_be,
        "chain_id": chain_id,
        "trader_id": trader_id,
        "pair": pair,
        "side": side,
        "entry_price": entry_price,
        "close_price": close_price,
        "exit_reason": exit_reason,
    }


def _make_run_result(
    run_id: int = 1,
    scenario_name: str = "baseline",
    output_dir: str = "/tmp/run_01",
    trades_count: int = 5,
    freqtrade_results_path: str | None = None,
) -> BacktestRunResult:
    return BacktestRunResult(
        run_id=run_id,
        scenario_name=scenario_name,
        status="COMPLETED",
        output_dir=output_dir,
        chains_count=10,
        trades_count=trades_count,
        freqtrade_results_path=freqtrade_results_path,
    )


# ---------------------------------------------------------------------------
# test_calculate_scenario_metrics
# ---------------------------------------------------------------------------

class TestCalculateScenarioMetrics:
    def test_basic_metrics(self):
        trades = [
            _make_trade(profit_pct=0.10, duration_seconds=7200),
            _make_trade(profit_pct=-0.05, duration_seconds=3600),
            _make_trade(profit_pct=0.08, duration_seconds=10800, sl_moved_to_be=1),
        ]
        m = _compute_scenario_metrics("test", trades, chains_blocked=2)

        assert m.scenario_name == "test"
        assert m.total_trades == 3
        assert m.win_rate_pct == pytest.approx(66.67, abs=0.01)
        assert m.total_profit_pct == pytest.approx(0.13, abs=1e-4)
        assert m.sl_moved_to_be_count == 1
        assert m.chains_blocked_count == 2

    def test_empty_trades_returns_zeros(self):
        m = _compute_scenario_metrics("empty", [], chains_blocked=5)
        assert m.total_trades == 0
        assert m.win_rate_pct == 0.0
        assert m.profit_factor == 0.0
        assert m.chains_blocked_count == 5

    def test_avg_duration_hours(self):
        trades = [
            _make_trade(duration_seconds=3600),   # 1h
            _make_trade(duration_seconds=7200),   # 2h
        ]
        m = _compute_scenario_metrics("dur", trades)
        assert m.avg_trade_duration_hours == pytest.approx(1.5, abs=0.01)


# ---------------------------------------------------------------------------
# test_win_rate_calculation
# ---------------------------------------------------------------------------

class TestWinRateCalculation:
    def test_all_wins(self):
        trades = [_make_trade(profit_pct=0.05) for _ in range(4)]
        m = _compute_scenario_metrics("s", trades)
        assert m.win_rate_pct == 100.0

    def test_all_losses(self):
        trades = [_make_trade(profit_pct=-0.03) for _ in range(3)]
        m = _compute_scenario_metrics("s", trades)
        assert m.win_rate_pct == 0.0

    def test_mixed(self):
        trades = [
            _make_trade(profit_pct=0.05),
            _make_trade(profit_pct=-0.02),
            _make_trade(profit_pct=0.03),
            _make_trade(profit_pct=-0.01),
        ]
        m = _compute_scenario_metrics("s", trades)
        assert m.win_rate_pct == 50.0


# ---------------------------------------------------------------------------
# test_profit_factor_no_losses
# ---------------------------------------------------------------------------

class TestProfitFactorNoLosses:
    def test_no_losses_caps_at_999(self):
        trades = [
            _make_trade(profit_pct=0.05),
            _make_trade(profit_pct=0.10),
        ]
        m = _compute_scenario_metrics("pf_no_loss", trades)
        assert m.profit_factor == 999.0

    def test_no_wins_no_losses_is_zero(self):
        trades = [_make_trade(profit_pct=0.0)]
        m = _compute_scenario_metrics("pf_zero", trades)
        assert m.profit_factor == 0.0

    def test_with_losses(self):
        trades = [
            _make_trade(profit_pct=0.10),
            _make_trade(profit_pct=-0.05),
        ]
        m = _compute_scenario_metrics("pf_normal", trades)
        # sum_wins=0.10, sum_losses=0.05 → pf = 2.0
        assert m.profit_factor == pytest.approx(2.0, abs=1e-4)


# ---------------------------------------------------------------------------
# test_sharpe_ratio_calculation
# ---------------------------------------------------------------------------

class TestSharpeRatioCalculation:
    def test_single_day_returns_zero(self):
        trades = [
            _make_trade(close_date="2025-03-01 10:00:00", profit_pct=0.05),
            _make_trade(close_date="2025-03-01 12:00:00", profit_pct=0.03),
        ]
        sharpe = _compute_sharpe(trades)
        assert sharpe == 0.0   # only 1 distinct day

    def test_two_days_positive_returns(self):
        trades = [
            _make_trade(close_date="2025-03-01 10:00:00", profit_pct=0.04),
            _make_trade(close_date="2025-03-02 10:00:00", profit_pct=0.06),
        ]
        sharpe = _compute_sharpe(trades)
        assert sharpe > 0.0    # consistent positive daily returns → positive Sharpe

    def test_uniform_returns_have_zero_std(self):
        """Identical daily returns → std=0 → Sharpe=0."""
        trades = [
            _make_trade(close_date=f"2025-03-0{d} 10:00:00", profit_pct=0.05)
            for d in range(1, 5)
        ]
        sharpe = _compute_sharpe(trades)
        assert sharpe == 0.0


# ---------------------------------------------------------------------------
# test_max_drawdown_calculation
# ---------------------------------------------------------------------------

class TestMaxDrawdownCalculation:
    def test_monotone_increase_no_drawdown(self):
        assert _compute_max_drawdown([0.02, 0.03, 0.05]) == pytest.approx(0.0)

    def test_single_loss_equals_loss(self):
        # equity: 0.05, then 0.05-0.10=-0.05 → DD from peak 0.05 is 0.10
        dd = _compute_max_drawdown([0.05, -0.10])
        assert dd == pytest.approx(0.10, abs=1e-6)

    def test_multiple_drawdowns_takes_max(self):
        # equity trace: 0.10, 0.05 (-0.05 dd), 0.15, -0.05 (0.20 dd from peak)
        dd = _compute_max_drawdown([0.10, -0.05, 0.10, -0.20])
        assert dd == pytest.approx(0.20, abs=1e-6)

    def test_empty_list_returns_zero(self):
        assert _compute_max_drawdown([]) == 0.0


# ---------------------------------------------------------------------------
# test_calculate_monthly_metrics
# ---------------------------------------------------------------------------

class TestCalculateMonthlyMetrics:
    def test_groups_by_month(self):
        trades = [
            _make_trade(open_date="2025-01-10 10:00:00", profit_pct=0.05),
            _make_trade(open_date="2025-01-15 10:00:00", profit_pct=-0.02),
            _make_trade(open_date="2025-02-05 10:00:00", profit_pct=0.08),
        ]
        monthly = _compute_monthly_metrics("s", trades)
        assert len(monthly) == 2

        jan = next(m for m in monthly if m.month == "2025-01")
        assert jan.total_trades == 2
        assert jan.win_rate_pct == 50.0
        assert jan.total_profit_pct == pytest.approx(0.03, abs=1e-4)

        feb = next(m for m in monthly if m.month == "2025-02")
        assert feb.total_trades == 1
        assert feb.win_rate_pct == 100.0

    def test_empty_trades_returns_empty_list(self):
        assert _compute_monthly_metrics("s", []) == []

    def test_scenario_name_propagated(self):
        trades = [_make_trade(open_date="2025-03-01 00:00:00")]
        monthly = _compute_monthly_metrics("my_scenario", trades)
        assert all(m.scenario_name == "my_scenario" for m in monthly)


# ---------------------------------------------------------------------------
# test_generate_comparison_csv
# ---------------------------------------------------------------------------

class TestGenerateComparisonCsv:
    def test_writes_correct_columns_and_rows(self, tmp_path):
        metrics = [
            ScenarioMetrics(
                scenario_name="baseline",
                total_trades=10,
                win_rate_pct=60.0,
                total_profit_pct=0.5,
                max_drawdown_pct=0.1,
                profit_factor=2.5,
                sharpe_ratio=1.2,
                avg_trade_duration_hours=12.0,
                sl_moved_to_be_count=3,
                chains_blocked_count=1,
            ),
            ScenarioMetrics(
                scenario_name="signals_only",
                total_trades=8,
                win_rate_pct=50.0,
                total_profit_pct=0.2,
                max_drawdown_pct=0.15,
                profit_factor=1.5,
                sharpe_ratio=0.8,
                avg_trade_duration_hours=24.0,
                sl_moved_to_be_count=0,
                chains_blocked_count=2,
            ),
        ]
        path = tmp_path / "comparison.csv"
        _write_comparison_csv(metrics, path)

        rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
        assert rows[0][0] == "scenario"
        assert len(rows) == 3   # header + 2 data rows
        assert rows[1][0] == "baseline"
        assert rows[2][0] == "signals_only"

    def test_uses_utf8sig_encoding(self, tmp_path):
        path = tmp_path / "cmp.csv"
        _write_comparison_csv([], path)
        raw = path.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"   # UTF-8 BOM


# ---------------------------------------------------------------------------
# test_generate_comparison_monthly_csv
# ---------------------------------------------------------------------------

class TestGenerateComparisonMonthlyCsv:
    def test_writes_month_rows(self, tmp_path):
        monthly = [
            MonthlyMetrics(
                scenario_name="baseline",
                month="2025-01",
                total_trades=5,
                win_rate_pct=60.0,
                total_profit_pct=0.25,
                max_drawdown_pct=0.05,
            ),
            MonthlyMetrics(
                scenario_name="baseline",
                month="2025-02",
                total_trades=3,
                win_rate_pct=33.33,
                total_profit_pct=-0.05,
                max_drawdown_pct=0.08,
            ),
        ]
        path = tmp_path / "monthly.csv"
        _write_comparison_monthly_csv(monthly, path)

        rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
        assert rows[0] == ["scenario", "month", "trades", "win_rate_pct", "profit_pct", "max_dd_pct"]
        assert len(rows) == 3
        assert rows[1][1] == "2025-01"
        assert rows[2][1] == "2025-02"


# ---------------------------------------------------------------------------
# test_generate_html_table
# ---------------------------------------------------------------------------

class TestGenerateHtmlTable:
    def test_contains_scenario_names(self):
        metrics = [
            ScenarioMetrics(
                scenario_name="alpha",
                total_trades=5,
                win_rate_pct=60.0,
                total_profit_pct=0.3,
                max_drawdown_pct=0.1,
                profit_factor=2.0,
                sharpe_ratio=1.0,
                avg_trade_duration_hours=8.0,
                sl_moved_to_be_count=1,
                chains_blocked_count=0,
            ),
        ]
        html = _generate_html_table(metrics)
        assert "alpha" in html
        assert "<table" in html
        assert "<th" in html
        assert "<td" in html

    def test_valid_html_structure(self):
        html = _generate_html_table([])
        assert html.startswith("<!DOCTYPE html>")
        assert "<tbody>" in html
        assert "</html>" in html

    def test_multiple_scenarios_each_row(self):
        metrics = [
            ScenarioMetrics(
                scenario_name=f"s{i}",
                total_trades=i,
                win_rate_pct=50.0,
                total_profit_pct=0.0,
                max_drawdown_pct=0.0,
                profit_factor=1.0,
                sharpe_ratio=0.0,
                avg_trade_duration_hours=0.0,
                sl_moved_to_be_count=0,
                chains_blocked_count=0,
            )
            for i in range(3)
        ]
        html = _generate_html_table(metrics)
        assert html.count("<tr") == 4   # 1 header + 3 data rows


# ---------------------------------------------------------------------------
# test_generate_summary_json
# ---------------------------------------------------------------------------

class TestGenerateSummaryJson:
    @pytest.mark.asyncio
    async def test_summary_json_written(self, tmp_path):
        """Full generate() call with mocked storage: summary.json is written correctly."""
        db_path = str(tmp_path / "bt.sqlite3")
        output_dir = str(tmp_path / "report")

        trades = [
            _make_trade(profit_pct=0.05, open_date="2025-03-01 10:00:00"),
            _make_trade(profit_pct=-0.02, open_date="2025-03-15 10:00:00"),
        ]
        run_result = _make_run_result(run_id=1, scenario_name="baseline")
        run_meta = {
            "run_id": 1,
            "scenario_name": "baseline",
            "chains_blocked": 1,
            "status": "COMPLETED",
        }

        with (
            patch("src.backtesting.report.BacktestTradeStore") as mock_ts_cls,
            patch("src.backtesting.report.BacktestRunStore") as mock_rs_cls,
            patch("src.backtesting.report._try_plot_profit"),
        ):
            mock_ts = AsyncMock()
            mock_ts.get_trades_by_run = AsyncMock(return_value=trades)
            mock_ts_cls.return_value = mock_ts

            mock_rs = AsyncMock()
            mock_rs.get_run = AsyncMock(return_value=run_meta)
            mock_rs_cls.return_value = mock_rs

            generator = ReportGenerator(db_path=db_path)
            report = await generator.generate([run_result], output_dir=output_dir)

        # BacktestSummaryReport is returned correctly
        assert isinstance(report, BacktestSummaryReport)
        assert len(report.scenarios) == 1
        assert report.scenarios[0].scenario_name == "baseline"
        assert report.scenarios[0].total_trades == 2

        # summary.json is written and parseable
        summary_path = Path(output_dir) / "summary.json"
        assert summary_path.exists()
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        assert data["scenarios"][0]["scenario_name"] == "baseline"
        assert "run_ts" in data

    @pytest.mark.asyncio
    async def test_failed_runs_are_skipped(self, tmp_path):
        """FAILED run results should produce no scenario metrics."""
        db_path = str(tmp_path / "bt.sqlite3")
        output_dir = str(tmp_path / "report")

        failed_run = BacktestRunResult(
            run_id=99,
            scenario_name="failed_scenario",
            status="FAILED",
            error="subprocess error",
            output_dir=str(tmp_path),
            chains_count=0,
            trades_count=0,
        )

        with (
            patch("src.backtesting.report.BacktestTradeStore"),
            patch("src.backtesting.report.BacktestRunStore"),
            patch("src.backtesting.report._try_plot_profit"),
        ):
            generator = ReportGenerator(db_path=db_path)
            report = await generator.generate([failed_run], output_dir=output_dir)

        assert report.scenarios == []
        assert report.monthly == []
