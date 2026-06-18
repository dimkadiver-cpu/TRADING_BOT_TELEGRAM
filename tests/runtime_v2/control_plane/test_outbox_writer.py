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


def test_entry_opened_first_full_leg_of_multi_entry_is_not_marked_partial(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 401)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET entry_mode=?, plan_state_json=?, risk_snapshot_json=?, "
            "entry_avg_price=?, current_stop_price=?, filled_entry_qty=?, initial_risk_amount=? "
            "WHERE trade_chain_id=?",
            (
                "SCALED",
                json.dumps({
                    "legs": [
                        {"sequence": 1, "entry_type": "MARKET", "price": 65020.0},
                        {"sequence": 2, "entry_type": "LIMIT", "price": 64000.0},
                    ],
                }),
                json.dumps({
                    "legs": [
                        {"sequence": 1, "qty": 0.007},
                        {"sequence": 2, "qty": 0.003},
                    ],
                    "open_fee_residual": 0.91,
                }),
                65020.0,
                62000.0,
                0.007,
                50.0,
                401,
            ),
        )
        _seed_event(conn, 401, "ENTRY_FILLED", "entry_filled:401:1", {
            "fill_price": 65020.0,
            "filled_qty": 0.007,
            "fill_qty": 0.007,
            "filled_leg_sequence": 1,
            "exec_fee": 0.91,
        })
        project_clean_log_for_chain(conn, 401)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "ENTRY_OPENED"
    payload = json.loads(row[1])
    assert payload["entry_type_for_leg"] == "MARKET"
    assert payload["is_partial_leg"] is False
    assert payload["_leg_fill_pct"] is None
    assert payload["actual_risk_usdt"] == pytest.approx(21.14)
    assert payload["planned_risk_usdt"] == 50.0


def test_projection_maps_update_done(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 500)
        _seed_event(conn, 500, "UPDATE_DONE", "update_done:500:1",
                    {"applied_actions": ["U_MOVE_STOP"], "changed": [{"field": "SL", "old": 100.0, "new": 110.0}]})
        project_clean_log_for_chain(conn, 500)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "UPDATE_DONE"
    p = json.loads(row[1])
    assert p["applied_actions"] == ["U_MOVE_STOP"]
    assert p["changed"] == [{"field": "SL", "old": 100.0, "new": 110.0}]


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
            "fee_rate": 0.00055,
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
    assert payload["closed_qty"] == 0.002
    assert payload["fee"] == 1.65
    assert payload["fee_rate"] == 0.00055
    # LONG pnl: (71000 - 65000) * 0.002 = 12.0
    assert abs(payload["pnl"] - 12.0) < 0.001
    assert payload["final_result"] is not None
    assert payload["final_result"]["close_reason"] == "TAKE_PROFIT"
    assert payload["final_result"]["gross_pnl"] == 350.0


def test_tp_filled_payload_includes_remaining_section_fields(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 701)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET entry_avg_price=?, current_stop_price=?, open_position_qty=?, filled_entry_qty=? "
            "WHERE trade_chain_id=?",
            (0.3662, 0.3662, 3365.0, 6730.0, 701),
        )
        _seed_event(conn, 701, "TP_FILLED", "tp_partial:701:1", {
            "tp_level": 1,
            "fill_price": 0.3841,
            "filled_qty": 3365.0,
            "closed_size": 3365.0,
            "exec_fee": 1.42,
            "fee_rate": 0.0011,
            "source": "exchange",
        })
        project_clean_log_for_chain(conn, 701)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "TP_FILLED"
    payload = json.loads(row[1])
    assert payload["remaining_qty"] == 3365.0
    assert payload["avg_entry"] == 0.3662
    assert payload["remaining_risk"] == pytest.approx(0.0)


def test_position_closed_final_result_subtracts_positive_funding_cost(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 710)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET entry_avg_price=?, open_position_qty=?, filled_entry_qty=?, "
            "cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, allocated_margin=? "
            "WHERE trade_chain_id=?",
            (0.2532, 0.0, 6042.0, 4.2294, 1.68514401, 0.07628025, 200.0, 710),
        )
        _seed_event(conn, 710, "CLOSE_FULL_FILLED", "close_full:710:1", {
            "fill_price": 0.2539,
            "filled_qty": 6042.0,
            "exec_fee": 0.84373509,
            "fee_rate": 0.00055,
            "closed_size": 6042.0,
            "close_reason": "BOT_COMMAND",
            "source_message_link": "https://t.me/c/3927267771/376",
        })
        project_clean_log_for_chain(conn, 710)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "POSITION_CLOSED"
    payload = json.loads(row[1])
    assert payload["closed_qty"] == 6042.0
    assert payload["fee_rate"] == 0.00055
    assert payload["link"] == "https://t.me/c/3927267771/376"
    assert payload["final_result"]["funding"] == -0.07628025
    assert payload["final_result"]["total_pnl_net"] == pytest.approx(2.46797574)


def test_position_closed_final_result_subtracts_positive_funding_cost_for_raw_symbol_chain(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 719, symbol="FIDAUSDT")
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET entry_avg_price=?, open_position_qty=?, filled_entry_qty=?, "
            "cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, allocated_margin=? "
            "WHERE trade_chain_id=?",
            (0.2532, 0.0, 6042.0, 4.2294, 1.68514401, 0.07628025, 200.0, 719),
        )
        _seed_event(conn, 719, "CLOSE_FULL_FILLED", "close_full:719:1", {
            "fill_price": 0.2539,
            "filled_qty": 6042.0,
            "exec_fee": 0.84373509,
            "fee_rate": 0.00055,
            "closed_size": 6042.0,
            "close_reason": "BOT_COMMAND",
        })
        project_clean_log_for_chain(conn, 719)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "POSITION_CLOSED"
    payload = json.loads(row[1])
    assert payload["symbol"] == "FIDAUSDT"
    assert payload["final_result"]["funding"] == -0.07628025
    assert payload["final_result"]["total_pnl_net"] == pytest.approx(2.46797574)


def test_position_closed_final_result_uses_peak_margin_and_return_on_risk(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 712)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, "
            "peak_margin_used=?, initial_risk_amount=? "
            "WHERE trade_chain_id=?",
            (46.73088, 6.33921077, 0.0, 571.62, 200.0, 712),
        )
        _seed_event(conn, 712, "CLOSE_FULL_FILLED", "close_full:712:1", {
            "fill_price": 0.2539,
            "filled_qty": 6042.0,
            "exec_fee": 0.84373509,
            "closed_size": 6042.0,
        })
        project_clean_log_for_chain(conn, 712)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    payload = json.loads(row[0])
    final_result = payload["final_result"]
    assert final_result["total_pnl_net"] == pytest.approx(40.39166923)
    assert final_result["roi_net_pct"] == pytest.approx(7.0667, rel=1e-3)
    assert final_result["return_on_risk_pct"] == pytest.approx(20.1958, rel=1e-3)


def test_position_closed_final_result_keeps_roi_none_when_peak_margin_missing(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 713)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, "
            "peak_margin_used=?, initial_risk_amount=? "
            "WHERE trade_chain_id=?",
            (10.0, 1.0, 0.0, None, 50.0, 713),
        )
        _seed_event(conn, 713, "CLOSE_FULL_FILLED", "close_full:713:1", {"filled_qty": 1.0})
        project_clean_log_for_chain(conn, 713)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    final_result = json.loads(row[0])["final_result"]
    assert final_result["roi_net_pct"] is None
    assert final_result["return_on_risk_pct"] == pytest.approx(18.0)


def test_tp_filled_final_result_uses_peak_margin_and_return_on_risk(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 714)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, "
            "peak_margin_used=?, initial_risk_amount=? "
            "WHERE trade_chain_id=?",
            (46.73088, 6.33921077, 0.0, 571.62, 200.0, 714),
        )
        _seed_event(conn, 714, "TP_FILLED", "tp_final:714:1", {
            "tp_level": 1,
            "is_final": True,
            "fill_price": 0.2539,
            "filled_qty": 6042.0,
            "exec_fee": 0.84373509,
            "closed_size": 6042.0,
        })
        project_clean_log_for_chain(conn, 714)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "TP_FILLED_FINAL"
    final_result = json.loads(row[1])["final_result"]
    assert final_result["close_reason"] == "TAKE_PROFIT"
    assert final_result["total_pnl_net"] == pytest.approx(40.39166923)
    assert final_result["roi_net_pct"] == pytest.approx(7.0667, rel=1e-3)
    assert final_result["return_on_risk_pct"] == pytest.approx(20.1958, rel=1e-3)


def test_sl_filled_final_result_uses_peak_margin_and_return_on_risk(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 715)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET be_protection_status='NOT_PROTECTED', cumulative_gross_pnl=?, "
            "cumulative_fees=?, cumulative_funding=?, peak_margin_used=?, "
            "initial_risk_amount=? WHERE trade_chain_id=?",
            (46.73088, 6.33921077, 0.0, 571.62, 200.0, 715),
        )
        _seed_event(conn, 715, "SL_FILLED", "sl_filled:715:1", {
            "fill_price": 0.2539,
            "filled_qty": 6042.0,
            "exec_fee": 0.84373509,
            "closed_size": 6042.0,
        })
        project_clean_log_for_chain(conn, 715)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "SL_FILLED"
    final_result = json.loads(row[1])["final_result"]
    assert final_result["close_reason"] == "STOP_LOSS"
    assert final_result["total_pnl_net"] == pytest.approx(40.39166923)
    assert final_result["roi_net_pct"] == pytest.approx(7.0667, rel=1e-3)
    assert final_result["return_on_risk_pct"] == pytest.approx(20.1958, rel=1e-3)


def test_be_exit_final_result_uses_peak_margin_and_return_on_risk(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 716)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET be_protection_status='PROTECTED', cumulative_gross_pnl=?, "
            "cumulative_fees=?, cumulative_funding=?, peak_margin_used=?, "
            "initial_risk_amount=? WHERE trade_chain_id=?",
            (46.73088, 6.33921077, 0.0, 571.62, 200.0, 716),
        )
        _seed_event(conn, 716, "CLOSE_FULL_FILLED", "close_full:716:1", {
            "fill_price": 0.2539,
            "filled_qty": 6042.0,
            "exec_fee": 0.84373509,
            "closed_size": 6042.0,
        })
        project_clean_log_for_chain(conn, 716)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "BE_EXIT"
    final_result = json.loads(row[1])["final_result"]
    assert final_result["close_reason"] == "BREAKEVEN_AFTER_TP"
    assert final_result["total_pnl_net"] == pytest.approx(40.39166923)
    assert final_result["roi_net_pct"] == pytest.approx(7.0667, rel=1e-3)
    assert final_result["return_on_risk_pct"] == pytest.approx(20.1958, rel=1e-3)


def test_position_closed_final_result_preserves_missing_metrics_as_none(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 711)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET entry_avg_price=?, open_position_qty=?, filled_entry_qty=?, "
            "cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, allocated_margin=? "
            "WHERE trade_chain_id=?",
            (65000.0, 0.0, 0.01, None, None, None, None, 711),
        )
        _seed_event(conn, 711, "CLOSE_FULL_FILLED", "close_full:711:1", {
            "fill_price": 65500.0,
            "filled_qty": 0.01,
            "exec_fee": 0.90,
            "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 711)
    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    payload = json.loads(row[0])
    assert payload["final_result"]["roi_net_pct"] is None
    assert payload["final_result"]["total_pnl_net"] is None
    assert payload["final_result"]["gross_pnl"] is None
    assert payload["final_result"]["fees"] is None
    assert payload["final_result"]["funding"] is None


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


def test_tp_filled_outbox_has_no_delay_and_no_group(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="TP_FILLED",
            chain_id=145,
            payload={"chain_id": 145},
            dedupe_key="clean:tp:145:1",
        )
    row = conn.execute(
        "SELECT send_after, aggregation_group FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    now = datetime.now(timezone.utc).isoformat()
    assert row[0] is None or row[0] <= now or row[0][:19] == now[:19], "TP_FILLED must not have future send_after"
    assert row[1] is None, "TP_FILLED must not have aggregation_group"


def test_high_priority_clean_log_has_send_after_set(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SL_FILLED",
            chain_id=145,
            payload={"chain_id": 145},
            dedupe_key="clean:sl:145:1",
        )
    row = conn.execute("SELECT send_after FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row[0] is not None, "send_after must be set"


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


def test_close_full_filled_exchange_manual_on_protected_chain_projects_position_closed(ops_db):
    # Regression: manual close from exchange UI with source="exchange_manual" on a
    # PROTECTED chain must NOT be promoted to BE_EXIT.
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1104)
        conn.execute(
            "UPDATE ops_trade_chains SET be_protection_status='PROTECTED', "
            "entry_avg_price=65000.0, cumulative_gross_pnl=118.0, "
            "cumulative_fees=5.70, allocated_margin=10000.0 WHERE trade_chain_id=?",
            (1104,),
        )
        _seed_event(conn, 1104, "CLOSE_FULL_FILLED", "close_full:1104:1", {
            "fill_price": 65020.0,
            "filled_qty": 0.01,
            "exec_fee": 1.70,
            "closed_size": 0.01,
            "source": "exchange_manual",
        })
        project_clean_log_for_chain(conn, 1104)
    row = conn.execute("SELECT notification_type, payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "POSITION_CLOSED"
    payload = json.loads(row[1])
    assert payload.get("close_reason") != "BREAKEVEN_AFTER_TP"


def test_sl_filled_on_unprotected_chain_projects_stop_loss(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 901)
        conn.execute(
            "UPDATE ops_trade_chains SET be_protection_status='NOT_PROTECTED', "
            "entry_avg_price=65000.0, cumulative_gross_pnl=-12.0, "
            "cumulative_fees=1.80 WHERE trade_chain_id=?",
            (901,),
        )
        _seed_event(conn, 901, "SL_FILLED", "sl_filled:901:1", {
            "fill_price": 64880.0,
            "filled_qty": 0.01,
            "exec_fee": 0.90,
            "fee_rate": 0.00055,
            "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 901)
    row = conn.execute(
        "SELECT notification_type, payload_json "
        "FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "SL_FILLED"
    payload = json.loads(row[1])
    assert payload["close_reason"] == "STOP_LOSS"
    assert payload["sl_price"] == 64880.0
    assert payload["closed_qty"] == 0.01
    assert payload["fee_rate"] == 0.00055


def test_sl_filled_on_protected_chain_projects_be_close_reason(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 902)
        conn.execute(
            "UPDATE ops_trade_chains SET be_protection_status='PROTECTED', "
            "entry_avg_price=65000.0, cumulative_gross_pnl=-0.20, "
            "cumulative_fees=1.70 WHERE trade_chain_id=?",
            (902,),
        )
        _seed_event(conn, 902, "SL_FILLED", "sl_filled:902:1", {
            "fill_price": 65000.0,
            "filled_qty": 0.01,
            "exec_fee": 1.70,
            "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 902)
    row = conn.execute(
        "SELECT notification_type, payload_json "
        "FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "SL_FILLED"
    payload = json.loads(row[1])
    assert payload["close_reason"] == "BREAKEVEN_AFTER_TP"
    assert payload["sl_price"] == 65000.0


def test_entry_cancel_failed_projects_cancel_failed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 950)
        _seed_event(conn, 950, "ENTRY_CANCEL_FAILED", "entry_cancel_failed:950:1", {
            "entry_ref": "Entry_2",
            "entry_price": 64000.0,
            "attempts": 3,
            "source": "timeout_worker",
        })
        project_clean_log_for_chain(conn, 950)
    row = conn.execute("SELECT notification_type FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "CANCEL_FAILED"


# ── cancel_origin filter tests ────────────────────────────────────────────────
#
# Caso 1: trader manda "убираем лимитки" → UPDATE_DONE già mostra Entry_2 cancelled.
#         La conferma exchange (PENDING_ENTRY_CANCELLED) deve essere soppressa.
def test_entry_cancelled_trader_update_no_partial_fill_is_suppressed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1001)
        _seed_event(conn, 1001, "PENDING_ENTRY_CANCELLED", "pec:1001:1", {
            "sequence": 2,
            "price": 61192.03,
            "entry_type": "LIMIT",
            "cancel_origin": "trader_update",
        })
        project_clean_log_for_chain(conn, 1001)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 0, "trader_update cancel senza partial fill deve essere soppressa"


# Caso 2: trader cancella Entry_2 che aveva già 35% di fill parziale.
#         L'info del fill parziale è operativamente rilevante → ENTRY_CANCELLED visibile.
def test_entry_cancelled_trader_update_with_partial_fill_is_shown(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1002)
        _seed_event(conn, 1002, "PENDING_ENTRY_CANCELLED", "pec:1002:1", {
            "sequence": 2,
            "price": 61192.03,
            "entry_type": "LIMIT",
            "cancel_origin": "trader_update",
            "partial_fill_pct": 35.0,
            "partial_fill_qty": 0.002,
        })
        project_clean_log_for_chain(conn, 1002)
    row = conn.execute("SELECT notification_type FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row is not None and row[0] == "ENTRY_CANCELLED", (
        "trader_update cancel con partial fill deve essere visibile"
    )


# Caso 3: timeout_worker scade Entry_2 dopo 24h (cancel_averaging_pending_after o pending_timeout_hours).
#         PENDING_ENTRY_EXPIRED già notifica la scadenza — ENTRY_CANCELLED è rumore.
def test_entry_cancelled_timeout_worker_is_suppressed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1003)
        _seed_event(conn, 1003, "PENDING_TIMEOUT", "pt:1003:1", {})
        _seed_event(conn, 1003, "PENDING_ENTRY_CANCELLED", "pec:1003:1", {
            "sequence": 2,
            "cancel_origin": "timeout_worker",
        })
        project_clean_log_for_chain(conn, 1003)
    types = [r[0] for r in conn.execute(
        "SELECT notification_type FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()]
    conn.close()
    assert "PENDING_ENTRY_EXPIRED" in types, "PENDING_ENTRY_EXPIRED deve essere visibile"
    assert "ENTRY_CANCELLED" not in types, "ENTRY_CANCELLED da timeout deve essere soppressa"


# Caso 4: engine_rule cancella Entry_2 dopo TP1 (cancel_averaging_pending_after: tp1).
#         UPDATE_DONE da operation_rules già copre l'operazione → sopprimi.
def test_entry_cancelled_engine_rule_no_partial_fill_is_suppressed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1004)
        _seed_event(conn, 1004, "PENDING_ENTRY_CANCELLED", "pec:1004:1", {
            "sequence": 2,
            "cancel_origin": "engine_rule",
        })
        project_clean_log_for_chain(conn, 1004)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 0, "engine_rule cancel senza partial fill deve essere soppressa"


# Caso 5: exchange cancella Entry_2 per ragione propria (margine insufficiente, liquidazione).
#         Nessun cancel_origin noto → mostrare per non perdere informazione operativa.
def test_entry_cancelled_unknown_origin_is_shown(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1005)
        _seed_event(conn, 1005, "PENDING_ENTRY_CANCELLED", "pec:1005:1", {
            "sequence": 2,
            "cancel_reason": "LIQUIDATED",
        })
        project_clean_log_for_chain(conn, 1005)
    row = conn.execute("SELECT notification_type FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row is not None and row[0] == "ENTRY_CANCELLED", (
        "cancel senza cancel_origin deve essere visibile (origine sconosciuta = potenziale problema)"
    )


def test_tp_filled_has_no_send_after_delay():
    from src.runtime_v2.control_plane.outbox_writer import _send_after_for
    result = _send_after_for("TP_FILLED")
    now = datetime.now(timezone.utc).isoformat()
    assert result <= now or result[:19] == now[:19]


def test_tp_filled_final_has_no_send_after_delay():
    from src.runtime_v2.control_plane.outbox_writer import _send_after_for
    result = _send_after_for("TP_FILLED_FINAL")
    now = datetime.now(timezone.utc).isoformat()
    assert result <= now or result[:19] == now[:19]


def test_update_done_has_no_send_after_delay():
    from src.runtime_v2.control_plane.outbox_writer import _send_after_for
    result = _send_after_for("UPDATE_DONE")
    now = datetime.now(timezone.utc).isoformat()
    assert result <= now or result[:19] == now[:19]


def test_multi_chain_summary_has_3s_send_after_delay():
    from src.runtime_v2.control_plane.outbox_writer import _send_after_for
    from datetime import datetime, timezone, timedelta
    result = _send_after_for("MULTI_CHAIN_SUMMARY")
    in_2s = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    in_5s = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    assert in_2s <= result <= in_5s, f"MULTI_CHAIN_SUMMARY deve avere ~3s delay, got: {result}"


def test_update_clean_log_includes_changed_field_for_be_move(ops_db):
    """_write_update_clean_log deve produrre UPDATE_DONE con campo changed
    popolato quando l'evento contiene is_breakeven=True."""
    import json, sqlite3
    from src.runtime_v2.lifecycle.entry_gate import _write_update_clean_log
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(ops_db)
    _seed_chain(conn, chain_id=77, symbol="ETH/USDT", side="LONG")
    conn.commit()

    event = LifecycleEvent(
        trade_chain_id=77,
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id="1",
        payload_json=json.dumps({
            "action": "MOVE_SL_TO_BE",
            "old_sl_price": 3100.0,
            "new_sl_price": 3340.0,
            "is_breakeven": True,
        }),
        idempotency_key="be_test:77:1",
    )
    cr = UpdateChainResult(
        trade_chain_id=77,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[event],
        execution_commands=[],
    )

    with conn:
        _write_update_clean_log(conn, cr, canonical_message_id=1, link=None)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='UPDATE_DONE'"
    ).fetchone()
    conn.close()
    assert row is not None, "UPDATE_DONE row not found in outbox"
    p = json.loads(row[0])
    changed = p.get("changed", [])
    assert any(
        c.get("field") == "SL" and c.get("note") == "BE"
        for c in changed
    ), f"Expected SL BE in changed, got: {changed}"


def test_trader_id_present_in_all_notification_payloads(ops_db):
    """Regression: trader_id must appear in every notification payload, not only SIGNAL_ACCEPTED.

    The bug was: _build_payload.base did not include trader_id, so ENTRY_OPENED,
    TP_FILLED, SL_FILLED, POSITION_CLOSED all had trader_id=None in the outbox,
    causing the dispatcher to fall back to the global clean_log thread instead of
    the trader-specific one.
    """
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 800)
        _seed_event(conn, 800, "SIGNAL_ACCEPTED", "sig:800:1", {})
        _seed_event(conn, 800, "ENTRY_FILLED", "fill:800:1", {
            "fill_price": 1.0, "fill_qty": 100.0, "exec_fee": 0.05,
        })
        _seed_event(conn, 800, "TP_FILLED", "tp:800:1", {
            "fill_price": 1.1, "filled_qty": 50.0, "exec_fee": 0.03,
        })
        project_clean_log_for_chain(conn, 800)

    rows = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    conn.close()

    assert len(rows) >= 3
    for notification_type, payload_json in rows:
        payload = json.loads(payload_json)
        assert payload.get("trader_id") == "trader_a", (
            f"{notification_type} payload missing trader_id: {payload}"
        )


def test_final_result_computes_net_pnl_without_explicit_funding(ops_db):
    # cumulative_funding defaults to 0.0 (DEFAULT 0.0 in schema) — production case
    # for chains that never received a FUNDING_SETTLED event.
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 720)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET cumulative_gross_pnl=?, cumulative_fees=?, peak_margin_used=?, initial_risk_amount=? "
            "WHERE trade_chain_id=?",
            (10.0, 2.0, 100.0, 50.0, 720),
        )
        _seed_event(conn, 720, "CLOSE_FULL_FILLED", "close_full:720:1", {
            "fill_price": 1.0, "filled_qty": 1.0, "exec_fee": 0.0, "closed_size": 1.0,
        })
        project_clean_log_for_chain(conn, 720)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    assert row is not None
    final_result = json.loads(row[0])["final_result"]
    assert final_result["total_pnl_net"] == pytest.approx(8.0)
    assert final_result["funding"] == pytest.approx(0.0)
    assert final_result["roi_net_pct"] == pytest.approx(8.0, rel=1e-3)
    assert final_result["return_on_risk_pct"] == pytest.approx(16.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Payload enrichment for the numbering rule and TP trim note
# ---------------------------------------------------------------------------

def _set_plan_state(conn, chain_id, plan):
    conn.execute(
        "UPDATE ops_trade_chains SET plan_state_json=? WHERE trade_chain_id=?",
        (json.dumps(plan), chain_id),
    )


def test_tp_filled_payload_includes_total_tps(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1100)
        _set_plan_state(conn, 1100, {
            "legs": [{"sequence": 1, "entry_type": "LIMIT", "price": 65000.0}],
            "intermediate_tps": [68000.0],
            "final_tp": 71000.0,
        })
        _seed_event(conn, 1100, "TP_FILLED", "tp:1100:1", {
            "tp_level": 1, "fill_price": 68000.0, "filled_qty": 0.002,
        })
        project_clean_log_for_chain(conn, 1100)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    payload = json.loads(row[0])
    assert payload["_total_tps"] == 2


def test_tp_filled_final_payload_includes_total_tps(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1101)
        _set_plan_state(conn, 1101, {
            "legs": [{"sequence": 1, "entry_type": "LIMIT", "price": 65000.0}],
            "intermediate_tps": [],
            "final_tp": 71000.0,
        })
        _seed_event(conn, 1101, "TP_FILLED", "tp:1101:1", {
            "tp_level": 1, "is_final": True, "fill_price": 71000.0, "filled_qty": 0.002,
        })
        project_clean_log_for_chain(conn, 1101)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row[0] == "TP_FILLED_FINAL"
    payload = json.loads(row[1])
    assert payload["_total_tps"] == 1


def test_entry_cancelled_payload_includes_total_legs(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1102)
        _set_plan_state(conn, 1102, {
            "legs": [
                {"sequence": 1, "entry_type": "MARKET", "price": None},
                {"sequence": 2, "entry_type": "LIMIT", "price": 64000.0},
            ],
        })
        _seed_event(conn, 1102, "PENDING_ENTRY_CANCELLED", "pec:1102:1", {
            "sequence": 2,
            "cancel_reason": "LIQUIDATED",
        })
        project_clean_log_for_chain(conn, 1102)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    payload = json.loads(row[0])
    assert payload["_total_legs"] == 2


def test_signal_accepted_payload_includes_tp_trimmed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1103)
        _set_plan_state(conn, 1103, {
            "legs": [{"sequence": 1, "entry_type": "LIMIT", "price": 65000.0}],
            "intermediate_tps": [68000.0],
            "final_tp": 71000.0,
            "tp_trimmed": {"original": 5, "used": 2},
        })
        _seed_event(conn, 1103, "SIGNAL_ACCEPTED", "sig:1103")
        project_clean_log_for_chain(conn, 1103)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    payload = json.loads(row[0])
    assert payload["tp_trimmed"] == {"original": 5, "used": 2}


def test_signal_accepted_payload_includes_entry_sequence_realigned(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 1104)
        _set_plan_state(conn, 1104, {
            "legs": [{"sequence": 1, "entry_type": "LIMIT", "price": 69795.0}],
            "final_tp": 71000.0,
            "entry_sequence_realigned": {
                "side": "LONG",
                "original": [
                    {"sequence": 1, "price": 69351.0},
                    {"sequence": 2, "price": 69795.0},
                ],
                "normalized": [
                    {"sequence": 1, "price": 69795.0},
                    {"sequence": 2, "price": 69351.0},
                ],
            },
        })
        _seed_event(conn, 1104, "SIGNAL_ACCEPTED", "sig:1104")
        project_clean_log_for_chain(conn, 1104)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    payload = json.loads(row[0])
    assert payload["entry_sequence_realigned"]["side"] == "LONG"
    assert payload["entry_sequence_realigned"]["normalized"][0]["price"] == 69795.0


# ---------------------------------------------------------------------------
# Intra-chain ordering: HIGH event must not jump ahead of preceding MEDIUM events
# ---------------------------------------------------------------------------

def test_high_priority_event_promotes_preceding_pending_rows_of_same_chain(ops_db):
    """When SL_FILLED (HIGH) is written, all preceding PENDING rows of the same
    chain are promoted to HIGH so the dispatcher sends them first (in insertion
    order), preventing SL from appearing before ENTRY_OPENED in Telegram."""
    conn = sqlite3.connect(ops_db)
    with conn:
        # Write ENTRY_OPENED first (MEDIUM by default)
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=2001,
            payload={"chain_id": 2001},
            dedupe_key="clean:ENTRY_OPENED:2001",
        )
        # Then write SL_FILLED (HIGH) for the same chain
        write_clean_log_event(
            conn,
            notification_type="SL_FILLED",
            chain_id=2001,
            payload={"chain_id": 2001},
            dedupe_key="clean:SL_FILLED:2001",
        )

    rows = conn.execute(
        "SELECT notification_type, priority, chain_id FROM ops_notification_outbox "
        "ORDER BY notification_id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    entry_type, entry_priority, entry_chain = rows[0]
    sl_type, sl_priority, sl_chain = rows[1]

    assert entry_type == "ENTRY_OPENED"
    assert sl_type == "SL_FILLED"
    # ENTRY_OPENED must have been promoted to HIGH
    assert entry_priority == "HIGH", (
        "ENTRY_OPENED preceding SL_FILLED on the same chain must be promoted to HIGH"
    )
    assert sl_priority == "HIGH"
    # chain_id must be stored on both rows
    assert entry_chain == 2001
    assert sl_chain == 2001


def test_high_priority_event_does_not_promote_rows_of_different_chain(ops_db):
    """Priority promotion must be scoped to the same chain_id only."""
    conn = sqlite3.connect(ops_db)
    with conn:
        # ENTRY_OPENED for chain 3001 (different chain)
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=3001,
            payload={"chain_id": 3001},
            dedupe_key="clean:ENTRY_OPENED:3001",
        )
        # SL_FILLED for chain 3002
        write_clean_log_event(
            conn,
            notification_type="SL_FILLED",
            chain_id=3002,
            payload={"chain_id": 3002},
            dedupe_key="clean:SL_FILLED:3002",
        )

    rows = conn.execute(
        "SELECT notification_type, priority FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    conn.close()

    by_type = {r[0]: r[1] for r in rows}
    # ENTRY_OPENED of a different chain must NOT be promoted
    assert by_type["ENTRY_OPENED"] == "MEDIUM", (
        "ENTRY_OPENED of a different chain must remain MEDIUM"
    )
    assert by_type["SL_FILLED"] == "HIGH"


def test_projection_entry_then_sl_same_chain_both_stored_with_correct_chain_id(ops_db):
    """Regression for the observed SL-before-ENTRY ordering bug.

    When ENTRY_FILLED and SL_FILLED arrive close together and are projected for
    the same chain, the SL (HIGH) must not appear before the ENTRY (promoted to
    HIGH) in the dispatcher's claim query.  Verified by checking that after
    projection the ENTRY row is HIGH and has a lower notification_id than SL.
    """
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 2002)
        _seed_event(conn, 2002, "ENTRY_FILLED", "entry:2002:1", {
            "fill_price": 65000.0, "fill_qty": 0.01,
        })
        _seed_event(conn, 2002, "SL_FILLED", "sl:2002:1", {
            "fill_price": 63000.0, "filled_qty": 0.01, "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 2002)

    rows = conn.execute(
        "SELECT notification_id, notification_type, priority, chain_id "
        "FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    conn.close()

    types = [r[1] for r in rows]
    assert "ENTRY_OPENED" in types
    assert "SL_FILLED" in types

    entry_row = next(r for r in rows if r[1] == "ENTRY_OPENED")
    sl_row = next(r for r in rows if r[1] == "SL_FILLED")

    # Both must carry the correct chain_id
    assert entry_row[3] == 2002
    assert sl_row[3] == 2002

    # ENTRY must have been promoted to HIGH
    assert entry_row[2] == "HIGH", "ENTRY_OPENED must be promoted to HIGH when SL_FILLED is written"
    assert sl_row[2] == "HIGH"

    # ENTRY must sort before SL in the dispatcher ORDER BY (same priority → notification_id wins)
    assert entry_row[0] < sl_row[0], (
        "ENTRY_OPENED notification_id must be lower than SL_FILLED so it dispatches first"
    )
