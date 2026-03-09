"""Persistence for minimal parse results."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass(slots=True)
class ParseResultRecord:
    raw_message_id: int
    eligibility_status: str
    eligibility_reason: str
    declared_trader_tag: str | None
    resolved_trader_id: str | None
    trader_resolution_method: str
    message_type: str
    parse_status: str
    completeness: str
    is_executable: bool
    symbol: str | None
    direction: str | None
    entry_raw: str | None
    stop_raw: str | None
    target_raw_list: str | None
    leverage_hint: str | None
    risk_hint: str | None
    risky_flag: bool
    linkage_method: str | None
    linkage_status: str | None
    warning_text: str | None
    notes: str | None
    created_at: str
    updated_at: str


class ParseResultStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def upsert(self, record: ParseResultRecord) -> None:
        query = """
            INSERT INTO parse_results(
              raw_message_id,
              eligibility_status,
              eligibility_reason,
              declared_trader_tag,
              resolved_trader_id,
              trader_resolution_method,
              message_type,
              parse_status,
              completeness,
              is_executable,
              symbol,
              direction,
              entry_raw,
              stop_raw,
              target_raw_list,
              leverage_hint,
              risk_hint,
              risky_flag,
              linkage_method,
              linkage_status,
              warning_text,
              notes,
              created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(raw_message_id) DO UPDATE SET
              eligibility_status=excluded.eligibility_status,
              eligibility_reason=excluded.eligibility_reason,
              declared_trader_tag=excluded.declared_trader_tag,
              resolved_trader_id=excluded.resolved_trader_id,
              trader_resolution_method=excluded.trader_resolution_method,
              message_type=excluded.message_type,
              parse_status=excluded.parse_status,
              completeness=excluded.completeness,
              is_executable=excluded.is_executable,
              symbol=excluded.symbol,
              direction=excluded.direction,
              entry_raw=excluded.entry_raw,
              stop_raw=excluded.stop_raw,
              target_raw_list=excluded.target_raw_list,
              leverage_hint=excluded.leverage_hint,
              risk_hint=excluded.risk_hint,
              risky_flag=excluded.risky_flag,
              linkage_method=excluded.linkage_method,
              linkage_status=excluded.linkage_status,
              warning_text=excluded.warning_text,
              notes=excluded.notes,
              updated_at=excluded.updated_at
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                query,
                (
                    record.raw_message_id,
                    record.eligibility_status,
                    record.eligibility_reason,
                    record.declared_trader_tag,
                    record.resolved_trader_id,
                    record.trader_resolution_method,
                    record.message_type,
                    record.parse_status,
                    record.completeness,
                    1 if record.is_executable else 0,
                    record.symbol,
                    record.direction,
                    record.entry_raw,
                    record.stop_raw,
                    record.target_raw_list,
                    record.leverage_hint,
                    record.risk_hint,
                    1 if record.risky_flag else 0,
                    record.linkage_method,
                    record.linkage_status,
                    record.warning_text,
                    record.notes,
                    record.created_at,
                    record.updated_at,
                ),
            )
            conn.commit()
