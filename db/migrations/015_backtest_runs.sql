-- Migration 015: Backtest run metadata table
-- Tracks each backtesting run: scenario, config, status, and output location.

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_name             TEXT NOT NULL,
    scenario_conditions_json  TEXT NOT NULL,
    trader_filter             TEXT,
    date_from                 TEXT,
    date_to                   TEXT,
    chains_count              INTEGER NOT NULL DEFAULT 0,
    chains_blocked            INTEGER NOT NULL DEFAULT 0,
    run_ts                    TEXT NOT NULL,
    status                    TEXT NOT NULL,   -- RUNNING | COMPLETED | FAILED
    error                     TEXT,
    output_dir                TEXT NOT NULL
);
