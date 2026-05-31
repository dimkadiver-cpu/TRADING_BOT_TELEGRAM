# tests/runtime_v2/control_plane/test_clean_log_formatter.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log


# ---------------------------------------------------------------------------
# Existing formatter tests (preserved + assertions updated for richer output)
# ---------------------------------------------------------------------------

def test_signal_accepted():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "trader_id": "trader_a",
    })
    assert "#145" in text
    assert "SIGNAL ACCEPTED" in text
    assert "BTC/USDT" in text
    assert "📈" in text          # LONG side emoji
    assert "Source:" in text


def test_review_required():
    text = format_clean_log("REVIEW_REQUIRED", {
        "chain_id": 147, "symbol": "ETH/USDT", "side": "SHORT",
        "reason": "ambiguous_entry_zone",
    })
    assert "REVIEW REQUIRED" in text
    assert "📉" in text          # SHORT side emoji
    assert "ambiguous_entry_zone" in text


def test_entry_opened():
    text = format_clean_log("ENTRY_OPENED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 65020.0, "filled_qty": 0.004,
    })
    assert "ENTRY OPENED" in text
    assert "65,020" in text or "65020" in text


def test_tp_filled():
    text = format_clean_log("TP_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG", "tp_level": 1,
    })
    assert "TP" in text and "FILLED" in text


def test_sl_filled_marks_closed():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
    })
    assert "SL FILLED" in text
    assert "POSITION CLOSED" in text
    assert "🛑" in text


def test_position_closed():
    text = format_clean_log("POSITION_CLOSED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
    })
    assert "POSITION CLOSED" in text


def test_unknown_type_has_safe_fallback():
    text = format_clean_log("WAT", {"chain_id": 1, "symbol": "X/Y", "side": "LONG"})
    assert "#1" in text
    assert "WAT" in text


# ---------------------------------------------------------------------------
# New formatter tests — enriched payloads
# ---------------------------------------------------------------------------

def test_signal_accepted_full():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "trader_id": "trader_a",
        "entries": [
            {"sequence": 1, "entry_type": "MARKET", "price": None},
            {"sequence": 2, "entry_type": "LIMIT", "price": 64000.0},
        ],
        "sl": 62000.0,
        "tps": [68000.0, 71000.0],
        "risk_pct": 0.5,
        "source": "original_message",
    })
    assert "Entry_1: Market" in text
    assert "Entry_2: 64,000 Limit" in text
    assert "SL: 62,000" in text
    assert "TP_1: 68,000" in text
    assert "TP_2: 71,000" in text
    assert "Risk: 0.5%" in text
    assert "Trader: trader_a" in text


def test_signal_accepted_market_with_price():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 1, "symbol": "ETH/USDT", "side": "LONG",
        "entries": [{"sequence": 1, "entry_type": "MARKET", "price": 3000.0}],
        "sl": None, "tps": [], "risk_pct": None, "source": "original_message",
    })
    assert "Entry_1: Market ~3,000" in text


def test_entry_opened_with_avg_and_pending():
    text = format_clean_log("ENTRY_OPENED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 65020.0, "filled_qty": 0.004,
        "avg_entry": 65020.0,
        "pending_entries": [{"sequence": 2, "entry_type": "LIMIT", "price": 64000.0}],
        "source": "exchange",
    })
    assert "ENTRY OPENED" in text
    assert "Avg entry: 65,020" in text
    assert "Entry_2" in text
    assert "64,000" in text


def test_entry_opened_no_pending():
    text = format_clean_log("ENTRY_OPENED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 65020.0, "filled_qty": 0.004,
        "avg_entry": 65020.0,
        "pending_entries": [],
        "source": "exchange",
    })
    assert "Pending: none" in text


def test_tp_filled_shows_price_and_sl():
    text = format_clean_log("TP_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "tp_level": 1, "tp_price": 68000.0,
        "is_final": False, "sl_current": 62000.0,
        "source": "exchange",
    })
    assert "TP1 FILLED" in text
    assert "TP_1: 68,000" in text
    assert "SL: 62,000" in text
    assert "POSITION CLOSED" not in text


def test_tp_filled_final():
    text = format_clean_log("TP_FILLED_FINAL", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "tp_level": 2, "tp_price": 71000.0,
        "is_final": True, "sl_current": None,
        "source": "exchange",
    })
    assert "TP2 FILLED — POSITION CLOSED" in text
    assert "TAKE_PROFIT" in text


def test_tp_filled_no_sl_shown_when_final():
    text = format_clean_log("TP_FILLED_FINAL", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "tp_level": 2, "tp_price": 71000.0,
        "is_final": True, "sl_current": 62000.0,
        "source": "exchange",
    })
    # sl_current should NOT appear on final TP (position is closed)
    assert "Remaining:" not in text


def test_sl_filled_shows_fill_price():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 62000.0, "source": "exchange",
    })
    assert "SL FILLED — POSITION CLOSED" in text
    assert "62,000" in text
    assert "STOP_LOSS" in text


def test_sl_filled_side_always_correct():
    """side must come from chain (LONG/SHORT), not from the exchange event (which may say 'Sell')."""
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 62000.0, "source": "exchange",
    })
    assert "LONG" in text
    assert "Sell" not in text


def test_signal_rejected():
    text = format_clean_log("SIGNAL_REJECTED", {
        "chain_id": 146, "symbol": "BTC/USDT", "side": "LONG",
        "trader_id": "trader_b",
        "entries": [{"sequence": 1, "entry_type": "LIMIT", "price": 65000.0}],
        "sl": 62000.0,
        "reason": "invalid_risk_profile",
        "source": "original_message",
    })
    assert "❌" in text
    assert "SIGNAL REJECTED" in text
    assert "#146" in text
    assert "Entry_1: 65,000 Limit" in text
    assert "SL: 62,000" in text
    assert "Rejected: invalid_risk_profile" in text
    assert "Trader: trader_b" in text


def test_signal_rejected_minimal():
    """Works even with empty payload (no chain yet)."""
    text = format_clean_log("SIGNAL_REJECTED", {
        "chain_id": None, "symbol": "ETH/USDT", "side": "SHORT",
        "reason": "risk_capacity_exceeded",
    })
    assert "SIGNAL REJECTED" in text
    assert "Rejected: risk_capacity_exceeded" in text


def test_position_closed_shows_fill_price():
    text = format_clean_log("POSITION_CLOSED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 65500.0, "source": "exchange",
    })
    assert "POSITION CLOSED" in text
    assert "65,500" in text
    assert "MANUAL_CLOSE" in text


# ---------------------------------------------------------------------------
# Integration test — outbox_writer enriches payload from ops_trade_chains
# ---------------------------------------------------------------------------

def test_outbox_writer_signal_accepted_enriches_from_chain(tmp_path):
    """project_clean_log_for_chain reads plan/risk from ops_trade_chains."""
    import sqlite3
    import json
    from pathlib import Path
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain

    db_path = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db_path)
    migrations_dir = Path("db/ops_migrations")
    for f in sorted(migrations_dir.glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()

    now = "2026-05-30T12:00:00+00:00"
    plan = json.dumps({
        "stop_loss": 62000.0,
        "final_tp": 71000.0,
        "intermediate_tps": [68000.0],
        "legs": [
            {
                "sequence": 1, "entry_type": "LIMIT",
                "price": 65000.0, "status": "PENDING", "weight": 1.0,
            }
        ],
    })
    risk = json.dumps({"capital": 10000.0, "risk_amount": 50.0})

    with conn:
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " management_plan_json, plan_state_json, risk_snapshot_json, "
            " created_at, updated_at) "
            "VALUES (10, 10, 10, 10, 'trader_a', 'main', 'BTC/USDT', 'LONG', "
            "        'WAITING_ENTRY', 'ONE_SHOT', '{}', ?, ?, ?, ?)",
            (plan, risk, now, now),
        )
        conn.execute(
            "INSERT INTO ops_lifecycle_events "
            "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
            "VALUES (10, 'SIGNAL_ACCEPTED', 'enrichment', '{}', 'sa:10', ?)",
            (now,),
        )

    project_clean_log_for_chain(conn, 10)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox "
        "WHERE notification_type='SIGNAL_ACCEPTED'"
    ).fetchone()
    conn.close()

    assert row is not None
    p = json.loads(row[0])
    assert p["sl"] == 62000.0
    assert len(p["tps"]) == 2          # intermediate (68000) + final (71000)
    assert p["tps"][0] == 68000.0
    assert p["tps"][1] == 71000.0
    assert p["risk_pct"] == 0.5        # 50/10000*100
    assert p["trader_id"] == "trader_a"
    assert p["side"] == "LONG"


def test_footer_adds_separator_before_link():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "source": "original_message",
        "link": "https://t.me/c/1/2",
    })
    # The link should appear after a separator
    assert "https://t.me/c/1/2" in text
    # Source line should appear before link
    source_pos = text.find("Source: original_message")
    link_pos = text.find("https://t.me/c/1/2")
    assert source_pos < link_pos, "Source must appear before link"


def test_update_done_uses_operation_label_and_square_bullet():
    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "operations": ["Move SL to BE"],
        "changed": [{"field": "SL", "old": 64000, "new": 65020, "note": "Changed by rule after TP_1"}],
        "source": "trader_update",
    })
    assert "Operation:" in text
    assert f"▪️ Move SL to BE" in text
    assert "SL: 64,000 -> 65,020 *" in text
    assert "* Changed by rule after TP_1" in text


def test_tp_filled_renders_closed_pnl_fee_remaining_and_be_label():
    text = format_clean_log("TP_FILLED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "tp_level": 1,
        "tp_price": 68000.0,
        "fill_price": 68000.0,
        "closed_pct": 30.0,
        "pnl": 70.20,
        "fee": 1.10,
        "remaining_pct": 70.0,
        "sl_current": 65020.0,
        "be_protection_status": "PROTECTED",
        "source": "exchange",
    })
    assert "TP_1: 68,000" in text
    assert "Closed: 30%" in text
    assert "PnL: +70.20 USDT" in text
    assert "Fee: 1.10 USDT" in text
    assert "Position: 70%" in text
    assert "SL: 65,020 BE" in text


def test_sl_filled_renders_sl_label_and_final_result():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "sl_price": 64000.0,
        "closed_pct": 100.0,
        "pnl": -50.0,
        "fee": 1.70,
        "final_result": {
            "roi_net_pct": -5.17,
            "total_pnl_net": -51.70,
            "gross_pnl": -50.0,
            "fees": -1.70,
            "funding": 0.0,
            "close_reason": "STOP_LOSS",
        },
        "source": "exchange",
    })
    assert "SL: 64,000" in text
    assert "Closed: 100%" in text
    assert "Final Result:" in text
    assert "PnL: -50.00 USDT" in text


def test_outbox_writer_sl_filled_side_from_chain(tmp_path):
    """Side in SL_FILLED payload must come from ops_trade_chains (LONG), not event (Sell)."""
    import sqlite3
    import json
    from pathlib import Path
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain

    db_path = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()

    now = "2026-05-30T12:00:00+00:00"
    sl_ev_payload = json.dumps({"fill_price": 62000.0, "fill_qty": 5333.4, "side": "Sell"})

    with conn:
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " management_plan_json, plan_state_json, risk_snapshot_json, "
            " created_at, updated_at) "
            "VALUES (20, 20, 20, 20, 'trader_a', 'main', 'ETH/USDT', 'LONG', "
            "        'CLOSED', 'ONE_SHOT', '{}', '{}', '{}', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO ops_lifecycle_events "
            "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
            "VALUES (20, 'SL_FILLED', 'exchange', ?, 'sl:20', ?)",
            (sl_ev_payload, now),
        )

    project_clean_log_for_chain(conn, 20)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox "
        "WHERE notification_type='SL_FILLED'"
    ).fetchone()
    conn.close()

    assert row is not None
    p = json.loads(row[0])
    assert p["side"] == "LONG"          # from chain, not "Sell" from event
    assert p["fill_price"] == 62000.0
