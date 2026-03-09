PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS exchange_events (
  event_pk INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL DEFAULT 'T',
  source TEXT NOT NULL,
  event_id TEXT NOT NULL,
  received_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_exchange_events_dedup
ON exchange_events(env, source, event_id);
