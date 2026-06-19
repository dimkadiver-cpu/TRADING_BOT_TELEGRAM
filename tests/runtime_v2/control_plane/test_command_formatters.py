# tests/runtime_v2/control_plane/test_command_formatters.py
from __future__ import annotations

import pytest

from src.runtime_v2.control_plane.formatters.pnl import format_pnl
from src.runtime_v2.control_plane.formatters.stats import format_stats
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import (
    PnlView, StatsRow, StatsView, TradeRow, TradesView,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scope(account_id: str = "demo_1", trader_id: str | None = None) -> QueryScope:
    return QueryScope(
        account_id=account_id,
        trader_ids=[trader_id] if trader_id else None,
    )


def _trades_view(**kw) -> TradesView:
    base = dict(updated_at="14:32:05", total=0, rows=[], mark_snapshot_max_age_seconds=None)
    base.update(kw)
    return TradesView(**base)


def _pnl_view(**kw) -> PnlView:
    base = dict(
        updated_at="14:32:05",
        account_id="master_account",
        captured_at="14:32:05",
        source="exchange",
        equity_usdt=10432.50,
        available_balance_usdt=9100.00,
        total_open_risk_usdt=None,
        total_margin_used_usdt=820.00,
        open_count=2,
        partial_count=0,
        waiting_entry_count=0,
        gross_pnl=234.80,
        total_fees=-18.40,
        pnl_net=214.30,
    )
    base.update(kw)
    return PnlView(**base)


def _stats_view(**kw) -> StatsView:
    base = dict(
        updated_at="14:32:05",
        rows=[
            StatsRow(label="Oggi:", trade_count=3, win_pct=67.0, pnl_net=42.10, fees=-3.20),
            StatsRow(label="7 giorni:", trade_count=18, win_pct=61.0, pnl_net=180.40, fees=-14.50),
            StatsRow(label="30 giorni:", trade_count=52, win_pct=58.0, pnl_net=420.80, fees=-38.20),
            StatsRow(label="Totale:", trade_count=87, win_pct=59.0, pnl_net=214.30, fees=-62.40),
        ],
        best_chain_id=12,
        best_pnl=89.20,
        worst_chain_id=31,
        worst_pnl=-45.10,
    )
    base.update(kw)
    return StatsView(**base)


# ---------------------------------------------------------------------------
# format_trades — header with scope
# ---------------------------------------------------------------------------

def test_trades_header_with_scope_account_and_trader():
    """Header includes account_id · trader_id when scope has single trader."""
    scope = _scope("demo_1", "trader_a")
    text = format_trades(_trades_view(), scope=scope)
    assert "📊 TRADES — demo_1 · trader_a" in text


def test_trades_header_with_scope_account_only():
    """Header shows only account_id when scope has no trader_ids."""
    scope = _scope("demo_1")
    text = format_trades(_trades_view(), scope=scope)
    assert "📊 TRADES — demo_1" in text
    # trader suffix should not be present in header
    assert "demo_1 ·" not in text


def test_trades_header_no_scope():
    """Without scope, account_id defaults to — and no trader label."""
    text = format_trades(_trades_view())
    assert "TRADES" in text
    # no trader label when no scope
    assert "— ·" not in text


# ---------------------------------------------------------------------------
# format_trades — trade rows
# ---------------------------------------------------------------------------

def test_trades_pnl_with_value():
    """Trade row with unrealized_pnl shows PnL: +12.40 USDT."""
    view = _trades_view(
        total=1,
        rows=[TradeRow(
            chain_id=5, symbol="BTCUSDT", side="LONG", state="OPEN",
            has_sl=True, has_be=False,
            entry_avg_price=63500.0, open_position_qty=0.01,
            unrealized_pnl=12.40, mark_price=63600.0, mark_captured_at="14:31:47",
        )],
    )
    text = format_trades(view)
    assert "PnL: +12.40 USDT" in text


def test_trades_pnl_none_shows_dash():
    """Trade row without mark_price / unrealized_pnl shows PnL: —."""
    view = _trades_view(
        total=1,
        rows=[TradeRow(
            chain_id=7, symbol="ETHUSDT", side="SHORT", state="OPEN",
            has_sl=True, has_be=False,
            entry_avg_price=2140.0, open_position_qty=0.5,
            unrealized_pnl=None, mark_price=None, mark_captured_at=None,
        )],
    )
    text = format_trades(view)
    assert "PnL: —" in text


def test_trades_pnl_negative():
    """Negative PnL shows sign."""
    view = _trades_view(
        total=1,
        rows=[TradeRow(
            chain_id=7, symbol="ETHUSDT", side="SHORT", state="OPEN",
            has_sl=True, has_be=False,
            unrealized_pnl=-3.20,
        )],
    )
    text = format_trades(view)
    assert "PnL: -3.20 USDT" in text


def test_trades_mark_snapshot_line():
    """Mark snapshot time and age are shown in header area."""
    view = _trades_view(
        total=1,
        rows=[TradeRow(
            chain_id=5, symbol="BTCUSDT", side="LONG", state="OPEN",
            has_sl=True,
            mark_captured_at="14:31:47",
            unrealized_pnl=12.40,
        )],
        mark_snapshot_max_age_seconds=18.0,
    )
    text = format_trades(view)
    assert "Mark snapshot" in text
    assert "14:31:47" in text
    assert "18s fa" in text


def test_trades_freshness_warning_when_stale():
    """Freshness warning appears when mark_snapshot_max_age_seconds > threshold."""
    view = _trades_view(
        total=1,
        rows=[TradeRow(
            chain_id=5, symbol="BTCUSDT", side="LONG", state="OPEN",
            has_sl=True,
            mark_captured_at="14:00:00",
            unrealized_pnl=0.0,
        )],
        mark_snapshot_max_age_seconds=120.0,  # > 60 threshold
    )
    text = format_trades(view)
    assert "Snapshot oltre" in text or "Snapshot" in text


def test_trades_no_warning_when_fresh():
    """No staleness warning when snapshot is recent."""
    view = _trades_view(
        total=1,
        rows=[TradeRow(
            chain_id=5, symbol="BTCUSDT", side="LONG", state="OPEN",
            has_sl=True,
            mark_captured_at="14:31:47",
            unrealized_pnl=0.0,
        )],
        mark_snapshot_max_age_seconds=18.0,
    )
    text = format_trades(view)
    assert "oltre intervallo" not in text


# ---------------------------------------------------------------------------
# format_pnl
# ---------------------------------------------------------------------------

def test_pnl_header_with_scope():
    """Header shows account_id from scope."""
    scope = _scope("demo_1")
    text = format_pnl(_pnl_view(), scope=scope)
    assert "💰 PNL — demo_1" in text


def test_pnl_header_no_scope():
    """Without scope, format_pnl still renders."""
    text = format_pnl(_pnl_view())
    assert "PNL" in text


def test_pnl_snapshot_section():
    """Snapshot account section includes equity and balance."""
    text = format_pnl(_pnl_view())
    assert "Snapshot account" in text
    assert "Equity" in text
    assert "10,432.50 USDT" in text


def test_pnl_realized_section():
    """Realized section shows gross PnL and net."""
    text = format_pnl(_pnl_view())
    assert "Realizzato" in text
    assert "Gross PnL" in text
    assert "Netto" in text
    assert "+214.30 USDT" in text


def test_pnl_fees_omitted_when_none():
    """Fees line omitted when total_fees is None."""
    text = format_pnl(_pnl_view(total_fees=None))
    # Should still render without error
    assert "PNL" in text


def test_pnl_open_count():
    """Open position count is shown."""
    text = format_pnl(_pnl_view(open_count=2, waiting_entry_count=0))
    assert "2" in text


# ---------------------------------------------------------------------------
# format_stats
# ---------------------------------------------------------------------------

def test_stats_header_with_scope():
    """Header shows account_id and trader."""
    scope = _scope("demo_1", "trader_a")
    text = format_stats(_stats_view(), scope=scope)
    assert "📈 STATS — demo_1 · trader_a" in text


def test_stats_table_contains_all_periods():
    """Table rows include Oggi, 7 giorni, 30 giorni, Totale."""
    text = format_stats(_stats_view())
    assert "Oggi:" in text
    assert "7 giorni:" in text
    assert "30 giorni:" in text
    assert "Totale:" in text


def test_stats_table_aligned_columns():
    """Table renders header labels."""
    text = format_stats(_stats_view())
    assert "Trades" in text
    assert "Win%" in text
    assert "PnL netto" in text
    assert "Fees" in text


def test_stats_win_pct_formatted():
    """Win% is formatted as integer percentage."""
    text = format_stats(_stats_view())
    assert "67%" in text


def test_stats_pnl_signed():
    """PnL netto columns show sign."""
    text = format_stats(_stats_view())
    assert "+42.10" in text


def test_stats_best_worst_trade():
    """Best and worst trade lines include chain_id."""
    text = format_stats(_stats_view())
    assert "#12" in text
    assert "#31" in text


def test_stats_win_pct_dash_when_zero_trades():
    """Win% shows — when trade_count is 0."""
    view = _stats_view(
        rows=[StatsRow(label="Oggi:", trade_count=0, win_pct=None, pnl_net=0.0, fees=0.0)]
    )
    text = format_stats(view)
    assert "—" in text


def test_stats_no_scope():
    """format_stats works without scope."""
    text = format_stats(_stats_view())
    assert "STATS" in text
