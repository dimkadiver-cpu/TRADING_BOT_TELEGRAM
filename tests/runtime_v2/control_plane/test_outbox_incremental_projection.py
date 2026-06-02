from __future__ import annotations

import json
import sqlite3


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
    CREATE TABLE ops_trade_chains (
        trade_chain_id INTEGER PRIMARY KEY,
        symbol TEXT DEFAULT 'BTCUSDT',
        side TEXT DEFAULT 'LONG',
        entry_mode TEXT,
        trader_id TEXT,
        plan_state_json TEXT DEFAULT '{}',
        risk_snapshot_json TEXT DEFAULT '{}',
        entry_avg_price REAL,
        current_stop_price REAL,
        source_chat_id TEXT,
        telegram_message_id TEXT,
        cumulative_gross_pnl REAL,
        cumulative_fees REAL,
        cumulative_funding REAL,
        allocated_margin REAL,
        filled_entry_qty REAL,
        open_position_qty REAL,
        be_protection_status TEXT DEFAULT 'NOT_PROTECTED',
        last_projected_event_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE ops_lifecycle_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_chain_id INTEGER,
        event_type TEXT,
        source_type TEXT DEFAULT 'test',
        previous_state TEXT,
        next_state TEXT,
        source_id TEXT,
        payload_json TEXT DEFAULT '{}',
        idempotency_key TEXT UNIQUE,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE ops_notification_outbox (
        outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
        notification_type TEXT,
        destination TEXT,
        payload_json TEXT,
        priority TEXT,
        status TEXT DEFAULT 'PENDING',
        dedupe_key TEXT UNIQUE,
        attempts INTEGER DEFAULT 0,
        created_at TEXT,
        send_after TEXT,
        aggregation_group TEXT,
        source_message_id TEXT
    );
    """)
    return conn


def _insert_chain(conn, chain_id=1):
    conn.execute("INSERT INTO ops_trade_chains (trade_chain_id) VALUES (?)", (chain_id,))
    conn.commit()


def _insert_event(conn, chain_id, event_type, idem, payload=None):
    cursor = conn.execute(
        "INSERT INTO ops_lifecycle_events (trade_chain_id, event_type, payload_json, idempotency_key) "
        "VALUES (?,?,?,?)",
        (chain_id, event_type, json.dumps(payload or {}), idem),
    )
    conn.commit()
    return cursor.lastrowid


def test_projection_updates_last_projected_event_id():
    """After projecting 3 events, last_projected_event_id == max(event_ids)."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    conn = _make_db()
    _insert_chain(conn, 1)
    id1 = _insert_event(conn, 1, "SIGNAL_ACCEPTED", "sig:1",
                        {"source": "msg", "entries": [], "sl": None, "tps": []})
    id2 = _insert_event(conn, 1, "ENTRY_FILLED", "entry:1",
                        {"fill_price": 50000.0, "filled_qty": 0.01, "source": "exchange"})
    id3 = _insert_event(conn, 1, "TP_FILLED", "tp:1",
                        {"tp_level": 1, "fill_price": 55000.0, "filled_qty": 0.01,
                         "is_final": True, "exec_fee": 0.3, "closed_size": 0.01, "source": "exchange"})
    project_clean_log_for_chain(conn, 1)
    row = conn.execute(
        "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()
    assert row[0] == max(id1, id2, id3)


def test_incremental_projection_only_processes_new_events():
    """After initial projection of 10 events, re-projecting with event 11 only processes event 11."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    conn = _make_db()
    _insert_chain(conn, 2)
    for i in range(10):
        _insert_event(conn, 2, "SIGNAL_ACCEPTED", f"sig:2:{i}",
                      {"source": "msg", "entries": [], "sl": None, "tps": []})

    # First full projection
    project_clean_log_for_chain(conn, 2)
    outbox_count_after_first = conn.execute(
        "SELECT COUNT(*) FROM ops_notification_outbox"
    ).fetchone()[0]

    # Add event 11
    id11 = _insert_event(conn, 2, "SIGNAL_ACCEPTED", "sig:2:10",
                         {"source": "msg", "entries": [], "sl": None, "tps": []})

    # Second projection — should only process event 11
    written = project_clean_log_for_chain(conn, 2)
    outbox_count_after_second = conn.execute(
        "SELECT COUNT(*) FROM ops_notification_outbox"
    ).fetchone()[0]
    assert written == 1
    assert outbox_count_after_second == outbox_count_after_first + 1
    new_cursor = conn.execute(
        "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=2"
    ).fetchone()[0]
    assert new_cursor == id11


def test_re_projection_after_reset_no_duplicates():
    """Reset cursor to 0 → re-project all events → idempotency prevents new outbox rows."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    conn = _make_db()
    _insert_chain(conn, 3)
    for i in range(3):
        _insert_event(conn, 3, "SIGNAL_ACCEPTED", f"sig:3:{i}",
                      {"source": "msg", "entries": [], "sl": None, "tps": []})

    project_clean_log_for_chain(conn, 3)
    count_first = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]

    # Reset cursor
    conn.execute("UPDATE ops_trade_chains SET last_projected_event_id=0 WHERE trade_chain_id=3")
    conn.commit()

    project_clean_log_for_chain(conn, 3)
    count_second = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    # dedupe_key prevents duplicates
    assert count_second == count_first
