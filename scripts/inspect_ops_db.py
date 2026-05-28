"""Inspect ops.sqlite3 for trade audit."""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(r"C:\TeleSignalBot\db\ops.sqlite3")

def dump_schema(cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print("=" * 60)
    print("TABLES:", tables)
    print("=" * 60)
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = cur.fetchall()
        print(f"\n-- {t} --")
        for c in cols:
            print(f"  {c[1]:30s} {c[2]}")
    return tables

def dump_table(cur, table, limit=50):
    cur.execute(f"SELECT * FROM {table} LIMIT {limit}")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print(f"\n{'='*60}")
    print(f"TABLE: {table}  ({len(rows)} rows shown)")
    print("=" * 60)
    for row in rows:
        print("-" * 40)
        for col, val in zip(cols, row):
            if isinstance(val, str) and len(val) > 200:
                val = val[:200] + "..."
            print(f"  {col:35s}: {val}")

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

tables = dump_schema(cur)

for t in tables:
    dump_table(cur, t)

conn.close()
