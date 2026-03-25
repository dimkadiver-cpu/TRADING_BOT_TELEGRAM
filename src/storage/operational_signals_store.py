"""Persistence for the operational_signals table.

Used by Layer 4+5 integration in the Router to INSERT operational signals
after the Operation Rules Engine and Target Resolver have both run.

Usage:
    from src.storage.operational_signals_store import (
        OperationalSignalsStore,
        OperationalSignalRecord,
    )
    store = OperationalSignalsStore(db_path)
    op_signal_id = store.insert(record)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(slots=True)
class OperationalSignalRecord:
    """All columns for an operational_signals row."""

    parse_result_id: int
    attempt_key: str | None        # NULL for UPDATE messages
    trader_id: str
    message_type: str              # NEW_SIGNAL | UPDATE

    is_blocked: bool
    block_reason: str | None

    # Set A — apertura posizione (solo NEW_SIGNAL) — modello risk-first
    risk_mode: str | None
    risk_pct_of_capital: float | None
    risk_usdt_fixed: float | None
    capital_base_usdt: float | None
    risk_budget_usdt: float | None
    sl_distance_pct: float | None
    position_size_usdt: float | None
    position_size_pct: float | None
    entry_split_json: str | None   # JSON {"E1": 0.5, "E2": 0.5}
    leverage: int | None
    risk_hint_used: bool

    # Set B — snapshot management rules
    management_rules_json: str | None

    # Price corrections hook (future)
    price_corrections_json: str | None

    # Audit
    applied_rules_json: str | None   # JSON list[str]
    warnings_json: str | None        # JSON list[str]

    # Target resolution
    resolved_target_ids: str | None  # JSON list[int]
    target_eligibility: str | None   # ELIGIBLE | INELIGIBLE | WARN | UNRESOLVED
    target_reason: str | None

    created_at: str


class OperationalSignalsStore:
    """Write accessor for the operational_signals table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def insert(self, record: OperationalSignalRecord) -> int:
        """INSERT a new operational_signal and return its op_signal_id."""
        query = """
            INSERT INTO operational_signals (
              parse_result_id, attempt_key, trader_id, message_type,
              is_blocked, block_reason,
              risk_mode, risk_pct_of_capital, risk_usdt_fixed,
              capital_base_usdt, risk_budget_usdt, sl_distance_pct,
              position_size_usdt, position_size_pct,
              entry_split_json, leverage, risk_hint_used,
              management_rules_json, price_corrections_json,
              applied_rules_json, warnings_json,
              resolved_target_ids, target_eligibility, target_reason,
              created_at
            ) VALUES (
              ?, ?, ?, ?,
              ?, ?,
              ?, ?, ?,
              ?, ?, ?,
              ?, ?,
              ?, ?, ?,
              ?, ?,
              ?, ?,
              ?, ?, ?,
              ?
            )
        """
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                query,
                (
                    record.parse_result_id,
                    record.attempt_key,
                    record.trader_id,
                    record.message_type,
                    1 if record.is_blocked else 0,
                    record.block_reason,
                    record.risk_mode,
                    record.risk_pct_of_capital,
                    record.risk_usdt_fixed,
                    record.capital_base_usdt,
                    record.risk_budget_usdt,
                    record.sl_distance_pct,
                    record.position_size_usdt,
                    record.position_size_pct,
                    record.entry_split_json,
                    record.leverage,
                    1 if record.risk_hint_used else 0,
                    record.management_rules_json,
                    record.price_corrections_json,
                    record.applied_rules_json,
                    record.warnings_json,
                    record.resolved_target_ids,
                    record.target_eligibility,
                    record.target_reason,
                    record.created_at,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)  # type: ignore[arg-type]

    def get_parse_result_id(self, raw_message_id: int) -> int | None:
        """Look up parse_result_id from parse_results by raw_message_id.

        This method is here so the Router can get the FK without touching
        ParseResultStore.
        """
        query = """
            SELECT parse_result_id FROM parse_results
            WHERE raw_message_id = ?
            LIMIT 1
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(query, (raw_message_id,)).fetchone()
            return int(row[0]) if row else None
        except sqlite3.OperationalError:
            return None
