PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
  attempt_key TEXT PRIMARY KEY,
  env TEXT NOT NULL DEFAULT 'T',
  channel_id TEXT NOT NULL,
  root_telegram_id TEXT NOT NULL,
  trader_id TEXT NOT NULL,
  trader_prefix TEXT NOT NULL,
  trader_signal_id INTEGER,

  symbol TEXT,
  side TEXT,

  entry_json TEXT,
  sl REAL,
  tp_json TEXT,

  status TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.0,

  raw_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_unique_root
ON signals(env, channel_id, root_telegram_id, trader_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_trader_signal
ON signals(trader_id, trader_signal_id)
WHERE trader_signal_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL DEFAULT 'T',
  channel_id TEXT NOT NULL,
  telegram_msg_id TEXT NOT NULL,
  trader_id TEXT,
  trader_prefix TEXT,

  attempt_key TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,

  confidence REAL NOT NULL DEFAULT 0.0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_attempt_key ON events(attempt_key);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, created_at);

CREATE TABLE IF NOT EXISTS warnings (
  warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL DEFAULT 'T',
  attempt_key TEXT,
  trader_id TEXT,
  code TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'WARN',
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_warnings_attempt_key ON warnings(attempt_key);
CREATE INDEX IF NOT EXISTS idx_warnings_code_time ON warnings(code, created_at);

CREATE TABLE IF NOT EXISTS trades (
  trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL DEFAULT 'T',
  attempt_key TEXT NOT NULL,
  trader_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,

  execution_mode TEXT NOT NULL,
  state TEXT NOT NULL,

  entry_zone_policy TEXT NOT NULL DEFAULT 'Z1',
  non_chase_policy TEXT NOT NULL DEFAULT 'NI3',

  opened_at TEXT,
  closed_at TEXT,
  close_reason TEXT,

  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_attempt ON trades(env, attempt_key);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_state ON trades(symbol, state);
