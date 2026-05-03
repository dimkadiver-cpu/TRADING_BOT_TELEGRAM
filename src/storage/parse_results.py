"""Persistence for minimal parse results."""

from __future__ import annotations

from dataclasses import dataclass
import json
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
    parse_result_normalized_json: str | None
    created_at: str
    updated_at: str


class ParseResultStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_by_raw_message_id(self, raw_message_id: int) -> ParseResultRecord | None:
        query = """
            SELECT raw_message_id,
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
                   parse_result_normalized_json,
                   created_at,
                   updated_at
            FROM parse_results
            WHERE raw_message_id = ?
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(query, (raw_message_id,)).fetchone()
        if row is None:
            return None
        return ParseResultRecord(
            raw_message_id=row[0],
            eligibility_status=row[1],
            eligibility_reason=row[2],
            declared_trader_tag=row[3],
            resolved_trader_id=row[4],
            trader_resolution_method=row[5],
            message_type=row[6],
            parse_status=row[7],
            completeness=row[8],
            is_executable=bool(row[9]),
            symbol=row[10],
            direction=row[11],
            entry_raw=row[12],
            stop_raw=row[13],
            target_raw_list=row[14],
            leverage_hint=row[15],
            risk_hint=row[16],
            risky_flag=bool(row[17]),
            linkage_method=row[18],
            linkage_status=row[19],
            warning_text=row[20],
            notes=row[21],
            parse_result_normalized_json=row[22],
            created_at=row[23],
            updated_at=row[24],
        )

    def get_raw_text_by_signal_id(self, resolved_trader_id: str, signal_id: int) -> str | None:
        query = """
            SELECT rm.raw_text, pr.parse_result_normalized_json
            FROM parse_results pr
            JOIN raw_messages rm ON rm.raw_message_id = pr.raw_message_id
            WHERE pr.resolved_trader_id = ? AND pr.message_type = 'NEW_SIGNAL'
            ORDER BY rm.raw_message_id ASC
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query, (resolved_trader_id,)).fetchall()
        for row in rows:
            payload = row[1] or "{}"
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            entities = data.get("entities") if isinstance(data, dict) else None
            if isinstance(entities, dict) and entities.get("signal_id") == signal_id:
                return row[0]
        return None

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
              parse_result_normalized_json,
              created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
              parse_result_normalized_json=excluded.parse_result_normalized_json,
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
                    record.parse_result_normalized_json,
                    record.created_at,
                    record.updated_at,
                ),
            )
            conn.commit()

    def delete_by_raw_message_ids(self, raw_message_ids: list[int]) -> int:
        ids = [int(raw_message_id) for raw_message_id in raw_message_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        query = f"DELETE FROM parse_results WHERE raw_message_id IN ({placeholders})"
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(query, ids)
            conn.commit()
        return int(cursor.rowcount or 0)
