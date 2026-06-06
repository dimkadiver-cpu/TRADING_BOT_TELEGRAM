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
    assert "BLOCCATE" in text
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
    assert "RIABILITATE" in text


def test_resume_no_block():
    text = format_resume(ResumeResult("GLOBAL", None, False))
    assert "NESSUN BLOCCO" in text


def test_resume_per_trader():
    text = format_resume(ResumeResult("TRADER", "trader_a", True))
    assert "trader_a" in text


def test_start():
    text = format_start(ResumeResult("GLOBAL", None, True))
    assert "ATTIVATO" in text


def test_block_global():
    text = format_block(BlockResult("GLOBAL", None, "BTCUSDT", ["BTCUSDT", "ETHUSDT"]))
    assert "BTC/USDT" in text
    assert "GLOBAL" in text
    assert "ETH/USDT" in text
    assert "/unblock BTCUSDT" in text


def test_block_per_trader():
    text = format_block(BlockResult("PER_TRADER", "trader_a", "SOLUSDT", ["SOLUSDT"]))
    assert "trader_a" in text
    assert "SOL/USDT" in text


def test_unblock_global():
    text = format_unblock(UnblockResult("GLOBAL", None, "BTCUSDT", ["ETHUSDT"]))
    assert "SBLOCCATO" in text
    assert "BTC/USDT" in text
    assert "ETH/USDT" in text


def test_format_pause_spec_english():
    text = format_pause(scope="GLOBAL", mode="BLOCK_NEW_ENTRIES", source="operator", command="/pause")
    assert "EXECUTION PAUSED" in text
    assert "Scope: GLOBAL" in text
    assert "Mode: BLOCK_NEW_ENTRIES" in text
    assert "Effect:" in text
    assert "Source: operator" in text
    assert "Command: /pause" in text


def test_format_resume_spec_english():
    text = format_resume(scope="GLOBAL", mode="LIVE", source="operator", command="/resume")
    assert "EXECUTION RESUMED" in text
    assert "Scope: GLOBAL" in text
    assert "Mode: LIVE" in text
    assert "Effect:" in text
    assert "Source: operator" in text
    assert "Command: /resume" in text
