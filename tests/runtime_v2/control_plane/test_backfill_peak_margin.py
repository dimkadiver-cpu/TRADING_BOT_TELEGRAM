from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.backfill_peak_margin import backfill_minimum_roi_fields


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_backfill_minimum_populates_initial_risk_and_peak_from_final_state(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, trader_id, "
        "account_id, symbol, side, lifecycle_state, entry_mode, risk_snapshot_json, "
        "entry_avg_price, filled_entry_qty, updated_at, created_at) "
        "VALUES (1,1,1,1,'t','main','BTCUSDT','LONG','CLOSED','ONE_SHOT',?,?,?,?,?)",
        (
            json.dumps({"risk_amount": 200.0, "leverage": 5}),
            28580.94,
            0.1,
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    updated = backfill_minimum_roi_fields(db)
    assert updated == 1

    conn2 = sqlite3.connect(db)
    row = conn2.execute(
        "SELECT initial_risk_amount, peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()
    conn2.close()
    assert row[0] == 200.0
    assert row[1] == pytest.approx(571.6188)


def test_backfill_minimum_leaves_peak_null_when_data_insufficient(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, trader_id, "
        "account_id, symbol, side, lifecycle_state, entry_mode, risk_snapshot_json, "
        "entry_avg_price, filled_entry_qty, updated_at, created_at) "
        "VALUES (2,1,1,1,'t','main','BTCUSDT','LONG','CLOSED','ONE_SHOT',?,?,?,?,?)",
        (
            json.dumps({"risk_amount": 100.0}),  # no leverage
            None,  # no entry_avg_price
            0,  # filled_entry_qty = 0 (NOT NULL DEFAULT 0) — insufficient for peak calc
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    updated = backfill_minimum_roi_fields(db)
    assert updated == 1  # initial_risk_amount is populated

    conn3 = sqlite3.connect(db)
    row = conn3.execute(
        "SELECT initial_risk_amount, peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=2"
    ).fetchone()
    conn3.close()
    assert row[0] == 100.0
    assert row[1] is None  # peak stays NULL when data insufficient


def test_backfill_minimum_skips_already_populated_rows(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, trader_id, "
        "account_id, symbol, side, lifecycle_state, entry_mode, risk_snapshot_json, "
        "entry_avg_price, filled_entry_qty, initial_risk_amount, peak_margin_used, updated_at, created_at) "
        "VALUES (3,1,1,1,'t','main','BTCUSDT','LONG','CLOSED','ONE_SHOT',?,?,?,?,?,?,?)",
        (
            json.dumps({"risk_amount": 150.0, "leverage": 10}),
            30000.0,
            0.05,
            150.0,
            150.0,
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    updated = backfill_minimum_roi_fields(db)
    assert updated == 0  # already populated — should not update
