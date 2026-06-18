-- db/migrations/025_drop_legacy_tables.sql
-- Eliminazione tabelle legacy: parser, operation rules, execution.
-- Tabelle runtime_v2 invariate: raw_messages, canonical_messages, schema_migrations.

DROP TABLE IF EXISTS parse_results;
DROP TABLE IF EXISTS parse_results_v1;
DROP TABLE IF EXISTS parsed_messages;
DROP TABLE IF EXISTS review_queue;
DROP TABLE IF EXISTS operational_signals;
DROP TABLE IF EXISTS signals;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS warnings;
DROP TABLE IF EXISTS trades;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS fills;
DROP TABLE IF EXISTS positions;
DROP TABLE IF EXISTS exchange_events;
DROP TABLE IF EXISTS backtest_runs;
DROP TABLE IF EXISTS backtest_trades;
DROP TABLE IF EXISTS protective_orders_mode;
