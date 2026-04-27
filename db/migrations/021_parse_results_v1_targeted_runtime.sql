PRAGMA foreign_keys=ON;

ALTER TABLE parse_results_v1
ADD COLUMN targeted_resolved_json TEXT;
