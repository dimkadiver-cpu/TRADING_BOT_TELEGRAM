-- db/ops_migrations/007_ops_control_plane.sql

CREATE TABLE IF NOT EXISTS ops_notification_outbox (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type TEXT NOT NULL,
    destination TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'MEDIUM',
    status TEXT NOT NULL DEFAULT 'PENDING',
    dedupe_key TEXT NOT NULL UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS ops_telegram_control_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_request_id TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL,
    message_thread_id TEXT NOT NULL,
    telegram_user_id TEXT NOT NULL,
    telegram_username TEXT,
    command_text TEXT NOT NULL,
    command_name TEXT,
    payload_json TEXT,
    received_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reject_reason TEXT,
    execution_result TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_config_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    override_key TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('GLOBAL', 'PER_TRADER')),
    scope_value TEXT,
    value_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (scope_type = 'GLOBAL' AND scope_value IS NULL)
        OR
        (scope_type = 'PER_TRADER' AND scope_value IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS ops_runtime_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT NOT NULL,
    control_mode TEXT NOT NULL,
    active_blocks_json TEXT NOT NULL,
    open_chain_count INTEGER NOT NULL,
    pending_command_count INTEGER NOT NULL,
    shutdown_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbox_status
    ON ops_notification_outbox(status, destination, created_at);

CREATE INDEX IF NOT EXISTS idx_cfg_override_active
    ON ops_config_overrides(active, override_key, scope_type, scope_value);

CREATE INDEX IF NOT EXISTS idx_runtime_snapshot_at
    ON ops_runtime_snapshot(snapshot_at);
