-- Shadow table for CanonicalMessage v1 output.
-- Written in parallel with existing parse_results; old flow is untouched.
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS parse_results_v1 (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_message_id    INTEGER NOT NULL,
  trader_id         TEXT    NOT NULL,
  primary_class     TEXT    NOT NULL,
  parse_status      TEXT    NOT NULL,
  confidence        REAL    NOT NULL,
  canonical_json    TEXT    NOT NULL,
  normalizer_error  TEXT,
  created_at        TEXT    NOT NULL,
  FOREIGN KEY(raw_message_id) REFERENCES raw_messages(raw_message_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_parse_results_v1_raw
ON parse_results_v1(raw_message_id);

CREATE INDEX IF NOT EXISTS idx_parse_results_v1_class
ON parse_results_v1(primary_class, parse_status);
