"""Read-only accessor for the signals table.

Used by Layer 4 (Operation Rules Engine) and Layer 5 (Target Resolver)
to query open signals without modifying them.

Usage:
    from src.storage.signals_query import SignalsQuery
    sq = SignalsQuery(db_path)
    count = sq.count_open_same_symbol("trader_3", "BTCUSDT")
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OpenSignal:
    """Minimal view of an open signal row."""

    attempt_key: str
    trader_id: str
    symbol: str | None
    side: str | None
    status: str
    entry_json: str | None
    sl: float | None
    confidence: float
    root_telegram_id: str | None


class SignalsQuery:
    """Read-only accessor for the signals table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def count_open_same_symbol(self, trader_id: str, symbol: str) -> int:
        """Count open (non-CLOSED/CANCELLED) signals for *trader_id* and *symbol*."""
        if not symbol:
            return 0
        query = """
            SELECT COUNT(*) FROM signals
            WHERE trader_id = ?
              AND UPPER(symbol) = UPPER(?)
              AND status NOT IN ('CLOSED', 'CANCELLED')
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(query, (trader_id, symbol)).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.OperationalError:
            return 0

    def get_open_by_trader(self, trader_id: str) -> list[OpenSignal]:
        """Return all open signals for *trader_id*."""
        query = """
            SELECT attempt_key, trader_id, symbol, side, status,
                   entry_json, sl, confidence, root_telegram_id
            FROM signals
            WHERE trader_id = ?
              AND status NOT IN ('CLOSED', 'CANCELLED')
            ORDER BY rowid ASC
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(query, (trader_id,)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._row_to_open(r) for r in rows]

    def get_open_by_trader_and_symbol(self, trader_id: str, symbol: str) -> list[OpenSignal]:
        """Return all open signals for *trader_id* and *symbol*."""
        query = """
            SELECT attempt_key, trader_id, symbol, side, status,
                   entry_json, sl, confidence, root_telegram_id
            FROM signals
            WHERE trader_id = ?
              AND UPPER(symbol) = UPPER(?)
              AND status NOT IN ('CLOSED', 'CANCELLED')
            ORDER BY rowid ASC
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(query, (trader_id, symbol)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._row_to_open(r) for r in rows]

    def get_open_by_side(self, trader_id: str, side: str) -> list[OpenSignal]:
        """Return all open signals for *trader_id* filtered by *side* (BUY/SELL)."""
        query = """
            SELECT attempt_key, trader_id, symbol, side, status,
                   entry_json, sl, confidence, root_telegram_id
            FROM signals
            WHERE trader_id = ?
              AND UPPER(side) = UPPER(?)
              AND status NOT IN ('CLOSED', 'CANCELLED')
            ORDER BY rowid ASC
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(query, (trader_id, side)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._row_to_open(r) for r in rows]

    def get_all_open(self, trader_id: str) -> list[OpenSignal]:
        """Return all open signals for *trader_id* regardless of side/symbol."""
        return self.get_open_by_trader(trader_id)

    def get_by_attempt_key(self, attempt_key: str) -> OpenSignal | None:
        """Return a signal by attempt_key, or None if not found."""
        query = """
            SELECT attempt_key, trader_id, symbol, side, status,
                   entry_json, sl, confidence, root_telegram_id
            FROM signals
            WHERE attempt_key = ?
            LIMIT 1
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(query, (attempt_key,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return self._row_to_open(row) if row else None

    def get_by_root_telegram_id(
        self, trader_id: str, root_telegram_id: str
    ) -> OpenSignal | None:
        """Return the signal matching *trader_id* and *root_telegram_id*, or None."""
        query = """
            SELECT attempt_key, trader_id, symbol, side, status,
                   entry_json, sl, confidence, root_telegram_id
            FROM signals
            WHERE trader_id = ?
              AND root_telegram_id = ?
            LIMIT 1
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(query, (trader_id, str(root_telegram_id))).fetchone()
        except sqlite3.OperationalError:
            return None
        return self._row_to_open(row) if row else None

    def get_op_signal_id_for_attempt_key(self, attempt_key: str) -> int | None:
        """Return the op_signal_id from operational_signals for *attempt_key*, or None."""
        query = """
            SELECT op_signal_id FROM operational_signals
            WHERE attempt_key = ?
            LIMIT 1
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(query, (attempt_key,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return int(row[0]) if row else None

    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_open(row: Any) -> OpenSignal:
        return OpenSignal(
            attempt_key=str(row[0]),
            trader_id=str(row[1]),
            symbol=row[2],
            side=row[3],
            status=str(row[4]),
            entry_json=row[5],
            sl=float(row[6]) if row[6] is not None else None,
            confidence=float(row[7]) if row[7] is not None else 0.0,
            root_telegram_id=row[8],
        )
