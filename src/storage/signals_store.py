"""Persistence for the signals table — write operations.

Used by Layer 4 integration in the Router to INSERT new signals when
a NEW_SIGNAL parse result passes all gate checks.

Usage:
    from src.storage.signals_store import SignalsStore, SignalRecord
    store = SignalsStore(db_path)
    store.insert(record)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(slots=True)
class SignalRecord:
    """Data needed to insert a row in the signals table."""

    attempt_key: str
    env: str
    channel_id: str
    root_telegram_id: str
    trader_id: str
    trader_prefix: str
    symbol: str | None
    side: str | None
    entry_json: str | None   # JSON list of {price, type} objects
    sl: float | None
    tp_json: str | None      # JSON list of {price} objects
    status: str              # "PENDING"
    confidence: float
    raw_text: str
    created_at: str
    updated_at: str


class SignalsStore:
    """Write accessor for the signals table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def insert(self, record: SignalRecord) -> None:
        """INSERT OR IGNORE a new signal.

        Uses INSERT OR IGNORE so duplicate attempt_keys (e.g. replayed messages)
        are silently skipped.
        """
        query = """
            INSERT OR IGNORE INTO signals (
              attempt_key, env, channel_id, root_telegram_id,
              trader_id, trader_prefix,
              symbol, side,
              entry_json, sl, tp_json,
              status, confidence, raw_text,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                query,
                (
                    record.attempt_key,
                    record.env,
                    record.channel_id,
                    record.root_telegram_id,
                    record.trader_id,
                    record.trader_prefix,
                    record.symbol,
                    record.side,
                    record.entry_json,
                    record.sl,
                    record.tp_json,
                    record.status,
                    record.confidence,
                    record.raw_text,
                    record.created_at,
                    record.updated_at,
                ),
            )
            conn.commit()
