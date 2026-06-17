ALTER TABLE ops_trade_chains ADD COLUMN planned_entry_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN filled_entry_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN open_position_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN closed_position_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN last_position_sync_at TEXT;
ALTER TABLE ops_trade_chains ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'a_sequential';
