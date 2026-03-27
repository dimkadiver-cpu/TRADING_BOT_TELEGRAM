"""Risk exposure calculations for Layer 4 — Operation Rules Engine.

Modello risk-first: il centro del sistema è il rischio massimo accettato,
non la size della posizione.

Tutte le esposizioni sono espresse come percentuale del capitale.

Usage:
    from src.operation_rules.risk_calculator import (
        compute_risk_pct,
        compute_position_size_from_risk,
        sum_trader_exposure,
        sum_global_exposure,
        count_open_same_symbol,
    )
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk % computation for a new signal
# ---------------------------------------------------------------------------


def compute_risk_pct(
    risk_mode: str,
    risk_pct_of_capital: float,
    risk_usdt_fixed: float,
    capital_base_usdt: float,
) -> float:
    """Return the % of capital at risk for a new signal.

    With risk_pct_of_capital mode the result is simply risk_pct_of_capital.
    With risk_usdt_fixed mode the result is risk_usdt_fixed / capital_base * 100.
    Returns 0.0 if capital_base_usdt <= 0 in fixed mode.
    """
    if risk_mode == "risk_usdt_fixed":
        if capital_base_usdt <= 0:
            return 0.0
        return risk_usdt_fixed / capital_base_usdt * 100.0
    # default: risk_pct_of_capital
    return float(risk_pct_of_capital)


def compute_risk_budget_usdt(
    risk_mode: str,
    risk_pct_of_capital: float,
    risk_usdt_fixed: float,
    capital_base_usdt: float,
) -> float:
    """Return the risk budget in USDT for a new signal."""
    if risk_mode == "risk_usdt_fixed":
        return float(risk_usdt_fixed)
    return capital_base_usdt * risk_pct_of_capital / 100.0


# ---------------------------------------------------------------------------
# Position size calculation
# ---------------------------------------------------------------------------


def compute_position_size_from_risk(
    entry_prices: list[float],
    sl_price: float,
    risk_budget_usdt: float,
    leverage: int,
    capital_base_usdt: float,
) -> tuple[float, float, float]:
    """Compute position size from risk budget.

    Returns (position_size_usdt, position_size_pct, sl_distance_pct).

    Raises:
        ValueError: if sl_distance is zero or leverage <= 0.
    """
    avg_entry = sum(entry_prices) / len(entry_prices)
    sl_distance_pct = abs(avg_entry - sl_price) / avg_entry
    if sl_distance_pct == 0.0:
        raise ValueError("sl_distance_pct is zero — cannot compute position size")
    if leverage <= 0:
        raise ValueError(f"leverage must be > 0, got {leverage}")
    position_size_usdt = risk_budget_usdt / (sl_distance_pct * leverage)
    position_size_pct = (
        (position_size_usdt / capital_base_usdt * 100.0) if capital_base_usdt > 0 else 0.0
    )
    return position_size_usdt, position_size_pct, sl_distance_pct


# ---------------------------------------------------------------------------
# Open-signal exposure queries
# ---------------------------------------------------------------------------


def sum_trader_exposure(trader_id: str, db_path: str) -> float:
    """Sum risk_pct for all open non-blocked signals of a trader.

    Reads risk_budget_usdt and capital_base_usdt stored in operational_signals.
    Rows without these columns (pre-migration) contribute 0.
    """
    query = """
        SELECT os.risk_budget_usdt, os.capital_base_usdt
        FROM signals s
        JOIN operational_signals os ON os.attempt_key = s.attempt_key
        WHERE s.trader_id = ?
          AND s.status NOT IN ('CLOSED', 'CANCELLED')
          AND os.is_blocked = 0
          AND os.risk_budget_usdt IS NOT NULL
          AND os.capital_base_usdt IS NOT NULL
          AND os.capital_base_usdt > 0
    """
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query, (trader_id,)).fetchall()
    except sqlite3.OperationalError:
        logger.warning(
            "sum_trader_exposure: DB query failed for trader_id=%r db=%r — "
            "returning 0.0 (gate 7 will be permissive)",
            trader_id, db_path,
            exc_info=True,
        )
        return 0.0
    return sum(risk_b / cap_b * 100.0 for risk_b, cap_b in rows if cap_b > 0)


def sum_global_exposure(db_path: str) -> float:
    """Sum risk_pct across ALL open non-blocked signals globally."""
    query = """
        SELECT os.risk_budget_usdt, os.capital_base_usdt
        FROM signals s
        JOIN operational_signals os ON os.attempt_key = s.attempt_key
        WHERE s.status NOT IN ('CLOSED', 'CANCELLED')
          AND os.is_blocked = 0
          AND os.risk_budget_usdt IS NOT NULL
          AND os.capital_base_usdt IS NOT NULL
          AND os.capital_base_usdt > 0
    """
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query).fetchall()
    except sqlite3.OperationalError:
        logger.warning(
            "sum_global_exposure: DB query failed db=%r — "
            "returning 0.0 (gate 8 will be permissive)",
            db_path,
            exc_info=True,
        )
        return 0.0
    return sum(risk_b / cap_b * 100.0 for risk_b, cap_b in rows if cap_b > 0)


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
        logger.warning(
            "count_open_same_symbol: DB query failed for trader_id=%r symbol=%r db=%r — "
            "returning 0 (gate 5 will be permissive)",
            trader_id, symbol, db_path,
            exc_info=True,
        )
        return 0
