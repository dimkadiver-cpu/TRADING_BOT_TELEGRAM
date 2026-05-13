from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.parser_v2.contracts.canonical_message import CanonicalMessage


class CanonicalMessageRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(
        self,
        raw_message_id: int,
        canonical: CanonicalMessage,
        run_context: str = "live",
    ) -> int:
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO canonical_messages
                    (raw_message_id, run_context, parser_profile, schema_version,
                     primary_class, parse_status, primary_intent, confidence,
                     canonical_json, warnings_json, diagnostics_json, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_message_id,
                    run_context,
                    canonical.parser_profile,
                    canonical.schema_version,
                    canonical.primary_class,
                    canonical.parse_status,
                    canonical.primary_intent,
                    canonical.confidence,
                    canonical.model_dump_json(),
                    json.dumps(canonical.warnings),
                    json.dumps(canonical.diagnostics),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                return cursor.lastrowid
            row = conn.execute(
                "SELECT canonical_message_id FROM canonical_messages "
                "WHERE raw_message_id = ? AND run_context = ?",
                (raw_message_id, run_context),
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def get_by_raw_message_id(
        self,
        raw_message_id: int,
        run_context: str = "live",
    ) -> CanonicalMessage | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT canonical_json FROM canonical_messages "
                "WHERE raw_message_id = ? AND run_context = ?",
                (raw_message_id, run_context),
            ).fetchone()
            if row is None:
                return None
            return CanonicalMessage.model_validate_json(row[0])
        finally:
            conn.close()


__all__ = ["CanonicalMessageRepository"]
