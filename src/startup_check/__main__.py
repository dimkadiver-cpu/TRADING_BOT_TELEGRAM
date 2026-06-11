"""Esecuzione standalone: python -m src.startup_check"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from src.startup_check.validator import run_startup_checks


def main() -> int:
    load_dotenv()
    root_dir = Path(__file__).resolve().parents[2]
    report = run_startup_checks(root_dir)
    print(report.render())
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
