CREATE TABLE IF NOT EXISTS ops_dashboard_messages (
    chat_id           INTEGER NOT NULL,
    thread_id         INTEGER NOT NULL DEFAULT 0,
    message_id        INTEGER NOT NULL,
    scope_account_id  TEXT NOT NULL,
    scope_trader_id   TEXT,        -- NULL = tutti i trader dell'account
    current_view      TEXT NOT NULL DEFAULT 'attivi:0',
    updated_at        TEXT,
    PRIMARY KEY (chat_id, thread_id)
);
