"""Replay parser_v2 su messaggi raw salvati nel DB parser_test."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.trader_resolution import build_trader_resolver, normalize_trader_id
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.registry import (
    canonicalize_trader_v2,
    get_parser_v2_profile,
)
from src.storage.parser_results_v2 import ParserResultV2Record, ParserResultV2Store
from src.storage.parser_runs import ParserRunStore
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver

_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,64})")


@dataclass(slots=True)
class _RawRow:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    source_trader_id: str | None
    raw_text: str | None
    reply_to_message_id: int | None
    source_topic_id: int | None
    message_ts: str


def _resolve_trader(
    *,
    source_trader_id: str | None,
    inferred_trader_id: str | None,
    explicit: str | None,
) -> str | None:
    if source_trader_id is not None:
        return normalize_trader_id(source_trader_id)
    if inferred_trader_id is not None:
        return normalize_trader_id(inferred_trader_id)
    return canonicalize_trader_v2(explicit)


def _resolve_inferred_trader(
    resolver: EffectiveTraderResolver,
    raw: _RawRow,
) -> str | None:
    result = resolver.resolve(
        EffectiveTraderContext(
            source_chat_id=raw.source_chat_id,
            source_chat_username=None,
            source_chat_title=None,
            raw_text=raw.raw_text,
            reply_to_message_id=raw.reply_to_message_id,
        )
    )
    return result.trader_id


def _extract_telegram_links(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _LINK_RE.finditer(text):
        v = m.group(0)
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _extract_hashtags(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _HASHTAG_RE.finditer(text):
        v = m.group(1)
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_raw_rows(
    conn: sqlite3.Connection,
    *,
    chat_id: str | None,
    from_date: str | None,
    to_date: str | None,
    limit: int | None,
    only_unparsed: bool,
) -> list[_RawRow]:
    query = (
        "SELECT raw_message_id, source_chat_id, telegram_message_id, "
        "source_trader_id, raw_text, reply_to_message_id, source_topic_id, message_ts "
        "FROM raw_messages WHERE 1=1"
    )
    params: list[object] = []
    if chat_id is not None:
        query += " AND source_chat_id = ?"
        params.append(chat_id)
    if from_date is not None:
        query += " AND message_ts >= ?"
        params.append(from_date)
    if to_date is not None:
        query += " AND message_ts <= ?"
        params.append(to_date)
    if only_unparsed:
        query += (
            " AND raw_message_id NOT IN "
            "(SELECT raw_message_id FROM parser_results_v2 WHERE error_status = 'OK')"
        )
    query += " ORDER BY message_ts ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return [
        _RawRow(
            raw_message_id=r[0],
            source_chat_id=r[1],
            telegram_message_id=r[2],
            source_trader_id=r[3],
            raw_text=r[4],
            reply_to_message_id=r[5],
            source_topic_id=r[6],
            message_ts=r[7],
        )
        for r in conn.execute(query, params).fetchall()
    ]


def run_replay(
    conn: sqlite3.Connection,
    *,
    db_path: str | None = None,
    trader: str | None = None,
    chat_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int | None = None,
    only_unparsed: bool = False,
    force_reparse: bool = False,
    show_samples: int = 0,
    trader_resolver: EffectiveTraderResolver | None = None,
) -> int:
    apply_parser_test_schema(conn)
    run_store = ParserRunStore(conn)
    result_store = ParserResultV2Store(conn)
    if trader_resolver is None and db_path:
        trader_resolver = build_trader_resolver(db_path)

    run_id = run_store.create_run(
        trader_filter=trader,
        force_reparse=force_reparse,
    )
    print(f"[replay] run_id={run_id} avviato", flush=True)

    rows = _fetch_raw_rows(
        conn,
        chat_id=chat_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        only_unparsed=only_unparsed,
    )
    print(f"[replay] {len(rows)} messaggi da processare", flush=True)

    runtime = UniversalParserRuntime()
    counts: Counter[str] = Counter()
    samples: list[str] = []

    for raw in rows:
        inferred_trader_id = (
            _resolve_inferred_trader(trader_resolver, raw) if trader_resolver is not None else None
        )
        trader_id = _resolve_trader(
            source_trader_id=raw.source_trader_id,
            inferred_trader_id=inferred_trader_id,
            explicit=trader,
        )

        if trader_id is None:
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=None,
                    parser_profile=None,
                    primary_class=None,
                    parse_status=None,
                    primary_intent=None,
                    confidence=None,
                    canonical_json=None,
                    warnings_json=None,
                    diagnostics_json=None,
                    error_status="UNRESOLVED_TRADER",
                    error_message=f"raw_message_id={raw.raw_message_id}",
                    created_at=_now_iso(),
                )
            )
            counts["UNRESOLVED_TRADER"] += 1
            continue

        try:
            profile = get_parser_v2_profile(trader_id)
        except KeyError as exc:
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=trader_id,
                    parser_profile=None,
                    primary_class=None,
                    parse_status=None,
                    primary_intent=None,
                    confidence=None,
                    canonical_json=None,
                    warnings_json=None,
                    diagnostics_json=None,
                    error_status="PARSER_ERROR",
                    error_message=str(exc)[:500],
                    created_at=_now_iso(),
                )
            )
            counts["PARSER_ERROR"] += 1
            continue

        try:
            text = raw.raw_text or ""
            raw_ctx = RawContext(
                raw_text=text,
                message_id=raw.telegram_message_id,
                reply_to_message_id=raw.reply_to_message_id,
                source_chat_id=raw.source_chat_id,
                source_topic_id=raw.source_topic_id,
                extracted_links=_extract_telegram_links(text),
                hashtags=_extract_hashtags(text),
            )
            context = ParserContext(
                raw_context=raw_ctx,
                message_id=raw.telegram_message_id,
                reply_to_message_id=raw.reply_to_message_id,
                source_chat_id=raw.source_chat_id,
                source_topic_id=raw.source_topic_id,
            )
            canonical = runtime.parse(text, context, profile)
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=trader_id,
                    parser_profile=canonical.parser_profile,
                    primary_class=canonical.primary_class,
                    parse_status=canonical.parse_status,
                    primary_intent=canonical.primary_intent,
                    confidence=canonical.confidence,
                    canonical_json=canonical.model_dump_json(exclude_none=True),
                    warnings_json=json.dumps(canonical.warnings) if canonical.warnings else None,
                    diagnostics_json=json.dumps(canonical.diagnostics) if canonical.diagnostics else None,
                    error_status="OK",
                    error_message=None,
                    created_at=_now_iso(),
                )
            )
            counts[canonical.parse_status] += 1
            if show_samples and len(samples) < show_samples:
                samples.append(f"  [{canonical.primary_class}/{canonical.parse_status}] {text[:80]}")
        except Exception as exc:
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=trader_id,
                    parser_profile=None,
                    primary_class=None,
                    parse_status=None,
                    primary_intent=None,
                    confidence=None,
                    canonical_json=None,
                    warnings_json=None,
                    diagnostics_json=None,
                    error_status="PARSER_ERROR",
                    error_message=repr(exc)[:500],
                    created_at=_now_iso(),
                )
            )
            counts["PARSER_ERROR"] += 1

    run_store.complete_run(run_id)

    total = sum(counts.values())
    print(f"\n[replay] run={run_id} completato — {total} messaggi")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    if samples:
        print("\n[replay] campioni:")
        for s in samples:
            print(s)

    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay parser_v2 su raw_messages")
    parser.add_argument("--db-path")
    parser.add_argument("--db-name")
    parser.add_argument("--db-per-chat", action="store_true")
    parser.add_argument("--chat-id")
    parser.add_argument("--trader")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only-unparsed", action="store_true")
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--show-samples", type=int, default=0)
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
    try:
        run_replay(
            conn,
            db_path=db_path,
            trader=args.trader,
            chat_id=args.chat_id,
            from_date=args.from_date,
            to_date=args.to_date,
            limit=args.limit,
            only_unparsed=args.only_unparsed,
            force_reparse=args.force_reparse,
            show_samples=args.show_samples,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
