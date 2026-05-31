# tests/runtime_v2/control_plane/test_outbox_writer.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.outbox_writer import (
    project_clean_log_for_chain,
    write_clean_log_event,
)


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_chain(conn, chain_id, symbol="BTC/USDT", side="LONG"):
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "trader_a", "main", symbol, side,
         "WAITING_ENTRY", "ONE_SHOT", "{}", "{}", "{}", now, now),
    )


def _seed_event(conn, chain_id, event_type, idem, payload=None):
    conn.execute(
        "INSERT OR IGNORE INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (chain_id, event_type, "test", json.dumps(payload or {}), idem, _now()),
    )


def test_write_clean_log_event_inserts_row(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SIGNAL_ACCEPTED",
            chain_id=145,
            payload={"symbol": "BTC/USDT", "side": "LONG"},
        )
    row = conn.execute(
        "SELECT destination, notification_type, status FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row == ("CLEAN_LOG", "SIGNAL_ACCEPTED", "PENDING")


def test_write_clean_log_event_dedupes(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(conn, notification_type="SIGNAL_ACCEPTED",
                              chain_id=145, payload={}, dedupe_key="k")
        write_clean_log_event(conn, notification_type="SIGNAL_ACCEPTED",
                              chain_id=145, payload={}, dedupe_key="k")
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 1


def test_projection_maps_signal_accepted(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 145)
        _seed_event(conn, 145, "SIGNAL_ACCEPTED", "sig_accepted:145")
        _seed_event(conn, 145, "TRADE_CHAIN_CREATED", "chain_created:145")
        project_clean_log_for_chain(conn, 145)
    rows = conn.execute(
        "SELECT notification_type FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    conn.close()
    # SIGNAL_ACCEPTED projected; TRADE_CHAIN_CREATED is policy=off
    assert [r[0] for r in rows] == ["SIGNAL_ACCEPTED"]


def test_projection_maps_fills(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 200)
        _seed_event(conn, 200, "ENTRY_FILLED", "entry_filled:200:1",
                    {"fill_price": 65020.0, "filled_qty": 0.004})
        _seed_event(conn, 200, "TP_FILLED", "tp_filled:200:2",
                    {"tp_level": 1, "is_final": False})
        _seed_event(conn, 200, "SL_FILLED", "sl_filled:200:3", {})
        project_clean_log_for_chain(conn, 200)
    types = {r[0] for r in conn.execute(
        "SELECT notification_type FROM ops_notification_outbox"
    ).fetchall()}
    conn.close()
    assert types == {"ENTRY_OPENED", "TP_FILLED", "SL_FILLED"}


def test_projection_is_idempotent(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 300)
        _seed_event(conn, 300, "SIGNAL_ACCEPTED", "sig_accepted:300")
        project_clean_log_for_chain(conn, 300)
        project_clean_log_for_chain(conn, 300)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 1


def test_projection_maps_entry_updated(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 400)
        _seed_event(conn, 400, "ENTRY_UPDATED", "entry_updated:400:1",
                    {"fill_price": 64500.0, "fill_qty": 0.002, "new_avg_entry": 64750.0})
        project_clean_log_for_chain(conn, 400)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "ENTRY_UPDATED"
    p = json.loads(row[1])
    assert p["fill_price"] == 64500.0
    assert p["new_avg_entry"] == 64750.0


def test_projection_maps_update_done(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 500)
        _seed_event(conn, 500, "UPDATE_DONE", "update_done:500:1",
                    {"applied_actions": ["U_MOVE_STOP"], "changed_fields": ["current_stop_price"]})
        project_clean_log_for_chain(conn, 500)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "UPDATE_DONE"
    p = json.loads(row[1])
    assert p["applied_actions"] == ["U_MOVE_STOP"]
    assert p["changed_fields"] == ["current_stop_price"]


def test_tp_final_payload_includes_final_result_and_pnl_fields(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 700)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET entry_avg_price=?, open_position_qty=?, filled_entry_qty=?, "
            "cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, allocated_margin=? "
            "WHERE trade_chain_id=?",
            (65000.0, 0.002, 0.01, 350.0, 5.75, 0.0, 1000.0, 700),
        )
        _seed_event(conn, 700, "TP_FILLED", "tp_final:700:1", {
            "tp_level": 3,
            "is_final": True,
            "fill_price": 71000.0,
            "filled_qty": 0.002,
            "exec_fee": 1.65,
            "closed_size": 0.002,
        })
        project_clean_log_for_chain(conn, 700)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    payload = json.loads(row[1])
    assert row[0] == "TP_FILLED_FINAL"
    assert payload["fill_price"] == 71000.0
    assert payload["fee"] == 1.65
    # LONG pnl: (71000 - 65000) * 0.002 = 12.0
    assert abs(payload["pnl"] - 12.0) < 0.001
    assert payload["final_result"] is not None
    assert payload["final_result"]["close_reason"] == "TAKE_PROFIT"
    assert payload["final_result"]["gross_pnl"] == 350.0


def test_projection_maps_pending_timeout_to_pending_entry_expired(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 600)
        _seed_event(conn, 600, "PENDING_TIMEOUT", "pending_timeout:600:1", {})
        project_clean_log_for_chain(conn, 600)
    row = conn.execute(
        "SELECT notification_type FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "PENDING_ENTRY_EXPIRED"


def test_pending_entry_cancelled_projects_entry_cancelled(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 800)
        _seed_event(conn, 800, "PENDING_ENTRY_CANCELLED", "pending_cancelled:800:1", {
            "sequence": 2,
            "price": 64000.0,
            "entry_type": "LIMIT",
            "cancel_reason": "trader_update",
        })
        project_clean_log_for_chain(conn, 800)
    row = conn.execute("SELECT notification_type FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "ENTRY_CANCELLED"


def test_pending_entry_cancelled_position_closed_is_filtered(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 801)
        _seed_event(conn, 801, "PENDING_ENTRY_CANCELLED", "pending_cancelled:801:1", {
            "sequence": 2,
            "cancel_reason": "position_closed",
        })
        project_clean_log_for_chain(conn, 801)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 0


def test_close_full_filled_on_protected_chain_projects_be_exit(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 900)
        conn.execute(
            "UPDATE ops_trade_chains SET be_protection_status='PROTECTED', "
            "entry_avg_price=65000.0, cumulative_gross_pnl=118.0, "
            "cumulative_fees=5.70, allocated_margin=10000.0 WHERE trade_chain_id=?",
            (900,),
        )
        _seed_event(conn, 900, "CLOSE_FULL_FILLED", "close_full:900:1", {
            "fill_price": 65020.0,
            "filled_qty": 0.01,
            "exec_fee": 1.70,
            "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 900)
    row = conn.execute("SELECT notification_type, payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "BE_EXIT"
    payload = json.loads(row[1])
    assert payload["close_reason"] == "BREAKEVEN_AFTER_TP"
    assert payload["exit_price"] == 65020.0
