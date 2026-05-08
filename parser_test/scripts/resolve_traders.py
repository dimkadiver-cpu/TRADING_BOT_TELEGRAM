"""Risolve e persiste resolved_trader_id su raw_messages."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.trader_resolution import build_trader_resolver, normalize_trader_id
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver


@dataclass(slots=True)
class _RawRow:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    source_trader_id: str | None
    raw_text: str | None
    reply_to_message_id: int | None
    resolved_trader_id: str | None


def _fetch_rows(conn: sqlite3.Connection) -> list[_RawRow]:
    return [
        _RawRow(
            raw_message_id=r[0],
            source_chat_id=r[1],
            telegram_message_id=r[2],
            source_trader_id=r[3],
            raw_text=r[4],
            reply_to_message_id=r[5],
            resolved_trader_id=r[6],
        )
        for r in conn.execute(
            "SELECT raw_message_id, source_chat_id, telegram_message_id, "
            "source_trader_id, raw_text, reply_to_message_id, resolved_trader_id "
            "FROM raw_messages ORDER BY raw_message_id ASC"
        ).fetchall()
    ]


def _write(
    conn: sqlite3.Connection,
    raw_message_id: int,
    resolved_trader_id: str | None,
    resolution_method: str,
) -> None:
    conn.execute(
        "UPDATE raw_messages SET resolved_trader_id=?, resolution_method=? WHERE raw_message_id=?",
        (resolved_trader_id, resolution_method, raw_message_id),
    )


def resolve_all(
    conn: sqlite3.Connection,
    *,
    resolver: EffectiveTraderResolver | None = None,
    db_path: str | None = None,
    assume_trader: str | None = None,
    force_re_resolve: bool = False,
) -> Counter[str]:
    if resolver is None and db_path:
        resolver = build_trader_resolver(db_path)

    rows = _fetch_rows(conn)
    counts: Counter[str] = Counter()

    for raw in rows:
        if raw.resolved_trader_id is not None and not force_re_resolve:
            counts["skipped_already_resolved"] += 1
            continue

        if raw.source_trader_id:
            _write(conn, raw.raw_message_id, normalize_trader_id(raw.source_trader_id), "source_trader_id")
            counts["source_trader_id"] += 1
            continue

        inferred_id: str | None = None
        inferred_method: str = "unresolved"
        if resolver is not None:
            result = resolver.resolve(
                EffectiveTraderContext(
                    source_chat_id=raw.source_chat_id,
                    source_chat_username=None,
                    source_chat_title=None,
                    raw_text=raw.raw_text,
                    reply_to_message_id=raw.reply_to_message_id,
                )
            )
            if result.trader_id:
                inferred_id = normalize_trader_id(result.trader_id)
                inferred_method = result.method

        if inferred_id:
            _write(conn, raw.raw_message_id, inferred_id, inferred_method)
            counts[inferred_method] += 1
            continue

        if assume_trader:
            _write(conn, raw.raw_message_id, normalize_trader_id(assume_trader), "assume_trader")
            counts["assume_trader"] += 1
            continue

        _write(conn, raw.raw_message_id, None, "unresolved")
        counts["unresolved"] += 1

    conn.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Risolve resolved_trader_id su raw_messages")
    parser.add_argument("--db-path")
    parser.add_argument("--db-name")
    parser.add_argument("--db-per-chat", action="store_true")
    parser.add_argument("--assume-trader")
    parser.add_argument("--force-re-resolve", action="store_true")
    args = parser.parse_args()

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=None,
    )
    conn = sqlite3.connect(db_path)
    try:
        apply_parser_test_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
        print(f"[resolve] {total} messaggi trovati")
        counts = resolve_all(
            conn,
            db_path=db_path,
            assume_trader=args.assume_trader,
            force_re_resolve=args.force_re_resolve,
        )
        summary = " | ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        print(f"[resolve] completato — {summary}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
