-- db/ops_migrations/011_ops_outbox_aggregation.sql

ALTER TABLE ops_notification_outbox ADD COLUMN send_after TEXT;
ALTER TABLE ops_notification_outbox ADD COLUMN aggregation_group TEXT;
ALTER TABLE ops_notification_outbox ADD COLUMN source_message_id TEXT;
