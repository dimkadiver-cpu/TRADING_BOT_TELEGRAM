CREATE TABLE IF NOT EXISTS operational_signals (
  op_signal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  parse_result_id        INTEGER NOT NULL
                           REFERENCES parse_results(parse_result_id),
  attempt_key            TEXT REFERENCES signals(attempt_key),  -- NULL per UPDATE
  trader_id              TEXT NOT NULL,
  message_type           TEXT NOT NULL,   -- NEW_SIGNAL | UPDATE

  -- Gate result
  is_blocked             INTEGER NOT NULL DEFAULT 0,
  block_reason           TEXT,            -- es. "trader_disabled", "global_cap_exceeded"

  -- Set A — parametri apertura (solo NEW_SIGNAL)
  position_size_pct      REAL,
  position_size_usdt     REAL,
  entry_split_json       TEXT,            -- {"E1": 0.3, "E2": 0.7} o {"E1":0.33,"E2":0.34,"E3":0.33}
  leverage               INTEGER,
  risk_hint_used         INTEGER NOT NULL DEFAULT 0,

  -- Set B — regole gestione (snapshot config al momento del segnale)
  management_rules_json  TEXT,

  -- Price corrections hook (implementazione futura)
  price_corrections_json TEXT,            -- NULL finché non implementato

  -- Audit
  applied_rules_json     TEXT,            -- list[str] regole applicate
  warnings_json          TEXT,            -- list[str]

  -- Target resolution
  resolved_target_ids    TEXT,            -- JSON list[int] di op_signal_id risolti
  target_eligibility     TEXT,            -- ELIGIBLE | INELIGIBLE | WARN | UNRESOLVED
  target_reason          TEXT,            -- motivo se non ELIGIBLE

  created_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_op_signals_parse_result
ON operational_signals(parse_result_id);

CREATE INDEX IF NOT EXISTS idx_op_signals_trader_type
ON operational_signals(trader_id, message_type);
