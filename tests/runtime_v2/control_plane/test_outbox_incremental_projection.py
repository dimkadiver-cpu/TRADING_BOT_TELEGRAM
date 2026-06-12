from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _make_db() -> sqlite3.Connection:
    """In-memory DB with the real ops migrations applied.

    The previous hand-rolled schema drifted from production (missing columns
    added by later migrations); applying db/ops_migrations keeps the fixture
    aligned by construction.
    """
    conn = sqlite3.connect(":memory:")
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_chain(conn, chain_id=1):
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "trader_a", "main", "BTCUSDT", "LONG",
         "WAITING_ENTRY", "ONE_SHOT", "{}", "{}", "{}", now, now),
    )
    conn.commit()


def _insert_event(conn, chain_id, event_type, idem, payload=None):
    cursor = conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (chain_id, event_type, "test", json.dumps(payload or {}), idem, _now()),
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
