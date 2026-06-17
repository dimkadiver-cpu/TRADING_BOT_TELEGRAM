ALTER TABLE ops_trade_chains ADD COLUMN risk_already_realized REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN risk_remaining REAL NOT NULL DEFAULT 0;
