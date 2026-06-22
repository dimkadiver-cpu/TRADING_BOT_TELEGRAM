CREATE TABLE IF NOT EXISTS raw_message_revisions (
  revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_message_id INTEGER NOT NULL,
  source_chat_id TEXT NOT NULL,
  telegram_message_id INTEGER NOT NULL,
  revision_kind TEXT NOT NULL,
  run_context TEXT NOT NULL,
  raw_text TEXT,
  message_ts TEXT NOT NULL,
  revision_ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  telegram_edit_ts TEXT,
  acquisition_status TEXT,
  reply_to_message_id INTEGER,
  source_topic_id INTEGER,
  has_media INTEGER NOT NULL DEFAULT 0,
  media_kind TEXT,
  media_mime_type TEXT,
  media_filename TEXT,
  applied_to_current INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(raw_message_id) REFERENCES raw_messages(raw_message_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_message_revisions_raw_message_id
ON raw_message_revisions(raw_message_id, revision_ts);

CREATE INDEX IF NOT EXISTS idx_raw_message_revisions_source_message
ON raw_message_revisions(source_chat_id, telegram_message_id, revision_ts);
