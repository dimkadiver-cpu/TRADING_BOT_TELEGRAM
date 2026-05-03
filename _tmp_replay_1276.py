"""Quick one-shot parse of raw_message_id=1276 using the live parser stack."""
from __future__ import annotations
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.registry import get_profile_parser

DB_PATH = "parser_test/db/parser_test__chat_1003171748254.sqlite3"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Fetch both messages
cur.execute("""
    SELECT raw_message_id, telegram_message_id, raw_text, source_trader_id,
           reply_to_message_id, source_chat_id
    FROM raw_messages
    WHERE raw_message_id = 1276 OR telegram_message_id = 1276
""")
rows = cur.fetchall()

for r in rows:
    raw_message_id, tg_id, raw_text, trader_id, reply_to, chat_id = r
    print(f"\n{'='*60}")
    print(f"raw_message_id={raw_message_id}  telegram_id={tg_id}  trader={trader_id}")
    print(f"reply_to={reply_to}  chat_id={chat_id}")
    print(f"TEXT:\n{raw_text}\n")

    # Try to detect trader from text if not set
    effective_trader = trader_id
    if not effective_trader:
        import re
        m = re.search(r'\[trader#(\w+)\]', raw_text or '', re.IGNORECASE)
        if m:
            effective_trader = f"trader_{m.group(1).lower()}"
        print(f"Detected trader from text: {effective_trader}")

    if not effective_trader:
        print("Cannot determine trader — skipping parse")
        continue

    try:
        parser = get_profile_parser(effective_trader)
    except Exception as e:
        print(f"No profile for '{effective_trader}': {e}")
        continue

    ctx = ParserContext(
        trader_code=effective_trader,
        message_id=tg_id,
        reply_to_message_id=reply_to,
        channel_id=str(chat_id) if chat_id else None,
        raw_text=raw_text or "",
    )

    if hasattr(parser, 'parse_canonical'):
        result = parser.parse_canonical(raw_text or "", ctx)
        print(f"parse_canonical result:")
        print(json.dumps(result.model_dump(mode='json'), indent=2, ensure_ascii=False))
    else:
        print("Profile does not implement parse_canonical")

conn.close()
