"""Tests for GAP-04: machine_event rule engine + callback integration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.core.migrations import apply_migrations
from src.execution.freqtrade_callback import (
    order_filled_callback,
    partial_exit_callback,
    stoploss_callback,
)
from src.execution.machine_event import MachineEventAction, evaluate_rules


# ---------------------------------------------------------------------------
# Unit tests — evaluate_rules (pure function)
# ---------------------------------------------------------------------------


_RULES_BE_ON_TP2 = {
    "machine_event": {
        "rules": [
            {
                "event_type": "TP_EXECUTED",
                "when": {"tp_level": 2},
                "actions": [{"type": "MOVE_STOP_TO_BE"}],
            },
            {
                "event_type": "EXIT_BE",
                "actions": [{"type": "MARK_EXIT_BE"}],
            },
        ]
    }
}


class TestEvaluateRules:
    def test_tp2_returns_move_stop_to_be(self) -> None:
        actions = evaluate_rules(
            event_type="TP_EXECUTED",
            event_context={"tp_level": 2},
            management_rules=_RULES_BE_ON_TP2,
        )
        assert actions == [MachineEventAction(action_type="MOVE_STOP_TO_BE")]

    def test_tp1_no_match(self) -> None:
        actions = evaluate_rules(
            event_type="TP_EXECUTED",
            event_context={"tp_level": 1},
            management_rules=_RULES_BE_ON_TP2,
        )
        assert actions == []

    def test_tp3_no_match(self) -> None:
        actions = evaluate_rules(
            event_type="TP_EXECUTED",
            event_context={"tp_level": 3},
            management_rules=_RULES_BE_ON_TP2,
        )
        assert actions == []

    def test_exit_be_returns_mark_exit_be(self) -> None:
        actions = evaluate_rules(
            event_type="EXIT_BE",
            event_context={},
            management_rules=_RULES_BE_ON_TP2,
        )
        assert actions == [MachineEventAction(action_type="MARK_EXIT_BE")]

    def test_unknown_event_no_match(self) -> None:
        actions = evaluate_rules(
            event_type="SL_HIT",
            event_context={},
            management_rules=_RULES_BE_ON_TP2,
        )
        assert actions == []

    def test_none_management_rules_returns_empty(self) -> None:
        assert evaluate_rules(event_type="TP_EXECUTED", event_context={"tp_level": 2}, management_rules=None) == []

    def test_missing_machine_event_section_returns_empty(self) -> None:
        assert evaluate_rules(event_type="TP_EXECUTED", event_context={"tp_level": 2}, management_rules={"mode": "hybrid"}) == []

    def test_unconditional_rule_always_matches(self) -> None:
        rules = {"machine_event": {"rules": [{"event_type": "TP_EXECUTED", "actions": [{"type": "MOVE_STOP_TO_BE"}]}]}}
        for tp_level in (1, 2, 3):
            actions = evaluate_rules(event_type="TP_EXECUTED", event_context={"tp_level": tp_level}, management_rules=rules)
            assert len(actions) == 1

    def test_multiple_actions_returned(self) -> None:
        rules = {
            "machine_event": {
                "rules": [
                    {
                        "event_type": "TP_EXECUTED",
                        "when": {"tp_level": 2},
                        "actions": [{"type": "MOVE_STOP_TO_BE"}, {"type": "MARK_EXIT_BE"}],
                    }
                ]
            }
        }
        actions = evaluate_rules(event_type="TP_EXECUTED", event_context={"tp_level": 2}, management_rules=rules)
        assert len(actions) == 2

    def test_malformed_rule_skipped(self) -> None:
        rules = {
            "machine_event": {
                "rules": [
                    "not_a_dict",
                    {"event_type": "TP_EXECUTED", "when": {"tp_level": 2}, "actions": [{"type": "MOVE_STOP_TO_BE"}]},
                ]
            }
        }
        actions = evaluate_rules(event_type="TP_EXECUTED", event_context={"tp_level": 2}, management_rules=rules)
        assert actions == [MachineEventAction(action_type="MOVE_STOP_TO_BE")]


# ---------------------------------------------------------------------------
# Integration tests — callback chain
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "machine_event.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def _management_rules_with_be_on_tp2() -> str:
    return json.dumps({
        "tp_handling": {
            "tp_handling_mode": "follow_all_signal_tps",
            "tp_close_distribution": {"2": [50, 50], "3": [30, 30, 40]},
        },
        "machine_event": {
            "rules": [
                {"event_type": "TP_EXECUTED", "when": {"tp_level": 2}, "actions": [{"type": "MOVE_STOP_TO_BE"}]},
                {"event_type": "EXIT_BE", "actions": [{"type": "MARK_EXIT_BE"}]},
            ]
        },
    })


def _seed_active_trade(
    db_path: str,
    *,
    attempt_key: str,
    tp_prices: list[float] | None = None,
    management_rules_json: str | None = None,
) -> None:
    """Seed a fully active trade (PENDING signal → filled → ACTIVE)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO parse_results
               (parse_result_id, raw_message_id, eligibility_status, eligibility_reason,
                resolved_trader_id, trader_resolution_method, message_type, parse_status,
                completeness, is_executable, risky_flag, created_at, updated_at)
               VALUES (1, 1, 'OK', 'ok', 'trader_3', 'direct', 'NEW_SIGNAL', 'PARSED',
                       'COMPLETE', 1, 0, '2026-01-01', '2026-01-01')"""
        )
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at)
               VALUES (?, 'T', '-100', '1', 'trader_3', 'T',
                       'BTCUSDT', 'BUY', ?, 57000.0, ?, 'PENDING', 0.9, 'test',
                       '2026-01-01', '2026-01-01')""",
            (
                attempt_key,
                json.dumps([{"price": 60000.0}]),
                json.dumps([{"price": p} for p in (tp_prices or [65000.0, 70000.0])]),
            ),
        )
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                position_size_usdt, leverage, management_rules_json, created_at)
               VALUES (1, ?, 'trader_3', 'NEW_SIGNAL', 0, 250.0, 3, ?, '2026-01-01')""",
            (attempt_key, management_rules_json or json.dumps({"tp_handling": "ladder"})),
        )
        conn.commit()

    order_filled_callback(
        db_path=db_path,
        attempt_key=attempt_key,
        qty=2.0,
        fill_price=60000.0,
        client_order_id=f"entry-{attempt_key}",
        exchange_order_id=f"ex-entry-{attempt_key}",
        protective_orders_mode="strategy_managed",
    )


class TestMoveStopToBeOnTp2:
    def test_tp2_fill_moves_sl_to_entry(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        _seed_active_trade(
            db_path,
            attempt_key="atk1",
            management_rules_json=_management_rules_with_be_on_tp2(),
        )

        partial_exit_callback(
            db_path=db_path,
            attempt_key="atk1",
            close_fraction=0.5,
            remaining_qty=1.0,
            tp_idx=1,  # 0-based → tp_level 2
        )

        with sqlite3.connect(db_path) as conn:
            sl = conn.execute("SELECT sl FROM signals WHERE attempt_key = 'atk1'").fetchone()[0]
            meta = json.loads(conn.execute("SELECT meta_json FROM trades WHERE attempt_key = 'atk1'").fetchone()[0])

        assert sl == pytest.approx(60000.0), "SL should have been moved to entry price (60000)"
        assert meta.get("be_stop_active") is True
        assert meta.get("be_entry_price") == pytest.approx(60000.0)

    def test_tp1_fill_does_not_move_sl(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        _seed_active_trade(
            db_path,
            attempt_key="atk2",
            management_rules_json=_management_rules_with_be_on_tp2(),
        )

        partial_exit_callback(
            db_path=db_path,
            attempt_key="atk2",
            close_fraction=0.3,
            remaining_qty=1.4,
            tp_idx=0,  # tp_level 1 → no rule match
        )

        with sqlite3.connect(db_path) as conn:
            sl = conn.execute("SELECT sl FROM signals WHERE attempt_key = 'atk2'").fetchone()[0]
            meta_raw = conn.execute("SELECT meta_json FROM trades WHERE attempt_key = 'atk2'").fetchone()[0]
            meta = json.loads(meta_raw) if meta_raw else {}

        assert sl == pytest.approx(57000.0), "SL should not have changed"
        assert not meta.get("be_stop_active")

    def test_tp2_fill_logs_machine_event(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        _seed_active_trade(
            db_path,
            attempt_key="atk3",
            management_rules_json=_management_rules_with_be_on_tp2(),
        )

        partial_exit_callback(
            db_path=db_path,
            attempt_key="atk3",
            close_fraction=0.5,
            remaining_qty=1.0,
            tp_idx=1,
        )

        with sqlite3.connect(db_path) as conn:
            events = [
                row[0]
                for row in conn.execute(
                    "SELECT event_type FROM events WHERE attempt_key = 'atk3'"
                ).fetchall()
            ]
        assert "MACHINE_EVENT_MOVE_STOP_TO_BE" in events

    def test_no_management_rules_no_change(self, tmp_path: Path) -> None:
        """Without machine_event rules in management_rules_json, SL stays unchanged."""
        db_path = _make_db(tmp_path)
        _seed_active_trade(db_path, attempt_key="atk4")  # no machine_event rules

        partial_exit_callback(
            db_path=db_path,
            attempt_key="atk4",
            close_fraction=0.5,
            remaining_qty=1.0,
            tp_idx=1,
        )

        with sqlite3.connect(db_path) as conn:
            sl = conn.execute("SELECT sl FROM signals WHERE attempt_key = 'atk4'").fetchone()[0]
        assert sl == pytest.approx(57000.0)


class TestMarkExitBeOnBreakevenStop:
    def _seed_be_trade(self, db_path: str, attempt_key: str) -> None:
        """Seed a trade that already had TP2 fill (be_stop_active=True in meta)."""
        _seed_active_trade(
            db_path,
            attempt_key=attempt_key,
            management_rules_json=_management_rules_with_be_on_tp2(),
        )
        # Simulate TP2 fill → triggers MOVE_STOP_TO_BE → sets be_stop_active
        partial_exit_callback(
            db_path=db_path,
            attempt_key=attempt_key,
            close_fraction=0.5,
            remaining_qty=1.0,
            tp_idx=1,
        )

    def test_sl_at_be_sets_breakeven_exit_flag(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        self._seed_be_trade(db_path, "be1")

        stoploss_callback(
            db_path=db_path,
            attempt_key="be1",
            qty=1.0,
            stop_price=60000.0,  # ≈ entry price → breakeven stop
        )

        with sqlite3.connect(db_path) as conn:
            meta = json.loads(conn.execute("SELECT meta_json FROM trades WHERE attempt_key = 'be1'").fetchone()[0])
        assert meta.get("breakeven_exit") is True

    def test_sl_at_be_logs_machine_event(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        self._seed_be_trade(db_path, "be2")

        stoploss_callback(db_path=db_path, attempt_key="be2", qty=1.0, stop_price=60000.0)

        with sqlite3.connect(db_path) as conn:
            events = [
                row[0]
                for row in conn.execute("SELECT event_type FROM events WHERE attempt_key = 'be2'").fetchall()
            ]
        assert "MACHINE_EVENT_EXIT_BE" in events

    def test_real_sl_without_be_flag_no_mark(self, tmp_path: Path) -> None:
        """A real SL hit (no be_stop_active flag) must NOT set breakeven_exit."""
        db_path = _make_db(tmp_path)
        _seed_active_trade(
            db_path,
            attempt_key="real_sl",
            management_rules_json=_management_rules_with_be_on_tp2(),
        )

        stoploss_callback(
            db_path=db_path,
            attempt_key="real_sl",
            qty=2.0,
            stop_price=57000.0,  # original SL level — NOT breakeven
        )

        with sqlite3.connect(db_path) as conn:
            meta_raw = conn.execute("SELECT meta_json FROM trades WHERE attempt_key = 'real_sl'").fetchone()[0]
            meta = json.loads(meta_raw) if meta_raw else {}
        assert not meta.get("breakeven_exit")

    def test_real_sl_without_management_rules_no_error(self, tmp_path: Path) -> None:
        """stoploss_callback with no machine_event rules must complete normally."""
        db_path = _make_db(tmp_path)
        _seed_active_trade(db_path, attempt_key="no_rules")

        result = stoploss_callback(
            db_path=db_path,
            attempt_key="no_rules",
            qty=2.0,
            stop_price=57000.0,
        )
        assert result.get("ok") is True
