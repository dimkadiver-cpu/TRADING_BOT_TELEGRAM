ALTER TABLE raw_messages
ADD COLUMN message_presentation_type TEXT NOT NULL DEFAULT 'PLAIN';

CREATE INDEX IF NOT EXISTS idx_raw_messages_presentation_type
ON raw_messages(message_presentation_type);
