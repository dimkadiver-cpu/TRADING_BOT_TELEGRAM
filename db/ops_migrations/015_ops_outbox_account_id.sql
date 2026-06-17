-- db/ops_migrations/015_ops_outbox_account_id.sql
-- Aggiunge account_id a ops_notification_outbox per routing per-account

ALTER TABLE ops_notification_outbox ADD COLUMN account_id TEXT;
