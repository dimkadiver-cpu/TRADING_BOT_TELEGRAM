-- db/migrations/027_enriched_canonical_messages.sql
-- Aggiunge la tabella enriched_canonical_messages per il Signal Enrichment Layer (PRD 03)

CREATE TABLE IF NOT EXISTS enriched_canonical_messages (
    enrichment_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_message_id     INTEGER NOT NULL UNIQUE,
    raw_message_id           INTEGER NOT NULL,
    trader_id                TEXT NOT NULL,
    account_id               TEXT NOT NULL,
    primary_class            TEXT NOT NULL,
    enrichment_decision      TEXT NOT NULL,
    reason_code              TEXT,
    enriched_signal_json     TEXT,
    enriched_actions_json    TEXT,
    management_plan_json     TEXT,
    enrichment_log_json      TEXT NOT NULL DEFAULT '[]',
    policy_snapshot_json     TEXT NOT NULL DEFAULT '{}',
    policy_version           TEXT NOT NULL DEFAULT '',
    lifecycle_processed      INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ecm_trader_id
    ON enriched_canonical_messages(trader_id);

CREATE INDEX IF NOT EXISTS idx_ecm_decision
    ON enriched_canonical_messages(enrichment_decision);

CREATE INDEX IF NOT EXISTS idx_ecm_lifecycle
    ON enriched_canonical_messages(lifecycle_processed, enrichment_decision, primary_class);

CREATE INDEX IF NOT EXISTS idx_ecm_created
    ON enriched_canonical_messages(created_at);
