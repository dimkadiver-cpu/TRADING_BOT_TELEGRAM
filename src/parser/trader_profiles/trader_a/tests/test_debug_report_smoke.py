from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from src.core.timeutils import utc_now_iso
from src.parser.trader_profiles.trader_a.debug_report import _as_text, fetch_trader_a_messages_from_db, generate_report_from_db


class TraderADebugReportSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(prefix="tsb_debug_report_", suffix=".sqlite3")
        os.close(fd)
        self.db_path = path
        self._create_minimal_db()

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    def _create_minimal_db(self) -> None:
        now = utc_now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE raw_messages (
                  raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_chat_id TEXT NOT NULL,
                  telegram_message_id INTEGER NOT NULL,
                  reply_to_message_id INTEGER,
                  raw_text TEXT,
                  message_ts TEXT NOT NULL,
                  acquired_at TEXT NOT NULL,
                  acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED'
                );
                CREATE TABLE parse_results (
                  parse_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  raw_message_id INTEGER NOT NULL,
                  declared_trader_tag TEXT,
                  resolved_trader_id TEXT
                );
                """
            )
            conn.execute(
                """
                INSERT INTO raw_messages(
                  source_chat_id, telegram_message_id, reply_to_message_id, raw_text, message_ts, acquired_at, acquisition_status
                ) VALUES ('-100123', 501, 500, 'move stop to be [trader#A]', ?, ?, 'ACQUIRED')
                """,
                (now, now),
            )
            conn.execute(
                """
                INSERT INTO parse_results(raw_message_id, declared_trader_tag, resolved_trader_id)
                VALUES (1, 'A', 'A')
                """
            )
            conn.commit()

    def test_generate_report_from_db_has_expected_shape(self) -> None:
        rows = generate_report_from_db(db_path=self.db_path, limit=10)
        self.assertTrue(rows)
        first = rows[0]
        self.assertIn("db_row_id", first)
        self.assertIn("telegram_message_id", first)
        self.assertIn("raw_text", first)
        self.assertIn("message_type", first)
        self.assertIn("intents", first)
        self.assertIn("target_refs", first)
        self.assertIn("entities", first)
        self.assertIn("reported_results", first)
        self.assertIn("warnings", first)
        self.assertIn("confidence", first)

    def test_trader_a_filter_and_text_output(self) -> None:
        rows = generate_report_from_db(db_path=self.db_path, limit=10, trader_a_only=True)
        self.assertEqual(len(rows), 1)
        fetched = fetch_trader_a_messages_from_db(db_path=self.db_path, limit=10, trader_a_only=True)
        self.assertEqual(len(fetched), 1)
        rendered = _as_text(rows)
        self.assertIn("raw_message_id=", rendered)
        self.assertIn("message_type:", rendered)


if __name__ == "__main__":
    unittest.main()
