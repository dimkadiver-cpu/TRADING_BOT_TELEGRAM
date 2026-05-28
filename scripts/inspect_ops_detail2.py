"""Deep audit part 2 — raw events summary + orders detail."""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(r"C:\TeleSignalBot\db\ops.sqlite3")
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 70)
print("RAW EVENTS — SEQUENCE GAPS + SUMMARY")
print("=" * 70)
cur.execute("SELECT raw_event_id, source_stream, exec_type, order_status, classified_event_type, trade_chain_id, forwarded_to_lifecycle FROM exchange_raw_events ORDER BY raw_event_id")
rows = [dict(r) for r in cur.fetchall()]
ids = [r["raw_event_id"] for r in rows]
max_id = max(ids)
missing = [i for i in range(1, max_id+1) if i not in ids]
print(f"Present IDs: {ids}")
print(f"Missing IDs: {missing}")
for r in rows:
    print(f"  id={r['raw_event_id']:2d}  {r['source_stream']:22s}  exec_type={str(r['exec_type']):8s}  status={str(r['order_status']):14s}  classified={str(r['classified_event_type']):30s}  chain={r['trade_chain_id']}  fwd={r['forwarded_to_lifecycle']}")

print("\n" + "=" * 70)
print("PARTIAL TP ORDERS")
print("=" * 70)
cur.execute("SELECT raw_event_id, order_id, stop_order_type, order_status, exec_qty, leaves_qty, exchange_time, raw_info_json FROM exchange_raw_events WHERE stop_order_type='PartialTakeProfit'")
for r in [dict(x) for x in cur.fetchall()]:
    info = json.loads(r["raw_info_json"]) if r["raw_info_json"] else {}
    print(f"\n  raw_id={r['raw_event_id']}  order_id={r['order_id']}")
    print(f"  status={r['order_status']}  leaves_qty={r['leaves_qty']}")
    print(f"  triggerPrice={info.get('triggerPrice')}  price={info.get('price')}  qty={info.get('qty')}")
    print(f"  time={r['exchange_time']}")

print("\n" + "=" * 70)
print("FULL TP + SL ORDERS")
print("=" * 70)
cur.execute("SELECT raw_event_id, order_id, stop_order_type, create_type, order_status, leaves_qty, raw_info_json FROM exchange_raw_events WHERE stop_order_type IN ('TakeProfit','StopLoss')")
for r in [dict(x) for x in cur.fetchall()]:
    info = json.loads(r["raw_info_json"]) if r["raw_info_json"] else {}
    print(f"\n  raw_id={r['raw_event_id']}  order_id={r['order_id']}")
    print(f"  type={r['stop_order_type']}  status={r['order_status']}  leaves_qty={r['leaves_qty']}")
    print(f"  triggerPrice={info.get('triggerPrice')}  price={info.get('price')}  qty={info.get('qty')}")

print("\n" + "=" * 70)
print("EXECUTION COMMANDS — FULL")
print("=" * 70)
cur.execute("SELECT command_id, command_type, status, payload_json, result_payload_json, client_order_id FROM ops_execution_commands ORDER BY command_id")
for r in [dict(x) for x in cur.fetchall()]:
    print(f"\n  command_id={r['command_id']}  type={r['command_type']}  status={r['status']}")
    print(f"  client_order_id={r['client_order_id']}")
    if r["payload_json"]:
        print(f"  payload={json.dumps(json.loads(r['payload_json']), indent=4)}")
    if r["result_payload_json"]:
        rp = json.loads(r["result_payload_json"])
        if rp:
            print(f"  result={json.dumps(rp, indent=4)}")

conn.close()
