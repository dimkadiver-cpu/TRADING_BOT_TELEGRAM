PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS parse_results (
  parse_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_message_id INTEGER NOT NULL,

  eligibility_status TEXT NOT NULL,
  eligibility_reason TEXT,

  declared_trader_tag TEXT,
  resolved_trader_id TEXT,
  trader_resolution_method TEXT,

  message_type TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  completeness TEXT NOT NULL,
  is_executable INTEGER NOT NULL DEFAULT 0,

  symbol TEXT,
  direction TEXT,
  entry_raw TEXT,
  stop_raw TEXT,
  target_raw_list TEXT,
  leverage_hint TEXT,
  risk_hint TEXT,
  risky_flag INTEGER NOT NULL DEFAULT 0,

  linkage_method TEXT,
  linkage_status TEXT,
  warning_text TEXT,
  notes TEXT,

  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,

  FOREIGN KEY(raw_message_id) REFERENCES raw_messages(raw_message_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_parse_results_raw
ON parse_results(raw_message_id);

CREATE INDEX IF NOT EXISTS idx_parse_results_type
ON parse_results(message_type, parse_status);
