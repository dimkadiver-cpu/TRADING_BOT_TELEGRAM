ALTER TABLE raw_messages ADD COLUMN has_media INTEGER NOT NULL DEFAULT 0;
ALTER TABLE raw_messages ADD COLUMN media_kind TEXT;
ALTER TABLE raw_messages ADD COLUMN media_mime_type TEXT;
ALTER TABLE raw_messages ADD COLUMN media_filename TEXT;
ALTER TABLE raw_messages ADD COLUMN media_blob BLOB;
