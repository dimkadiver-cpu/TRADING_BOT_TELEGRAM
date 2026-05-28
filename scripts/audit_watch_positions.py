"""Audit watch_positions events in exchange_raw_events."""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path

DB = Path(r"C:\TeleSignalBot\db\ops.sqlite3")
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 70)
print("ALL watch_positions events")
print("=" * 70)
cur.execute("""
    SELECT raw_event_id, exchange_event_id, side, order_status,
           pos_qty, position_take_profit, position_stop_loss,
           classified_event_type, trade_chain_id, forwarded_to_lifecycle,
           exchange_time, received_at, raw_info_json
    FROM exchange_raw_events
    WHERE source_stream = 'watch_positions'
    ORDER BY raw_event_id
""")
rows = [dict(r) for r in cur.fetchall()]
print(f"  Totale righe: {len(rows)}")
for r in rows:
    info = json.loads(r["raw_info_json"]) if r["raw_info_json"] else {}
    print(f"\n  raw_id={r['raw_event_id']}")
    print(f"  exchange_event_id : {r['exchange_event_id']}")
    print(f"  side              : {r['side']}")
    print(f"  order_status      : {r['order_status']}")
    print(f"  pos_qty           : {r['pos_qty']}")
    print(f"  position_tp       : {r['position_take_profit']}")
    print(f"  position_sl       : {r['position_stop_loss']}")
    print(f"  classified        : {r['classified_event_type']}")
    print(f"  trade_chain_id    : {r['trade_chain_id']}")
    print(f"  forwarded         : {r['forwarded_to_lifecycle']}")
    print(f"  exchange_time     : {r['exchange_time']}")
    print(f"  received_at       : {r['received_at']}")
    print(f"  entryPrice        : {info.get('entryPrice')}")
    print(f"  size              : {info.get('size')}")
    print(f"  takeProfit        : {info.get('takeProfit')}")
    print(f"  stopLoss          : {info.get('stopLoss')}")
    print(f"  positionIdx       : {info.get('positionIdx')}")

print("\n" + "=" * 70)
print("CLASSIFICAZIONI ANOMALE (pos_qty=0 ma classified != UNKNOWN)")
print("=" * 70)
anomalie = [r for r in rows if (r["pos_qty"] or 0.0) == 0.0 and r["classified_event_type"] != "UNKNOWN"]
if anomalie:
    for r in anomalie:
        print(f"  raw_id={r['raw_event_id']}  side={r['side']}  classified={r['classified_event_type']}  forwarded={r['forwarded_to_lifecycle']}")
else:
    print("  Nessuna anomalia trovata.")

print("\n" + "=" * 70)
print("PROTECTIVE_ORDER_CANCELLED — tutti i dettagli")
print("=" * 70)
poc = [r for r in rows if r["classified_event_type"] == "PROTECTIVE_ORDER_CANCELLED"]
if poc:
    for r in poc:
        print(f"  raw_id={r['raw_event_id']}  pos_qty={r['pos_qty']}  tp={r['position_take_profit']}  sl={r['position_stop_loss']}")
        print(f"  forwarded={r['forwarded_to_lifecycle']}  chain={r['trade_chain_id']}")
        info = json.loads(r["raw_info_json"]) if r["raw_info_json"] else {}
        print(f"  size={info.get('size')}  entryPrice={info.get('entryPrice')}")
else:
    print("  Nessuno.")

conn.close()
