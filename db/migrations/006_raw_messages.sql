PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS raw_messages (
  raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_chat_id TEXT NOT NULL,
  source_chat_title TEXT,
  source_type TEXT,
  source_trader_id TEXT,

  telegram_message_id INTEGER NOT NULL,
  reply_to_message_id INTEGER,

  raw_text TEXT,
  message_ts TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED',

  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_messages_dedup
ON raw_messages(source_chat_id, telegram_message_id);

CREATE INDEX IF NOT EXISTS idx_raw_messages_msg_ts
ON raw_messages(message_ts);
