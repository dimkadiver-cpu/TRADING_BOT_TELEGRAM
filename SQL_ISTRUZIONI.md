SELECT DISTINCT mf.trader_id, mf.root_id, mf.is_root, rm.telegram_id, rm.text, rm.date_iso
FROM message_features mf
JOIN raw_messages rm ON mf.channel_id = rm.channel_id AND mf.telegram_id = rm.telegram_id
WHERE mf.trader_id = 'D'
ORDER BY mf.root_id, rm.date_iso;
