# tests/runtime_v2/control_plane/test_status_queries_scoped.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

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


def _ts_offset(days: int = 0, hours: int = 0) -> str:
    """Return ISO timestamp offset by the given days/hours into the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return dt.isoformat()


def _add_chain(
    conn: sqlite3.Connection,
    cid: int,
    state: str,
    *,
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    sl: float | None = None,
    account_id: str = "account_A",
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


def _add_market_snapshot(
    conn: sqlite3.Connection,
    account_id: str,
    symbol: str,
    mark_price: float,
    *,
    side: str = "LONG",
    unrealized_pnl: float | None = None,
    cum_realized_pnl: float | None = None,
    captured_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ops_position_snapshots "
        "(account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        " cum_realized_pnl, source, captured_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            account_id,
            symbol,
            side,
            0.0,
            mark_price,
            unrealized_pnl,
            cum_realized_pnl,
            "test",
            captured_at or _now(),
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


SCOPE_A = QueryScope(account_id="account_A", trader_ids=None)
SCOPE_B = QueryScope(account_id="account_B", trader_ids=None)
SCOPE_A_ONLY_T1 = QueryScope(account_id="account_A", trader_ids=["trader_1"])


# ---------------------------------------------------------------------------
# Scope isolation tests
# ---------------------------------------------------------------------------

class TestScopeIsolation:
    """Data from account_B must not be visible when scope is account_A."""

    def test_get_status_isolates_by_account(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 1, "OPEN", account_id="account_A", trader_id="t1")
            _add_chain(conn, 2, "OPEN", account_id="account_B", trader_id="t2")
            _add_chain(conn, 3, "PARTIALLY_CLOSED", account_id="account_B", trader_id="t2")
        conn.close()

        q = StatusQueries(ops_db)
        view_a = q.get_status(SCOPE_A)
        assert view_a.open_count == 1
        assert view_a.partial_count == 0

        view_b = q.get_status(SCOPE_B)
        assert view_b.open_count == 1
        assert view_b.partial_count == 1

    def test_get_open_trades_isolates_by_account(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 10, "OPEN", account_id="account_A", symbol="ETH/USDT")
            _add_chain(conn, 11, "OPEN", account_id="account_B", symbol="BTC/USDT")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        assert view.total == 1
        assert view.rows[0].symbol == "ETH/USDT"

    def test_get_open_trades_isolates_by_trader(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 20, "OPEN", account_id="account_A", trader_id="trader_1", symbol="SOL/USDT")
            _add_chain(conn, 21, "OPEN", account_id="account_A", trader_id="trader_2", symbol="BNB/USDT")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A_ONLY_T1)
        assert view.total == 1
        assert view.rows[0].symbol == "SOL/USDT"

    def test_get_reviews_isolates_by_account(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 30, "REVIEW_REQUIRED", account_id="account_A")
            _add_chain(conn, 31, "REVIEW_REQUIRED", account_id="account_B")
        conn.close()

        q = StatusQueries(ops_db)
        items_a = q.get_reviews(SCOPE_A).items
        assert len(items_a) == 1
        assert items_a[0].chain_id == 30

        items_b = q.get_reviews(SCOPE_B).items
        assert len(items_b) == 1
        assert items_b[0].chain_id == 31

    def test_get_pnl_isolates_by_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 40, "OPEN", account_id="account_A")
            _add_chain(conn, 41, "OPEN", account_id="account_B")
            _add_chain(conn, 42, "WAITING_ENTRY", account_id="account_A")
            conn.execute(
                "INSERT INTO ops_account_snapshots "
                "(account_id, equity_usdt, available_balance_usdt, "
                " total_open_risk_usdt, total_margin_used_usdt, source, captured_at, payload_json) "
                "VALUES ('account_A', 5000.0, 4000.0, 100.0, 200.0, 'sync', ?, '{}')",
                (_now(),),
            )
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_pnl(SCOPE_A)
        assert view.account_id == "account_A"
        assert view.open_count == 1
        assert view.waiting_entry_count == 1

    def test_get_stats_isolates_by_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            # account_A: 2 closed trades, 1 winning
            _add_chain(conn, 50, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=100.0, cumulative_fees=5.0)
            _add_chain(conn, 51, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=-50.0, cumulative_fees=3.0)
            # account_B: 1 closed trade
            _add_chain(conn, 52, "CLOSED", account_id="account_B",
                       cumulative_gross_pnl=999.0, cumulative_fees=1.0)
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        total_row = next(r for r in stats.rows if r.label == "All time")
        assert total_row.trade_count == 2
        # best PnL is account_A chain 50 = 100.0, NOT 999.0 from B
        assert stats.best_pnl == 100.0
        assert stats.best_chain_id == 50

    def test_get_closed_trades_isolates_by_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 60, "CLOSED", account_id="account_A", symbol="ETH/USDT")
            _add_chain(conn, 61, "CLOSED", account_id="account_B", symbol="BTC/USDT")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_closed_trades(SCOPE_A)
        assert view.total_count == 1
        assert view.rows[0].symbol == "ETH/USDT"

    def test_get_blocked_trades_isolates_by_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 70, "REVIEW_REQUIRED", account_id="account_A")
            _add_chain(conn, 71, "REVIEW_REQUIRED", account_id="account_B")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_blocked_trades(SCOPE_A)
        assert len(view.rows) == 1
        assert view.rows[0].chain_id == 70


# ---------------------------------------------------------------------------
# Unrealized PnL tests
# ---------------------------------------------------------------------------

class TestUnrealizedPnl:
    def test_long_pnl_positive(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 100, "OPEN",
                account_id="account_A", symbol="BTC/USDT", side="LONG",
                entry_avg_price=50000.0, open_position_qty=0.1,
            )
            _add_market_snapshot(conn, "account_A", "BTC/USDT", mark_price=51000.0)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        assert view.total == 1
        row = view.rows[0]
        assert row.unrealized_pnl is not None
        # (51000 - 50000) * 0.1 * 1 = 100.0
        assert abs(row.unrealized_pnl - 100.0) < 0.001

    def test_short_pnl_positive_when_price_drops(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 101, "OPEN",
                account_id="account_A", symbol="ETH/USDT", side="SHORT",
                entry_avg_price=3000.0, open_position_qty=1.0,
            )
            _add_market_snapshot(conn, "account_A", "ETH/USDT", mark_price=2900.0, side="SHORT")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        row = view.rows[0]
        assert row.unrealized_pnl is not None
        # (2900 - 3000) * 1.0 * -1 = 100.0
        assert abs(row.unrealized_pnl - 100.0) < 0.001

    def test_pnl_none_when_no_mark_price(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 102, "OPEN",
                account_id="account_A", symbol="SOL/USDT", side="LONG",
                entry_avg_price=50.0, open_position_qty=10.0,
            )
            # No market snapshot for SOL/USDT
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        assert view.rows[0].unrealized_pnl is None

    def test_pnl_none_when_no_entry_price(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 103, "OPEN",
                account_id="account_A", symbol="BNB/USDT", side="LONG",
                entry_avg_price=None, open_position_qty=5.0,
            )
            _add_market_snapshot(conn, "account_A", "BNB/USDT", mark_price=500.0)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        assert view.rows[0].unrealized_pnl is None

    def test_pnl_none_when_zero_qty(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 104, "WAITING_ENTRY",
                account_id="account_A", symbol="ADA/USDT", side="LONG",
                entry_avg_price=0.5, open_position_qty=0.0,
            )
            _add_market_snapshot(conn, "account_A", "ADA/USDT", mark_price=0.6)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        assert view.rows[0].unrealized_pnl is None

    def test_mark_snapshot_age_returned(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 105, "OPEN",
                account_id="account_A", symbol="BTC/USDT", side="LONG",
                entry_avg_price=50000.0, open_position_qty=0.5,
            )
            _add_market_snapshot(conn, "account_A", "BTC/USDT", mark_price=50100.0,
                                 captured_at=_ts_offset(hours=0))
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        assert view.mark_snapshot_max_age_seconds is not None
        assert view.mark_snapshot_max_age_seconds >= 0

    def test_mark_price_from_correct_account(self, ops_db):
        """mark_price for account_A must not use snapshot from account_B."""
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(
                conn, 110, "OPEN",
                account_id="account_A", symbol="BTC/USDT", side="LONG",
                entry_avg_price=50000.0, open_position_qty=1.0,
            )
            # account_A has no snapshot for BTC/USDT
            _add_market_snapshot(conn, "account_B", "BTC/USDT", mark_price=99999.0)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_open_trades(SCOPE_A)
        # Should not use account_B's mark_price
        assert view.rows[0].unrealized_pnl is None


# ---------------------------------------------------------------------------
# Stats bucketing tests
# ---------------------------------------------------------------------------

class TestStatsBucketing:
    def test_today_bucket_counts_only_today(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            # today
            _add_chain(conn, 200, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=50.0, created_at=_ts_offset(hours=1))
            # 3 days ago
            _add_chain(conn, 201, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=-20.0, created_at=_ts_offset(days=3))
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        today_row = next(r for r in stats.rows if r.label == "Today")
        assert today_row.trade_count == 1
        assert abs(today_row.pnl_net - 50.0) < 0.001

    def test_7d_bucket_includes_recent_trades(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 210, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=30.0, created_at=_ts_offset(days=5))
            _add_chain(conn, 211, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=20.0, created_at=_ts_offset(days=10))
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        d7_row = next(r for r in stats.rows if r.label == "Last 7d")
        assert d7_row.trade_count == 1

    def test_total_bucket_includes_all(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            for i, pnl in enumerate([100.0, -30.0, 50.0]):
                _add_chain(conn, 220 + i, "CLOSED", account_id="account_A",
                           cumulative_gross_pnl=pnl,
                           created_at=_ts_offset(days=i * 10))
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        total_row = next(r for r in stats.rows if r.label == "All time")
        assert total_row.trade_count == 3

    def test_win_pct_calculation(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            # 2 wins, 1 loss
            _add_chain(conn, 230, "CLOSED", account_id="account_A", cumulative_gross_pnl=10.0)
            _add_chain(conn, 231, "CLOSED", account_id="account_A", cumulative_gross_pnl=20.0)
            _add_chain(conn, 232, "CLOSED", account_id="account_A", cumulative_gross_pnl=-5.0)
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        total_row = next(r for r in stats.rows if r.label == "All time")
        assert total_row.win_pct is not None
        assert abs(total_row.win_pct - (2 / 3 * 100)) < 0.01

    def test_win_pct_none_when_no_trades(self, ops_db):
        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        total_row = next(r for r in stats.rows if r.label == "All time")
        assert total_row.win_pct is None
        assert total_row.trade_count == 0

    def test_best_worst_chain(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 240, "CLOSED", account_id="account_A", cumulative_gross_pnl=200.0)
            _add_chain(conn, 241, "CLOSED", account_id="account_A", cumulative_gross_pnl=-80.0)
            _add_chain(conn, 242, "CLOSED", account_id="account_A", cumulative_gross_pnl=50.0)
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        assert stats.best_chain_id == 240
        assert abs(stats.best_pnl - 200.0) < 0.001
        assert stats.worst_chain_id == 241
        assert abs(stats.worst_pnl - (-80.0)) < 0.001


# ---------------------------------------------------------------------------
# get_closed_trades pagination
# ---------------------------------------------------------------------------

class TestClosedTrades:
    def test_pagination(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            for i in range(7):
                _add_chain(conn, 300 + i, "CLOSED", account_id="account_A",
                           symbol=f"COIN{i}/USDT")
        conn.close()

        q = StatusQueries(ops_db)
        page0 = q.get_closed_trades(SCOPE_A, page=0, page_size=5)
        assert page0.total_count == 7
        assert len(page0.rows) == 5
        assert page0.page == 0

        page1 = q.get_closed_trades(SCOPE_A, page=1, page_size=5)
        assert len(page1.rows) == 2
        assert page1.page == 1

    def test_gross_pnl_in_rows(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 310, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=75.5)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_closed_trades(SCOPE_A)
        assert view.rows[0].gross_pnl == 75.5

    def test_closed_at_falls_back_to_updated_at(self, ops_db):
        """Since closed_at column does not exist in migrations, updated_at is used."""
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 320, "CLOSED", account_id="account_A")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_closed_trades(SCOPE_A)
        assert view.rows[0].closed_at is not None  # falls back to updated_at


# ---------------------------------------------------------------------------
# get_blocked_trades
# ---------------------------------------------------------------------------

class TestBlockedTrades:
    def test_review_required_included(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 400, "REVIEW_REQUIRED", account_id="account_A", symbol="SOL/USDT")
            conn.execute(
                "INSERT INTO ops_lifecycle_events "
                "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
                "VALUES (400,'REVIEW_REQUIRED','enrichment','{\"reason\": \"missing_sl\"}','ev400',?)",
                (_now(),),
            )
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_blocked_trades(SCOPE_A)
        assert len(view.rows) == 1
        row = view.rows[0]
        assert row.chain_id == 400
        assert row.state == "REVIEW_REQUIRED"
        assert row.reason == "missing_sl"

    def test_exec_failed_command_included(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 410, "OPEN", account_id="account_A", symbol="BTC/USDT")
            conn.execute(
                "INSERT INTO ops_execution_commands "
                "(trade_chain_id, command_type, status, payload_json, idempotency_key, created_at, updated_at) "
                "VALUES (410, 'PLACE_ENTRY', 'FAILED', '{\"error\": \"timeout\"}', 'cmd410', ?, ?)",
                (_now(), _now()),
            )
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_blocked_trades(SCOPE_A)
        assert len(view.rows) == 1
        row = view.rows[0]
        assert row.chain_id == 410
        assert row.state == "EXEC_FAILED"

    def test_deduplication_review_wins(self, ops_db):
        """A chain that is both REVIEW_REQUIRED and has EXEC_FAILED command appears once."""
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 420, "REVIEW_REQUIRED", account_id="account_A")
            conn.execute(
                "INSERT INTO ops_execution_commands "
                "(trade_chain_id, command_type, status, payload_json, idempotency_key, created_at, updated_at) "
                "VALUES (420, 'PLACE_ENTRY', 'FAILED', '{}', 'cmd420', ?, ?)",
                (_now(), _now()),
            )
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_blocked_trades(SCOPE_A)
        # Should appear only once, as REVIEW_REQUIRED (seen first)
        assert len(view.rows) == 1
        assert view.rows[0].state == "REVIEW_REQUIRED"


# ---------------------------------------------------------------------------
# get_pnl extended (realized PnL fields)
# ---------------------------------------------------------------------------

class TestPnlExtended:
    def test_gross_pnl_aggregated_from_closed_chains(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 500, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=100.0, cumulative_fees=5.0, cumulative_funding=2.0)
            _add_chain(conn, 501, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=-30.0, cumulative_fees=3.0, cumulative_funding=1.0)
            # Open trade — must not be included
            _add_chain(conn, 502, "OPEN", account_id="account_A",
                       cumulative_gross_pnl=999.0, cumulative_fees=0.0)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_pnl(SCOPE_A)
        assert view.gross_pnl is not None
        assert abs(view.gross_pnl - 70.0) < 0.001   # 100 + (-30)
        assert view.total_fees is not None
        assert abs(view.total_fees - 11.0) < 0.001  # 5+2+3+1
        assert view.pnl_net is not None
        assert abs(view.pnl_net - 59.0) < 0.001     # 70 - 11

    def test_gross_pnl_none_when_no_closed_trades(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 510, "OPEN", account_id="account_A")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_pnl(SCOPE_A)
        assert view.gross_pnl is None
        assert view.pnl_net is None

    def test_pnl_scope_b_not_included_in_scope_a(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 520, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=50.0, cumulative_fees=2.0)
            _add_chain(conn, 521, "CLOSED", account_id="account_B",
                       cumulative_gross_pnl=9999.0, cumulative_fees=1.0)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_pnl(SCOPE_A)
        assert view.gross_pnl is not None
        assert abs(view.gross_pnl - 50.0) < 0.001


# ---------------------------------------------------------------------------
# health — global, no scope
# ---------------------------------------------------------------------------

class TestHealthIsGlobal:
    def test_health_has_no_scope_param(self, ops_db):
        """get_health() must not accept a scope parameter (global only)."""
        q = StatusQueries(ops_db)
        view = q.get_health()
        assert view.db_ok is True


# ---------------------------------------------------------------------------
# Backward compatibility — unscoped calls still work
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_get_status_no_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 600, "OPEN", account_id="account_A")
        conn.close()
        q = StatusQueries(ops_db)
        view = q.get_status()  # No scope — should still work
        assert view.open_count >= 1

    def test_get_open_trades_no_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 610, "OPEN", account_id="account_A")
        conn.close()
        q = StatusQueries(ops_db)
        view = q.get_open_trades()
        assert view.total >= 1

    def test_get_pnl_no_scope(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            conn.execute(
                "INSERT INTO ops_account_snapshots "
                "(account_id, equity_usdt, available_balance_usdt, "
                " total_open_risk_usdt, total_margin_used_usdt, source, captured_at, payload_json) "
                "VALUES ('account_A', 1000.0, 900.0, 50.0, 100.0, 'sync', ?, '{}')",
                (_now(),),
            )
        conn.close()
        q = StatusQueries(ops_db)
        view = q.get_pnl()  # No scope
        assert view.equity_usdt == 1000.0


# ---------------------------------------------------------------------------
# GAP 1 — get_pnl: fees e funding separati
# ---------------------------------------------------------------------------

class TestPnlFundingSeparated:
    def test_fees_and_funding_returned_separately(self, ops_db):
        """PnlView deve esporre fees_usdt e funding_usdt come campi distinti."""
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 700, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=100.0, cumulative_fees=5.0, cumulative_funding=2.0)
            _add_chain(conn, 701, "CLOSED", account_id="account_A",
                       cumulative_gross_pnl=-30.0, cumulative_fees=3.0, cumulative_funding=1.0)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_pnl(SCOPE_A)

        assert view.fees_usdt is not None, "fees_usdt deve essere presente"
        assert abs(view.fees_usdt - 8.0) < 0.001, f"fees attesi 8.0, got {view.fees_usdt}"
        assert view.funding_usdt is not None, "funding_usdt deve essere presente"
        assert abs(view.funding_usdt - 3.0) < 0.001, f"funding attesi 3.0, got {view.funding_usdt}"
        # total_fees resta la somma per backward compat
        assert abs(view.total_fees - 11.0) < 0.001

    def test_fees_zero_funding_zero_when_no_closed_trades(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 710, "OPEN", account_id="account_A")
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_pnl(SCOPE_A)
        assert view.fees_usdt is None
        assert view.funding_usdt is None


# ---------------------------------------------------------------------------
# GAP 2 — get_stats: best_symbol e worst_symbol
# ---------------------------------------------------------------------------

class TestStatsBestWorstSymbol:
    def test_best_worst_chain_includes_symbol(self, ops_db):
        """StatsView deve includere best_symbol e worst_symbol."""
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 800, "CLOSED", account_id="account_A",
                       symbol="BTC/USDT", cumulative_gross_pnl=200.0)
            _add_chain(conn, 801, "CLOSED", account_id="account_A",
                       symbol="ETH/USDT", cumulative_gross_pnl=-80.0)
            _add_chain(conn, 802, "CLOSED", account_id="account_A",
                       symbol="SOL/USDT", cumulative_gross_pnl=50.0)
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)

        assert stats.best_symbol == "BTC/USDT", f"best_symbol atteso BTC/USDT, got {stats.best_symbol!r}"
        assert stats.worst_symbol == "ETH/USDT", f"worst_symbol atteso ETH/USDT, got {stats.worst_symbol!r}"

    def test_best_worst_symbol_none_when_no_closed_trades(self, ops_db):
        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        assert stats.best_symbol is None
        assert stats.worst_symbol is None


# ---------------------------------------------------------------------------
# Global scope (account_id=None) — _scope_where produces WHERE 1=1
# ---------------------------------------------------------------------------

def test_get_open_trades_global_scope_returns_all_accounts(tmp_path):
    """Global scope must return trades from every account."""
    db_path = str(tmp_path / "ops.db")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)

    _add_chain(conn, 1, "OPEN", account_id="account_A", trader_id="trader_a")
    _add_chain(conn, 2, "OPEN", account_id="account_B", trader_id="trader_b")
    _add_chain(conn, 3, "OPEN", account_id="account_C", trader_id="trader_c")
    conn.commit()
    conn.close()

    sq = StatusQueries(db_path)
    global_scope = QueryScope(account_id=None, trader_ids=None)
    view = sq.get_open_trades(global_scope)

    ids = {r.chain_id for r in view.rows}
    assert ids == {1, 2, 3}


def test_get_closed_trades_global_scope_returns_all_accounts(tmp_path):
    """Global scope must return closed trades from every account."""
    db_path = str(tmp_path / "ops.db")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)

    _add_chain(conn, 10, "CLOSED", account_id="account_A", trader_id="trader_a",
               cumulative_gross_pnl=10.0)
    _add_chain(conn, 11, "CLOSED", account_id="account_B", trader_id="trader_b",
               cumulative_gross_pnl=20.0)
    conn.commit()
    conn.close()

    sq = StatusQueries(db_path)
    global_scope = QueryScope(account_id=None, trader_ids=None)
    view = sq.get_closed_trades(global_scope)

    ids = {r.chain_id for r in view.rows}
    assert ids == {10, 11}


def test_get_stats_global_scope_aggregates_all_accounts(tmp_path):
    """Global scope stats must count trades from all accounts."""
    db_path = str(tmp_path / "ops.db")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)

    _add_chain(conn, 20, "CLOSED", account_id="account_A", trader_id="trader_a",
               cumulative_gross_pnl=5.0)
    _add_chain(conn, 21, "CLOSED", account_id="account_B", trader_id="trader_b",
               cumulative_gross_pnl=15.0)
    conn.commit()
    conn.close()

    sq = StatusQueries(db_path)
    global_scope = QueryScope(account_id=None, trader_ids=None)
    view = sq.get_stats(global_scope)

    totale = next(r for r in view.rows if r.label == "All time")
    assert totale.trade_count == 2


def test_get_pnl_global_scope_uses_latest_snapshot_across_all_accounts(tmp_path):
    """Global scope get_pnl must return the most-recent snapshot across ALL accounts."""
    db_path = str(tmp_path / "ops.db")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)

    # Insert snapshots for two different accounts; demo_2 snapshot is newer
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        " total_margin_used_usdt, source, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("demo_1", 1000.0, 900.0, 50.0, 100.0, "ws", "2026-01-01T10:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        " total_margin_used_usdt, source, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("demo_2", 5000.0, 4500.0, 200.0, 500.0, "ws", "2026-01-01T11:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    sq = StatusQueries(db_path)
    global_scope = QueryScope(account_id=None, trader_ids=None)
    view = sq.get_pnl(global_scope)

    # Should return demo_2 snapshot (more recent)
    assert view.account_id == "demo_2"
    assert view.equity_usdt == 5000.0
