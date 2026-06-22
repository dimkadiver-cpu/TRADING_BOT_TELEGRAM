#!/usr/bin/env python3
"""Reset or rebuild parser/ops DBs used by the bot.

Usage:
    python scripts/reset_dbs.py
    python scripts/reset_dbs.py --yes
    python scripts/reset_dbs.py --base-dir db/Test_live/db --yes
    python scripts/reset_dbs.py --recreate --yes
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.migrations import apply_migrations

DEFAULT_DB_DIR = ROOT / "db"
PARSER_DB_NAME = "parser.sqlite3"
OPS_DB_NAME = "ops.sqlite3"

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
    "ops_clean_log_tracking",
    "ops_config_overrides",
    "ops_control_state",
    "ops_dashboard_messages",
    "ops_exchange_events",
    "ops_execution_commands",
    "ops_lifecycle_events",
    "ops_market_snapshots",
    "ops_notification_outbox",
    "ops_order_snapshots",
    "ops_pending_multi_chain_summaries",
    "ops_position_snapshots",
    "ops_runtime_snapshot",
    "ops_telegram_control_commands",
    "ops_trade_chains",
]


def _existing_tables(path: Path) -> set[str]:
    if not path.exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _resolve_db_paths(base_dir: Path) -> tuple[Path, Path]:
    return base_dir / PARSER_DB_NAME, base_dir / OPS_DB_NAME


def reset_db(path: Path, tables: list[str], label: str) -> None:
    existing = _existing_tables(path)
    if not path.exists():
        raise FileNotFoundError(f"{label} DB non trovato: {path}")

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in tables:
            if table not in existing:
                print(f"  [{label}] {table}: skip (tabella assente)")
                continue
            deleted = conn.execute(f"DELETE FROM {table}").rowcount
            print(f"  [{label}] {table}: {deleted} rows deleted")
        if "sqlite_sequence" in existing:
            conn.execute("DELETE FROM sqlite_sequence WHERE name != 'schema_migrations'")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("VACUUM")
    finally:
        conn.close()


def recreate_db(db_path: Path, migrations_dir: Path, label: str) -> None:
    if db_path.exists():
        db_path.unlink()
        print(f"  [{label}] file rimosso: {db_path}")
    applied = apply_migrations(str(db_path), str(migrations_dir))
    print(f"  [{label}] migrations applicate: {applied}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset or rebuild runtime DBs")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--base-dir",
        default=str(DEFAULT_DB_DIR),
        help="Directory contenente parser.sqlite3 e ops.sqlite3",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Cancella i file SQLite e li ricrea da zero applicando le migration",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    parser_db, ops_db = _resolve_db_paths(base_dir)
    parser_migrations = ROOT / "db" / "migrations"
    ops_migrations = ROOT / "db" / "ops_migrations"

    print(f"Base dir  : {base_dir}")
    print(f"Parser DB : {parser_db}")
    print(f"Ops DB    : {ops_db}")
    print()

    if args.recreate:
        print("Azione: rebuild completo dei DB tramite delete file + migrations")
    else:
        print("Tables to clear:")
        for t in PARSER_TABLES:
            print(f"  parser  -> {t}")
        for t in OPS_TABLES:
            print(f"  ops     -> {t}")
    print()

    if not args.yes:
        confirm = input("Confermi operazione? [y/N] ").strip().lower()
        if confirm != "y":
            print("Annullato.")
            return

    print()
    if args.recreate:
        base_dir.mkdir(parents=True, exist_ok=True)
        recreate_db(parser_db, parser_migrations, "parser")
        recreate_db(ops_db, ops_migrations, "ops")
    else:
        reset_db(parser_db, PARSER_TABLES, "parser")
        reset_db(ops_db, OPS_TABLES, "ops")
    print()
    print("Operazione completata.")


if __name__ == "__main__":
    main()
