-- db/ops_migrations/002_ops_execution_gateway.sql

ALTER TABLE ops_execution_commands ADD COLUMN adapter TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN execution_account_id TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN client_order_id TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN result_payload_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ops_execution_commands ADD COLUMN sent_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN acknowledged_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN completed_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ops_execution_commands ADD COLUMN next_retry_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_oec_client_order_id
    ON ops_execution_commands(client_order_id)
    WHERE client_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oec_retry
    ON ops_execution_commands(status, next_retry_at)
    WHERE status = 'SENT' AND next_retry_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oec_waiting
    ON ops_execution_commands(status)
    WHERE status = 'WAITING_POSITION';
