-- db/migrations/028_ops_lifecycle_core.sql

CREATE TABLE IF NOT EXISTS ops_trade_chains (
    trade_chain_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_enrichment_id    INTEGER NOT NULL UNIQUE,
    canonical_message_id    INTEGER NOT NULL,
    raw_message_id          INTEGER NOT NULL,
    trader_id               TEXT NOT NULL,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    lifecycle_state         TEXT NOT NULL,
    entry_mode              TEXT NOT NULL,
    entry_avg_price         REAL,
    current_stop_price      REAL,
    expected_stop_price     REAL,
    be_protection_status    TEXT NOT NULL DEFAULT 'NOT_PROTECTED',
    entry_timeout_at        TEXT,
    management_plan_json    TEXT NOT NULL DEFAULT '{}',
    risk_snapshot_json      TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_lifecycle_events (
    event_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER,
    event_type              TEXT NOT NULL,
    source_type             TEXT NOT NULL,
    source_id               TEXT,
    previous_state          TEXT,
    next_state              TEXT,
    payload_json            TEXT NOT NULL DEFAULT '{}',
    idempotency_key         TEXT NOT NULL UNIQUE,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_execution_commands (
    command_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER NOT NULL,
    command_type            TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'PENDING',
    payload_json            TEXT NOT NULL DEFAULT '{}',
    idempotency_key         TEXT NOT NULL UNIQUE,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_account_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    equity_usdt             REAL,
    available_balance_usdt  REAL,
    total_open_risk_usdt    REAL,
    total_margin_used_usdt  REAL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL,
    payload_json            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ops_market_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    mark_price              REAL,
    bid                     REAL,
    ask                     REAL,
    min_order_size          REAL,
    price_precision         INTEGER,
    qty_precision           INTEGER,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL,
    payload_json            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ops_order_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT,
    payload_json            TEXT NOT NULL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_position_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    payload_json            TEXT NOT NULL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_exchange_events (
    exchange_event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER,
    event_type              TEXT NOT NULL,
    payload_json            TEXT NOT NULL DEFAULT '{}',
    processing_status       TEXT NOT NULL DEFAULT 'NEW',
    idempotency_key         TEXT NOT NULL UNIQUE,
    received_at             TEXT NOT NULL,
    processed_at            TEXT
);

CREATE TABLE IF NOT EXISTS ops_control_state (
    control_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type              TEXT NOT NULL,
    scope_value             TEXT,
    execution_pause_mode    TEXT NOT NULL DEFAULT 'NONE',
    emergency_action        TEXT,
    reason                  TEXT,
    created_by              TEXT,
    active                  INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_otc_trader_state
    ON ops_trade_chains(trader_id, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_otc_symbol_state
    ON ops_trade_chains(symbol, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_ole_chain
    ON ops_lifecycle_events(trade_chain_id);
CREATE INDEX IF NOT EXISTS idx_oec_chain_status
    ON ops_execution_commands(trade_chain_id, status);
CREATE INDEX IF NOT EXISTS idx_oee_status
    ON ops_exchange_events(processing_status);
CREATE INDEX IF NOT EXISTS idx_ocs_active
    ON ops_control_state(active, scope_type);

CREATE VIEW IF NOT EXISTS view_active_trade_chains AS
SELECT * FROM ops_trade_chains
WHERE lifecycle_state NOT IN ('CLOSED', 'CANCELLED', 'EXPIRED');
