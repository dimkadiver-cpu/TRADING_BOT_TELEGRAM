# tests/runtime_v2/control_plane/test_readonly_formatters.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.control import format_control
from src.runtime_v2.control_plane.formatters.health import format_health
from src.runtime_v2.control_plane.formatters.reviews import format_reviews
from src.runtime_v2.control_plane.formatters.status import format_status, status_level
from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.status_queries import (
    BlockInfo, ControlView, HealthView, ReviewItem, ReviewsView,
    StatusView, TradeDetail, TradeEvent, TradeRow, TradesView,
)


def _status(**kw) -> StatusView:
    base = dict(
        updated_at="14:32:10", control_mode="NONE", new_entries_enabled=True,
        sync_age_seconds=4.0, open_count=7, partial_count=1, waiting_entry_count=2,
        review_count=0, pending_commands=2, failed_commands=0, no_sl_count=0,
    )
    base.update(kw)
    return StatusView(**base)


def test_status_level_green():
    assert status_level(_status()) == "🟢"


def test_status_level_yellow_on_review():
    assert status_level(_status(review_count=3)) == "🟡"


def test_status_level_red_on_no_sl():
    assert status_level(_status(no_sl_count=1)) == "🔴"


def test_status_level_red_on_failed_command():
    assert status_level(_status(failed_commands=2)) == "🔴"


def test_format_status_contains_sections():
    text = format_status(_status())
    assert "STATUS" in text
    assert "Open: 7" in text
    assert "Pending commands: 2" in text


def test_format_trades_empty():
    text = format_trades(TradesView(updated_at="14:32:10", total=0, rows=[]))
    assert "TRADES" in text
    assert "No open trades" in text


def test_format_trades_rows():
    view = TradesView(updated_at="14:32:10", total=1, rows=[
        TradeRow(chain_id=145, symbol="BTCUSDT", side="LONG", state="OPEN", has_sl=True),
    ])
    text = format_trades(view)
    assert "145" in text
    assert "BTC/USDT" in text
    # New spec-compact format: no SL/Entry lines in list view
    assert "#145 · BTC/USDT · LONG · OPEN" in text
    assert "Details: /trade 145" in text


def test_format_trade_detail():
    detail = TradeDetail(
        chain_id=145, symbol="BTCUSDT", side="LONG", trader_id="trader_a",
        account_id="main", state="OPEN", entry_avg_price=65020.0,
        current_stop_price=62000.0, original_message_link="https://t.me/c/1/2",
        last_events=["14:10 ENTRY_FILLED"],
    )
    text = format_trade_detail(detail)
    assert "#145" in text
    assert "BTC/USDT" in text
    assert "trader_a" in text


def test_format_trade_detail_none():
    assert "not found" in format_trade_detail(None).lower()


# ---------------------------------------------------------------------------
# Task-4 spec tests
# ---------------------------------------------------------------------------

def _make_detail(**kw) -> TradeDetail:
    defaults = dict(
        chain_id=5, symbol="BTCUSDT", side="LONG", trader_id="trader_a",
        account_id="demo_2", state="OPEN", entry_avg_price=63500.0,
        current_stop_price=62000.0, original_message_link=None,
        last_events=[], events=[], entry_legs=[], tp_legs=[],
        sl_price="62,000", has_be=False, unrealized_pnl=34.20,
        cum_realized_pnl=14.20, final_result=None,
        is_actionable=True, is_terminal=False,
    )
    defaults.update(kw)
    return TradeDetail(**defaults)


def test_trade_detail_header():
    detail = _make_detail(state="PARTIALLY_CLOSED")
    text = format_trade_detail(detail)
    assert "#5 · BTC/USDT · LONG · PARTIALLY_CLOSED" in text


def test_trade_detail_meta_section():
    text = format_trade_detail(_make_detail())
    assert "Trader: trader_a" in text
    assert "Exchange Account: demo_2" in text


def test_trade_detail_pnl_section_open_trade():
    text = format_trade_detail(_make_detail())
    assert "uPnL:" in text
    assert "rPnL:" in text


def test_trade_detail_actions_present_when_actionable():
    text = format_trade_detail(_make_detail(is_actionable=True))
    assert "Actions:" in text
    assert "/cancel 5" in text or "/close 5" in text


def test_trade_detail_no_actions_when_terminal():
    detail = _make_detail(
        state="CLOSED", is_actionable=False, is_terminal=True,
        unrealized_pnl=None, cum_realized_pnl=None,
        final_result={
            "pnl_net": 44.17, "pnl_gross": 45.20,
            "fees": -2.06, "funding": 0.03,
            "roi_net": 3.67, "ror": 9.12, "r_mult": 0.22,
        },
    )
    text = format_trade_detail(detail)
    assert "Final Result:" in text
    assert "Actions:" not in text


def test_trade_detail_waiting_entry_no_pnl():
    detail = _make_detail(state="WAITING_ENTRY", unrealized_pnl=None, cum_realized_pnl=None)
    text = format_trade_detail(detail)
    assert "uPnL:" not in text


def test_trade_detail_timeline_events():
    detail = _make_detail(events=[
        TradeEvent(
            label="SIGNAL ACCEPTED", timestamp="14 Jun 09:10:00",
            source="Signal", event_type=None, reason=None, clean_log_link=None,
        ),
        TradeEvent(
            label="ENTRY OPENED", timestamp="14 Jun 09:10:01",
            source="exchange", event_type=None, reason=None, clean_log_link=None,
        ),
    ])
    text = format_trade_detail(detail)
    assert "Events:" in text
    assert "SIGNAL ACCEPTED" in text
    assert "ENTRY OPENED" in text


def test_trade_detail_not_found():
    assert format_trade_detail(None) == "Trade not found."


def test_format_health():
    view = HealthView(
        updated_at="14:32:10",
        workers=[("Exchange sync", "OK", "last event 4s ago")],
        db_ok=True, exchange_connected=True, last_event_age_seconds=4.0,
    )
    text = format_health(view)
    assert "HEALTH" in text
    assert "Exchange sync" in text


def test_format_control_no_blocks():
    text = format_control(ControlView(new_entries_enabled=True))
    assert "ENABLED" in text
    assert "none" in text.lower()


def test_format_control_with_block_and_blacklist():
    view = ControlView(
        new_entries_enabled=False,
        active_blocks=[BlockInfo("GLOBAL", None, "BLOCK_NEW_ENTRIES", "14:10:33")],
        blacklist_global=["BTCUSDT"],
    )
    text = format_control(view)
    assert "BLOCKED" in text
    assert "BTC/USDT" in text


def test_format_reviews():
    view = ReviewsView(updated_at="14:32:10", items=[
        ReviewItem(chain_id=151, symbol="SOLUSDT", reason="missing_sl"),
    ])
    text = format_reviews(view)
    assert "#151" in text
    assert "SOL/USDT" in text
    assert "missing_sl" in text
