-- Aggiunge sent_message_id e sent_chat_id a ops_notification_outbox
-- per consentire la costruzione di link diretti al clean log
ALTER TABLE ops_notification_outbox ADD COLUMN IF NOT EXISTS sent_message_id TEXT DEFAULT NULL;
ALTER TABLE ops_notification_outbox ADD COLUMN IF NOT EXISTS sent_chat_id TEXT DEFAULT NULL;
