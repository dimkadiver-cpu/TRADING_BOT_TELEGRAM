PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS orders (
  order_pk INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL DEFAULT 'T',
  attempt_key TEXT NOT NULL,

  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  order_type TEXT NOT NULL,
  purpose TEXT NOT NULL,
  idx INTEGER NOT NULL DEFAULT 0,

  qty REAL NOT NULL,
  price REAL,
  trigger_price REAL,
  reduce_only INTEGER NOT NULL DEFAULT 0,

  client_order_id TEXT NOT NULL,
  exchange_order_id TEXT,
  status TEXT NOT NULL,

  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client
ON orders(env, client_order_id);

CREATE INDEX IF NOT EXISTS idx_orders_attempt
ON orders(env, attempt_key);

CREATE INDEX IF NOT EXISTS idx_orders_status
ON orders(status);
