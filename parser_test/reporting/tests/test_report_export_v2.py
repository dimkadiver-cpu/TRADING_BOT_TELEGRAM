from __future__ import annotations

import sqlite3
from pathlib import Path

from parser_test.db.schema import apply_parser_test_schema
from parser_test.reporting.report_export_v2 import export_all


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def _insert_raw_message(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int,
    telegram_message_id: int,
    trader_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO raw_messages (
            raw_message_id, source_chat_id, source_topic_id, telegram_message_id,
            raw_text, message_ts, acquired_at, resolved_trader_id
        ) VALUES (?, 'chat1', NULL, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?)
        """,
        (raw_message_id, telegram_message_id, f"message {trader_id}", trader_id),
    )
    conn.execute(
        """
        INSERT INTO parser_results_v2 (
            run_id, raw_message_id, trader_id, parser_profile, primary_class, parse_status,
            primary_intent, confidence, canonical_json, warnings_json, diagnostics_json,
            error_status, error_message, created_at
        ) VALUES (?, ?, ?, 'trader_prova', 'INFO', 'PARSED', NULL, 0.9, '{}', NULL, NULL, 'OK', NULL, '2026-01-01T00:00:00+00:00')
        """,
        (1, raw_message_id, trader_id),
    )
    conn.commit()


def test_export_all_without_trader_filter_creates_separate_csv_sets_per_trader(tmp_path: Path) -> None:
    conn = _make_db()
    _insert_raw_message(conn, raw_message_id=1, telegram_message_id=101, trader_id="trader_a")
    _insert_raw_message(conn, raw_message_id=2, telegram_message_id=102, trader_id="trader_b")

    generated = export_all(conn, run_id=1, trader=None, reports_dir=tmp_path)

    assert generated
    assert (tmp_path / "run_1" / "trader_a_message_types_csv" / "trader_a_all_messages.csv").exists()
    assert (tmp_path / "run_1" / "trader_b_message_types_csv" / "trader_b_all_messages.csv").exists()
    assert not (tmp_path / "run_1" / "all_traders_message_types_csv").exists()


def test_export_all_with_trader_filter_keeps_single_trader_output(tmp_path: Path) -> None:
    conn = _make_db()
    _insert_raw_message(conn, raw_message_id=1, telegram_message_id=101, trader_id="trader_a")
    _insert_raw_message(conn, raw_message_id=2, telegram_message_id=102, trader_id="trader_b")

    generated = export_all(conn, run_id=1, trader="trader_a", reports_dir=tmp_path)

    assert generated
    assert (tmp_path / "run_1" / "trader_a_message_types_csv" / "trader_a_all_messages.csv").exists()
    assert not (tmp_path / "run_1" / "trader_b_message_types_csv").exists()
