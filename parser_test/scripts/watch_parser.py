"""Watch parser source files and auto-rerun replay + report generation on change.

Usage:
    python parser_test/scripts/watch_parser.py --trader trader_3
    python parser_test/scripts/watch_parser.py --trader trader_3 --dry-run
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
_WATCHED_FILENAMES: tuple[str, ...] = ("parsing_rules.json", "profile.py")


def _monitored_files(trader: str) -> list[Path]:
    profile_dir = PROJECT_ROOT / "src" / "parser" / "trader_profiles" / trader
    return [profile_dir / name for name in _WATCHED_FILENAMES]


def _run_pipeline(trader: str) -> None:
    print(f"\n[watch_parser] change detected — running pipeline for {trader}", flush=True)
    replay_script = PROJECT_ROOT / "parser_test" / "scripts" / "replay_parser.py"
    report_script = PROJECT_ROOT / "parser_test" / "scripts" / "generate_parser_reports.py"

    for script in (replay_script, report_script):
        cmd = [sys.executable, str(script), "--trader", trader]
        print(f"[watch_parser] running: {' '.join(cmd)}", flush=True)
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            print(
                f"[watch_parser] WARNING: {script.name} exited with code {result.returncode}",
                flush=True,
            )

    print(f"[watch_parser] pipeline done for {trader}", flush=True)


if _WATCHDOG_AVAILABLE:

    class _DebounceHandler(FileSystemEventHandler):
        def __init__(self, trader: str, watched_paths: set[Path]) -> None:
            self._trader = trader
            self._watched = {str(p.resolve()) for p in watched_paths}
            self._last_trigger: float = 0.0

        def on_modified(self, event: FileSystemEvent) -> None:
            if event.is_directory:
                return
            if str(Path(event.src_path).resolve()) not in self._watched:
                return
            now = time.monotonic()
            if now - self._last_trigger < _DEBOUNCE_SECONDS:
                return
            self._last_trigger = now
            _run_pipeline(self._trader)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch parser files and auto-rerun replay + report generation."
    )
    parser.add_argument(
        "--trader",
        required=True,
        help="Trader profile to watch (e.g. trader_3, trader_a).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print monitored files and exit without starting the watcher.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    files = _monitored_files(args.trader)

    if args.dry_run:
        print(f"[watch_parser] dry-run — monitored files for trader '{args.trader}':")
        for f in files:
            status = "exists" if f.exists() else "NOT FOUND"
            print(f"  {f}  [{status}]")
        return

    if not _WATCHDOG_AVAILABLE:
        print(
            "[watch_parser] ERROR: watchdog is not installed. Run: pip install watchdog",
            file=sys.stderr,
        )
        sys.exit(1)

    profile_dir = PROJECT_ROOT / "src" / "parser" / "trader_profiles" / args.trader
    handler = _DebounceHandler(trader=args.trader, watched_paths=set(files))
    observer = Observer()
    observer.schedule(handler, str(profile_dir), recursive=False)
    observer.start()

    print(f"[watch_parser] watching trader '{args.trader}':")
    for f in files:
        print(f"  {f}")
    print("[watch_parser] press Ctrl+C to stop", flush=True)

    try:
        while observer.is_alive():
            observer.join(timeout=1.0)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print("[watch_parser] stopped", flush=True)


if __name__ == "__main__":
    main()
