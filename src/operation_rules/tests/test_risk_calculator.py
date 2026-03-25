"""Tests for src/operation_rules/risk_calculator.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.core.migrations import apply_migrations
from src.operation_rules.risk_calculator import (
    compute_position_size_from_risk,
    compute_risk_budget_usdt,
    compute_risk_pct,
    count_open_same_symbol,
    sum_global_exposure,
    sum_trader_exposure,
)


# ---------------------------------------------------------------------------
# compute_risk_pct
# ---------------------------------------------------------------------------


class TestComputeRiskPct:
    def test_risk_pct_of_capital_mode(self) -> None:
        # risk_mode=risk_pct_of_capital → returns risk_pct_of_capital directly
        pct = compute_risk_pct("risk_pct_of_capital", 1.0, 10.0, 1000.0)
        assert abs(pct - 1.0) < 1e-9

    def test_risk_usdt_fixed_mode(self) -> None:
        # risk=10 USDT, capital=1000 → 10/1000*100 = 1.0%
        pct = compute_risk_pct("risk_usdt_fixed", 0.0, 10.0, 1000.0)
        assert abs(pct - 1.0) < 1e-9

    def test_risk_usdt_fixed_zero_capital_returns_zero(self) -> None:
        pct = compute_risk_pct("risk_usdt_fixed", 0.0, 10.0, 0.0)
        assert pct == 0.0

    def test_risk_pct_of_capital_ignores_usdt_fixed(self) -> None:
        # risk_usdt_fixed is irrelevant when mode is risk_pct_of_capital
        pct = compute_risk_pct("risk_pct_of_capital", 2.0, 999.0, 1000.0)
        assert abs(pct - 2.0) < 1e-9


class TestComputeRiskBudgetUsdt:
    def test_pct_mode(self) -> None:
        # 1% of 1000 = 10 USDT
        budget = compute_risk_budget_usdt("risk_pct_of_capital", 1.0, 0.0, 1000.0)
        assert abs(budget - 10.0) < 1e-9

    def test_fixed_mode(self) -> None:
        budget = compute_risk_budget_usdt("risk_usdt_fixed", 0.0, 25.0, 1000.0)
        assert abs(budget - 25.0) < 1e-9


# ---------------------------------------------------------------------------
# compute_position_size_from_risk
# ---------------------------------------------------------------------------


class TestComputePositionSizeFromRisk:
    def test_basic(self) -> None:
        # entry=100, sl=90 (10% dist), risk_budget=10, lev=1, capital=1000
        # position_size_usdt = 10 / (0.10 * 1) = 100
        # position_size_pct  = 100/1000*100 = 10%
        # sl_distance_pct    = 0.10
        size_usdt, size_pct, sl_dist = compute_position_size_from_risk(
            [100.0], 90.0, 10.0, 1, 1000.0
        )
        assert abs(size_usdt - 100.0) < 1e-6
        assert abs(size_pct - 10.0) < 1e-6
        assert abs(sl_dist - 0.10) < 1e-9

    def test_with_leverage(self) -> None:
        # entry=100, sl=90 (10%), risk=10, lev=5, capital=1000
        # position_size_usdt = 10 / (0.10 * 5) = 20
        size_usdt, size_pct, sl_dist = compute_position_size_from_risk(
            [100.0], 90.0, 10.0, 5, 1000.0
        )
        assert abs(size_usdt - 20.0) < 1e-6

    def test_sl_above_entry_short(self) -> None:
        # short: entry=100, sl=110 (10% dist)
        size_usdt, size_pct, sl_dist = compute_position_size_from_risk(
            [100.0], 110.0, 10.0, 1, 1000.0
        )
        assert abs(sl_dist - 0.10) < 1e-9
        assert abs(size_usdt - 100.0) < 1e-6

    def test_multiple_entries_simple_avg(self) -> None:
        # avg entry = 100, sl = 90 → same result as single entry
        size_usdt, _, sl_dist = compute_position_size_from_risk(
            [95.0, 105.0], 90.0, 10.0, 1, 1000.0
        )
        assert abs(sl_dist - 0.10) < 1e-9

    def test_zero_sl_distance_raises(self) -> None:
        with pytest.raises(ValueError, match="zero"):
            compute_position_size_from_risk([100.0], 100.0, 10.0, 1, 1000.0)

    def test_invalid_leverage_raises(self) -> None:
        with pytest.raises(ValueError, match="leverage"):
            compute_position_size_from_risk([100.0], 90.0, 10.0, 0, 1000.0)


# ---------------------------------------------------------------------------
# DB-based tests
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "risk_test.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def _insert_signal(db_path: str, *, attempt_key: str, trader_id: str, symbol: str,
                   entry_json: str, sl: float, status: str = "PENDING") -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_key, "T", "100", "1", trader_id, "TP", symbol, "BUY",
             entry_json, sl, "[]", status, 0.9, "test", "2026-01-01", "2026-01-01"),
        )
        conn.commit()


def _insert_op_signal(db_path: str, *, parse_result_id: int, attempt_key: str,
                      trader_id: str, risk_budget_usdt: float, capital_base_usdt: float,
                      is_blocked: int = 0) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                risk_budget_usdt, capital_base_usdt, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (parse_result_id, attempt_key, trader_id, "NEW_SIGNAL", is_blocked,
             risk_budget_usdt, capital_base_usdt, "2026-01-01"),
        )
        conn.commit()


class TestSumExposure:
    def test_empty_db_returns_zero(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        assert sum_trader_exposure("any", db_path) == 0.0
        assert sum_global_exposure(db_path) == 0.0

    def test_single_signal_exposure(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO parse_results
                   (raw_message_id,eligibility_status,eligibility_reason,
                    resolved_trader_id,trader_resolution_method,message_type,
                    parse_status,completeness,is_executable,risky_flag,created_at,updated_at)
                   VALUES (1,'OK','ok','tr_a','direct','NEW_SIGNAL','PARSED','COMPLETE',1,0,
                           '2026-01-01','2026-01-01')"""
            )
            conn.commit()
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", entry_json=json.dumps([{"price": 100.0}]), sl=90.0)
        # risk_budget=10 USDT, capital=1000 → exposure = 10/1000*100 = 1.0%
        _insert_op_signal(db_path, parse_result_id=1, attempt_key="T_100_1_tr_a",
                          trader_id="tr_a", risk_budget_usdt=10.0, capital_base_usdt=1000.0)

        exp = sum_trader_exposure("tr_a", db_path)
        assert abs(exp - 1.0) < 1e-6

    def test_blocked_signal_not_counted(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO parse_results
                   (raw_message_id,eligibility_status,eligibility_reason,
                    resolved_trader_id,trader_resolution_method,message_type,
                    parse_status,completeness,is_executable,risky_flag,created_at,updated_at)
                   VALUES (1,'OK','ok','tr_a','direct','NEW_SIGNAL','PARSED','COMPLETE',1,0,
                           '2026-01-01','2026-01-01')"""
            )
            conn.commit()
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", entry_json=json.dumps([{"price": 100.0}]), sl=90.0)
        _insert_op_signal(db_path, parse_result_id=1, attempt_key="T_100_1_tr_a",
                          trader_id="tr_a", risk_budget_usdt=10.0, capital_base_usdt=1000.0,
                          is_blocked=1)  # blocked

        exp = sum_trader_exposure("tr_a", db_path)
        assert exp == 0.0  # blocked signals excluded

    def test_signal_without_risk_fields_not_counted(self, tmp_path: Path) -> None:
        """Old-style signals without risk_budget_usdt contribute 0 to exposure."""
        db_path = _make_db(tmp_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO parse_results
                   (raw_message_id,eligibility_status,eligibility_reason,
                    resolved_trader_id,trader_resolution_method,message_type,
                    parse_status,completeness,is_executable,risky_flag,created_at,updated_at)
                   VALUES (1,'OK','ok','tr_a','direct','NEW_SIGNAL','PARSED','COMPLETE',1,0,
                           '2026-01-01','2026-01-01')"""
            )
            # Insert without risk_budget_usdt (NULL)
            conn.execute(
                """INSERT INTO operational_signals
                   (parse_result_id, attempt_key, trader_id, message_type, is_blocked, created_at)
                   VALUES (1,'T_100_1_tr_a','tr_a','NEW_SIGNAL',0,'2026-01-01')"""
            )
            conn.commit()
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", entry_json=json.dumps([{"price": 100.0}]), sl=90.0)

        exp = sum_trader_exposure("tr_a", db_path)
        assert exp == 0.0  # NULL risk_budget_usdt → 0 contribution


class TestCountOpenSameSymbol:
    def test_no_signals_returns_zero(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        assert count_open_same_symbol("any", "BTCUSDT", db_path) == 0

    def test_open_signal_counted(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT",
                       entry_json=json.dumps([{"price": 100.0}]), sl=90.0,
                       status="PENDING")
        assert count_open_same_symbol("tr_a", "BTCUSDT", db_path) == 1

    def test_closed_not_counted(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT",
                       entry_json=json.dumps([{"price": 100.0}]), sl=90.0,
                       status="CLOSED")
        assert count_open_same_symbol("tr_a", "BTCUSDT", db_path) == 0

    def test_case_insensitive(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="btcusdt",
                       entry_json=json.dumps([{"price": 100.0}]), sl=90.0,
                       status="PENDING")
        assert count_open_same_symbol("tr_a", "BTCUSDT", db_path) == 1

    def test_different_trader_not_counted(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        _insert_signal(db_path, attempt_key="T_100_1_tr_b", trader_id="tr_b",
                       symbol="BTCUSDT",
                       entry_json=json.dumps([{"price": 100.0}]), sl=90.0,
                       status="PENDING")
        assert count_open_same_symbol("tr_a", "BTCUSDT", db_path) == 0
