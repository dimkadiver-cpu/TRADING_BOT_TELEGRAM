-- Denormalize source_chat_id and telegram_message_id from parser.raw_messages
-- into ops_trade_chains to avoid cross-DB lookups at query time.
ALTER TABLE ops_trade_chains ADD COLUMN source_chat_id TEXT;
ALTER TABLE ops_trade_chains ADD COLUMN telegram_message_id INTEGER;
