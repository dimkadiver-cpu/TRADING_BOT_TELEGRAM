-- Enable WAL journal mode on the ops database.
-- WAL allows concurrent readers with a single writer, removing the whole-database
-- lock contention of the default "delete" rollback journal. This is persistent at
-- the database-file level once applied.
PRAGMA journal_mode=WAL;
