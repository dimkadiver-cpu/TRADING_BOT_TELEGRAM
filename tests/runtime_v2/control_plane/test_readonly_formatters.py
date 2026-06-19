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
    StatusView, TradeDetail, TradeRow, TradesView,
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
    assert "SL: set" in text


def test_format_trade_detail():
    detail = TradeDetail(
        chain_id=145, symbol="BTCUSDT", side="LONG", trader_id="trader_a",
        account_id="main", state="OPEN", entry_avg_price=65020.0,
        current_stop_price=62000.0, original_message_link="https://t.me/c/1/2",
        last_events=["14:10 ENTRY_FILLED"],
    )
    text = format_trade_detail(detail)
    assert "TRADE #145" in text
    assert "BTC/USDT" in text
    assert "trader_a" in text
    assert "14:10 ENTRY_FILLED" in text
    assert "Use:" in text
    assert "https://t.me/c/1/2" in text


def test_format_trade_detail_none():
    assert "not found" in format_trade_detail(None).lower()


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
