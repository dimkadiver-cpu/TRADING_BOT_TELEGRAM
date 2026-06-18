#!/usr/bin/env python3
"""Reset script for live testing — clears all runtime data from parser and ops DBs.

Preserves schema_migrations. Does NOT touch tele_signal_bot.sqlite3.
Usage:
    python scripts/reset_dbs.py             # asks for confirmation
    python scripts/reset_dbs.py --yes       # skip confirmation
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
PARSER_DB = ROOT / "db" / "parser.sqlite3"
OPS_DB = ROOT / "db" / "ops.sqlite3"

PARSER_TABLES = [
    "enriched_canonical_messages",
    "parser_results_v2",
    "parser_runs",
    "canonical_messages",
    "raw_messages",
    "raw_message_revisions",
]

OPS_TABLES = [
    "exchange_raw_events",
    "ops_account_snapshots",
    "ops_order_snapshots",
    "ops_clean_log_tracking",
    "ops_config_overrides",
    "ops_control_state",
    "ops_exchange_events",
    "ops_execution_commands",
    "ops_lifecycle_events",
    "ops_market_snapshots",
    "ops_position_snapshots", 
    "ops_notification_outbox",
    "ops_runtime_snapshots",
    "ops_telegram_control_commands",
    "ops_trade_chains",
]


def reset_db(path: Path, tables: list[str], label: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in tables:
        deleted = conn.execute(f"DELETE FROM {table}").rowcount
        print(f"  [{label}] {table}: {deleted} rows deleted")
    conn.execute("DELETE FROM sqlite_sequence WHERE name != 'schema_migrations'")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("VACUUM")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset runtime DBs for testing")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    print(f"Parser DB : {PARSER_DB}")
    print(f"Ops DB    : {OPS_DB}")
    print()
    print("Tables to clear:")
    for t in PARSER_TABLES:
        print(f"  parser  -> {t}")
    for t in OPS_TABLES:
        print(f"  ops     -> {t}")
    print()

    if not args.yes:
        confirm = input("Confermi reset? [y/N] ").strip().lower()
        if confirm != "y":
            print("Annullato.")
            return

    print()
    reset_db(PARSER_DB, PARSER_TABLES, "parser")
    reset_db(OPS_DB, OPS_TABLES, "ops")
    print()
    print("Reset completato.")


if __name__ == "__main__":
    main()
