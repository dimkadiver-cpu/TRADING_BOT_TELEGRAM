"""Replay parser on a dedicated parser_test database."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config_loader import load_config
from src.core.migrations import apply_migrations
from src.parser.parser_config import normalize_parser_mode
from src.parser.pipeline import MinimalParserPipeline, ParserInput
from src.storage.parse_results import ParseResultStore
from src.storage.raw_messages import RawMessageStore
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.telegram.trader_mapping import TelegramSourceTraderMapper


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
    parser.add_argument("--only-unparsed", action="store_true", help="Replay only rows without parse_results.")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process.")
    parser.add_argument("--chat-id", default=None, help="Filter by raw_messages.source_chat_id.")
    parser.add_argument("--trader", default=None, help="Filter by resolved trader id (e.g. TA, TB).")
    parser.add_argument("--from-date", default=None, help="Inclusive lower bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument("--to-date", default=None, help="Inclusive upper bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument("--parser-mode", default=None, help="Parser mode override: regex_only | llm_only | hybrid_auto")
    parser.add_argument(
        "--show-normalized-samples",
        type=int,
        default=3,
        help="How many normalized parse_result examples to print (default: 3, 0 to disable).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    parser_test_dir = PROJECT_ROOT / "parser_test"
    env_path = parser_test_dir / ".env"
    if env_path.exists():
        _load_env_file(env_path)

    db_path = args.db_path or os.getenv(
        "PARSER_TEST_DB_PATH",
        str(parser_test_dir / "db" / "parser_test.sqlite3"),
    )
    db_path = str((PROJECT_ROOT / db_path).resolve()) if not Path(db_path).is_absolute() else db_path

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
    parser_pipeline = MinimalParserPipeline(
        trader_aliases=config.trader_aliases,
        global_parser_mode=normalize_parser_mode(args.parser_mode or os.getenv("PARSER_MODE", "regex_only")),
        trader_parser_modes={},
    )
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
    by_normalized_event_type: Counter[str] = Counter()
    by_resolved_trader_id: Counter[str] = Counter()
    by_eligibility_status: Counter[str] = Counter()
    normalized_samples: list[dict[str, object]] = []

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

            parse_record = parser_pipeline.parse(
                ParserInput(
                    raw_message_id=row.raw_message_id,
                    raw_text=row.raw_text,
                    eligibility_status=acquisition_status,
                    eligibility_reason=eligibility_reason,
                    resolved_trader_id=item.resolved_trader_id,
                    trader_resolution_method=item.trader_resolution_method,
                    linkage_method=eligibility.strong_link_method,
                    source_chat_id=row.source_chat_id,
                    source_message_id=row.telegram_message_id,
                    linkage_reference_id=eligibility.referenced_message_id,
                )
            )
            parse_results_store.upsert(parse_record)
            processed += 1
            by_message_type[parse_record.message_type] += 1
            by_resolved_trader_id[parse_record.resolved_trader_id or "UNRESOLVED"] += 1
            by_eligibility_status[parse_record.eligibility_status] += 1
            normalized_obj = _parse_normalized_json(parse_record.parse_result_normalized_json)
            event_type = str(normalized_obj.get("event_type", "UNKNOWN"))
            by_normalized_event_type[event_type] += 1
            if args.show_normalized_samples > 0 and len(normalized_samples) < args.show_normalized_samples:
                normalized_samples.append(normalized_obj)
        except Exception:
            skipped += 1

    print(f"db_path: {db_path}")
    print(f"total raw selected: {len(selected)}")
    print(f"total processed: {processed}")
    print(f"total skipped: {skipped}")
    print_counter("counts by message_type", by_message_type)
    print_counter("counts by normalized event_type", by_normalized_event_type)
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
    normalized_trader_filter = trader_filter.strip() if trader_filter else None
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
        if normalized_trader_filter and resolved.trader_id != normalized_trader_filter:
            continue
        selected.append(
            SelectedRaw(
                row=row,
                resolved_trader_id=resolved.trader_id,
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


if __name__ == "__main__":
    main()


