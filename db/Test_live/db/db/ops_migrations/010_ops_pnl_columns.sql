-- db/ops_migrations/010_ops_pnl_columns.sql

ALTER TABLE ops_trade_chains ADD COLUMN cumulative_gross_pnl REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN cumulative_fees REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN cumulative_funding REAL DEFAULT 0.0;
ALTER TABLE ops_trade_chains ADD COLUMN allocated_margin REAL;
