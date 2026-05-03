"""Persistence for ParsedMessage dual-stack records (Fasa 4.5)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(slots=True)
class ParsedMessageRecord:
    raw_message_id: int
    trader_id: str
    primary_class: str
    validation_status: str
    composite: bool
    parsed_json: str
    intents_confirmed_json: str
    created_at: str


class ParsedMessageStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def upsert(self, record: ParsedMessageRecord) -> None:
        query = """
            INSERT INTO parsed_messages (
              raw_message_id,
              trader_id,
              primary_class,
              validation_status,
              composite,
              parsed_json,
              intents_confirmed_json,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(raw_message_id) DO UPDATE SET
              trader_id = excluded.trader_id,
              primary_class = excluded.primary_class,
              validation_status = excluded.validation_status,
              composite = excluded.composite,
              parsed_json = excluded.parsed_json,
              intents_confirmed_json = excluded.intents_confirmed_json
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                query,
                (
                    record.raw_message_id,
                    record.trader_id,
                    record.primary_class,
                    record.validation_status,
                    1 if record.composite else 0,
                    record.parsed_json,
                    record.intents_confirmed_json,
                    record.created_at,
                ),
            )
            conn.commit()

    def get_by_raw_message_id(self, raw_message_id: int) -> ParsedMessageRecord | None:
        query = """
            SELECT raw_message_id, trader_id, primary_class, validation_status,
                   composite, parsed_json, intents_confirmed_json, created_at
            FROM parsed_messages
            WHERE raw_message_id = ?
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(query, (raw_message_id,)).fetchone()
        if row is None:
            return None
        return ParsedMessageRecord(
            raw_message_id=row[0],
            trader_id=row[1],
            primary_class=row[2],
            validation_status=row[3],
            composite=bool(row[4]),
            parsed_json=row[5],
            intents_confirmed_json=row[6],
            created_at=row[7],
        )

    def delete_by_raw_message_ids(self, raw_message_ids: list[int]) -> int:
        ids = [int(raw_message_id) for raw_message_id in raw_message_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        query = f"DELETE FROM parsed_messages WHERE raw_message_id IN ({placeholders})"
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(query, ids)
            conn.commit()
        return int(cursor.rowcount or 0)
