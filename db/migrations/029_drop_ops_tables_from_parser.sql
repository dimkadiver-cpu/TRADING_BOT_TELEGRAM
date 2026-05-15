-- db/migrations/029_drop_ops_tables_from_parser.sql
-- Le tabelle ops_* sono migrate in ops.sqlite3 (db/ops_migrations/).
-- Questo file le rimuove da parser.sqlite3.

DROP VIEW IF EXISTS view_active_trade_chains;

DROP TABLE IF EXISTS ops_control_state;
DROP TABLE IF EXISTS ops_exchange_events;
DROP TABLE IF EXISTS ops_position_snapshots;
DROP TABLE IF EXISTS ops_order_snapshots;
DROP TABLE IF EXISTS ops_market_snapshots;
DROP TABLE IF EXISTS ops_account_snapshots;
DROP TABLE IF EXISTS ops_execution_commands;
DROP TABLE IF EXISTS ops_lifecycle_events;
DROP TABLE IF EXISTS ops_trade_chains;
