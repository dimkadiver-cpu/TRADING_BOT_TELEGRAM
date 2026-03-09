PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS fills (
  fill_pk INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL DEFAULT 'T',

  exchange_fill_id TEXT NOT NULL,
  exchange_order_id TEXT,
  client_order_id TEXT,

  symbol TEXT NOT NULL,
  side TEXT NOT NULL,

  qty REAL NOT NULL,
  price REAL NOT NULL,
  fee REAL,
  fee_currency TEXT,

  ts TEXT NOT NULL,
  received_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_dedup
ON fills(env, exchange_fill_id);

CREATE INDEX IF NOT EXISTS idx_fills_client
ON fills(env, client_order_id);

CREATE INDEX IF NOT EXISTS idx_fills_order
ON fills(env, exchange_order_id);
