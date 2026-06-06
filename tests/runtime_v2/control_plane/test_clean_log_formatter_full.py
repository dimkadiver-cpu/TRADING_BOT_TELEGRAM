# tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log


# ---------------------------------------------------------------------------
# ENTRY_UPDATED
# ---------------------------------------------------------------------------

def test_entry_updated_renders_fill_and_new_avg():
    text = format_clean_log("ENTRY_UPDATED", {
        "chain_id": 200, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 64500.0, "filled_qty": 0.002, "new_avg_entry": 64750.0,
        "source": "exchange",
    })
    assert "ENTRY UPDATED" in text
    assert "#200" in text
    assert "64,500" in text or "64500" in text
    assert "64,750" in text or "64750" in text
    assert "✏️" in text
    assert "Source: exchange" in text


def test_entry_updated_without_new_avg():
    text = format_clean_log("ENTRY_UPDATED", {
        "chain_id": 201, "symbol": "ETH/USDT", "side": "SHORT",
        "fill_price": 3100.0, "filled_qty": 1.5, "new_avg_entry": None,
        "source": "exchange",
    })
    assert "ENTRY UPDATED" in text
    assert "3,100" in text or "3100" in text


# ---------------------------------------------------------------------------
# UPDATE_DONE
# ---------------------------------------------------------------------------

def test_update_done_renders_operations_and_changes():
    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 300, "symbol": "BTC/USDT", "side": "LONG",
        "applied_actions": ["U_MOVE_STOP", "U_UPDATE_TAKE_PROFITS"],
        "changed_fields": ["current_stop_price", "plan_state_json"],
        "source": "runtime",
    })
    assert "UPDATE DONE" in text
    assert "#300" in text
    assert "✅" in text
    assert "U_MOVE_STOP" in text
    assert "current_stop_price" in text
    assert "Source: runtime" in text


def test_update_done_empty_lists():
    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 301, "symbol": "ETH/USDT", "side": "LONG",
        "applied_actions": [], "changed_fields": [],
        "source": "runtime",
    })
    assert "UPDATE DONE" in text


# ---------------------------------------------------------------------------
# UPDATE_PARTIAL
# ---------------------------------------------------------------------------

def test_update_partial_renders_applied_and_rejected():
    text = format_clean_log("UPDATE_PARTIAL", {
        "chain_id": 400, "symbol": "SOL/USDT", "side": "LONG",
        "applied_actions": ["U_MOVE_STOP"],
        "rejected_actions": ["U_ADD_ENTRY"],
        "source": "runtime",
    })
    assert "UPDATE PARTIAL" in text
    assert "#400" in text
    assert "⚠️" in text
    assert "U_MOVE_STOP" in text
    assert "U_ADD_ENTRY" in text
    assert "Source: runtime" in text


# ---------------------------------------------------------------------------
# UPDATE_REJECTED
# ---------------------------------------------------------------------------

def test_update_rejected_renders_reason():
    text = format_clean_log("UPDATE_REJECTED", {
        "chain_id": 500, "symbol": "BTC/USDT", "side": "SHORT",
        "reason": "no_open_position",
        "source": "runtime",
    })
    assert "UPDATE REJECTED" in text
    assert "#500" in text
    assert "❌" in text
    assert "no_open_position" in text
    assert "Source: runtime" in text


def test_update_rejected_no_reason():
    text = format_clean_log("UPDATE_REJECTED", {
        "chain_id": 501, "symbol": "ETH/USDT", "side": "LONG",
        "reason": None, "source": "runtime",
    })
    assert "UPDATE REJECTED" in text


# ---------------------------------------------------------------------------
# PENDING_ENTRY_EXPIRED
# ---------------------------------------------------------------------------

def test_pending_entry_expired_renders_timeout_worker_source():
    text = format_clean_log("PENDING_ENTRY_EXPIRED", {
        "chain_id": 600, "symbol": "BTC/USDT", "side": "LONG",
        "source": "worker",
    })
    assert "PENDING ENTRY EXPIRED" in text
    assert "#600" in text
    assert "⏰" in text
    assert "Timeout" in text or "expired" in text.lower()
    assert "Source: worker" in text


def test_pending_entry_expired_with_link():
    text = format_clean_log("PENDING_ENTRY_EXPIRED", {
        "chain_id": 601, "symbol": "ETH/USDT", "side": "SHORT",
        "source": "worker",
        "link": "https://t.me/c/123/456",
    })
    assert "PENDING ENTRY EXPIRED" in text
    assert "https://t.me/c/123/456" in text


# ---------------------------------------------------------------------------
# RECONCILIATION_WARNING
# ---------------------------------------------------------------------------

def test_reconciliation_warning_renders_issue_risk_action():
    text = format_clean_log("RECONCILIATION_WARNING", {
        "chain_id": 700, "symbol": "BTC/USDT", "side": "LONG",
        "issue": "position_size_mismatch",
        "risk": "MEDIUM",
        "action": "monitor",
        "source": "runtime",
    })
    assert "RECONCILIATION WARNING" in text
    assert "#700" in text
    assert "⚠️" in text
    assert "position_size_mismatch" in text
    assert "MEDIUM" in text
    assert "monitor" in text
    assert "Source: runtime" in text


def test_reconciliation_warning_partial_fields():
    text = format_clean_log("RECONCILIATION_WARNING", {
        "chain_id": 701, "symbol": "ETH/USDT", "side": "SHORT",
        "issue": "sl_drift", "risk": None, "action": None,
        "source": "runtime",
    })
    assert "RECONCILIATION WARNING" in text
    assert "sl_drift" in text


# ---------------------------------------------------------------------------
# RECONCILIATION_FIXED
# ---------------------------------------------------------------------------

def test_reconciliation_fixed_renders_issue():
    text = format_clean_log("RECONCILIATION_FIXED", {
        "chain_id": 800, "symbol": "BTC/USDT", "side": "LONG",
        "issue": "sl_adjusted",
        "source": "runtime",
    })
    assert "RECONCILIATION FIXED" in text
    assert "#800" in text
    assert "✅" in text
    assert "sl_adjusted" in text
    assert "Source: runtime" in text


# ---------------------------------------------------------------------------
# REENTRY_ACCEPTED
# ---------------------------------------------------------------------------

def test_reentry_accepted_renders_previous_chain():
    text = format_clean_log("REENTRY_ACCEPTED", {
        "chain_id": 900, "symbol": "BTC/USDT", "side": "LONG",
        "previous_chain_id": 899,
        "source": "runtime",
    })
    assert "REENTRY ACCEPTED" in text
    assert "#900" in text
    assert "🔄" in text
    assert "899" in text
    assert "Source: runtime" in text


def test_reentry_accepted_without_previous_chain():
    text = format_clean_log("REENTRY_ACCEPTED", {
        "chain_id": 901, "symbol": "ETH/USDT", "side": "SHORT",
        "previous_chain_id": None,
        "source": "runtime",
    })
    assert "REENTRY ACCEPTED" in text
    assert "Source: runtime" in text


# ---------------------------------------------------------------------------
# ENTRY_CANCELLED
# ---------------------------------------------------------------------------

def test_entry_cancelled_formatter_renders_type_and_source():
    text = format_clean_log("ENTRY_CANCELLED", {
        "chain_id": 150,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "cancelled_entry": {"sequence": 2, "price": 64000.0, "entry_type": "LIMIT"},
        "partial_fill_pct": None,
        "avg_entry": 64820.0,
        "total_filled_qty": 0.006,
        "source": "trader_update",
    })
    assert "ENTRY CANCELLED" in text
    assert "Entry_2: 64,000 Limit" in text
    assert "Avg entry: 64,820" in text
    assert "0.006 BTC" in text


# ---------------------------------------------------------------------------
# BE_EXIT
# ---------------------------------------------------------------------------

def test_be_exit_formatter_renders_exit_and_final_result():
    text = format_clean_log("BE_EXIT", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "exit_price": 65020.0,
        "close_reason": "BREAKEVEN_AFTER_TP",
        "pnl": 0.20,
        "fee": 1.70,
        "final_result": {
            "roi_net_pct": 1.15,
            "total_pnl_net": 112.30,
            "gross_pnl": 118.0,
            "fees": -5.70,
            "funding": 0.0,
            "close_reason": "BREAKEVEN_AFTER_TP",
        },
        "source": "exchange",
    })
    assert "POSITION CLOSED" in text
    assert "Price: 65,020" in text
    assert "Close reason: BREAKEVEN_AFTER_TP" in text
    assert "Final Result:" in text
    assert "Qty: n/a" in text


def test_sl_filled_with_be_close_reason_renders_be_exit():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 146,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "fill_price": 65000.0,
        "sl_price": 65000.0,
        "close_reason": "BREAKEVEN_AFTER_TP",
        "pnl": -0.20,
        "fee": 1.70,
        "final_result": {
            "roi_net_pct": None,
            "total_pnl_net": -1.90,
            "gross_pnl": -0.20,
            "fees": -1.70,
            "funding": 0.0,
            "close_reason": "BREAKEVEN_AFTER_TP",
        },
        "source": "exchange",
    })
    assert "POSITION CLOSED" in text
    assert "Price" not in text
    assert "SL: 65,000" in text
    assert "Close reason: BREAKEVEN_AFTER_TP" in text
    assert "Qty: n/a" in text


# ---------------------------------------------------------------------------
# MULTI_CHAIN_UPDATE
# ---------------------------------------------------------------------------

def test_multi_chain_update_formatter():
    text = format_clean_log("MULTI_CHAIN_UPDATE", {
        "operations": ["Move SL to BE"],
        "chains": [
            {"chain_id": 160, "symbol": "BTC/USDT", "side": "SHORT", "status": "DONE"},
            {"chain_id": 161, "symbol": "ETH/USDT", "side": "SHORT", "status": "DONE"},
            {"chain_id": 163, "symbol": "XRP/USDT", "side": "SHORT", "status": "DONE"},
        ],
        "summary": {"done": 3, "rejected": 0},
        "source": "trader_update",
    })
    assert "UPDATE APPLICATO - 3 chain" in text
    assert "#160" in text
    assert "BTC/USDT" in text
    assert "DONE" in text


# ---------------------------------------------------------------------------
# CANCEL_FAILED
# ---------------------------------------------------------------------------

def test_cancel_failed_formatter():
    text = format_clean_log("CANCEL_FAILED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_ref": "Entry_2",
        "entry_price": 64000.0,
        "attempts": 3,
        "source": "timeout_worker",
    })
    assert "CANCEL FAILED" in text
    assert "Cancellation of Entry_2 failed after 3 attempts." in text
    assert "manual review required" in text.lower()
    assert "#145" in text
    assert "64,000" in text or "64000" in text
