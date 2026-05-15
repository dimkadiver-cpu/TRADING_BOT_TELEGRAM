#!/usr/bin/env python3
# scripts/setup_parser_db_separation.py
"""
One-time script: copia db/tele_signal_bot.sqlite3 → db/parser.sqlite3
e crea db/ops.sqlite3 vuoto. Poi applica le migrazioni SQL a parser.sqlite3.

Eseguire UNA VOLTA prima di avviare il sistema con la nuova configurazione.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def main() -> None:
    old_path = PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3"
    parser_path = PROJECT_ROOT / "db" / "parser.sqlite3"
    ops_path = PROJECT_ROOT / "db" / "ops.sqlite3"

    # 1. Rinomina il DB esistente
    if old_path.exists() and not parser_path.exists():
        print(f"Copia {old_path} -> {parser_path}")
        shutil.copy2(str(old_path), str(parser_path))
        print("Copia completata. Rimuovi manualmente il vecchio file quando sicuro.")
    elif parser_path.exists():
        print("parser.sqlite3 gia esiste, skip rinomina.")
    else:
        print(f"ATTENZIONE: ne {old_path} ne {parser_path} esistono. Crea un DB vuoto.")
        parser_path.touch()

    # 2. Crea ops.sqlite3 vuoto con schema_migrations
    if not ops_path.exists():
        print(f"Crea {ops_path}")
        conn = sqlite3.connect(str(ops_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
    else:
        print("ops.sqlite3 gia esiste, skip.")

    # 3. Applica migrazione 027 a parser.sqlite3
    migration = PROJECT_ROOT / "db" / "migrations" / "027_enriched_canonical_messages.sql"
    if migration.exists():
        conn = sqlite3.connect(str(parser_path))
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()
        print("Migrazione 027 applicata a parser.sqlite3.")
    else:
        print(f"ATTENZIONE: migrazione 027 non trovata in {migration}")

    print("\nDB setup completato.")
    print(f"  parser.sqlite3: {parser_path}")
    print(f"  ops.sqlite3:    {ops_path}")
    print("\nAggiorna .env:")
    print("  PARSER_DB_PATH=db/parser.sqlite3")
    print("  OPS_DB_PATH=db/ops.sqlite3")


if __name__ == "__main__":
    main()
