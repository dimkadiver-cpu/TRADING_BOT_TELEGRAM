PRAGMA foreign_keys=ON;

ALTER TABLE parse_results
ADD COLUMN parse_result_normalized_json TEXT;
