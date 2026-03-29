-- Migration 016: Backtest trade results table
-- One row per simulated trade produced by freqtrade backtesting.

CREATE TABLE IF NOT EXISTS backtest_trades (
    bt_trade_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             INTEGER NOT NULL REFERENCES backtest_runs(run_id),
    chain_id           TEXT NOT NULL,
    trader_id          TEXT NOT NULL,
    pair               TEXT NOT NULL,
    side               TEXT NOT NULL,
    open_date          TEXT NOT NULL,
    close_date         TEXT,
    entry_price        REAL NOT NULL,
    close_price        REAL,
    profit_usdt        REAL,
    profit_pct         REAL,
    exit_reason        TEXT,
    max_drawdown_pct   REAL,
    duration_seconds   INTEGER,
    sl_moved_to_be     INTEGER NOT NULL DEFAULT 0,
    raw_freqtrade_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_run ON backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_bt_trades_chain ON backtest_trades(chain_id);
