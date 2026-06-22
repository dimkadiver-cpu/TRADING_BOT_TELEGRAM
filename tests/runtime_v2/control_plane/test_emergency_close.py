from __future__ import annotations
import json
import os
import sqlite3
import tempfile
import glob

from src.runtime_v2.control_plane.emergency_close import EmergencyCloseService
from src.runtime_v2.control_plane.status_queries import StatusQueries
from src.runtime_v2.control_plane.scope_resolver import QueryScope


def _make_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    # Applica le migration reali: ops_trade_chains + ops_execution_commands con lo
    # schema autentico (idempotency_key NOT NULL UNIQUE, updated_at NOT NULL, niente created_by).
    for sql_file in sorted(glob.glob("db/ops_migrations/*.sql")):
        with open(sql_file, encoding="utf-8") as f:
            conn.executescript(f.read())
    # INSERT con colonne esplicite: ops_trade_chains reale ha molte colonne NOT NULL
    # (source_enrichment_id UNIQUE, canonical_message_id, raw_message_id, entry_mode, ...).
    now = "2026-06-19T10:00:00+00:00"
    def _chain(cid, symbol, side, trader, state):
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, cid, cid, cid, trader, "demo_1", symbol, side, state, "limit", now, now),
        )
    _chain(1, "BTCUSDT", "LONG", "trader_a", "OPEN")
    _chain(2, "ETHUSDT", "SHORT", "trader_a", "PARTIALLY_CLOSED")
    _chain(3, "SOLUSDT", "LONG", "trader_a", "WAITING_ENTRY")
    _chain(4, "BNBUSDT", "SHORT", "trader_b", "OPEN")
    conn.commit()
    conn.close()
    return path


def test_get_open_for_close_returns_open_and_partially():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    result = q.get_open_for_close(scope)
    ids = {r.chain_id for r in result}
    assert 1 in ids  # OPEN
    assert 2 in ids  # PARTIALLY_CLOSED
    assert 3 not in ids  # WAITING_ENTRY escluso
    os.unlink(db)


def test_get_open_for_close_filters_by_trader():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=["trader_a"])
    result = q.get_open_for_close(scope)
    ids = {r.chain_id for r in result}
    assert 4 not in ids  # trader_b escluso
    os.unlink(db)


def test_get_waiting_for_cancel():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    result = q.get_waiting_for_cancel(scope)
    assert len(result) == 1
    assert result[0].chain_id == 3
    os.unlink(db)


def test_execute_close_inserts_close_full_with_raw_symbol_and_side():
    db = _make_db()
    q = StatusQueries(db)
    svc = EmergencyCloseService(db)
    candidates = q.get_open_for_close(QueryScope(account_id="demo_1", trader_ids=["trader_a"]))
    count = svc.execute_close(candidates, created_by="42")
    assert count == 2
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT command_type, trade_chain_id, payload_json "
        "FROM ops_execution_commands ORDER BY command_id"
    ).fetchall()
    conn.close()
    assert [(r[0], r[1]) for r in rows] == [("CLOSE_FULL", 1), ("CLOSE_FULL", 2)]
    p0 = json.loads(rows[0][2])
    assert p0["symbol"] == "BTCUSDT"  # raw, non "BTC/USDT"
    assert p0["side"] == "LONG"


def test_execute_cancel_expands_per_pending_entry_leg():
    db = _make_db()
    # chain 3 (WAITING_ENTRY) deve avere comandi PLACE_ENTRY con client_order_id reale
    conn = sqlite3.connect(db)
    now = "2026-06-19T10:00:00+00:00"
    for i, coid in enumerate(("tsb:leg1", "tsb:leg2"), start=1):
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(trade_chain_id, command_type, status, payload_json, client_order_id, "
            " idempotency_key, created_at, updated_at) "
            "VALUES (3,'PLACE_ENTRY','PENDING','{}',?,?,?,?)",
            (coid, f"place_entry:3:leg{i}", now, now),
        )
    conn.commit()
    conn.close()

    q = StatusQueries(db)
    svc = EmergencyCloseService(db)
    candidates = q.get_waiting_for_cancel(QueryScope(account_id="demo_1", trader_ids=None))
    count = svc.execute_cancel(candidates, created_by="42")
    assert count == 2  # una cancel per gamba
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT payload_json FROM ops_execution_commands WHERE command_type='CANCEL_PENDING_ENTRY'"
    ).fetchall()
    conn.close()
    coids = {json.loads(r[0])["entry_client_order_id"] for r in rows}
    assert coids == {"tsb:leg1", "tsb:leg2"}


def test_execute_close_empty_is_noop():
    db = _make_db()
    svc = EmergencyCloseService(db)
    count = svc.execute_close([], created_by="42")
    assert count == 0
    os.unlink(db)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import pytest

@pytest.fixture
def ops_db():
    path = _make_db()
    yield path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Safety: global unfiltered scope must be rejected
# ---------------------------------------------------------------------------

def test_close_all_refused_in_global_scope_without_filter(ops_db):
    """close_all deve rifiutare in global scope senza trader/account filter."""
    from src.runtime_v2.control_plane.emergency_close import (
        build_close_all_preview,
        GLOBAL_SCOPE_SAFETY_MSG,
    )
    global_scope = QueryScope(account_id=None, trader_ids=None)
    result = build_close_all_preview(global_scope, ops_db, trader_filter=None)
    assert result is not None
    assert GLOBAL_SCOPE_SAFETY_MSG in result


def test_close_all_allowed_in_global_scope_with_trader_filter(ops_db):
    """close_all è permesso in global scope se è presente un trader filter."""
    from src.runtime_v2.control_plane.emergency_close import (
        build_close_all_preview,
        GLOBAL_SCOPE_SAFETY_MSG,
    )
    global_scope = QueryScope(account_id=None, trader_ids=["trader_a"])
    result = build_close_all_preview(global_scope, ops_db, trader_filter="trader_a")
    # result può essere None (nessuna chain) ma NON il safety message
    if result is not None:
        assert GLOBAL_SCOPE_SAFETY_MSG not in result


def test_cancel_all_refused_in_global_scope_without_filter(ops_db):
    """cancel_all deve rifiutare in global scope senza trader/account filter."""
    from src.runtime_v2.control_plane.emergency_close import (
        build_cancel_all_preview,
        GLOBAL_SCOPE_SAFETY_MSG,
    )
    global_scope = QueryScope(account_id=None, trader_ids=None)
    result = build_cancel_all_preview(global_scope, ops_db, trader_filter=None)
    assert result is not None
    assert GLOBAL_SCOPE_SAFETY_MSG in result


def test_close_all_allowed_with_account_scope(ops_db):
    """close_all è permesso quando account_id è specificato (account scope)."""
    from src.runtime_v2.control_plane.emergency_close import (
        build_close_all_preview,
        GLOBAL_SCOPE_SAFETY_MSG,
    )
    account_scope = QueryScope(account_id="demo_1", trader_ids=None)
    result = build_close_all_preview(account_scope, ops_db, trader_filter=None)
    # Deve procedere (non safety message) — result può essere stringa con lista o None
    if result is not None:
        assert GLOBAL_SCOPE_SAFETY_MSG not in result
