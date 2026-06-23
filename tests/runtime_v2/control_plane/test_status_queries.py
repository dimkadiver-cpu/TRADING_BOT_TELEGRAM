# tests/runtime_v2/control_plane/test_status_queries.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import StatusQueries


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _apply_raw_messages_migration(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("db/migrations/006_raw_messages.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _add_chain(
    conn,
    cid,
    state,
    symbol="BTC/USDT",
    side="LONG",
    sl=None,
    account_id="main",
    raw_message_id=None,
):
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " current_stop_price, management_plan_json, risk_snapshot_json, plan_state_json, "
        " created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, cid, cid, raw_message_id or cid, "trader_a", account_id, symbol, side, state, "ONE_SHOT",
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


def test_status_reflects_scoped_blocks(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "INSERT INTO ops_control_state "
            "(scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) "
            "VALUES ('TRADER', 'trader_a', 'BLOCK_NEW_ENTRIES', 1, ?, ?)",
            (_now(), _now()),
        )
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_status()
    assert view.new_entries_enabled is True
    assert view.control_mode == "NONE"


def test_control_view_keeps_global_entries_enabled_for_scoped_blocks(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "INSERT INTO ops_control_state "
            "(scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) "
            "VALUES ('TRADER', 'trader_a', 'BLOCK_NEW_ENTRIES', 1, ?, ?)",
            (_now(), _now()),
        )
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_control()
    assert view.new_entries_enabled is True
    assert any(
        block.scope_type == "TRADER" and block.scope_value == "trader_a"
        for block in view.active_blocks
    )


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


def test_get_trade_detail_exposes_original_message_link_when_available(ops_db):
    _apply_raw_messages_migration(ops_db)
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 21, "OPEN", raw_message_id=2100)
        conn.execute(
            "INSERT INTO raw_messages "
            "(raw_message_id, source_chat_id, telegram_message_id, message_ts, acquired_at) "
            "VALUES (2100, '-1001234567890', 987, ?, ?)",
            (_now(), _now()),
        )
    conn.close()

    detail = StatusQueries(ops_db).get_trade(21)
    assert detail is not None
    assert detail.original_message_link == "https://t.me/c/1234567890/987"


def test_get_trade_detail_falls_back_to_planned_stop_when_current_stop_is_null(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 22, "WAITING_ENTRY", sl=None)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET management_plan_json='{\"stop_loss\": 62000.0}' "
            "WHERE trade_chain_id=22"
        )
    conn.close()

    detail = StatusQueries(ops_db).get_trade(22)
    assert detail is not None
    assert detail.current_stop_price == 62000.0


def test_get_pnl_uses_latest_account_snapshot(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 30, "OPEN")
        _add_chain(conn, 31, "PARTIALLY_CLOSED")
        _add_chain(conn, 32, "WAITING_ENTRY")
        conn.execute(
            "INSERT INTO ops_account_snapshots "
            "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
            " total_margin_used_usdt, source, captured_at, payload_json) "
            "VALUES ('main', 1000.0, 900.0, 50.0, 125.0, 'sync_old', ?, '{}')",
            ("2026-05-30T10:00:00+00:00",),
        )
        conn.execute(
            "INSERT INTO ops_account_snapshots "
            "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
            " total_margin_used_usdt, source, captured_at, payload_json) "
            "VALUES ('main', 1111.0, 888.0, 45.0, 120.0, 'sync_new', ?, '{}')",
            ("2026-05-30T10:05:00+00:00",),
        )
    conn.close()

    view = StatusQueries(ops_db).get_pnl()
    assert view.account_id == "main"
    assert view.equity_usdt == 1111.0
    assert view.available_balance_usdt == 888.0
    assert view.total_open_risk_usdt == 45.0
    assert view.total_margin_used_usdt == 120.0
    assert view.source == "sync_new"
    assert view.open_count == 1
    assert view.partial_count == 1
    assert view.waiting_entry_count == 1


def test_get_pnl_counts_only_latest_snapshot_account(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 50, "OPEN", account_id="main")
        _add_chain(conn, 51, "PARTIALLY_CLOSED", account_id="main")
        _add_chain(conn, 52, "WAITING_ENTRY", account_id="secondary")
        _add_chain(conn, 53, "OPEN", account_id="secondary")
        conn.execute(
            "INSERT INTO ops_account_snapshots "
            "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
            " total_margin_used_usdt, source, captured_at, payload_json) "
            "VALUES ('main', 1000.0, 900.0, 50.0, 125.0, 'sync_main', ?, '{}')",
            ("2026-05-30T10:05:00+00:00",),
        )
    conn.close()

    view = StatusQueries(ops_db).get_pnl()
    assert view.account_id == "main"
    assert view.open_count == 1
    assert view.partial_count == 1
    assert view.waiting_entry_count == 0


def test_get_pnl_without_snapshot_returns_counts_only(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 40, "OPEN")
    conn.close()

    view = StatusQueries(ops_db).get_pnl()
    assert view.account_id is None
    assert view.equity_usdt is None
    assert view.available_balance_usdt is None
    assert view.total_open_risk_usdt is None
    assert view.total_margin_used_usdt is None
    assert view.open_count == 1
    assert view.partial_count == 0
    assert view.waiting_entry_count == 0


def test_get_open_trades_reads_live_position_snapshot_by_account_symbol_side(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(
            conn,
            60,
            "OPEN",
            symbol="BTC/USDT:USDT",
            side="LONG",
            account_id="main",
        )
        conn.execute(
            "UPDATE ops_trade_chains SET cumulative_gross_pnl=?, cumulative_fees=? "
            "WHERE trade_chain_id=?",
            (30.0, 5.0, 60),
        )
        conn.execute(
            "INSERT INTO ops_position_snapshots "
            "(account_id, symbol, side, qty, mark_price, unrealized_pnl, "
            " cum_realized_pnl, source, captured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("main", "BTC/USDT:USDT", "LONG", 0.1, 65000.0, 500.0, 25.0,
             "bulk_position_sync", "2026-06-20T10:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO ops_position_snapshots "
            "(account_id, symbol, side, qty, mark_price, unrealized_pnl, "
            " cum_realized_pnl, source, captured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("main", "BTC/USDT:USDT", "SHORT", 0.2, 64000.0, -100.0, 5.0,
             "bulk_position_sync", "2026-06-20T10:01:00+00:00"),
        )
        conn.execute(
            "INSERT INTO ops_position_snapshots "
            "(account_id, symbol, side, qty, mark_price, unrealized_pnl, "
            " cum_realized_pnl, source, captured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("secondary", "BTC/USDT:USDT", "LONG", 0.3, 99999.0, 999.0, 99.0,
             "bulk_position_sync", "2026-06-20T10:02:00+00:00"),
        )
    conn.close()

    view = StatusQueries(ops_db).get_open_trades(QueryScope(account_id="main", trader_ids=None))

    assert len(view.rows) == 1
    row = view.rows[0]
    assert row.mark_price == pytest.approx(65000.0)
    assert row.unrealized_pnl == pytest.approx(500.0)
    # rPnL from chain: gross(30) - fees(5) - funding(0) = 25.0
    assert row.cum_realized_pnl == pytest.approx(25.0)
    assert row.mark_captured_at == "2026-06-20T10:00:00+00:00"


def test_get_open_trades_falls_back_to_calculated_upl_when_snapshot_has_no_upl(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(
            conn,
            61,
            "OPEN",
            symbol="ETH/USDT:USDT",
            side="SHORT",
            account_id="main",
        )
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET entry_avg_price=?, open_position_qty=? "
            "WHERE trade_chain_id=?",
            (3100.0, 1.0, 61),
        )
        conn.execute(
            "INSERT INTO ops_position_snapshots "
            "(account_id, symbol, side, qty, mark_price, unrealized_pnl, "
            " cum_realized_pnl, source, captured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("main", "ETH/USDT:USDT", "SHORT", 1.0, 3000.0, None, None,
             "bulk_position_sync", "2026-06-20T10:03:00+00:00"),
        )
    conn.close()

    view = StatusQueries(ops_db).get_open_trades(QueryScope(account_id="main", trader_ids=None))

    row = next(r for r in view.rows if r.chain_id == 61)
    assert row.mark_price == pytest.approx(3000.0)
    assert row.unrealized_pnl == pytest.approx(100.0)
    assert row.cum_realized_pnl == pytest.approx(0.0)


def test_get_open_trades_keeps_row_when_live_snapshot_missing(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(
            conn,
            62,
            "OPEN",
            symbol="SOL/USDT:USDT",
            side="LONG",
            account_id="main",
        )
    conn.close()

    view = StatusQueries(ops_db).get_open_trades(QueryScope(account_id="main", trader_ids=None))

    row = next(r for r in view.rows if r.chain_id == 62)
    assert row.mark_price is None
    assert row.unrealized_pnl is None
    assert row.cum_realized_pnl == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Task 1: TradeEvent + TradeDetail extended fields + StatusView.by_account
# ---------------------------------------------------------------------------

def test_trade_event_dataclass():
    from src.runtime_v2.control_plane.status_queries import TradeEvent
    ev = TradeEvent(
        label="SIGNAL ACCEPTED",
        timestamp="14 Jun 09:10:00",
        source="Signal",
        event_type=None,
        reason=None,
        clean_log_link=None,
    )
    assert ev.label == "SIGNAL ACCEPTED"
    assert ev.source == "Signal"
    assert ev.clean_log_link is None


def test_trade_detail_has_events_list(ops_db):
    from src.runtime_v2.control_plane.status_queries import StatusQueries, TradeEvent
    conn = sqlite3.connect(ops_db)
    _add_chain(conn, 99, "OPEN")
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (99, 'ENTRY_OPENED', 'system', '{}', 'k_ev99', '2024-06-14T09:10:00Z')"
    )
    conn.commit()
    conn.close()
    q = StatusQueries(ops_db)
    detail = q.get_trade(99)
    assert detail is not None
    assert hasattr(detail, "events")
    assert isinstance(detail.events, list)
    if detail.events:
        ev = detail.events[0]
        assert isinstance(ev, TradeEvent)
        assert ev.label  # not empty


def test_trade_detail_extended_fields(ops_db):
    from src.runtime_v2.control_plane.status_queries import StatusQueries
    conn = sqlite3.connect(ops_db)
    _add_chain(conn, 100, "OPEN", sl=62000.0)
    conn.commit()
    conn.close()
    q = StatusQueries(ops_db)
    detail = q.get_trade(100)
    assert detail is not None
    assert hasattr(detail, "is_actionable")
    assert hasattr(detail, "is_terminal")
    assert hasattr(detail, "has_be")
    assert hasattr(detail, "entry_legs")
    assert hasattr(detail, "tp_legs")
    assert isinstance(detail.is_actionable, bool)
    assert isinstance(detail.is_terminal, bool)


def test_trade_detail_last_events_backward_compat(ops_db):
    """last_events list[str] must still be present for backward compatibility."""
    from src.runtime_v2.control_plane.status_queries import StatusQueries
    conn = sqlite3.connect(ops_db)
    _add_chain(conn, 101, "OPEN")
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (101, 'ENTRY_OPENED', 'system', '{}', 'k_ev101', '2024-06-14T09:10:00Z')"
    )
    conn.commit()
    conn.close()
    q = StatusQueries(ops_db)
    detail = q.get_trade(101)
    assert detail is not None
    assert hasattr(detail, "last_events")
    assert isinstance(detail.last_events, list)


def test_status_view_has_by_account():
    from src.runtime_v2.control_plane.status_queries import StatusView
    sv = StatusView(
        updated_at="00:00:00",
        control_mode="NONE",
        new_entries_enabled=True,
        sync_age_seconds=None,
        open_count=0,
        partial_count=0,
        waiting_entry_count=0,
        review_count=0,
        pending_commands=0,
        failed_commands=0,
        no_sl_count=0,
    )
    assert hasattr(sv, "by_account")
    assert sv.by_account is None


def test_get_status_by_account(ops_db):
    from src.runtime_v2.control_plane.status_queries import StatusQueries
    conn = sqlite3.connect(ops_db)
    _add_chain(conn, 200, "OPEN", account_id="acc_a")
    _add_chain(conn, 201, "OPEN", account_id="acc_a")
    _add_chain(conn, 202, "WAITING_ENTRY", account_id="acc_a")
    _add_chain(conn, 203, "OPEN", account_id="acc_b")
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(trade_chain_id, command_type, status, idempotency_key, created_at, updated_at) "
        "VALUES (200,'PLACE_ENTRY','FAILED','k200',?,?)", (_now(), _now()),
    )
    conn.commit()
    conn.close()
    q = StatusQueries(ops_db)
    result = q.get_status_by_account(["acc_a", "acc_b"])
    assert isinstance(result, list)
    assert len(result) == 2
    acc_a = next(r for r in result if r["account_id"] == "acc_a")
    acc_b = next(r for r in result if r["account_id"] == "acc_b")
    assert acc_a["open_count"] == 2
    assert acc_a["waiting_count"] == 1
    assert acc_a["failed_commands"] == 1
    assert acc_b["open_count"] == 1
    assert acc_b["waiting_count"] == 0


# ---------------------------------------------------------------------------
# Gap #3: real health probes — workers should reflect table staleness
# ---------------------------------------------------------------------------

def test_get_health_lifecycle_gate_warns_when_stale(ops_db):
    """Lifecycle gate returns WARNING when ops_lifecycle_events has no recent entry."""
    import sqlite3
    # Insert a lifecycle event with a timestamp 10 minutes old (600s) — past any reasonable threshold
    old_ts = "2000-01-01T00:00:00+00:00"
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, idempotency_key, created_at) "
        "VALUES (1,'OPEN_TRADE','manual','k1',?)",
        (old_ts,),
    )
    conn.commit()
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_health()
    lifecycle_worker = next(w for w in view.workers if "Lifecycle" in w[0] or "lifecycle" in w[0].lower())
    assert lifecycle_worker[1] == "WARNING", (
        f"Lifecycle gate should be WARNING when last event is stale, got {lifecycle_worker[1]}"
    )


def test_get_health_execution_worker_warns_when_stale(ops_db):
    """Execution worker returns WARNING when ops_execution_commands has no recent update."""
    import sqlite3
    old_ts = "2000-01-01T00:00:00+00:00"
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(trade_chain_id, command_type, status, idempotency_key, created_at, updated_at) "
        "VALUES (1,'PLACE_ENTRY','DONE','k1',?,?)",
        (old_ts, old_ts),
    )
    conn.commit()
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_health()
    exec_worker = next(w for w in view.workers if "Execution" in w[0])
    assert exec_worker[1] == "WARNING", (
        f"Execution worker should be WARNING when last command update is stale, got {exec_worker[1]}"
    )


def test_get_health_workers_ok_when_recent(ops_db):
    """Workers return OK when events are recent (just now)."""
    import sqlite3
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, idempotency_key, created_at) "
        "VALUES (1,'OPEN_TRADE','manual','k1',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(trade_chain_id, command_type, status, idempotency_key, created_at, updated_at) "
        "VALUES (1,'PLACE_ENTRY','DONE','k1',?,?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_health()
    lifecycle_worker = next(w for w in view.workers if "Lifecycle" in w[0] or "lifecycle" in w[0].lower())
    exec_worker = next(w for w in view.workers if "Execution" in w[0])
    assert lifecycle_worker[1] == "OK"
    assert exec_worker[1] == "OK"


# ---------------------------------------------------------------------------
# Task 9: PnlView snapshot freshness + CTE per-account global scope
# ---------------------------------------------------------------------------

def _add_snapshot(conn, account_id, equity, captured_at, status="OK"):
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        "total_margin_used_usdt, account_unrealized_pnl_usdt, source, captured_at, "
        "payload_json, snapshot_status, error_code) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, equity, equity * 0.9, 5.0, 10.0, equity * 0.01,
         "ccxt_bybit:demo", captured_at, "{}", status, None),
    )


def test_get_pnl_global_uses_cte_per_account(ops_db):
    """Globale deve restituire latest snapshot per OGNI account, non LIMIT 1 globale."""
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 100, "OPEN", account_id="demo_1")
        _add_chain(conn, 101, "OPEN", account_id="demo_2")
        _add_snapshot(conn, "demo_1", 1000.0, "2026-06-23T10:00:00+00:00")
        _add_snapshot(conn, "demo_2", 2000.0, "2026-06-23T09:00:00+00:00")  # più vecchio

    view = StatusQueries(ops_db).get_pnl()
    # Entrambi gli account devono apparire in by_account
    assert view.by_account is not None
    accs = {r["account_id"] for r in view.by_account}
    assert "demo_1" in accs
    assert "demo_2" in accs


def test_get_pnl_global_excludes_stale_from_aggregate(ops_db):
    """Account stale non contribuisce ai totali live dell'aggregato."""
    from datetime import timedelta
    fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, fresh_time)
        _add_snapshot(conn, "demo_2", 500.0,  stale_time)

    view = StatusQueries(ops_db).get_pnl()
    assert view.accounts_fresh == 1
    assert view.accounts_stale == 1
    # Il totale equity deve includere solo demo_1
    assert view.equity_usdt == pytest.approx(1000.0)


def test_get_pnl_account_scope_includes_unrealized_pnl(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, "2026-06-23T10:00:00+00:00")

    view = StatusQueries(ops_db).get_pnl(
        scope=QueryScope(account_id="demo_1", trader_ids=None)
    )
    assert view.account_unrealized_pnl_usdt == pytest.approx(10.0)  # 1000 * 0.01


def test_get_pnl_account_scope_returns_snapshot_age(ops_db):
    from datetime import timedelta
    recent = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, recent)

    view = StatusQueries(ops_db).get_pnl(
        scope=QueryScope(account_id="demo_1", trader_ids=None)
    )
    assert view.snapshot_age_seconds is not None
    assert 40 < view.snapshot_age_seconds < 60


def test_get_pnl_stale_snapshot_sets_flag(ops_db):
    from datetime import timedelta
    old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, old)

    view = StatusQueries(ops_db).get_pnl(
        scope=QueryScope(account_id="demo_1", trader_ids=None)
    )
    assert view.snapshot_stale is True
