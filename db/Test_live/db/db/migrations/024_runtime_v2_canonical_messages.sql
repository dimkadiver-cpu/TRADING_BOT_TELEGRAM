-- db/migrations/024_runtime_v2_canonical_messages.sql
CREATE TABLE IF NOT EXISTS canonical_messages (
    canonical_message_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id        INTEGER NOT NULL,
    run_context           TEXT    NOT NULL DEFAULT 'live',
    parser_profile        TEXT    NOT NULL,
    schema_version        TEXT    NOT NULL,
    primary_class         TEXT    NOT NULL,
    parse_status          TEXT    NOT NULL,
    primary_intent        TEXT,
    confidence            REAL    NOT NULL,
    canonical_json        TEXT    NOT NULL,
    warnings_json         TEXT    NOT NULL DEFAULT '[]',
    diagnostics_json      TEXT    NOT NULL DEFAULT '{}',
    parsed_at             TEXT    NOT NULL,
    UNIQUE(raw_message_id, run_context)
);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_raw
    ON canonical_messages(raw_message_id);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_class
    ON canonical_messages(primary_class, parse_status);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_profile
    ON canonical_messages(parser_profile);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_parsed_at
    ON canonical_messages(parsed_at);
