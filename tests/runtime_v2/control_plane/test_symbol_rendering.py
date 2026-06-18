from __future__ import annotations

from src.runtime_v2.control_plane.formatters.block import format_block, format_unblock
from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log
from src.runtime_v2.control_plane.formatters.control import format_control
from src.runtime_v2.control_plane.formatters.reviews import format_reviews
from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log
from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.service import BlockResult, UnblockResult
from src.runtime_v2.control_plane.status_queries import (
    BlockInfo,
    ControlView,
    ReviewItem,
    ReviewsView,
    TradeDetail,
    TradeRow,
    TradesView,
)


def test_clean_log_formats_compact_symbol_for_display() -> None:
    text = format_clean_log(
        "SIGNAL_ACCEPTED",
        {"chain_id": 1, "symbol": "ASRUSDT", "side": "LONG", "source": "runtime"},
    )
    assert "ASR/USDT" in text


def test_trade_detail_formats_compact_symbol_for_display() -> None:
    text = format_trade_detail(
        TradeDetail(
            chain_id=1,
            symbol="ASRUSDT",
            side="LONG",
            trader_id="trader_a",
            account_id="main",
            state="OPEN",
            entry_avg_price=None,
            current_stop_price=None,
            original_message_link=None,
            last_events=[],
        )
    )
    assert "ASR/USDT" in text


def test_trades_formats_compact_symbol_for_display() -> None:
    text = format_trades(
        TradesView(
            updated_at="14:32:10",
            total=1,
            rows=[TradeRow(chain_id=1, symbol="ASRUSDT", side="LONG", state="OPEN", has_sl=True)],
        )
    )
    assert "ASR/USDT" in text


def test_reviews_formats_compact_symbol_for_display() -> None:
    text = format_reviews(
        ReviewsView(updated_at="14:32:10", items=[ReviewItem(chain_id=1, symbol="ASRUSDT", reason="missing_sl")])
    )
    assert "ASR/USDT" in text


def test_control_formats_blacklist_symbols_for_display() -> None:
    text = format_control(
        ControlView(
            new_entries_enabled=False,
            active_blocks=[BlockInfo("GLOBAL", None, "BLOCK_NEW_ENTRIES", "14:10:33")],
            blacklist_global=["ASRUSDT"],
            blacklist_per_trader={"trader_a": ["BTCUSDT"]},
        )
    )
    assert "ASR/USDT" in text
    assert "BTC/USDT" in text


def test_block_formatters_format_display_but_keep_command_raw() -> None:
    blocked = format_block(BlockResult("GLOBAL", None, "ASRUSDT", ["ASRUSDT"]))
    unblocked = format_unblock(UnblockResult("GLOBAL", None, "ASRUSDT", ["BTCUSDT"]))
    assert "ASR/USDT" in blocked
    assert "/unblock ASRUSDT" in blocked
    assert "ASR/USDT" in unblocked
    assert "BTC/USDT" in unblocked


def test_tech_log_formats_symbol_in_context_for_display() -> None:
    text = format_tech_log(
        "GATEWAY_ENTRY_ALL_FAILED",
        {
            "level": "ERROR",
            "symbol": "ASRUSDT",
            "chain_id": 42,
            "reason": "test",
        },
    )
    assert "ASR/USDT" in text
    assert "#42" in text
