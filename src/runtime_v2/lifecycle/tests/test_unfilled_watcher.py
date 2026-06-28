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
    conn.execute("""
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL,
            destination TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'MEDIUM',
            status TEXT NOT NULL DEFAULT 'PENDING',
            dedupe_key TEXT NOT NULL UNIQUE,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT,
            account_id TEXT,
            chain_id INTEGER
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


# Task 3 tests: UnfilledPriceWatcher worker

from unittest.mock import MagicMock
from src.runtime_v2.lifecycle.unfilled_price_watcher import UnfilledPriceWatcher, resolve_tp_threshold


# ── Pure function: resolve_tp_threshold ──────────────────────────────────────

def test_resolve_threshold_tp1_from_intermediate():
    plan = {"intermediate_tps": [110.0, 120.0], "final_tp": 130.0}
    assert resolve_tp_threshold(plan, "tp1") == 110.0


def test_resolve_threshold_tp2_from_intermediate():
    plan = {"intermediate_tps": [110.0, 120.0], "final_tp": 130.0}
    assert resolve_tp_threshold(plan, "tp2") == 120.0


def test_resolve_threshold_tp1_fallback_to_final():
    plan = {"intermediate_tps": [], "final_tp": 115.0}
    assert resolve_tp_threshold(plan, "tp1") == 115.0


def test_resolve_threshold_tp2_fallback_to_final_when_only_one_intermediate():
    plan = {"intermediate_tps": [110.0], "final_tp": 130.0}
    assert resolve_tp_threshold(plan, "tp2") == 130.0


def test_resolve_threshold_returns_none_when_no_tps():
    plan = {"intermediate_tps": [], "final_tp": None}
    assert resolve_tp_threshold(plan, "tp1") is None


# ── Worker: run_once ──────────────────────────────────────────────────────────

def _make_chain_mock(
    chain_id=1,
    symbol="BTC/USDT",
    side="LONG",
    account_id="acc1",
    cancel_after="tp1",
    cancel_by_engine=True,
    intermediate_tps=None,
    final_tp=120.0,
    legs=None,
):
    chain = MagicMock()
    chain.trade_chain_id = chain_id
    chain.symbol = symbol
    chain.side = side
    chain.account_id = account_id
    mp = {
        "cancel_unfilled_pending_after": cancel_after,
        "cancel_pending_by_engine": cancel_by_engine,
    }
    plan = {
        "intermediate_tps": intermediate_tps or [],
        "final_tp": final_tp,
        "legs": legs or [{"sequence": 1, "status": "PENDING", "price": 100.0,
                          "client_order_id": "place_entry_attached:1:leg1"}],
    }
    chain.management_plan_json = json.dumps(mp)
    chain.plan_state_json = json.dumps(plan)
    return chain


def _make_worker(tmp_path, chains, mark_price):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    # Insert the chain rows into DB so the worker can write events
    for ch in chains:
        conn.execute(
            """INSERT INTO ops_trade_chains
               (trade_chain_id, symbol, side, lifecycle_state,
                management_plan_json, plan_state_json, risk_snapshot_json,
                execution_mode, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ch.trade_chain_id, ch.symbol, ch.side, "WAITING_ENTRY",
             ch.management_plan_json, ch.plan_state_json, "{}",
             "D_POSITION_TPSL",
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, event_type TEXT, source_type TEXT,
            previous_state TEXT, next_state TEXT, payload_json TEXT,
            idempotency_key TEXT UNIQUE, created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_execution_commands (
            command_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, command_type TEXT, status TEXT,
            payload_json TEXT, idempotency_key TEXT UNIQUE,
            client_order_id TEXT, created_at TEXT, updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    repo = MagicMock()
    repo.get_waiting_entry_with_unfilled_cancel_config.return_value = chains

    adapter = MagicMock()
    adapter.fetch_mark_price.return_value = mark_price

    worker = UnfilledPriceWatcher(
        ops_db_path=db_path,
        chain_repo=repo,
        adapter=adapter,
        execution_account_id="acc1",
        interval_seconds=60,
    )
    return worker, db_path


def test_run_once_cancels_long_chain_when_price_above_tp(tmp_path):
    chain = _make_chain_mock(side="LONG", final_tp=120.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    count = worker.run_once()

    assert count == 1
    conn = sqlite3.connect(db_path)
    events = conn.execute(
        "SELECT event_type FROM ops_lifecycle_events WHERE trade_chain_id=1"
    ).fetchall()
    states = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()
    conn.close()
    assert any(e[0] == "UNFILLED_TP_CANCEL" for e in events)
    assert states[0] == "EXPIRED"


def test_run_once_does_not_cancel_long_chain_when_price_below_tp(tmp_path):
    chain = _make_chain_mock(side="LONG", final_tp=120.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=115.0)

    count = worker.run_once()

    assert count == 0
    conn = sqlite3.connect(db_path)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()
    assert state == "WAITING_ENTRY"


def test_run_once_cancels_short_chain_when_price_below_tp(tmp_path):
    chain = _make_chain_mock(side="SHORT", final_tp=80.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=75.0)

    count = worker.run_once()
    assert count == 1


def test_run_once_skips_when_cancel_pending_by_engine_false(tmp_path):
    chain = _make_chain_mock(final_tp=120.0, cancel_by_engine=False)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    count = worker.run_once()
    assert count == 0


def test_run_once_skips_when_threshold_is_none(tmp_path):
    chain = _make_chain_mock(final_tp=None)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    count = worker.run_once()
    assert count == 0


def test_run_once_idempotent_second_tick(tmp_path):
    chain = _make_chain_mock(side="LONG", final_tp=120.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    worker.run_once()
    # Second tick with same chain still in repo mock
    count2 = worker.run_once()
    # Idempotency key deduplicates — no new events
    conn = sqlite3.connect(db_path)
    event_count = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE event_type='UNFILLED_TP_CANCEL'"
    ).fetchone()[0]
    conn.close()
    assert event_count == 1  # still exactly 1, not 2
