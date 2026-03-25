"""Tests for src/operation_rules/risk_calculator.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.core.migrations import apply_migrations
from src.operation_rules.risk_calculator import (
    compute_exposure,
    count_open_same_symbol,
    sum_global_exposure,
    sum_trader_exposure,
)


# ---------------------------------------------------------------------------
# compute_exposure
# ---------------------------------------------------------------------------


class TestComputeExposure:
    def test_basic(self) -> None:
        # entry=100, sl=90 → sl_distance=10%, pct=1.0, lev=1 → exp=0.10
        exp = compute_exposure([100.0], 90.0, 1.0, 1)
        assert abs(exp - 0.10) < 1e-9

    def test_with_leverage(self) -> None:
        exp = compute_exposure([100.0], 90.0, 1.0, 10)
        assert abs(exp - 1.0) < 1e-9

    def test_multiple_entries_averaged(self) -> None:
        # avg entry = 100, sl = 90 → same as single entry
        exp = compute_exposure([95.0, 105.0], 90.0, 1.0, 1)
        assert abs(exp - 0.10) < 1e-9

    def test_no_entries_returns_zero(self) -> None:
        assert compute_exposure([], 90.0, 1.0, 1) == 0.0

    def test_no_sl_returns_zero(self) -> None:
        assert compute_exposure([100.0], None, 1.0, 1) == 0.0

    def test_sl_zero_returns_zero(self) -> None:
        assert compute_exposure([100.0], 0.0, 1.0, 1) == 0.0

    def test_sl_above_entry(self) -> None:
        # short position: entry=100, sl=110 → distance=10%
        exp = compute_exposure([100.0], 110.0, 1.0, 1)
        assert abs(exp - 0.10) < 1e-9


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
                      trader_id: str, position_size_pct: float, leverage: int = 1,
                      is_blocked: int = 0) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                position_size_pct, leverage, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (parse_result_id, attempt_key, trader_id, "NEW_SIGNAL", is_blocked,
             position_size_pct, leverage, "2026-01-01"),
        )
        conn.commit()


class TestSumExposure:
    def test_empty_db_returns_zero(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        assert sum_trader_exposure("any", db_path) == 0.0
        assert sum_global_exposure(db_path) == 0.0

    def test_single_signal_exposure(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        # Need a parse_result row first (FK)
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
        pr_id = 1
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", entry_json=json.dumps([{"price": 100.0}]), sl=90.0)
        _insert_op_signal(db_path, parse_result_id=pr_id, attempt_key="T_100_1_tr_a",
                          trader_id="tr_a", position_size_pct=1.0, leverage=1)

        exp = sum_trader_exposure("tr_a", db_path)
        assert abs(exp - 0.10) < 1e-6

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
                          trader_id="tr_a", position_size_pct=1.0, leverage=1,
                          is_blocked=1)  # blocked

        exp = sum_trader_exposure("tr_a", db_path)
        assert exp == 0.0  # blocked signals excluded


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
