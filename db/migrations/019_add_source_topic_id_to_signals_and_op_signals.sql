-- Additive migration: add source_topic_id provenance to signals and operational_signals.
-- NULL for existing rows (legacy records without topic information).
ALTER TABLE signals ADD COLUMN source_topic_id INTEGER;
ALTER TABLE operational_signals ADD COLUMN source_topic_id INTEGER;
