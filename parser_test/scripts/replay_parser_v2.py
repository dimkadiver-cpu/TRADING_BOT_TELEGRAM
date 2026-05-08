"""Replay parser_v2 su messaggi raw salvati nel DB parser_test."""
from __future__ import annotations

import argparse
import csv
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
from src.parser_v2.profiles.registry import get_parser_v2_profile
from src.storage.parser_results_v2 import ParserResultV2Record, ParserResultV2Store
from src.storage.parser_runs import ParserRunStore
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver

_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,64})")

_TRADER_DEPRECATED_MSG = (
    "[warning] --trader is deprecated; use --trader-filter for message selection "
    "or --assume-trader for fallback."
)


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
    resolved_trader_id: str | None


@dataclass(slots=True)
class _AuditRow:
    raw_message_id: int
    source_trader_id: str | None
    resolved_trader_id: str | None
    parser_profile: str | None
    error_status: str
    error_message: str | None
    source_chat_id: str
    source_topic_id: int | None
    telegram_message_id: int
    message_ts: str
    text_preview: str | None


def _resolve_trader_filter_from_args(args: argparse.Namespace) -> str | None:
    if args.trader is not None:
        print(_TRADER_DEPRECATED_MSG, file=sys.stderr)
        return args.trader_filter if args.trader_filter is not None else args.trader
    return args.trader_filter


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
        "source_trader_id, raw_text, reply_to_message_id, source_topic_id, "
        "message_ts, resolved_trader_id "
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
            resolved_trader_id=r[8],
        )
        for r in conn.execute(query, params).fetchall()
    ]


def _build_audit_row(
    raw: _RawRow,
    resolved_trader_id: str | None,
    parser_profile: str | None,
    error_status: str,
    error_message: str | None,
) -> _AuditRow:
    return _AuditRow(
        raw_message_id=raw.raw_message_id,
        source_trader_id=raw.source_trader_id,
        resolved_trader_id=resolved_trader_id,
        parser_profile=parser_profile,
        error_status=error_status,
        error_message=error_message,
        source_chat_id=raw.source_chat_id,
        source_topic_id=raw.source_topic_id,
        telegram_message_id=raw.telegram_message_id,
        message_ts=raw.message_ts,
        text_preview=(raw.raw_text or "")[:120] if raw.raw_text else None,
    )


def _write_audit_csv(rows: list[_AuditRow], path: Path) -> None:
    columns = [
        "raw_message_id", "source_trader_id", "resolved_trader_id", "parser_profile",
        "error_status", "error_message", "source_chat_id", "source_topic_id",
        "telegram_message_id", "message_ts", "text_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "raw_message_id": row.raw_message_id,
                "source_trader_id": row.source_trader_id or "",
                "resolved_trader_id": row.resolved_trader_id or "",
                "parser_profile": row.parser_profile or "",
                "error_status": row.error_status,
                "error_message": row.error_message or "",
                "source_chat_id": row.source_chat_id,
                "source_topic_id": row.source_topic_id if row.source_topic_id is not None else "",
                "telegram_message_id": row.telegram_message_id,
                "message_ts": row.message_ts,
                "text_preview": row.text_preview or "",
            })


def run_replay(
    conn: sqlite3.Connection,
    *,
    db_path: str | None = None,
    trader_filter: str | None = None,
    assume_trader: str | None = None,
    parser_system: str = "parser_v2",
    parser_profile: str = "auto",
    allow_cross_profile_parse: bool = False,
    audit_csv_dir: Path | None = None,
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
        trader_filter=trader_filter,
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
    audit_rows: list[_AuditRow] | None = [] if audit_csv_dir is not None else None

    for raw in rows:
        effective_trader = normalize_trader_id(raw.resolved_trader_id)
        if effective_trader is None and trader_resolver is not None:
            inferred = _resolve_inferred_trader(trader_resolver, raw)
            effective_trader = normalize_trader_id(inferred)
        if effective_trader is None and assume_trader is not None:
            effective_trader = normalize_trader_id(assume_trader)

        if effective_trader is None:
            counts["UNRESOLVED_TRADER"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(raw, None, None, "UNRESOLVED_TRADER", None))
            continue

        if trader_filter is not None and effective_trader != trader_filter:
            counts["SKIPPED_TRADER_FILTER"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, None, "SKIPPED_TRADER_FILTER",
                    f"filter={trader_filter}",
                ))
            continue

        effective_profile = effective_trader if parser_profile == "auto" else parser_profile

        try:
            profile = get_parser_v2_profile(effective_profile)
        except KeyError:
            counts["SKIPPED_UNSUPPORTED_PARSER_PROFILE"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, effective_profile,
                    "SKIPPED_UNSUPPORTED_PARSER_PROFILE",
                    f"profile={effective_profile}",
                ))
            continue

        if (
            parser_profile != "auto"
            and effective_profile != effective_trader
            and not allow_cross_profile_parse
        ):
            counts["SKIPPED_TRADER_FILTER"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, effective_profile,
                    "SKIPPED_TRADER_FILTER",
                    f"cross_profile:profile={effective_profile},trader={effective_trader}",
                ))
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
                    trader_id=effective_trader,
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
                    trader_id=effective_trader,
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
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, effective_profile,
                    "PARSER_ERROR", repr(exc)[:200],
                ))

    run_store.complete_run(run_id)

    if audit_rows is not None and audit_csv_dir is not None:
        audit_csv_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_csv_dir / f"audit_run_{run_id}.csv"
        _write_audit_csv(audit_rows, audit_path)
        print(f"[replay] audit CSV: {audit_path}", flush=True)

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
    parser.add_argument("--trader-filter", dest="trader_filter")
    parser.add_argument("--message-trader-filter", dest="trader_filter")
    parser.add_argument("--assume-trader")
    parser.add_argument("--parser-system", default="parser_v2")
    parser.add_argument("--parser-profile", default="auto")
    parser.add_argument("--allow-cross-profile-parse", action="store_true")
    parser.add_argument("--audit-csv", action="store_true")
    parser.add_argument("--trader")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only-unparsed", action="store_true")
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--show-samples", type=int, default=0)
    args = parser.parse_args()

    trader_filter = _resolve_trader_filter_from_args(args)

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )
    audit_csv_dir = Path(db_path).parent if args.audit_csv else None
    conn = sqlite3.connect(db_path)
    try:
        run_replay(
            conn,
            db_path=db_path,
            trader_filter=trader_filter,
            assume_trader=args.assume_trader,
            parser_system=args.parser_system,
            parser_profile=args.parser_profile,
            allow_cross_profile_parse=args.allow_cross_profile_parse,
            audit_csv_dir=audit_csv_dir,
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
