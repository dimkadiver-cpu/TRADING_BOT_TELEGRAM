from __future__ import annotations

from src.runtime_v2.control_plane.formatters.block import format_block, format_unblock
from src.runtime_v2.control_plane.formatters.pause import (
    format_pause, format_resume, format_start,
)
from src.runtime_v2.control_plane.service import (
    BlockResult, PauseResult, ResumeResult, UnblockResult,
)


def test_pause_global():
    text = format_pause(PauseResult("GLOBAL", None, "BLOCK_NEW_ENTRIES", False))
    assert "BLOCKED" in text
    assert "GLOBAL" in text
    assert "/resume" in text


def test_pause_per_trader():
    text = format_pause(PauseResult("TRADER", "trader_a", "BLOCK_NEW_ENTRIES", False))
    assert "trader_a" in text
    assert "/resume trader_a" in text


def test_pause_already_active_mentions_existing():
    text = format_pause(PauseResult("GLOBAL", None, "BLOCK_NEW_ENTRIES", True))
    assert "already" in text.lower()


def test_resume_with_block():
    text = format_resume(ResumeResult("GLOBAL", None, True))
    assert "RE-ENABLED" in text


def test_resume_no_block():
    text = format_resume(ResumeResult("GLOBAL", None, False))
    assert "NO ACTIVE BLOCK" in text


def test_resume_per_trader():
    text = format_resume(ResumeResult("TRADER", "trader_a", True))
    assert "trader_a" in text


def test_start():
    text = format_start(ResumeResult("GLOBAL", None, True))
    assert "ACTIVATED" in text


def test_block_global():
    text = format_block(BlockResult("GLOBAL", None, "BTCUSDT", ["BTCUSDT", "ETHUSDT"]))
    assert "BTCUSDT" in text
    assert "GLOBAL" in text
    assert "ETHUSDT" in text


def test_block_per_trader():
    text = format_block(BlockResult("PER_TRADER", "trader_a", "SOLUSDT", ["SOLUSDT"]))
    assert "trader_a" in text
    assert "SOLUSDT" in text


def test_unblock_global():
    text = format_unblock(UnblockResult("GLOBAL", None, "BTCUSDT", ["ETHUSDT"]))
    assert "UNBLOCKED" in text
    assert "ETHUSDT" in text
