"""Storage layer for CanonicalMessage v1 shadow results (parse_results_v1 table)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(slots=True)
class ParseResultV1Record:
    raw_message_id: int
    trader_id: str
    primary_class: str
    parse_status: str
    confidence: float
    canonical_json: str
    normalizer_error: str | None
    created_at: str


class ParseResultV1Store:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def upsert(self, record: ParseResultV1Record) -> None:
        query = """
            INSERT INTO parse_results_v1 (
              raw_message_id,
              trader_id,
              primary_class,
              parse_status,
              confidence,
              canonical_json,
              normalizer_error,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(raw_message_id) DO UPDATE SET
              trader_id         = excluded.trader_id,
              primary_class     = excluded.primary_class,
              parse_status      = excluded.parse_status,
              confidence        = excluded.confidence,
              canonical_json    = excluded.canonical_json,
              normalizer_error  = excluded.normalizer_error
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                query,
                (
                    record.raw_message_id,
                    record.trader_id,
                    record.primary_class,
                    record.parse_status,
                    record.confidence,
                    record.canonical_json,
                    record.normalizer_error,
                    record.created_at,
                ),
            )
            conn.commit()

    def get_by_raw_message_id(self, raw_message_id: int) -> ParseResultV1Record | None:
        query = """
            SELECT raw_message_id, trader_id, primary_class, parse_status,
                   confidence, canonical_json, normalizer_error, created_at
            FROM parse_results_v1
            WHERE raw_message_id = ?
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(query, (raw_message_id,)).fetchone()
        if row is None:
            return None
        return ParseResultV1Record(
            raw_message_id=row[0],
            trader_id=row[1],
            primary_class=row[2],
            parse_status=row[3],
            confidence=row[4],
            canonical_json=row[5],
            normalizer_error=row[6],
            created_at=row[7],
        )

    def count_by_class(self) -> dict[str, int]:
        query = """
            SELECT primary_class, COUNT(*) FROM parse_results_v1 GROUP BY primary_class
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_canonical_message(self, raw_message_id: int) -> dict | None:
        record = self.get_by_raw_message_id(raw_message_id)
        if record is None or record.normalizer_error:
            return None
        try:
            return json.loads(record.canonical_json)
        except (json.JSONDecodeError, ValueError):
            return None
