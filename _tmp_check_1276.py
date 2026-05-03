import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

db_path = "parser_test/db/parser_test__chat_1003171748254.sqlite3"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("""
    SELECT raw_message_id, telegram_message_id, raw_text, source_trader_id, processing_status
    FROM raw_messages
    WHERE raw_message_id = 1276 OR telegram_message_id = 1276
""")
rows = cur.fetchall()
print(f"raw_messages matches: {len(rows)}")
for r in rows:
    print(f"  raw_message_id={r[0]}, telegram_id={r[1]}, trader={r[3]}, status={r[4]}")
    print(f"  text: {r[2][:400] if r[2] else 'None'}")
    print()

# Check parse_results_v1 for both raw_message_ids
ids = [r[0] for r in rows]
for rid in ids:
    cur.execute("""
        SELECT id, raw_message_id, primary_class, parse_status, confidence, canonical_json
        FROM parse_results_v1
        WHERE raw_message_id = ?
    """, (rid,))
    pr = cur.fetchone()
    if pr:
        print(f"parse_results_v1 (raw_message_id={rid}): primary_class={pr[2]}, parse_status={pr[3]}, confidence={pr[4]}")
        if pr[5]:
            canon = json.loads(pr[5])
            print(json.dumps(canon, indent=2, ensure_ascii=False)[:2000])
    else:
        print(f"No parse_results_v1 for raw_message_id={rid}")

conn.close()
