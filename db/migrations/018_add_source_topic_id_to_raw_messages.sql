ALTER TABLE raw_messages ADD COLUMN source_topic_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_raw_messages_topic
ON raw_messages(source_chat_id, source_topic_id, telegram_message_id);
