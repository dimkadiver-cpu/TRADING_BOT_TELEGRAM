from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_COLUMNS = [
    "raw_message_id",
    "telegram_message_id",
    "source_chat_id",
    "source_topic_id",
    "reply_to_message_id",
    "message_ts",
    "source_trader_id",
    "resolved_trader_id",
    "raw_text",
]


def _available_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(raw_messages)").fetchall()
    return {str(row[1]) for row in rows}


def _build_query(args: argparse.Namespace, columns: list[str]) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if args.resolved_trader:
        where.append("resolved_trader_id = ?")
        params.append(args.resolved_trader)
    if args.source_trader:
        where.append("source_trader_id = ?")
        params.append(args.source_trader)
    if args.source_topic is not None:
        where.append("source_topic_id = ?")
        params.append(args.source_topic)
    if args.reply_only:
        where.append("reply_to_message_id IS NOT NULL")
    if args.contains:
        where.append("raw_text LIKE ?")
        params.append(f"%{args.contains}%")

    sql = f"SELECT {', '.join(columns)} FROM raw_messages"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY raw_message_id ASC"
    sql += " LIMIT ?"
    params.append(args.limit)
    return sql, params


def _rows_to_dicts(cursor: sqlite3.Cursor, rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    names = [col[0] for col in cursor.description or []]
    return [dict(zip(names, row)) for row in rows]


def _write_jsonl(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _print_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        print(f"[raw_message_id={row.get('raw_message_id')}]")
        for key, value in row.items():
            if key == "raw_message_id":
                continue
            print(f"{key}: {value}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample parser_test raw_messages for parser_v2 profile design.")
    parser.add_argument("--db-path", required=True, help="Path to the parser_test sqlite database.")
    parser.add_argument("--resolved-trader", help="Filter by raw_messages.resolved_trader_id.")
    parser.add_argument("--source-trader", help="Filter by raw_messages.source_trader_id.")
    parser.add_argument("--source-topic", type=int, help="Filter by raw_messages.source_topic_id.")
    parser.add_argument("--contains", help="Substring filter applied to raw_text with LIKE.")
    parser.add_argument("--reply-only", action="store_true", help="Keep only rows that reply to another message.")
    parser.add_argument("--limit", type=int, default=80, help="Maximum number of rows to fetch.")
    parser.add_argument("--jsonl-out", help="Optional path where sampled rows are written as JSONL.")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        columns = [name for name in DEFAULT_COLUMNS if name in _available_columns(conn)]
        if "raw_text" not in columns:
            raise SystemExit("raw_messages.raw_text is required but missing from this DB.")
        sql, params = _build_query(args, columns)
        cursor = conn.execute(sql, params)
        rows = _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        conn.close()

    if args.jsonl_out:
        _write_jsonl(rows, Path(args.jsonl_out))

    _print_rows(rows)
    print(f"rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
