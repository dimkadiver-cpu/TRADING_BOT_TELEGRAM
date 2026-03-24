"""Copia il DB live nel path di test e genera i CSV di report."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE_DB = ROOT / "db" / "tele_signal_bot.sqlite3"
TEST_DB = ROOT / "parser_test" / "db" / "parser_test.sqlite3"

if not LIVE_DB.exists():
    print(f"DB live non trovato: {LIVE_DB}")
    sys.exit(1)

print(f"Copio {LIVE_DB} → {TEST_DB}")
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(LIVE_DB, TEST_DB)
print("Copia completata.")

print("Genero report CSV...")
subprocess.run(
    [sys.executable, "parser_test/scripts/generate_parser_reports.py", "--trader", "trader_all"],
    cwd=ROOT,
    check=True,
)
print("Report generati in parser_test/reports/")
