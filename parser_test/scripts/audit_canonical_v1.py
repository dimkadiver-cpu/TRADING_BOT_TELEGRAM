"""Differential audit: legacy parse_results vs CanonicalMessage v1."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.reporting.canonical_v1_audit import run_canonical_v1_audit
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.replay_parser import _is_live_db_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit legacy parser output against canonical_v1 normalizer.")
    parser.add_argument("--db-path", default=None, help="Path to parser_test sqlite DB.")
    parser.add_argument("--db-name", default=None, help="Logical DB name under parser_test/db.")
    parser.add_argument(
        "--db-per-chat",
        action="store_true",
        help="Use parser_test/db/parser_test__chat_<chat>.sqlite3 based on --chat-id.",
    )
    parser.add_argument("--chat-id", default=None, help="Resolve per-chat parser_test DB.")
    parser.add_argument("--trader", default=None, help="Trader filter: trader_a, TA, trader_3, all.")
    parser.add_argument("--limit", type=int, default=None, help="Max parse_results rows to audit.")
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "parser_test" / "reports" / "canonical_v1_audit"),
        help="Output directory for JSON summary and CSV rows.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=PROJECT_ROOT / "parser_test",
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )
    if _is_live_db_path(db_path):
        raise RuntimeError(f"Refusing to run on live DB path: {db_path}")

    trader = None if args.trader in {None, "", "all", "trader_all", "*"} else args.trader
    result = run_canonical_v1_audit(
        db_path=db_path,
        output_dir=args.output_dir,
        trader=trader,
        limit=args.limit,
    )

    print(f"db_path: {db_path}")
    print(f"trader filter: {result.trader_filter or '(all)'}")
    print(f"total rows: {result.total_rows}")
    print(f"canonical valid rows: {result.canonical_valid_rows}")
    print(f"normalizer error rows: {result.normalizer_error_rows}")
    print(f"class mismatches: {result.class_mismatch_rows}")
    print(f"parse_status_counts: {result.parse_status_counts}")
    print(f"primary_class_counts: {result.primary_class_counts}")
    if result.mismatch_counts:
        print(f"mismatch_counts: {result.mismatch_counts}")
    if result.unmapped_intent_counts:
        print(f"unmapped_intent_counts: {result.unmapped_intent_counts}")
    print(f"summary_path: {result.summary_path}")
    print(f"rows_path: {result.rows_path}")


if __name__ == "__main__":
    main()
