from __future__ import annotations

import sqlite3


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def apply_parser_test_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    # Check if raw_messages exists
    raw_messages_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_messages'"
    ).fetchone()

    if not raw_messages_exists:
        conn.execute("""
            CREATE TABLE raw_messages (
                raw_message_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id       TEXT    NOT NULL,
                source_chat_title    TEXT,
                source_type          TEXT,
                source_trader_id     TEXT,
                source_topic_id      INTEGER,
                telegram_message_id  INTEGER NOT NULL,
                reply_to_message_id  INTEGER,
                raw_text             TEXT,
                message_ts           TEXT    NOT NULL,
                acquired_at          TEXT    NOT NULL,
                acquisition_status   TEXT,
                has_media            INTEGER DEFAULT 0,
                media_kind           TEXT,
                media_mime_type      TEXT,
                media_filename       TEXT,
                media_blob           BLOB,
                UNIQUE(source_chat_id, telegram_message_id)
            )
        """)

    # Check if parser_runs exists
    parser_runs_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='parser_runs'"
    ).fetchone()

    if not parser_runs_exists:
        conn.execute("""
            CREATE TABLE parser_runs (
                run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at      TEXT    NOT NULL,
                completed_at    TEXT,
                db_scope        TEXT,
                trader_filter   TEXT,
                parser_system   TEXT    NOT NULL DEFAULT 'parser_v2',
                parser_version  TEXT,
                force_reparse   INTEGER NOT NULL DEFAULT 0,
                notes           TEXT
            )
        """)

    # Check if parser_results_v2 exists
    parser_results_v2_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='parser_results_v2'"
    ).fetchone()

    if not parser_results_v2_exists:
        conn.execute("""
            CREATE TABLE parser_results_v2 (
                parser_result_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            INTEGER NOT NULL,
                raw_message_id    INTEGER NOT NULL,
                trader_id         TEXT,
                parser_profile    TEXT,
                primary_class     TEXT,
                parse_status      TEXT,
                primary_intent    TEXT,
                confidence        REAL,
                canonical_json    TEXT,
                warnings_json     TEXT,
                diagnostics_json  TEXT,
                error_status      TEXT NOT NULL DEFAULT 'OK',
                error_message     TEXT,
                created_at        TEXT NOT NULL,
                UNIQUE(run_id, raw_message_id),
                FOREIGN KEY(run_id)           REFERENCES parser_runs(run_id),
                FOREIGN KEY(raw_message_id)   REFERENCES raw_messages(raw_message_id)
            )
        """)

    # Add new columns to raw_messages if missing
    _add_column_if_missing(conn, "raw_messages", "resolved_trader_id", "TEXT")
    _add_column_if_missing(conn, "raw_messages", "resolution_method", "TEXT")

    # Add new columns to parser_results_v2 if missing
    _add_column_if_missing(conn, "parser_results_v2", "trader_id", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "parser_profile", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "primary_class", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "parse_status", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "primary_intent", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "confidence", "REAL")
    _add_column_if_missing(conn, "parser_results_v2", "canonical_json", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "warnings_json", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "diagnostics_json", "TEXT")
    _add_column_if_missing(conn, "parser_results_v2", "error_message", "TEXT")

    # Add new columns to parser_runs if missing
    _add_column_if_missing(conn, "parser_runs", "completed_at", "TEXT")
    _add_column_if_missing(conn, "parser_runs", "db_scope", "TEXT")
    _add_column_if_missing(conn, "parser_runs", "trader_filter", "TEXT")
    _add_column_if_missing(conn, "parser_runs", "parser_version", "TEXT")
    _add_column_if_missing(conn, "parser_runs", "notes", "TEXT")

    # Now create indices if they don't exist (after all columns are guaranteed to exist)
    index_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_parser_runs_started_at'"
    ).fetchone()

    if not index_exists:
        conn.execute("""
            CREATE INDEX idx_parser_runs_started_at
                ON parser_runs(started_at)
        """)

    indices = [
        ("idx_parser_results_v2_run", "CREATE INDEX idx_parser_results_v2_run ON parser_results_v2(run_id)"),
        ("idx_parser_results_v2_raw", "CREATE INDEX idx_parser_results_v2_raw ON parser_results_v2(raw_message_id)"),
        ("idx_parser_results_v2_trader", "CREATE INDEX idx_parser_results_v2_trader ON parser_results_v2(trader_id)"),
        ("idx_parser_results_v2_class_status", "CREATE INDEX idx_parser_results_v2_class_status ON parser_results_v2(primary_class, parse_status)"),
        ("idx_parser_results_v2_error", "CREATE INDEX idx_parser_results_v2_error ON parser_results_v2(error_status)"),
    ]

    for idx_name, create_idx_sql in indices:
        idx_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (idx_name,)
        ).fetchone()
        if not idx_exists:
            conn.execute(create_idx_sql)

    conn.commit()


__all__ = ["apply_parser_test_schema"]
