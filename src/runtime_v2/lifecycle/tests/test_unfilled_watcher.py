"""Tests for Task 1: verify plumbing — event type, outbox map, template."""
from src.runtime_v2.lifecycle.models import LifecycleEventType
from src.runtime_v2.control_plane.outbox_writer import _CLEAN_LOG_EVENT_MAP
from src.runtime_v2.control_plane.formatters.templates.clean_log import TEMPLATE_REGISTRY


def test_unfilled_tp_cancel_in_lifecycle_event_type():
    # LifecycleEventType is a Literal — check its args
    import typing
    args = typing.get_args(LifecycleEventType)
    assert "UNFILLED_TP_CANCEL" in args


def test_outbox_map_has_entry_cancelled_tp_reached():
    assert _CLEAN_LOG_EVENT_MAP.get("UNFILLED_TP_CANCEL") == "ENTRY_CANCELLED_TP_REACHED"


def test_template_map_has_entry_cancelled_tp_reached():
    assert "ENTRY_CANCELLED_TP_REACHED" in TEMPLATE_REGISTRY


# Task 2 tests: DB query for unfilled WAITING_ENTRY chains

import json
import sqlite3
import pytest
from src.runtime_v2.lifecycle.repositories import TradeChainRepository


def _make_ops_db(path: str) -> sqlite3.Connection:
    """Minimal ops DB schema for chain queries."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_enrichment_id INTEGER, canonical_message_id INTEGER,
            raw_message_id INTEGER, trader_id TEXT, account_id TEXT,
            symbol TEXT, side TEXT, lifecycle_state TEXT, entry_mode TEXT,
            entry_avg_price REAL, current_stop_price REAL,
            expected_stop_price REAL, be_protection_status TEXT,
            entry_timeout_at TEXT, management_plan_json TEXT,
            risk_snapshot_json TEXT, planned_entry_qty REAL,
            filled_entry_qty REAL, open_position_qty REAL,
            closed_position_qty REAL, last_position_sync_at TEXT,
            execution_mode TEXT, risk_already_realized REAL,
            risk_remaining REAL, plan_state_json TEXT,
            source_chat_id INTEGER, telegram_message_id INTEGER,
            external_signal_id TEXT, cumulative_gross_pnl REAL,
            cumulative_fees REAL, cumulative_funding REAL,
            allocated_margin REAL, initial_risk_amount REAL,
            peak_margin_used REAL, created_at TEXT, updated_at TEXT,
            last_projected_event_id INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _insert_chain(conn, *, symbol="BTC/USDT", side="LONG",
                  lifecycle_state="WAITING_ENTRY",
                  cancel_unfilled_pending_after=None,
                  cancel_pending_by_engine=True,
                  plan_legs=None):
    mp = {
        "cancel_unfilled_pending_after": cancel_unfilled_pending_after,
        "cancel_pending_by_engine": cancel_pending_by_engine,
    }
    legs = plan_legs or [{"sequence": 1, "status": "PENDING", "price": 100.0}]
    plan = {"legs": legs}
    conn.execute(
        """INSERT INTO ops_trade_chains
           (source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            be_protection_status, management_plan_json, plan_state_json,
            risk_snapshot_json, execution_mode, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (1, 1, 1, "trader1", "acc1", symbol, side, lifecycle_state, "MARKET",
         "NOT_PROTECTED",
         json.dumps(mp), json.dumps(plan), "{}", "D_POSITION_TPSL",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_get_waiting_entry_unfilled_returns_eligible(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(conn, cancel_unfilled_pending_after="tp1")
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert len(result) == 1
    assert result[0].symbol == "BTC/USDT"


def test_get_waiting_entry_unfilled_skips_null_config(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(conn, cancel_unfilled_pending_after=None)
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert result == []


def test_get_waiting_entry_unfilled_skips_non_waiting(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(conn, lifecycle_state="OPEN", cancel_unfilled_pending_after="tp1")
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert result == []


def test_get_waiting_entry_unfilled_skips_filled_legs(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(
        conn,
        cancel_unfilled_pending_after="tp1",
        plan_legs=[{"sequence": 1, "status": "FILLED", "price": 100.0}],
    )
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert result == []
