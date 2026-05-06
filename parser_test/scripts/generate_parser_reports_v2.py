"""Replay + generazione CSV per parser_v2."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.db.schema import apply_parser_test_schema
from parser_test.reporting.report_export_v2 import export_all
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.replay_parser_v2 import run_replay
from src.storage.parser_runs import ParserRunStore

_DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports_v2"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay parser_v2 + genera CSV")
    parser.add_argument("--db-path")
    parser.add_argument("--db-name")
    parser.add_argument("--db-per-chat", action="store_true")
    parser.add_argument("--chat-id")
    parser.add_argument("--trader")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--run", default="latest", help="'latest' oppure run_id numerico")
    parser.add_argument("--reports-dir", default=str(_DEFAULT_REPORTS_DIR))
    args = parser.parse_args()

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )
    conn = sqlite3.connect(db_path)
    apply_parser_test_schema(conn)

    try:
        if not args.skip_replay:
            run_id = run_replay(
                conn,
                trader=args.trader,
                chat_id=args.chat_id,
                from_date=args.from_date,
                to_date=args.to_date,
                limit=args.limit,
                force_reparse=args.force_reparse,
            )
        else:
            if args.run == "latest":
                record = ParserRunStore(conn).get_latest_run(trader_filter=args.trader)
                if record is None:
                    print("[generate] Nessun run trovato. Esegui prima senza --skip-replay.")
                    sys.exit(1)
                run_id = record.run_id
            else:
                run_id = int(args.run)
            print(f"[generate] Uso run_id={run_id}")

        reports_dir = Path(args.reports_dir)
        print(f"\n[generate] Produco CSV in {reports_dir}/run_{run_id}/")
        generated = export_all(conn, run_id, args.trader, reports_dir)
        print(f"\n[generate] {len(generated)} file generati.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
