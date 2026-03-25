"""Risk exposure calculations for Layer 4 — Operation Rules Engine.

All exposure values are expressed as a percentage of total portfolio capital.

Usage:
    from src.operation_rules.risk_calculator import (
        compute_exposure,
        sum_trader_exposure,
        sum_global_exposure,
    )
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


# ---------------------------------------------------------------------------
# Exposure computation for a new signal
# ---------------------------------------------------------------------------


def compute_exposure(
    entry_prices: list[float],
    sl_price: float | None,
    position_size_pct: float,
    leverage: int,
) -> float:
    """Compute the % portfolio at risk for a single signal.

    Formula: position_size_pct × (|avg_entry - SL| / avg_entry) × leverage

    Returns 0.0 if data is insufficient (conservative — won't trigger cap blocks).
    """
    if not entry_prices or sl_price is None or sl_price <= 0:
        return 0.0
    avg_entry = sum(entry_prices) / len(entry_prices)
    if avg_entry <= 0:
        return 0.0
    sl_distance_pct = abs(avg_entry - sl_price) / avg_entry
    return position_size_pct * sl_distance_pct * leverage


# ---------------------------------------------------------------------------
# Open-signal exposure queries
# ---------------------------------------------------------------------------


def _signal_exposure_from_row(
    entry_json: str | None,
    sl: float | None,
    position_size_pct: float | None,
    leverage: int | None,
) -> float:
    """Compute exposure for a single stored signal row. Returns 0.0 on any error."""
    if not entry_json or sl is None or sl <= 0:
        return 0.0
    ps = float(position_size_pct) if position_size_pct is not None else 1.0
    lev = int(leverage) if leverage is not None else 1
    try:
        entries_data: Any = json.loads(entry_json)
    except (json.JSONDecodeError, TypeError):
        return 0.0

    prices: list[float] = []
    if isinstance(entries_data, list):
        for item in entries_data:
            if isinstance(item, dict):
                p = item.get("price")
            else:
                p = item
            if p is not None:
                try:
                    prices.append(float(p))
                except (TypeError, ValueError):
                    pass
    elif isinstance(entries_data, (int, float)):
        prices = [float(entries_data)]

    return compute_exposure(prices, sl, ps, lev)


def sum_trader_exposure(trader_id: str, db_path: str) -> float:
    """Sum exposure (% portfolio) for all open non-blocked signals of a trader.

    Joins operational_signals for position_size_pct and leverage because the
    signals table alone does not store these parameters.
    """
    query = """
        SELECT s.entry_json, s.sl, os.position_size_pct, os.leverage
        FROM signals s
        JOIN operational_signals os ON os.attempt_key = s.attempt_key
        WHERE s.trader_id = ?
          AND s.status NOT IN ('CLOSED', 'CANCELLED')
          AND os.is_blocked = 0
    """
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query, (trader_id,)).fetchall()
    except sqlite3.OperationalError:
        # Table may not exist yet in test DBs without full migration
        return 0.0

    total = 0.0
    for entry_json, sl, ps_pct, lev in rows:
        total += _signal_exposure_from_row(entry_json, sl, ps_pct, lev)
    return total


def sum_global_exposure(db_path: str) -> float:
    """Sum exposure (% portfolio) across ALL open non-blocked signals globally."""
    query = """
        SELECT s.entry_json, s.sl, os.position_size_pct, os.leverage
        FROM signals s
        JOIN operational_signals os ON os.attempt_key = s.attempt_key
        WHERE s.status NOT IN ('CLOSED', 'CANCELLED')
          AND os.is_blocked = 0
    """
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query).fetchall()
    except sqlite3.OperationalError:
        return 0.0

    total = 0.0
    for entry_json, sl, ps_pct, lev in rows:
        total += _signal_exposure_from_row(entry_json, sl, ps_pct, lev)
    return total


def count_open_same_symbol(trader_id: str, symbol: str, db_path: str) -> int:
    """Count open signals for *trader_id* and *symbol* (case-insensitive)."""
    if not symbol:
        return 0
    query = """
        SELECT COUNT(*) FROM signals
        WHERE trader_id = ?
          AND UPPER(symbol) = UPPER(?)
          AND status NOT IN ('CLOSED', 'CANCELLED')
    """
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(query, (trader_id, symbol)).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
