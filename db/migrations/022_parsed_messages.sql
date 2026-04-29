-- ParsedMessage dual-stack storage (Fasa 4.5).
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS parsed_messages (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_message_id        INTEGER NOT NULL,
  trader_id             TEXT    NOT NULL,
  primary_class         TEXT    NOT NULL,
  validation_status     TEXT    NOT NULL,
  composite             INTEGER NOT NULL DEFAULT 0,
  parsed_json           TEXT    NOT NULL,
  intents_confirmed_json TEXT   NOT NULL,
  created_at            TEXT    NOT NULL,
  FOREIGN KEY(raw_message_id) REFERENCES raw_messages(raw_message_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_parsed_messages_raw
ON parsed_messages(raw_message_id);

CREATE INDEX IF NOT EXISTS idx_parsed_messages_validation
ON parsed_messages(validation_status, primary_class);
