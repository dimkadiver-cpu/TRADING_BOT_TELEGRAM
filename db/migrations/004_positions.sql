PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS positions (
  position_pk INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL DEFAULT 'T',

  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  size REAL NOT NULL,
  entry_price REAL,
  mark_price REAL,

  unrealized_pnl REAL,
  realized_pnl REAL,

  leverage REAL,
  margin_mode TEXT,

  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_symbol
ON positions(env, symbol);
