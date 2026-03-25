"""Async accessor functions for the `signals` table.

All functions use aiosqlite and return typed SignalRow dataclasses.
They are read-only — writes to signals belong to Sistema 1 (Fase 5).

Usage:
    from src.storage.signals_query import (
        SignalRow,
        count_open,
        get_by_root_telegram_id,
        get_by_trader_signal_id,
        get_open_by_symbol,
        get_open_by_trader,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiosqlite


# ---------------------------------------------------------------------------
# Row type
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SignalRow:
    """Typed representation of a row from the signals table."""

    attempt_key: str
    env: str
    channel_id: str
    root_telegram_id: str
    trader_id: str
    trader_prefix: str
    trader_signal_id: int | None
    symbol: str | None
    side: str | None
    entry_json: str | None
    sl: float | None
    tp_json: str | None
    status: str
    confidence: float
    raw_text: str
    created_at: str
    updated_at: str


# column order must match SELECT * from signals (001_init.sql schema)
_COLUMNS = (
    "attempt_key", "env", "channel_id", "root_telegram_id",
    "trader_id", "trader_prefix", "trader_signal_id",
    "symbol", "side",
    "entry_json", "sl", "tp_json",
    "status", "confidence",
    "raw_text", "created_at", "updated_at",
)

_SELECT = (
    "SELECT attempt_key, env, channel_id, root_telegram_id, "
    "trader_id, trader_prefix, trader_signal_id, "
    "symbol, side, "
    "entry_json, sl, tp_json, "
    "status, confidence, "
    "raw_text, created_at, updated_at "
    "FROM signals"
)


def _row(raw: tuple) -> SignalRow:  # type: ignore[type-arg]
    return SignalRow(
        attempt_key=raw[0],
        env=raw[1],
        channel_id=raw[2],
        root_telegram_id=raw[3],
        trader_id=raw[4],
        trader_prefix=raw[5],
        trader_signal_id=raw[6],
        symbol=raw[7],
        side=raw[8],
        entry_json=raw[9],
        sl=raw[10],
        tp_json=raw[11],
        status=raw[12],
        confidence=raw[13],
        raw_text=raw[14],
        created_at=raw[15],
        updated_at=raw[16],
    )


# ---------------------------------------------------------------------------
# Accessor functions
# ---------------------------------------------------------------------------

async def count_open(
    trader_id: str,
    symbol: str | None,
    db_path: Path | str,
) -> int:
    """Count open signals for *trader_id* + *symbol* (status != 'CLOSED').

    Returns 0 when the table does not exist yet (fresh DB).
    """
    if symbol is None:
        return 0
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM signals "
                "WHERE trader_id = ? AND symbol = ? AND status != 'CLOSED'",
                (trader_id, symbol),
            ) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else 0
    except aiosqlite.OperationalError:
        return 0


async def get_by_root_telegram_id(
    telegram_msg_id: int | str,
    trader_id: str,
    db_path: Path | str,
) -> SignalRow | None:
    """Look up a signal by its root Telegram message ID (STRONG/REPLY resolution).

    Searches across all statuses — eligibility is checked separately by the
    resolver after this call.

    Args:
        telegram_msg_id: The Telegram message ID of the original signal
            (stored as root_telegram_id TEXT in signals).
        trader_id: Scoped to this trader.
        db_path: Path to the SQLite DB.

    Returns:
        First matching SignalRow, or None if not found.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                f"{_SELECT} WHERE root_telegram_id = ? AND trader_id = ?",
                (str(telegram_msg_id), trader_id),
            ) as cur:
                raw = await cur.fetchone()
                return _row(raw) if raw else None
    except aiosqlite.OperationalError:
        return None


async def get_by_trader_signal_id(
    signal_id: int,
    trader_id: str,
    db_path: Path | str,
) -> SignalRow | None:
    """Look up a signal by trader_signal_id (STRONG/EXPLICIT_ID resolution).

    Args:
        signal_id: The integer ID used by the trader channel to label signals.
        trader_id: Scoped to this trader (index enforces uniqueness per trader).
        db_path: Path to the SQLite DB.

    Returns:
        Matching SignalRow, or None if not found.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                f"{_SELECT} WHERE trader_signal_id = ? AND trader_id = ?",
                (signal_id, trader_id),
            ) as cur:
                raw = await cur.fetchone()
                return _row(raw) if raw else None
    except aiosqlite.OperationalError:
        return None


async def get_open_by_symbol(
    trader_id: str,
    symbol: str,
    db_path: Path | str,
) -> list[SignalRow]:
    """Return all open signals for *trader_id* + *symbol* (SYMBOL resolution).

    "Open" means status != 'CLOSED'.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                f"{_SELECT} WHERE trader_id = ? AND symbol = ? AND status != 'CLOSED'",
                (trader_id, symbol),
            ) as cur:
                return [_row(r) async for r in cur]
    except aiosqlite.OperationalError:
        return []


async def get_open_by_trader(
    trader_id: str,
    side: Literal["BUY", "SELL"] | None,
    db_path: Path | str,
) -> list[SignalRow]:
    """Return open signals for *trader_id*, optionally filtered by side (GLOBAL resolution).

    Args:
        trader_id: Scoped to this trader.
        side: 'BUY' for all_long, 'SELL' for all_short, None for all_positions.
        db_path: Path to the SQLite DB.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            if side is None:
                async with db.execute(
                    f"{_SELECT} WHERE trader_id = ? AND status != 'CLOSED'",
                    (trader_id,),
                ) as cur:
                    return [_row(r) async for r in cur]
            else:
                async with db.execute(
                    f"{_SELECT} WHERE trader_id = ? AND side = ? AND status != 'CLOSED'",
                    (trader_id, side),
                ) as cur:
                    return [_row(r) async for r in cur]
    except aiosqlite.OperationalError:
        return []
