# tests/runtime_v2/control_plane/test_status_queries.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.status_queries import StatusQueries


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _add_chain(conn, cid, state, symbol="BTC/USDT", side="LONG", sl=None):
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " current_stop_price, management_plan_json, risk_snapshot_json, plan_state_json, "
        " created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, cid, cid, cid, "trader_a", "main", symbol, side, state, "ONE_SHOT",
         sl, "{}", "{}", "{}", now, now),
    )


def test_status_counts(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 1, "OPEN", sl=62000.0)
        _add_chain(conn, 2, "OPEN", sl=None)          # no SL
        _add_chain(conn, 3, "WAITING_ENTRY")
        _add_chain(conn, 4, "PARTIALLY_CLOSED", sl=100.0)
        _add_chain(conn, 5, "REVIEW_REQUIRED")
        _add_chain(conn, 6, "CLOSED")
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(trade_chain_id, command_type, status, idempotency_key, created_at, updated_at) "
            "VALUES (1,'PLACE_ENTRY','PENDING','k1',?,?)", (_now(), _now()),
        )
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(trade_chain_id, command_type, status, idempotency_key, created_at, updated_at) "
            "VALUES (2,'PLACE_ENTRY','FAILED','k2',?,?)", (_now(), _now()),
        )
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_status()
    assert view.open_count == 2          # OPEN x2
    assert view.partial_count == 1       # PARTIALLY_CLOSED
    assert view.waiting_entry_count == 1
    assert view.review_count == 1
    assert view.pending_commands == 1
    assert view.failed_commands == 1
    assert view.no_sl_count == 1         # chain 2 OPEN without SL


def test_control_view_blocks_and_blacklist(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "INSERT INTO ops_control_state "
            "(scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) "
            "VALUES ('GLOBAL', NULL, 'BLOCK_NEW_ENTRIES', 1, ?, ?)", (_now(), _now()),
        )
        conn.execute(
            "INSERT INTO ops_config_overrides "
            "(override_key, scope_type, scope_value, value_json, created_by, active, created_at, updated_at) "
            "VALUES ('symbol_blacklist.global','GLOBAL',NULL,'[\"BTCUSDT\"]','42',1,?,?)",
            (_now(), _now()),
        )
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_control()
    assert view.new_entries_enabled is False
    assert any(b.scope_type == "GLOBAL" for b in view.active_blocks)
    assert "BTCUSDT" in view.blacklist_global


def test_reviews(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 10, "REVIEW_REQUIRED", symbol="SOL/USDT")
        conn.execute(
            "INSERT INTO ops_lifecycle_events "
            "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
            "VALUES (10,'REVIEW_REQUIRED','enrichment','{\"reason\": \"missing_sl\"}','r10',?)",
            (_now(),),
        )
    conn.close()
    q = StatusQueries(ops_db)
    items = q.get_reviews().items
    assert any(it.chain_id == 10 for it in items)


def test_get_trade_detail(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 20, "OPEN", symbol="ETH/USDT", side="SHORT", sl=3500.0)
    conn.close()
    q = StatusQueries(ops_db)
    detail = q.get_trade(20)
    assert detail is not None
    assert detail.symbol == "ETH/USDT"
    assert detail.side == "SHORT"
    assert q.get_trade(999) is None
