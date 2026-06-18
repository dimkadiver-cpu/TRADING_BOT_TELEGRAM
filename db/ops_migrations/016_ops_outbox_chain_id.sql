-- db/ops_migrations/016_ops_outbox_chain_id.sql
-- Aggiunge chain_id a ops_notification_outbox per il mantenimento dell'ordine
-- intra-chain: quando un evento HIGH (es. SL_FILLED) viene scritto, tutti i
-- PENDING precedenti della stessa chain vengono promossi a HIGH, evitando che
-- SL_FILLED salti davanti a ENTRY_OPENED della stessa posizione.

ALTER TABLE ops_notification_outbox ADD COLUMN chain_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_outbox_chain_pending
    ON ops_notification_outbox(chain_id, status)
    WHERE chain_id IS NOT NULL;
