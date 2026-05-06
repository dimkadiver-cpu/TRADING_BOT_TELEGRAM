"""Guarda i file del profilo parser_v2 e rilancia replay + CSV al cambio.

Uso:
    python parser_test/scripts/watch_parser.py --trader trader_a
    python parser_test/scripts/watch_parser.py --trader trader_a --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False

_DEBOUNCE_SECONDS: float = 2.0
_WATCHED_FILENAMES: tuple[str, ...] = (
    "semantic_markers.json",
    "semantic_markers_1.json",
    "rules.json",
    "profile.py",
    "signal_extractor.py",
    "intent_entity_extractor.py",
)


def _monitored_files(trader: str) -> list[Path]:
    profile_dir = PROJECT_ROOT / "src" / "parser_v2" / "profiles" / trader
    return [profile_dir / name for name in _WATCHED_FILENAMES]


def _run_pipeline(trader: str, db_name: str | None, dry_run: bool) -> None:
    print(f"\n[watch_parser] cambio rilevato — avvio pipeline per {trader}", flush=True)
    report_script = PROJECT_ROOT / "parser_test" / "scripts" / "generate_parser_reports_v2.py"
    cmd = [sys.executable, str(report_script), "--trader", trader, "--force-reparse"]
    if db_name:
        cmd += ["--db-name", db_name]
    print(f"[watch_parser] {' '.join(cmd)}", flush=True)
    if not dry_run:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            print(f"[watch_parser] WARNING: exit code {result.returncode}", flush=True)
    print(f"[watch_parser] pipeline completata per {trader}", flush=True)


if _WATCHDOG_AVAILABLE:

    class _DebounceHandler(FileSystemEventHandler):
        def __init__(self, trader: str, db_name: str | None, dry_run: bool, watched_paths: set[Path]) -> None:
            self._trader = trader
            self._db_name = db_name
            self._dry_run = dry_run
            self._watched = {str(p.resolve()) for p in watched_paths}
            self._last_trigger: float = 0.0

        def on_modified(self, event: FileSystemEvent) -> None:
            if str(Path(event.src_path).resolve()) not in self._watched:
                return
            now = time.monotonic()
            if now - self._last_trigger < _DEBOUNCE_SECONDS:
                return
            self._last_trigger = now
            _run_pipeline(self._trader, self._db_name, self._dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch parser_v2 profile files and re-run pipeline")
    parser.add_argument("--trader", required=True)
    parser.add_argument("--db-name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    watched = set(_monitored_files(args.trader))
    existing = [p for p in watched if p.exists()]

    if not existing:
        print(f"[watch_parser] ERRORE: nessun file trovato per trader={args.trader!r}")
        print(f"  cercato in: {PROJECT_ROOT / 'src' / 'parser_v2' / 'profiles' / args.trader}")
        sys.exit(1)

    if not _WATCHDOG_AVAILABLE:
        print("[watch_parser] watchdog non installato. Installa con: pip install watchdog")
        sys.exit(1)

    print(f"[watch_parser] monitoraggio trader={args.trader!r}")
    for p in sorted(existing):
        print(f"  {p.relative_to(PROJECT_ROOT)}")
    print("[watch_parser] Ctrl+C per fermare\n")

    handler = _DebounceHandler(args.trader, args.db_name, args.dry_run, watched)
    observer = Observer()
    for p in existing:
        observer.schedule(handler, str(p.parent), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
