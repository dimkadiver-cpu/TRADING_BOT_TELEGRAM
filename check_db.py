import sqlite3, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv()
db = os.getenv("DB_PATH", "db/tele_signal_bot.sqlite3")
with sqlite3.connect(db) as c:
    print("--- processing_status ---")
    for r in c.execute("SELECT processing_status, COUNT(*) FROM raw_messages GROUP BY processing_status").fetchall():
        print(r)
    print("--- message_type ---")
    for r in c.execute("SELECT message_type, COUNT(*) FROM parse_results GROUP BY message_type").fetchall():
        print(r)
    print("\n--- messaggi in review ---")
    rows = c.execute("""
        SELECT rm.raw_message_id, rm.telegram_message_id, rm.source_trader_id,
               rm.reply_to_message_id, rq.reason, rm.raw_text
        FROM raw_messages rm
        JOIN review_queue rq ON rq.raw_message_id = rm.raw_message_id
        WHERE rm.processing_status = 'review'
        ORDER BY rm.raw_message_id
    """).fetchall()
    for r in rows:
        print(f"\n[id={r[0]} tg_msg={r[1]} source_trader={r[2]} reply_to={r[3]} reason={r[4]}]")
        print(r[5])
