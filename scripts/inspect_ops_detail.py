"""Deep audit of ops.sqlite3 trade chain state."""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(r"C:\TeleSignalBot\db\ops.sqlite3")
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 70)
print("TRADE CHAIN — FULL JSON FIELDS")
print("=" * 70)
cur.execute("SELECT * FROM ops_trade_chains WHERE trade_chain_id=1")
row = dict(cur.fetchone())
for k, v in row.items():
    if v and isinstance(v, str) and v.startswith("{"):
        print(f"\n-- {k} --")
        try:
            print(json.dumps(json.loads(v), indent=2))
        except Exception:
            print(v)
    else:
        print(f"  {k:40s}: {v}")

print("\n" + "=" * 70)
print("OPS_EXCHANGE_EVENTS — FULL PAYLOADS")
print("=" * 70)
cur.execute("SELECT * FROM ops_exchange_events ORDER BY exchange_event_id")
for row in cur.fetchall():
    row = dict(row)
    print(f"\n  exchange_event_id={row['exchange_event_id']} type={row['event_type']} status={row['processing_status']}")
    print(f"  idempotency_key: {row['idempotency_key']}")
    if row['payload_json']:
        print(f"  payload: {json.dumps(json.loads(row['payload_json']), indent=4)}")

print("\n" + "=" * 70)
print("RAW EVENTS — GAPS IN SEQUENCE")
print("=" * 70)
cur.execute("SELECT raw_event_id, exchange_event_id, source_stream, exec_type, order_status, classified_event_type, trade_chain_id, forwarded_to_lifecycle FROM exchange_raw_events ORDER BY raw_event_id")
rows = cur.fetchall()
ids = [r[0] for r in rows]
max_id = max(ids)
missing = [i for i in range(1, max_id+1) if i not in ids]
print(f"  Present IDs : {ids}")
print(f"  Missing IDs : {missing}  (deleted or never committed)")
for r in rows:
    print(f"  id={r[0]:2d}  stream={r[2]:20s}  exec_type={str(r[3]):10s}  status={str(r[4]):15s}  classified={r[6]}  chain={r[7]}  fwd={r[8]}")

print("\n" + "=" * 70)
print("PARTIAL TP ORDERS — DETAIL")
print("=" * 70)
cur.execute("SELECT raw_event_id, order_id, stop_order_type, order_status, exec_qty, leaves_qty, exchange_time, raw_info_json FROM exchange_raw_events WHERE stop_order_type='PartialTakeProfit'")
for r in cur.fetchall():
    info = json.loads(r[7]) if r[7] else {}
    print(f"\n  raw_id={r[0]}  order_id={r[1]}")
    print(f"  stop_order_type={r[2]}  status={r[3]}")
    print(f"  exec_qty={r[4]}  leaves_qty={r[5]}  time={r[6]}")
    print(f"  triggerPrice={info.get('triggerPrice')}  price={info.get('price')}  qty={info.get('qty')}")

print("\n" + "=" * 70)
print("FULL TP + SL ORDERS — DETAIL")
print("=" * 70)
cur.execute("SELECT raw_event_id, order_id, stop_order_type, create_type, order_status, leaves_qty, raw_info_json FROM exchange_raw_events WHERE stop_order_type IN ('TakeProfit','StopLoss')")
for r in cur.fetchall():
    info = json.loads(r[6]) if r[6] else {}
    print(f"\n  raw_id={r[0]}  order_id={r[1]}")
    print(f"  type={r[2]}  create={r[3]}  status={r[4]}  leaves_qty={r[5]}")
    print(f"  triggerPrice={info.get('triggerPrice')}  price={info.get('price')}  qty={info.get('qty')}")

print("\n" + "=" * 70)
print("COMMANDS — FULL PAYLOADS (PLACE_ENTRY leg2 + REBUILD_TPS)")
print("=" * 70)
cur.execute("SELECT command_id, command_type, status, payload_json, result_payload_json FROM ops_execution_commands ORDER BY command_id")
for r in cur.fetchall():
    print(f"\n  command_id={r[0]}  type={r[1]}  status={r[2]}")
    if r[3]:
        p = json.loads(r[3])
        print(f"  payload: {json.dumps(p, indent=4)}")
    if r[4]:
        rp = json.loads(r[4])
        if rp:
            print(f"  result: {json.dumps(rp, indent=4)}")

conn.close()
