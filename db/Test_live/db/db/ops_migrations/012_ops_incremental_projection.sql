ALTER TABLE ops_trade_chains
ADD COLUMN last_projected_event_id INTEGER NOT NULL DEFAULT 0;
