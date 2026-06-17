PRAGMA foreign_keys=ON;

ALTER TABLE raw_messages ADD COLUMN processing_status TEXT NOT NULL DEFAULT 'pending';

CREATE INDEX IF NOT EXISTS idx_raw_messages_processing_status
ON raw_messages(processing_status);
