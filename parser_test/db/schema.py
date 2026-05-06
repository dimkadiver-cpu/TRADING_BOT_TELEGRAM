from __future__ import annotations

import sqlite3


def apply_parser_test_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_messages (
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
        );

        CREATE TABLE IF NOT EXISTS parser_runs (
            run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT    NOT NULL,
            completed_at    TEXT,
            db_scope        TEXT,
            trader_filter   TEXT,
            parser_system   TEXT    NOT NULL DEFAULT 'parser_v2',
            parser_version  TEXT,
            force_reparse   INTEGER NOT NULL DEFAULT 0,
            notes           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_parser_runs_started_at
            ON parser_runs(started_at);

        CREATE TABLE IF NOT EXISTS parser_results_v2 (
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
        );

        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_run
            ON parser_results_v2(run_id);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_raw
            ON parser_results_v2(raw_message_id);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_trader
            ON parser_results_v2(trader_id);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_class_status
            ON parser_results_v2(primary_class, parse_status);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_error
            ON parser_results_v2(error_status);
    """)
    conn.commit()


__all__ = ["apply_parser_test_schema"]
