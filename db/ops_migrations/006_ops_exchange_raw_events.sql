-- db/ops_migrations/006_ops_exchange_raw_events.sql

CREATE TABLE IF NOT EXISTS exchange_raw_events (
    raw_event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_event_id       TEXT NOT NULL,
    source_stream           TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    create_type             TEXT,
    stop_order_type         TEXT,
    exec_type               TEXT,
    order_status            TEXT,
    order_link_id           TEXT,
    order_id                TEXT,
    seq                     INTEGER,
    exec_price              REAL,
    exec_qty                REAL,
    closed_size             REAL,
    leaves_qty              REAL,
    pos_qty                 REAL,
    exec_value              REAL,
    exec_fee                REAL,
    fee_rate                REAL,
    cum_exec_qty            REAL,
    position_take_profit    REAL,
    position_stop_loss      REAL,
    classified_event_type   TEXT,
    classified_source       TEXT,
    trade_chain_id          INTEGER,
    tp_level                INTEGER,
    forwarded_to_lifecycle  INTEGER NOT NULL DEFAULT 0,
    forwarded_at            TEXT,
    raw_info_json           TEXT NOT NULL DEFAULT '{}',
    exchange_time           TEXT,
    received_at             TEXT NOT NULL,
    idempotency_key         TEXT NOT NULL,
    UNIQUE(idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_ere_chain_type
    ON exchange_raw_events (trade_chain_id, classified_event_type);

CREATE INDEX IF NOT EXISTS idx_ere_symbol_side
    ON exchange_raw_events (symbol, side, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_ere_not_forwarded
    ON exchange_raw_events (forwarded_to_lifecycle)
    WHERE forwarded_to_lifecycle = 0;

CREATE INDEX IF NOT EXISTS idx_ere_stream
    ON exchange_raw_events (source_stream, received_at DESC);
