"""Reset completo dello stato live/dry-run per una nuova sessione di test.

Pulisce:
- DB del bot (`db/tele_signal_bot.sqlite3`)
- DB dry-run di Freqtrade (`freqtrade/tradesv3.dryrun.sqlite`)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOT_DB = PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3"
FREQTRADE_DB = PROJECT_ROOT / "freqtrade" / "tradesv3.dryrun.sqlite"

BOT_TABLES = [
    "events",
    "warnings",
    "orders",
    "positions",
    "trades",
    "operational_signals",
    "signals",
    "parse_results",
    "raw_messages",
]

FREQTRADE_TABLES = [
    "orders",
    "trade_custom_data",
    "trades",
    "pairlocks",
    "KeyValueStore",
]


def _reset_sqlite(db_path: Path, tables: list[str], *, label: str) -> None:
    if not db_path.exists():
        print(f"[{label}] DB non trovato: {db_path}")
        return

    print(f"[{label}] {db_path}")
    con = sqlite3.connect(str(db_path))
    try:
        existing_tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table in tables:
            if table not in existing_tables:
                print(f"  {table}: tabella assente")
                continue
            deleted = con.execute(f"DELETE FROM {table}").rowcount
            print(f"  {table}: {deleted} righe eliminate")
        if "sqlite_sequence" in existing_tables:
            con.execute("DELETE FROM sqlite_sequence")
        con.commit()
    finally:
        con.close()


def main() -> int:
    _reset_sqlite(BOT_DB, BOT_TABLES, label="bot_db")
    _reset_sqlite(FREQTRADE_DB, FREQTRADE_TABLES, label="freqtrade_db")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
