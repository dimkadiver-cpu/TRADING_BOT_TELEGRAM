# tests/runtime_v2/control_plane/test_dashboard_formatter.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.formatters.dashboard import format_dashboard_view
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import StatusQueries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_chain(
    conn: sqlite3.Connection,
    cid: int,
    state: str,
    *,
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    sl: float | None = None,
    account_id: str = "demo_1",
    trader_id: str = "trader_a",
    entry_avg_price: float | None = None,
    open_position_qty: float = 0.0,
    cumulative_gross_pnl: float = 0.0,
    cumulative_fees: float = 0.0,
    cumulative_funding: float = 0.0,
    created_at: str | None = None,
) -> None:
    ts = created_at or _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " current_stop_price, entry_avg_price, open_position_qty, "
        " cumulative_gross_pnl, cumulative_fees, cumulative_funding, "
        " management_plan_json, risk_snapshot_json, plan_state_json, "
        " created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            cid, cid, cid, cid,
            trader_id, account_id, symbol, side, state, "ONE_SHOT",
            sl, entry_avg_price, open_position_qty,
            cumulative_gross_pnl, cumulative_fees, cumulative_funding,
            "{}", "{}", "{}",
            ts, ts,
        ),
    )


def _add_account_snapshot(
    conn: sqlite3.Connection,
    account_id: str = "demo_1",
    equity: float = 10432.50,
    balance: float = 9100.00,
    margin: float = 820.00,
) -> None:
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, "
        " total_open_risk_usdt, total_margin_used_usdt, source, captured_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (account_id, equity, balance, None, margin, "test", _now()),
    )


def _add_market_snapshot(
    conn: sqlite3.Connection,
    account_id: str,
    symbol: str,
    mark_price: float,
) -> None:
    conn.execute(
        "INSERT INTO ops_market_snapshots "
        "(account_id, symbol, mark_price, source, captured_at) "
        "VALUES (?,?,?,?,?)",
        (account_id, symbol, mark_price, "test", _now()),
    )


def _add_exec_failed_command(
    conn: sqlite3.Connection,
    chain_id: int,
    reason: str = "insufficient_margin",
) -> None:
    import json
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(trade_chain_id, command_type, status, idempotency_key, payload_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (chain_id, "PLACE_ENTRY", "FAILED", f"key_{chain_id}",
         json.dumps({"reason": reason}), _now(), _now()),
    )


def _add_lifecycle_event(
    conn: sqlite3.Connection,
    chain_id: int,
    event_type: str,
    reason: str | None = None,
) -> None:
    import json
    payload = json.dumps({"reason": reason}) if reason else "{}"
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (chain_id, event_type, "test", payload, f"idem_{chain_id}_{event_type}", _now()),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


SCOPE = QueryScope(account_id="demo_1", trader_ids=["trader_a"])
SCOPE_ACCOUNT = QueryScope(account_id="demo_1", trader_ids=None)


# ---------------------------------------------------------------------------
# Tests: vista attivi
# ---------------------------------------------------------------------------

class TestVistaAttivi:
    def test_shows_open_trade_with_pnl(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 5, "OPEN",
                symbol="BTCUSDT", side="LONG",
                entry_avg_price=63500.0, open_position_qty=0.01,
            )
            _add_market_snapshot(conn, "demo_1", "BTCUSDT", 64740.0)
        conn.close()

        q = StatusQueries(ops_db)
        text, total = format_dashboard_view("attivi", SCOPE, q)

        assert "📊 DASHBOARD" in text
        assert "demo_1" in text
        assert "trader_a" in text
        assert "BTCUSDT" in text
        assert "OPEN" in text
        assert "+12.40" in text  # (64740 - 63500) * 0.01 * 1 = 12.40
        assert total == 1

    def test_pnl_dash_when_no_mark_price(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 9, "WAITING_ENTRY", symbol="SOLUSDT", side="LONG")
        conn.close()

        q = StatusQueries(ops_db)
        text, total = format_dashboard_view("attivi", SCOPE, q)

        assert "PnL: —" in text
        assert total == 1

    def test_header_no_trader_when_account_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 1, "OPEN")
        conn.close()

        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("attivi", SCOPE_ACCOUNT, q)
        assert "📊 DASHBOARD — demo_1" in text
        # No trader_id in header when account-level scope
        assert "· trader" not in text

    def test_pagination_limits_rows(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            for cid in range(1, 8):
                _add_chain(conn, cid, "OPEN", symbol=f"COIN{cid}USDT")
        conn.close()

        q = StatusQueries(ops_db)
        text, total = format_dashboard_view("attivi", SCOPE_ACCOUNT, q, page=0, page_size=5)
        assert total == 7
        # Page 0 has 5 items
        assert "COIN1USDT" in text
        assert "COIN5USDT" in text
        assert "COIN6USDT" not in text

        text2, _ = format_dashboard_view("attivi", SCOPE_ACCOUNT, q, page=1, page_size=5)
        assert "COIN6USDT" in text2
        assert "COIN7USDT" in text2


# ---------------------------------------------------------------------------
# Tests: vista chiusi
# ---------------------------------------------------------------------------

class TestVistaChiusi:
    def test_shows_closed_trades(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 22, "CLOSED",
                symbol="BNBUSDT", side="SHORT",
                cumulative_gross_pnl=-12.80,
                trader_id="trader_a",
            )
            _add_chain(
                conn, 18, "CLOSED",
                symbol="SOLUSDT", side="LONG",
                cumulative_gross_pnl=34.50,
                trader_id="trader_a",
            )
        conn.close()

        q = StatusQueries(ops_db)
        text, total = format_dashboard_view("chiusi", SCOPE, q)

        assert "✅ DASHBOARD" in text
        assert "BNBUSDT" in text
        assert "-12.80" in text
        assert "SOLUSDT" in text
        assert "+34.50" in text
        assert total == 2

    def test_paginated_chiusi(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            for cid in range(1, 9):
                _add_chain(
                    conn, cid, "CLOSED",
                    symbol=f"COIN{cid}USDT",
                    cumulative_gross_pnl=float(cid * 10),
                    trader_id="trader_a",
                )
        conn.close()

        q = StatusQueries(ops_db)
        text, total = format_dashboard_view("chiusi", SCOPE, q, page=0, page_size=5)
        assert total == 8
        text2, _ = format_dashboard_view("chiusi", SCOPE, q, page=1, page_size=5)
        # Second page has remaining 3 trades
        assert "COIN" in text2

    def test_empty_chiusi(self, ops_db):
        q = StatusQueries(ops_db)
        text, total = format_dashboard_view("chiusi", SCOPE, q)
        assert "Nessun trade chiuso" in text
        assert total == 0


# ---------------------------------------------------------------------------
# Tests: vista bloccati
# ---------------------------------------------------------------------------

class TestVistaBloccati:
    def test_shows_review_required_and_exec_failed(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 7, "REVIEW_REQUIRED", symbol="ETHUSDT", trader_id="trader_a")
            _add_lifecycle_event(conn, 7, "REVIEW_REQUIRED", reason="missing_sl")

            _add_chain(conn, 12, "OPEN", symbol="SOLUSDT", trader_id="trader_a")
            _add_exec_failed_command(conn, 12, reason="insufficient_margin")
        conn.close()

        q = StatusQueries(ops_db)
        text, total = format_dashboard_view("bloccati", SCOPE, q)

        assert "🚫 DASHBOARD" in text
        assert "ETHUSDT" in text
        assert "REVIEW_REQUIRED" in text
        assert "missing_sl" in text
        assert "SOLUSDT" in text
        assert "EXEC_FAILED" in text
        assert "insufficient_margin" in text

    def test_empty_bloccati(self, ops_db):
        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("bloccati", SCOPE, q)
        assert "Nessun trade bloccato" in text


# ---------------------------------------------------------------------------
# Tests: vista pnl
# ---------------------------------------------------------------------------

class TestVistaPnl:
    def test_shows_snapshot_and_realized(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_account_snapshot(conn, equity=10432.50, balance=9100.00, margin=820.00)
            _add_chain(
                conn, 1, "CLOSED",
                cumulative_gross_pnl=142.60,
                cumulative_fees=11.20,
                trader_id="trader_a",
            )
            _add_chain(conn, 2, "OPEN", trader_id="trader_a")
            _add_chain(conn, 3, "WAITING_ENTRY", trader_id="trader_a")
        conn.close()

        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("pnl", SCOPE, q)

        assert "💰 DASHBOARD" in text
        assert "10,432.50" in text
        assert "9,100.00" in text
        assert "820.00" in text
        assert "Realizzato (trader_a):" in text
        assert "+142.60" in text
        assert "Open: 1" in text
        assert "Waiting: 1" in text

    def test_realized_label_account_level(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_account_snapshot(conn)
        conn.close()

        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("pnl", SCOPE_ACCOUNT, q)

        # account-level scope → no trader_id in label
        assert "Realizzato:" in text
        assert "Realizzato (trader_a):" not in text


# ---------------------------------------------------------------------------
# Tests: vista stats
# ---------------------------------------------------------------------------

class TestVistaStats:
    def test_shows_stats_table(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 8, "CLOSED",
                symbol="SOLUSDT", side="LONG",
                cumulative_gross_pnl=34.50,
                cumulative_fees=1.50,
                trader_id="trader_a",
            )
            _add_chain(
                conn, 22, "CLOSED",
                symbol="BNBUSDT", side="SHORT",
                cumulative_gross_pnl=-12.80,
                cumulative_fees=0.80,
                trader_id="trader_a",
            )
        conn.close()

        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("stats", SCOPE, q)

        assert "📉 DASHBOARD" in text
        # Table header
        assert "Trades" in text
        assert "Win%" in text
        assert "Netto" in text
        # Labels
        assert "Oggi" in text
        assert "7 giorni" in text or "7" in text
        assert "30 giorni" in text or "30" in text
        assert "Totale" in text

    def test_shows_best_and_worst(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 8, "CLOSED",
                symbol="SOLUSDT",
                cumulative_gross_pnl=34.50,
                trader_id="trader_a",
            )
            _add_chain(
                conn, 22, "CLOSED",
                symbol="BNBUSDT",
                cumulative_gross_pnl=-12.80,
                trader_id="trader_a",
            )
        conn.close()

        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("stats", SCOPE, q)

        assert "#8" in text   # best
        assert "#22" in text  # worst
        assert "+34.50" in text
        assert "-12.80" in text


# ---------------------------------------------------------------------------
# Tests: keyboard
# ---------------------------------------------------------------------------

telegram = pytest.importorskip("telegram", reason="python-telegram-bot not installed")


class TestDashboardKeyboard:
    def test_no_pagination_row_when_total_lte_page_size(self):
        from src.runtime_v2.control_plane.formatters.dashboard import build_dashboard_keyboard

        kb = build_dashboard_keyboard("attivi", page=0, total_count=3, page_size=5)
        assert len(kb.inline_keyboard) == 2  # only row1 + row2

    def test_pagination_row_when_total_gt_page_size(self):
        from src.runtime_v2.control_plane.formatters.dashboard import build_dashboard_keyboard

        kb = build_dashboard_keyboard("chiusi", page=0, total_count=8, page_size=5)
        assert len(kb.inline_keyboard) == 3  # row1 + row2 + pagination

    def test_no_prev_button_on_first_page(self):
        from src.runtime_v2.control_plane.formatters.dashboard import build_dashboard_keyboard

        kb = build_dashboard_keyboard("chiusi", page=0, total_count=8, page_size=5)
        pagination_row = kb.inline_keyboard[2]
        callbacks = [btn.callback_data for btn in pagination_row]
        assert "page:prev" not in callbacks

    def test_prev_button_on_page_gt_0(self):
        from src.runtime_v2.control_plane.formatters.dashboard import build_dashboard_keyboard

        kb = build_dashboard_keyboard("chiusi", page=1, total_count=8, page_size=5)
        pagination_row = kb.inline_keyboard[2]
        callbacks = [btn.callback_data for btn in pagination_row]
        assert "page:prev" in callbacks

    def test_no_next_button_on_last_page(self):
        from src.runtime_v2.control_plane.formatters.dashboard import build_dashboard_keyboard

        # 8 items, page_size=5 → 2 pages; last page is index 1
        kb = build_dashboard_keyboard("chiusi", page=1, total_count=8, page_size=5)
        pagination_row = kb.inline_keyboard[2]
        callbacks = [btn.callback_data for btn in pagination_row]
        assert "page:next" not in callbacks

    def test_tab_buttons_present(self):
        from src.runtime_v2.control_plane.formatters.dashboard import build_dashboard_keyboard

        kb = build_dashboard_keyboard("attivi", page=0, total_count=3, page_size=5)
        all_callbacks = [
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
        ]
        assert "view:attivi" in all_callbacks
        assert "view:chiusi" in all_callbacks
        assert "view:bloccati" in all_callbacks
        assert "view:pnl" in all_callbacks
        assert "view:stats" in all_callbacks
        assert "refresh" in all_callbacks

    def test_noop_for_page_indicator(self):
        from src.runtime_v2.control_plane.formatters.dashboard import build_dashboard_keyboard

        kb = build_dashboard_keyboard("chiusi", page=0, total_count=8, page_size=5)
        pagination_row = kb.inline_keyboard[2]
        callbacks = [btn.callback_data for btn in pagination_row]
        assert "noop" in callbacks
