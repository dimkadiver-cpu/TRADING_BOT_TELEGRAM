-- db/ops_migrations/020_ops_account_snapshot_fields.sql

ALTER TABLE ops_account_snapshots ADD COLUMN account_unrealized_pnl_usdt REAL;
ALTER TABLE ops_account_snapshots ADD COLUMN snapshot_status TEXT NOT NULL DEFAULT 'OK';
ALTER TABLE ops_account_snapshots ADD COLUMN error_code TEXT;

CREATE INDEX IF NOT EXISTS idx_ops_account_snapshots_account_captured
ON ops_account_snapshots(account_id, captured_at DESC, snapshot_id DESC);
