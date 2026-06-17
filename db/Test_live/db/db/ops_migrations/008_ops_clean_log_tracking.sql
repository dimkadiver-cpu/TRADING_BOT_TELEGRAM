-- db/ops_migrations/008_ops_clean_log_tracking.sql

CREATE TABLE IF NOT EXISTS ops_clean_log_tracking (
    trade_chain_id INTEGER PRIMARY KEY,
    clean_log_root_message_id TEXT,
    clean_log_last_message_id TEXT,
    telegram_chat_id TEXT NOT NULL,
    telegram_thread_id TEXT,
    original_message_link TEXT,
    last_clean_log_event_type TEXT,
    last_clean_log_sent_at TEXT,
    updated_at TEXT NOT NULL
);
