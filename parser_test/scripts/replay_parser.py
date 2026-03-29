"""Replay parser on a dedicated parser_test database."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import re
from pathlib import Path
import sqlite3
import sys
import traceback

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config_loader import load_config
from src.core.migrations import apply_migrations
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_hashtags, extract_telegram_links
from src.parser.trader_profiles.registry import canonicalize_trader_code, get_profile_parser
from src.storage.parse_results import ParseResultRecord, ParseResultStore
from src.storage.raw_messages import RawMessageStore
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.telegram.trader_mapping import TelegramSourceTraderMapper
from parser_test.scripts.db_paths import resolve_parser_test_db_path

_SIGNAL_ID_RE = re.compile(r"\bSIGNAL\s*ID\s*:\s*#?\s*(?P<id>\d+)\b", re.IGNORECASE)

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def _get_error_logger() -> logging.Logger:
    logger = logging.getLogger("replay_parser.errors")
    if not logger.handlers:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(_LOG_DIR / "replay_errors.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    return logger


_error_logger = _get_error_logger()


@dataclass(slots=True)
class ReplayRawMessage:
    raw_message_id: int
    source_chat_id: str
    source_chat_title: str | None
    source_chat_username: str | None
    telegram_message_id: int
    reply_to_message_id: int | None
    raw_text: str | None
    message_ts: str


@dataclass(slots=True)
class SelectedRaw:
    row: ReplayRawMessage
    resolved_trader_id: str | None
    trader_resolution_method: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay parser on parser_test DB.")
    parser.add_argument("--db-path", default=None, help="Path to parser_test sqlite DB.")
    parser.add_argument("--db-name", default=None, help="Logical DB name under parser_test/db (e.g. trader_a_mar).")
    parser.add_argument(
        "--db-per-chat",
        action="store_true",
        help="Use parser_test/db/parser_test__chat_<chat>.sqlite3 based on --chat-id.",
    )
    parser.add_argument("--only-unparsed", action="store_true", help="Replay only rows without parse_results.")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process.")
    parser.add_argument("--chat-id", default=None, help="Filter by raw_messages.source_chat_id.")
    parser.add_argument(
        "--trader",
        default=None,
        help="Filter by resolved trader id alias/canonical (e.g. TA, A, trader_a, TB).",
    )
    parser.add_argument("--from-date", default=None, help="Inclusive lower bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument("--to-date", default=None, help="Inclusive upper bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument(
        "--show-normalized-samples",
        type=int,
        default=3,
        help="How many normalized parse_result examples to print (default: 3, 0 to disable).",
    )
    return parser.parse_args()


def _build_parse_result_record(
    *,
    result: TraderParseResult,
    row: ReplayRawMessage,
    item: SelectedRaw,
    acquisition_status: str,
    eligibility_reason: str,
    linkage_method: str | None,
    now_ts: str,
) -> ParseResultRecord:
    entities = result.entities or {}
    completeness = "INCOMPLETE" if result.message_type == "SETUP_INCOMPLETE" else "COMPLETE"
    normalized_json = json.dumps(
        {
            "message_type": result.message_type,
            "intents": result.intents,
            "entities": result.entities,
            "target_refs": result.target_refs,
            "actions_structured": result.actions_structured,
            "warnings": result.warnings,
            "confidence": result.confidence,
        },
        ensure_ascii=False,
        default=str,
    )
    return ParseResultRecord(
        raw_message_id=row.raw_message_id,
        eligibility_status=acquisition_status,
        eligibility_reason=eligibility_reason,
        declared_trader_tag=None,
        resolved_trader_id=item.resolved_trader_id,
        trader_resolution_method=item.trader_resolution_method,
        message_type=result.message_type,
        parse_status="PARSED",
        completeness=completeness,
        is_executable=result.message_type == "NEW_SIGNAL" and completeness == "COMPLETE",
        symbol=entities.get("symbol") or None,
        direction=entities.get("side") or None,
        entry_raw=entities.get("entry_raw") or None,
        stop_raw=entities.get("stop_raw") or None,
        target_raw_list=None,
        leverage_hint=None,
        risk_hint=None,
        risky_flag=False,
        linkage_method=linkage_method,
        linkage_status=None,
        warning_text=" | ".join(result.warnings) if result.warnings else None,
        notes=None,
        parse_result_normalized_json=normalized_json,
        created_at=now_ts,
        updated_at=now_ts,
    )


def _build_skipped_record(
    *,
    row: ReplayRawMessage,
    item: SelectedRaw,
    acquisition_status: str,
    eligibility_reason: str,
    linkage_method: str | None,
    now_ts: str,
) -> ParseResultRecord:
    return ParseResultRecord(
        raw_message_id=row.raw_message_id,
        eligibility_status=acquisition_status,
        eligibility_reason=eligibility_reason,
        declared_trader_tag=None,
        resolved_trader_id=item.resolved_trader_id,
        trader_resolution_method=item.trader_resolution_method,
        message_type="UNCLASSIFIED",
        parse_status="SKIPPED",
        completeness="COMPLETE",
        is_executable=False,
        symbol=None,
        direction=None,
        entry_raw=None,
        stop_raw=None,
        target_raw_list=None,
        leverage_hint=None,
        risk_hint=None,
        risky_flag=False,
        linkage_method=linkage_method,
        linkage_status=None,
        warning_text="no_profile_parser",
        notes=None,
        parse_result_normalized_json=None,
        created_at=now_ts,
        updated_at=now_ts,
    )


def _parse_one(
    *,
    row: ReplayRawMessage,
    item: SelectedRaw,
    acquisition_status: str,
    eligibility_reason: str,
    linkage_method: str | None,
    reply_raw_text: str | None,
) -> ParseResultRecord:
    now_ts = datetime.now(timezone.utc).isoformat()
    profile_parser = get_profile_parser(item.resolved_trader_id or "")
    if profile_parser is None:
        return _build_skipped_record(
            row=row,
            item=item,
            acquisition_status=acquisition_status,
            eligibility_reason=eligibility_reason,
            linkage_method=linkage_method,
            now_ts=now_ts,
        )
    context = ParserContext(
        trader_code=item.resolved_trader_id or "",
        message_id=row.telegram_message_id,
        reply_to_message_id=row.reply_to_message_id,
        channel_id=row.source_chat_id,
        raw_text=row.raw_text or "",
        reply_raw_text=reply_raw_text,
        extracted_links=_context_links(row.raw_text or ""),
        hashtags=_context_hashtags(row.raw_text or ""),
    )
    result = profile_parser.parse_message(text=row.raw_text or "", context=context)
    return _build_parse_result_record(
        result=result,
        row=row,
        item=item,
        acquisition_status=acquisition_status,
        eligibility_reason=eligibility_reason,
        linkage_method=linkage_method,
        now_ts=now_ts,
    )


def main() -> None:
    args = parse_args()
    parser_test_dir = PROJECT_ROOT / "parser_test"
    env_path = parser_test_dir / ".env"
    if env_path.exists():
        _load_env_file(env_path)

    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )

    if _is_live_db_path(db_path):
        raise RuntimeError(f"Refusing to run on live DB path: {db_path}")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    apply_migrations(
        db_path=db_path,
        migrations_dir=str(PROJECT_ROOT / "db" / "migrations"),
    )

    config = load_config(str(PROJECT_ROOT))
    source_map_path = os.getenv(
        "PARSER_TEST_TELEGRAM_SOURCE_MAP_PATH",
        str(PROJECT_ROOT / "config" / "telegram_source_map.json"),
    )
    source_map_path = (
        str((PROJECT_ROOT / source_map_path).resolve())
        if not Path(source_map_path).is_absolute()
        else source_map_path
    )

    trader_mapper = TelegramSourceTraderMapper.from_json_file(
        file_path=source_map_path,
        trader_aliases=config.trader_aliases,
        known_trader_ids=set(config.traders.keys()),
    )
    raw_store = RawMessageStore(db_path=db_path)
    trader_resolver = EffectiveTraderResolver(
        source_mapper=trader_mapper,
        raw_store=raw_store,
        trader_aliases=config.trader_aliases,
        known_trader_ids=set(config.traders.keys()),
    )
    eligibility_evaluator = MessageEligibilityEvaluator(raw_store=raw_store)
    parse_results_store = ParseResultStore(db_path=db_path)

    raws = fetch_raw_messages(
        db_path=db_path,
        only_unparsed=args.only_unparsed,
        limit=args.limit,
        chat_id=args.chat_id,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    selected = select_rows(raws=raws, trader_filter=args.trader, trader_resolver=trader_resolver)

    processed = 0
    skipped = 0
    by_message_type: Counter[str] = Counter()
    by_resolved_trader_id: Counter[str] = Counter()
    by_eligibility_status: Counter[str] = Counter()
    normalized_samples: list[dict[str, object]] = []
    raw_store = RawMessageStore(db_path=db_path)

    for item in selected:
        row = item.row
        try:
            eligibility = eligibility_evaluator.evaluate(
                source_chat_id=row.source_chat_id,
                raw_text=row.raw_text,
                reply_to_message_id=row.reply_to_message_id,
            )

            acquisition_status = eligibility.status
            eligibility_reason = eligibility.reason
            if item.resolved_trader_id is None:
                acquisition_status = "ACQUIRED_UNKNOWN_TRADER"
                eligibility_reason = f"{eligibility_reason}; unresolved_trader"

            reply_raw_text = None
            if row.reply_to_message_id is not None:
                parent = raw_store.get_by_source_and_message_id(row.source_chat_id, row.reply_to_message_id)
                if parent is not None:
                    reply_raw_text = parent.raw_text
            if reply_raw_text is None:
                signal_id = _extract_signal_id(row.raw_text or "")
                if signal_id is not None:
                    reply_raw_text = parse_results_store.get_raw_text_by_signal_id(
                        resolved_trader_id=item.resolved_trader_id or "",
                        signal_id=signal_id,
                    )

            parse_record = _parse_one(
                row=row,
                item=item,
                acquisition_status=acquisition_status,
                eligibility_reason=eligibility_reason,
                linkage_method=eligibility.strong_link_method,
                reply_raw_text=reply_raw_text,
            )
            parse_results_store.upsert(parse_record)
            processed += 1
            by_message_type[parse_record.message_type] += 1
            by_resolved_trader_id[parse_record.resolved_trader_id or "UNRESOLVED"] += 1
            by_eligibility_status[parse_record.eligibility_status] += 1
            if args.show_normalized_samples > 0 and len(normalized_samples) < args.show_normalized_samples:
                normalized_samples.append(_parse_normalized_json(parse_record.parse_result_normalized_json))
        except Exception as exc:
            skipped += 1
            _error_logger.error(
                "raw_message_id=%s error=%s\n%s",
                row.raw_message_id,
                exc,
                traceback.format_exc(),
            )

    print(f"db_path: {db_path}")
    print(f"total raw selected: {len(selected)}")
    print(f"total processed: {processed}")
    print(f"total skipped: {skipped}")
    print_counter("counts by message_type", by_message_type)
    print_counter("counts by resolved_trader_id", by_resolved_trader_id)
    print_counter("counts by eligibility_status", by_eligibility_status)
    if normalized_samples:
        print("normalized parse_result samples:")
        for index, sample in enumerate(normalized_samples, start=1):
            print(f"  sample #{index}: {json.dumps(sample, ensure_ascii=False, sort_keys=True)}")


def fetch_raw_messages(
    db_path: str,
    only_unparsed: bool,
    limit: int | None,
    chat_id: str | None,
    from_date: str | None,
    to_date: str | None,
) -> list[ReplayRawMessage]:
    query_parts = [
        """
        SELECT
          rm.raw_message_id,
          rm.source_chat_id,
          rm.source_chat_title,
          NULL as source_chat_username,
          rm.telegram_message_id,
          rm.reply_to_message_id,
          rm.raw_text,
          rm.message_ts
        FROM raw_messages rm
        """.strip()
    ]
    where: list[str] = []
    params: list[object] = []

    if only_unparsed:
        query_parts.append("LEFT JOIN parse_results pr ON pr.raw_message_id = rm.raw_message_id")
        where.append("pr.raw_message_id IS NULL")
    if chat_id:
        where.append("rm.source_chat_id = ?")
        params.append(chat_id)
    if from_date:
        where.append("rm.message_ts >= ?")
        params.append(_normalize_cli_date(from_date, end_of_day=False))
    if to_date:
        where.append("rm.message_ts <= ?")
        params.append(_normalize_cli_date(to_date, end_of_day=True))

    if where:
        query_parts.append("WHERE " + " AND ".join(where))
    query_parts.append("ORDER BY rm.message_ts ASC, rm.raw_message_id ASC")
    if limit is not None and limit > 0:
        query_parts.append("LIMIT ?")
        params.append(limit)

    sql = "\n".join(query_parts)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        ReplayRawMessage(
            raw_message_id=int(row[0]),
            source_chat_id=str(row[1]),
            source_chat_title=row[2],
            source_chat_username=row[3],
            telegram_message_id=int(row[4]),
            reply_to_message_id=int(row[5]) if row[5] is not None else None,
            raw_text=row[6],
            message_ts=row[7],
        )
        for row in rows
    ]


def select_rows(
    raws: list[ReplayRawMessage],
    trader_filter: str | None,
    trader_resolver: EffectiveTraderResolver,
) -> list[SelectedRaw]:
    selected: list[SelectedRaw] = []
    normalized_trader_filter = canonicalize_trader_code(trader_filter) if trader_filter else None
    for row in raws:
        resolved = trader_resolver.resolve(
            EffectiveTraderContext(
                source_chat_id=row.source_chat_id,
                source_chat_username=row.source_chat_username,
                source_chat_title=row.source_chat_title,
                raw_text=row.raw_text,
                reply_to_message_id=row.reply_to_message_id,
            )
        )
        resolved_canonical = canonicalize_trader_code(resolved.trader_id) or resolved.trader_id
        if normalized_trader_filter and resolved_canonical != normalized_trader_filter:
            continue
        selected.append(
            SelectedRaw(
                row=row,
                resolved_trader_id=resolved_canonical,
                trader_resolution_method=resolved.method,
            )
        )
    return selected


def print_counter(title: str, counts: Counter[str]) -> None:
    print(f"{title}:")
    if not counts:
        print("  (none)")
        return
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")


def _parse_normalized_json(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return {}
    return {}


def _context_links(raw_text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for link in extract_telegram_links(raw_text):
        normalized = link if link.startswith("http") else f"https://{link}"
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


def _context_hashtags(raw_text: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for tag in extract_hashtags(raw_text):
        rendered = f"#{tag}"
        lowered = rendered.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        tags.append(rendered)
    return tags


def _extract_signal_id(raw_text: str) -> int | None:
    match = _SIGNAL_ID_RE.search(raw_text or "")
    if not match:
        return None
    try:
        return int(match.group("id"))
    except ValueError:
        return None


def _normalize_cli_date(value: str, end_of_day: bool) -> str:
    text = value.strip()
    if "T" not in text:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            day = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        return day.isoformat()

    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _is_live_db_path(db_path: str) -> bool:
    candidate = Path(db_path).resolve()
    live = (PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3").resolve()
    return candidate == live


def _load_env_file(path: Path) -> None:
    if load_dotenv is not None:
        load_dotenv(path)
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def replay_database(
    *,
    db_path: str | None = None,
    db_name: str | None = None,
    db_per_chat: bool = False,
    only_unparsed: bool = False,
    limit: int | None = None,
    chat_id: str | None = None,
    trader: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    parser_mode: str | None = None,
    show_normalized_samples: int = 3,
) -> None:
    """Callable entry point for use by other scripts (e.g. generate_parser_reports.py)."""
    parser_test_dir = PROJECT_ROOT / "parser_test"
    env_path = parser_test_dir / ".env"
    if env_path.exists():
        _load_env_file(env_path)

    resolved_db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=db_path,
        db_name=db_name,
        db_per_chat=db_per_chat,
        chat_ref=chat_id,
    )

    if _is_live_db_path(resolved_db_path):
        raise RuntimeError(f"Refusing to run on live DB path: {resolved_db_path}")

    Path(resolved_db_path).parent.mkdir(parents=True, exist_ok=True)
    apply_migrations(
        db_path=resolved_db_path,
        migrations_dir=str(PROJECT_ROOT / "db" / "migrations"),
    )

    config = load_config(str(PROJECT_ROOT))
    source_map_path = os.getenv(
        "PARSER_TEST_TELEGRAM_SOURCE_MAP_PATH",
        str(PROJECT_ROOT / "config" / "telegram_source_map.json"),
    )
    source_map_path = (
        str((PROJECT_ROOT / source_map_path).resolve())
        if not Path(source_map_path).is_absolute()
        else source_map_path
    )

    trader_mapper = TelegramSourceTraderMapper.from_json_file(
        file_path=source_map_path,
        trader_aliases=config.trader_aliases,
        known_trader_ids=set(config.traders.keys()),
    )
    raw_store = RawMessageStore(db_path=resolved_db_path)
    trader_resolver = EffectiveTraderResolver(
        source_mapper=trader_mapper,
        raw_store=raw_store,
        trader_aliases=config.trader_aliases,
        known_trader_ids=set(config.traders.keys()),
    )
    eligibility_evaluator = MessageEligibilityEvaluator(raw_store=raw_store)
    parse_results_store = ParseResultStore(db_path=resolved_db_path)

    effective_trader = None if trader in (None, "trader_all") else trader

    raws = fetch_raw_messages(
        db_path=resolved_db_path,
        only_unparsed=only_unparsed,
        limit=limit,
        chat_id=chat_id,
        from_date=from_date,
        to_date=to_date,
    )
    selected = select_rows(raws=raws, trader_filter=effective_trader, trader_resolver=trader_resolver)

    processed = 0
    skipped = 0
    by_message_type: Counter[str] = Counter()
    by_resolved_trader_id: Counter[str] = Counter()
    by_eligibility_status: Counter[str] = Counter()
    normalized_samples: list[dict[str, object]] = []
    raw_store = RawMessageStore(db_path=resolved_db_path)

    for item in selected:
        row = item.row
        try:
            eligibility = eligibility_evaluator.evaluate(
                source_chat_id=row.source_chat_id,
                raw_text=row.raw_text,
                reply_to_message_id=row.reply_to_message_id,
            )

            acquisition_status = eligibility.status
            eligibility_reason = eligibility.reason
            if item.resolved_trader_id is None:
                acquisition_status = "ACQUIRED_UNKNOWN_TRADER"
                eligibility_reason = f"{eligibility_reason}; unresolved_trader"

            reply_raw_text = None
            if row.reply_to_message_id is not None:
                parent = raw_store.get_by_source_and_message_id(row.source_chat_id, row.reply_to_message_id)
                if parent is not None:
                    reply_raw_text = parent.raw_text
            if reply_raw_text is None:
                signal_id = _extract_signal_id(row.raw_text or "")
                if signal_id is not None:
                    reply_raw_text = parse_results_store.get_raw_text_by_signal_id(
                        resolved_trader_id=item.resolved_trader_id or "",
                        signal_id=signal_id,
                    )

            parse_record = _parse_one(
                row=row,
                item=item,
                acquisition_status=acquisition_status,
                eligibility_reason=eligibility_reason,
                linkage_method=eligibility.strong_link_method,
                reply_raw_text=reply_raw_text,
            )
            parse_results_store.upsert(parse_record)
            processed += 1
            by_message_type[parse_record.message_type] += 1
            by_resolved_trader_id[parse_record.resolved_trader_id or "UNRESOLVED"] += 1
            by_eligibility_status[parse_record.eligibility_status] += 1
            if show_normalized_samples > 0 and len(normalized_samples) < show_normalized_samples:
                normalized_samples.append(_parse_normalized_json(parse_record.parse_result_normalized_json))
        except Exception as exc:
            skipped += 1
            _error_logger.error(
                "raw_message_id=%s error=%s\n%s",
                row.raw_message_id,
                exc,
                traceback.format_exc(),
            )

    print(f"db_path: {resolved_db_path}")
    print(f"trader filter: {effective_trader or '(all)'}")
    print(f"total raw selected: {len(selected)}")
    print(f"total processed: {processed}")
    print(f"total skipped: {skipped}")
    print_counter("counts by message_type", by_message_type)
    print_counter("counts by resolved_trader_id", by_resolved_trader_id)
    print_counter("counts by eligibility_status", by_eligibility_status)
    if normalized_samples:
        print("normalized parse_result samples:")
        for index, sample in enumerate(normalized_samples, start=1):
            print(f"  sample #{index}: {json.dumps(sample, ensure_ascii=False, sort_keys=True)}")


if __name__ == "__main__":
    main()
