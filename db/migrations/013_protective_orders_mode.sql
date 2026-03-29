PRAGMA foreign_keys=ON;

ALTER TABLE trades ADD COLUMN protective_orders_mode TEXT NOT NULL DEFAULT 'strategy_managed';

ALTER TABLE orders ADD COLUMN venue_status_raw TEXT;

ALTER TABLE orders ADD COLUMN last_exchange_sync_at TEXT;

CREATE INDEX IF NOT EXISTS idx_orders_exchange_order_id
ON orders(env, exchange_order_id)
WHERE exchange_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_orders_attempt_purpose_idx
ON orders(env, attempt_key, purpose, idx);

CREATE INDEX IF NOT EXISTS idx_trades_state_protective_mode
ON trades(state, protective_orders_mode);
