-- db/migrations/023_runtime_v2_raw_messages.sql
-- Add columns required by runtime_v2 intake layer.
-- acquisition_status already exists; new columns are additive.

ALTER TABLE raw_messages ADD COLUMN acquisition_mode TEXT NOT NULL DEFAULT 'live';
ALTER TABLE raw_messages ADD COLUMN resolved_trader_id TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_method TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_detail TEXT;

CREATE INDEX IF NOT EXISTS idx_raw_messages_resolved_trader_id
    ON raw_messages(resolved_trader_id);
