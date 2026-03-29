"""Run parser replay and refresh CSV reports in one terminal command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.reporting.report_export import export_reports_csv_v2
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.replay_parser import replay_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run parser replay and export CSV reports.")
    parser.add_argument("--db-path", default=None, help="Path to parser_test sqlite DB.")
    parser.add_argument("--db-name", default=None, help="Logical DB name under parser_test/db (e.g. trader_a_mar).")
    parser.add_argument(
        "--db-per-chat",
        action="store_true",
        help="Use parser_test/db/parser_test__chat_<chat>.sqlite3 based on --chat-id.",
    )
    parser.add_argument("--trader", default=None, help="Trader filter: TA, trader_a, TB, trader_b, all.")
    parser.add_argument("--only-unparsed", action="store_true", help="Replay only rows without parse_results.")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process.")
    parser.add_argument("--chat-id", default=None, help="Filter by raw_messages.source_chat_id.")
    parser.add_argument("--from-date", default=None, help="Inclusive lower bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument("--to-date", default=None, help="Inclusive upper bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument("--parser-mode", default=None, help="Parser mode override: regex_only | llm_only | hybrid_auto")
    parser.add_argument(
        "--show-normalized-samples",
        type=int,
        default=3,
        help="How many normalized parse_result examples to print (default: 3, 0 to disable).",
    )
    parser.add_argument(
        "--reports-dir",
        default=str(PROJECT_ROOT / "parser_test" / "reports"),
        help="Directory containing the report CSV folders.",
    )
    parser.add_argument(
        "--include-legacy-debug",
        action="store_true",
        help="Include legacy actions as a debug-only CSV column.",
    )
    parser.add_argument(
        "--include-json-debug",
        action="store_true",
        help="Include the full normalized JSON as a debug-only CSV column.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    replay_database(
        db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        only_unparsed=args.only_unparsed,
        limit=args.limit,
        chat_id=args.chat_id,
        trader=args.trader,
        from_date=args.from_date,
        to_date=args.to_date,
        parser_mode=args.parser_mode,
        show_normalized_samples=args.show_normalized_samples,
    )
    db_path = Path(
        resolve_parser_test_db_path(
            project_root=PROJECT_ROOT,
            parser_test_dir=PROJECT_ROOT / "parser_test",
            explicit_db_path=args.db_path,
            db_name=args.db_name,
            db_per_chat=args.db_per_chat,
            chat_ref=args.chat_id,
        )
    )
    updated = export_reports_csv_v2(
        db_path=db_path,
        reports_dir=args.reports_dir,
        trader=args.trader,
        include_legacy_debug=args.include_legacy_debug,
        include_json_debug=args.include_json_debug,
    )
    _print_summary(updated)

def _format_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _print_summary(updated: list[object]) -> None:
    if not updated:
        print("no report files updated")
        return

    total_rows = 0
    for item in updated:
        path = getattr(item, "path", None)
        row_count = int(getattr(item, "row_count", 0))
        trader_id = getattr(item, "trader_id", "unknown")
        scope = getattr(item, "scope", "unknown")
        total_rows += row_count
        print(f"updated {_format_path(path)} [{trader_id} / {scope}] ({row_count} rows)")
    print(f"total report rows written: {total_rows}")


if __name__ == "__main__":
    main()
