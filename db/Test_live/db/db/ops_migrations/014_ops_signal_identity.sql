-- db/ops_migrations/014_ops_signal_identity.sql
-- Aggiunge identità segnale minima alla chain (Patch V1)

ALTER TABLE ops_trade_chains
ADD COLUMN external_signal_id TEXT;

CREATE INDEX IF NOT EXISTS idx_otc_signal_identity
ON ops_trade_chains(trader_id, external_signal_id)
WHERE external_signal_id IS NOT NULL;
