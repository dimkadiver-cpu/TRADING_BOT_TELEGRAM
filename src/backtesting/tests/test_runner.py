"""Tests for BacktestRunner and helper functions in src/backtesting/runner.py."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backtesting.runner import (
    BacktestRunResult,
    BacktestRunner,
    _collect_pairs,
    _generate_freqtrade_config,
    _normalize_pair,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_ready_chain(symbol: str = "BTCUSDT", side: str = "BUY") -> Any:
    """Return a minimal BacktestReadyChain-like mock."""
    chain = MagicMock()
    chain.chain.symbol = symbol
    chain.chain.side = side
    chain.chain.new_signal.is_blocked = False
    chain.model_dump.return_value = {"chain_id": f"trader_3:{symbol}"}
    return chain


def _make_scenario(name: str = "baseline") -> Any:
    sc = MagicMock()
    sc.name = name
    sc.conditions.model_dump_json.return_value = '{"follow_full_chain": true}'
    return sc


def _make_settings(
    *,
    exchange: str = "bybit",
    timeframe: str = "5m",
    trader_filter: str | None = "trader_3",
    date_from: str | None = "2025-01-01",
    date_to: str | None = "2025-12-31",
    max_open_trades: int = 5,
    capital_base_usdt: float = 1000.0,
) -> Any:
    s = MagicMock()
    s.exchange = exchange
    s.timeframe = timeframe
    s.trader_filter = trader_filter
    s.date_from = date_from
    s.date_to = date_to
    s.max_open_trades = max_open_trades
    s.capital_base_usdt = capital_base_usdt
    return s


def _make_freqtrade_result_json(chain_id: str = "trader_3:BTCUSDT") -> dict[str, Any]:
    """Minimal freqtrade results JSON with one trade."""
    return {
        "strategy": {
            "SignalBridgeBacktestStrategy": {
                "trades": [
                    {
                        "pair": "BTC/USDT:USDT",
                        "enter_tag": chain_id,
                        "is_short": False,
                        "open_date": "2025-03-01 10:00:00",
                        "close_date": "2025-03-02 10:00:00",
                        "open_rate": 90000.0,
                        "close_rate": 95000.0,
                        "profit_abs": 50.0,
                        "profit_ratio": 0.05,
                        "exit_reason": "tp1",
                        "max_drawdown": 0.02,
                        "trade_duration": 1440,
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Unit tests — pure helpers
# ---------------------------------------------------------------------------

class TestNormalizePair:
    def test_btcusdt(self):
        assert _normalize_pair("BTCUSDT") == "BTC/USDT:USDT"

    def test_ethusdt(self):
        assert _normalize_pair("ETHUSDT") == "ETH/USDT:USDT"

    def test_solusdt(self):
        assert _normalize_pair("SOLUSDT") == "SOL/USDT:USDT"

    def test_already_normalised(self):
        assert _normalize_pair("BTC/USDT:USDT") == "BTC/USDT:USDT"

    def test_lowercase_input(self):
        assert _normalize_pair("btcusdt") == "BTC/USDT:USDT"

    def test_usd_suffix(self):
        assert _normalize_pair("BTCUSD") == "BTC/USD:USD"

    def test_unknown_suffix_passthrough(self):
        # Symbol without a recognised quote is returned as-is (uppercased)
        assert _normalize_pair("XYZABC") == "XYZABC"


class TestCollectPairs:
    def test_unique_pairs(self):
        chains = [
            _make_ready_chain("BTCUSDT"),
            _make_ready_chain("ETHUSDT"),
            _make_ready_chain("BTCUSDT"),  # duplicate
        ]
        pairs = _collect_pairs(chains)
        assert pairs == ["BTC/USDT:USDT", "ETH/USDT:USDT"]

    def test_sorted_output(self):
        chains = [_make_ready_chain("SOLUSDT"), _make_ready_chain("BTCUSDT")]
        pairs = _collect_pairs(chains)
        assert pairs == sorted(pairs)

    def test_empty_chains(self):
        assert _collect_pairs([]) == []


class TestGenerateFreqtradeConfig:
    def test_structure(self):
        chains = [_make_ready_chain("BTCUSDT")]
        settings = _make_settings()
        cfg = _generate_freqtrade_config(
            chains=chains,
            signal_chains_path="/tmp/chains.json",
            results_path="/tmp/results.json",
            settings=settings,
        )
        assert cfg["exchange"]["name"] == "bybit"
        assert cfg["trading_mode"] == "futures"
        assert cfg["margin_mode"] == "isolated"
        assert cfg["stake_currency"] == "USDT"
        assert cfg["dry_run"] is True
        assert cfg["pairs"] == ["BTC/USDT:USDT"]
        assert cfg["strategy_params"]["signal_chains_path"] == "/tmp/chains.json"
        assert cfg["exportfilename"] == "/tmp/results.json"
        assert cfg["max_open_trades"] == 5

    def test_multiple_pairs(self):
        chains = [_make_ready_chain("BTCUSDT"), _make_ready_chain("ETHUSDT")]
        settings = _make_settings()
        cfg = _generate_freqtrade_config(
            chains=chains,
            signal_chains_path="/tmp/chains.json",
            results_path="/tmp/results.json",
            settings=settings,
        )
        assert len(cfg["pairs"]) == 2


# ---------------------------------------------------------------------------
# Integration tests — BacktestRunner._run_scenario
# ---------------------------------------------------------------------------

class TestBacktestRunnerScenario:

    @pytest.mark.asyncio
    async def test_run_scenario_success(self, tmp_path):
        """Full happy-path: subprocess succeeds, trades are imported."""
        db_path = str(tmp_path / "bt.sqlite3")
        runner = BacktestRunner(db_path=db_path, output_base=str(tmp_path / "reports"))

        chains = [_make_ready_chain("BTCUSDT")]
        scenario = _make_scenario("baseline")
        settings = _make_settings()

        ft_results = _make_freqtrade_result_json()

        def _fake_freqtrade(*, config_path: str, timeframe: str):
            # Write fake results to the expected path
            cfg = json.loads(Path(config_path).read_text())
            Path(cfg["exportfilename"]).write_text(json.dumps(ft_results))
            return (0, "OK", "")

        with (
            patch("src.backtesting.scenario.ScenarioApplier") as mock_applier_cls,
            patch("src.backtesting.runner.BacktestRunStore") as mock_run_store_cls,
            patch("src.backtesting.runner.BacktestTradeStore") as mock_trade_store_cls,
        ):
            mock_applier_cls.apply_all.return_value = chains

            mock_run_store = AsyncMock()
            mock_run_store.insert_run = AsyncMock(return_value=42)
            mock_run_store.update_status = AsyncMock()
            mock_run_store_cls.return_value = mock_run_store

            mock_trade_store = AsyncMock()
            mock_trade_store.import_from_freqtrade_json = AsyncMock(return_value=1)
            mock_trade_store_cls.return_value = mock_trade_store

            runner._run_freqtrade = _fake_freqtrade

            result = await runner._run_scenario(
                scenario=scenario,
                all_chains=chains,
                settings=settings,
            )

        assert result.status == "COMPLETED"
        assert result.run_id == 42
        assert result.chains_count == 1
        assert result.trades_count == 1
        assert result.error is None

    @pytest.mark.asyncio
    async def test_run_scenario_failure(self, tmp_path):
        """Subprocess returns non-zero → result is FAILED."""
        db_path = str(tmp_path / "bt.sqlite3")
        runner = BacktestRunner(db_path=db_path, output_base=str(tmp_path / "reports"))

        chains = [_make_ready_chain("BTCUSDT")]
        scenario = _make_scenario("fail_case")
        settings = _make_settings()

        def _bad_freqtrade(*, config_path: str, timeframe: str):
            return (1, "", "ERROR: something went wrong")

        with (
            patch("src.backtesting.scenario.ScenarioApplier") as mock_applier_cls,
            patch("src.backtesting.runner.BacktestRunStore") as mock_run_store_cls,
        ):
            mock_applier_cls.apply_all.return_value = chains

            mock_run_store = AsyncMock()
            mock_run_store.insert_run = AsyncMock(return_value=7)
            mock_run_store.update_status = AsyncMock()
            mock_run_store_cls.return_value = mock_run_store

            runner._run_freqtrade = _bad_freqtrade

            result = await runner._run_scenario(
                scenario=scenario,
                all_chains=chains,
                settings=settings,
            )

        assert result.status == "FAILED"
        assert result.run_id == 7
        assert result.trades_count == 0
        assert "freqtrade exited with code 1" in result.error

    @pytest.mark.asyncio
    async def test_run_scenario_exception_propagates(self, tmp_path):
        """Unexpected exception inside scenario → FAILED result, not a crash."""
        db_path = str(tmp_path / "bt.sqlite3")
        runner = BacktestRunner(db_path=db_path, output_base=str(tmp_path / "reports"))

        chains = [_make_ready_chain("BTCUSDT")]
        scenario = _make_scenario("exc_case")
        settings = _make_settings()

        def _crash(*, config_path: str, timeframe: str):
            raise RuntimeError("disk full")

        with (
            patch("src.backtesting.scenario.ScenarioApplier") as mock_applier_cls,
            patch("src.backtesting.runner.BacktestRunStore") as mock_run_store_cls,
        ):
            mock_applier_cls.apply_all.return_value = chains

            mock_run_store = AsyncMock()
            mock_run_store.insert_run = AsyncMock(return_value=3)
            mock_run_store.update_status = AsyncMock()
            mock_run_store_cls.return_value = mock_run_store

            runner._run_freqtrade = _crash

            result = await runner._run_scenario(
                scenario=scenario,
                all_chains=chains,
                settings=settings,
            )

        assert result.status == "FAILED"
        assert "disk full" in result.error


# ---------------------------------------------------------------------------
# Windows command detection
# ---------------------------------------------------------------------------

class TestWindowsCommandDetection:
    def test_win32_uses_python_module(self):
        runner = BacktestRunner(db_path=":memory:")
        with (
            patch.object(sys, "platform", "win32"),
            patch("subprocess.run") as mock_run,
        ):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            runner._run_freqtrade(config_path="/tmp/cfg.json", timeframe="5m")

            call_args = mock_run.call_args[0][0]
            assert call_args[:3] == ["python", "-m", "freqtrade"]

    def test_linux_uses_freqtrade_directly(self):
        runner = BacktestRunner(db_path=":memory:")
        with (
            patch.object(sys, "platform", "linux"),
            patch("subprocess.run") as mock_run,
        ):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            runner._run_freqtrade(config_path="/tmp/cfg.json", timeframe="5m")

            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "freqtrade"
            assert "python" not in call_args


# ---------------------------------------------------------------------------
# _find_results_file
# ---------------------------------------------------------------------------

class TestFindResultsFile:
    def test_returns_expected_path_when_exists(self, tmp_path):
        expected = tmp_path / "freqtrade_results.json"
        expected.write_text("{}")
        result = BacktestRunner._find_results_file(tmp_path, str(expected))
        assert result == expected

    def test_fallback_to_newest_json(self, tmp_path):
        # No file at expected path
        expected = tmp_path / "missing.json"
        # Create two json files; newest should win
        (tmp_path / "freqtrade_config.json").write_text("{}")  # skip
        (tmp_path / "signal_chains.json").write_text("{}")     # skip
        other = tmp_path / "results_20250301.json"
        other.write_text("{}")
        result = BacktestRunner._find_results_file(tmp_path, str(expected))
        assert result == other

    def test_returns_none_when_no_candidates(self, tmp_path):
        # Only the excluded files exist
        (tmp_path / "freqtrade_config.json").write_text("{}")
        (tmp_path / "signal_chains.json").write_text("{}")
        result = BacktestRunner._find_results_file(tmp_path, str(tmp_path / "missing.json"))
        assert result is None
