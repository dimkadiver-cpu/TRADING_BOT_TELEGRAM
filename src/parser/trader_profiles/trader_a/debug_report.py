"""DB-first debug report runner for Trader A parser outputs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import sys
from typing import Any

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_hashtags, extract_telegram_links
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


@dataclass(slots=True)
class DebugCase:
    case_id: str
    text: str
    reply_to_message_id: int | None = None
    extracted_links: list[str] | None = None
    expected_message_type: str | None = None
    expected_intents: list[str] | None = None


@dataclass(slots=True)
class DebugRawMessage:
    raw_message_id: int
    telegram_message_id: int
    source_chat_id: str
    reply_to_message_id: int | None
    raw_text: str
    resolved_trader_id: str | None
    declared_trader_tag: str | None


DEFAULT_CASES: list[DebugCase] = [
    DebugCase(
        case_id="new_signal_basic",
        text="BTCUSDT long entry 62000 sl 61000 tp1 63000 tp2 64000",
        expected_message_type="NEW_SIGNAL",
    ),
    DebugCase(
        case_id="setup_incomplete",
        text="ETHUSDT long entry only, sl later",
        expected_message_type="SETUP_INCOMPLETE",
    ),
    DebugCase(
        case_id="update_reply",
        text="move stop to be",
        reply_to_message_id=555,
        expected_message_type="UPDATE",
        expected_intents=["U_MOVE_STOP_TO_BE", "U_MOVE_STOP"],
    ),
    DebugCase(
        case_id="update_link",
        text="close all positions https://t.me/c/123/456",
        expected_message_type="UPDATE",
        expected_intents=["U_CLOSE_FULL"],
    ),
    DebugCase(
        case_id="cancel_pending",
        text="cancel pending limits",
        reply_to_message_id=777,
        expected_message_type="UPDATE",
        expected_intents=["U_CANCEL_PENDING_ORDERS"],
    ),
    DebugCase(
        case_id="close_partial",
        text="partial close 50%",
        reply_to_message_id=778,
        expected_message_type="UPDATE",
        expected_intents=["U_CLOSE_PARTIAL"],
    ),
    DebugCase(
        case_id="tp_hit",
        text="tp1 hit on this setup",
        reply_to_message_id=779,
        expected_message_type="UPDATE",
        expected_intents=["U_TP_HIT"],
    ),
    DebugCase(
        case_id="stop_hit",
        text="stopped out",
        reply_to_message_id=780,
        expected_message_type="UPDATE",
        expected_intents=["U_STOP_HIT"],
    ),
    DebugCase(
        case_id="mark_filled",
        text="entry filled",
        reply_to_message_id=781,
        expected_message_type="UPDATE",
        expected_intents=["U_MARK_FILLED"],
    ),
    DebugCase(
        case_id="result_report_r",
        text="Final result BTCUSDT - 1.2R ETHUSDT - -0.3R",
        expected_intents=["U_REPORT_FINAL_RESULT"],
    ),
    DebugCase(
        case_id="ambiguous",
        text="maybe close maybe move later",
        expected_message_type="UNCLASSIFIED",
    ),
]


def default_db_path() -> Path:
    return Path(__file__).resolve().parents[4] / "parser_test" / "db" / "parser_test.sqlite3"


def fetch_trader_a_messages_from_db(
    *,
    db_path: str | Path,
    limit: int = 50,
    contains: str | None = None,
    trader_a_only: bool = True,
) -> list[DebugRawMessage]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB not found: {path}")

    where: list[str] = ["rm.raw_text IS NOT NULL", "TRIM(rm.raw_text) <> ''"]
    params: list[object] = []
    if trader_a_only:
        where.append(
            """(
                LOWER(COALESCE(pr.resolved_trader_id, '')) IN ('ta', 'a', 'trader_a')
                OR LOWER(COALESCE(pr.declared_trader_tag, '')) IN ('a', 'trader#a', 'trader_a')
                OR LOWER(rm.raw_text) LIKE '%[trader#a]%'
                OR LOWER(rm.raw_text) LIKE '%trader#a%'
            )"""
        )
    if contains:
        where.append("LOWER(rm.raw_text) LIKE ?")
        params.append(f"%{contains.lower()}%")

    sql = f"""
        SELECT
          rm.raw_message_id,
          rm.telegram_message_id,
          rm.source_chat_id,
          rm.reply_to_message_id,
          rm.raw_text,
          pr.resolved_trader_id,
          pr.declared_trader_tag
        FROM raw_messages rm
        LEFT JOIN parse_results pr ON pr.raw_message_id = rm.raw_message_id
        WHERE {' AND '.join(where)}
        ORDER BY rm.message_ts DESC, rm.raw_message_id DESC
        LIMIT ?
    """
    params.append(max(1, limit))

    with sqlite3.connect(str(path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        DebugRawMessage(
            raw_message_id=int(row[0]),
            telegram_message_id=int(row[1]),
            source_chat_id=str(row[2]),
            reply_to_message_id=int(row[3]) if row[3] is not None else None,
            raw_text=str(row[4] or ""),
            resolved_trader_id=row[5],
            declared_trader_tag=row[6],
        )
        for row in rows
    ]


def generate_report_from_db(
    *,
    db_path: str | Path,
    limit: int = 50,
    contains: str | None = None,
    trader_a_only: bool = True,
) -> list[dict[str, Any]]:
    parser = TraderAProfileParser()
    messages = fetch_trader_a_messages_from_db(
        db_path=db_path,
        limit=limit,
        contains=contains,
        trader_a_only=trader_a_only,
    )
    rows: list[dict[str, Any]] = []
    for message in messages:
        context = ParserContext(
            trader_code="trader_a",
            message_id=message.telegram_message_id,
            reply_to_message_id=message.reply_to_message_id,
            channel_id=message.source_chat_id,
            raw_text=message.raw_text,
            extracted_links=extract_telegram_links(message.raw_text),
            hashtags=extract_hashtags(message.raw_text),
        )
        result = parser.parse_message(message.raw_text, context)
        rows.append(_row_from_db(message=message, result=result))
    return rows


def generate_report(
    cases: list[DebugCase] | None = None,
) -> list[dict[str, Any]]:
    # Backward-compatible synthetic fallback. DB report is primary.
    parser = TraderAProfileParser()
    rows: list[dict[str, Any]] = []
    for idx, case in enumerate(cases or DEFAULT_CASES, start=1):
        context = ParserContext(
            trader_code="trader_a",
            message_id=1000 + idx,
            reply_to_message_id=case.reply_to_message_id,
            channel_id="-100123",
            raw_text=case.text,
            extracted_links=case.extracted_links or [],
            hashtags=[],
        )
        result = parser.parse_message(case.text, context)
        rows.append(_row(case=case, result=result))
    return rows


def _row(*, case: DebugCase, result: TraderParseResult) -> dict[str, Any]:
    expected_intents = case.expected_intents or []
    missing_expected_intents = [intent for intent in expected_intents if intent not in result.intents]
    return {
        "case_id": case.case_id,
        "input_text": case.text,
        "expected_message_type": case.expected_message_type,
        "actual_message_type": result.message_type,
        "expected_intents": expected_intents,
        "actual_intents": result.intents,
        "missing_expected_intents": missing_expected_intents,
        "target_refs": result.target_refs,
        "entities": result.entities,
        "reported_results": result.reported_results,
        "warnings": result.warnings,
        "confidence": result.confidence,
    }


def _row_from_db(*, message: DebugRawMessage, result: TraderParseResult) -> dict[str, Any]:
    return {
        "db_row_id": message.raw_message_id,
        "telegram_message_id": message.telegram_message_id,
        "source_chat_id": message.source_chat_id,
        "trader_marker": message.declared_trader_tag,
        "resolved_trader_id": message.resolved_trader_id,
        "raw_text": message.raw_text,
        "message_type": result.message_type,
        "target_refs": result.target_refs,
        "intents": result.intents,
        "entities": result.entities,
        "reported_results": result.reported_results,
        "warnings": result.warnings,
        "confidence": result.confidence,
    }


def _as_text(rows: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for row in rows:
        if "case_id" in row:
            blocks.append(
                "\n".join(
                    [
                        f"[{row['case_id']}]",
                        f"input: {row['input_text']}",
                        f"message_type: expected={row['expected_message_type']} actual={row['actual_message_type']}",
                        f"intents: expected={row['expected_intents']} actual={row['actual_intents']}",
                        f"missing_expected_intents: {row['missing_expected_intents']}",
                        f"target_refs: {row['target_refs']}",
                        f"entities: {row['entities']}",
                        f"reported_results: {row['reported_results']}",
                        f"warnings: {row['warnings']}",
                        f"confidence: {row['confidence']}",
                    ]
                )
            )
            continue
        blocks.append(
            "\n".join(
                [
                    f"[raw_message_id={row['db_row_id']} telegram_message_id={row['telegram_message_id']}]",
                    f"trader: resolved={row['resolved_trader_id']} declared={row['trader_marker']}",
                    f"raw_text: {row['raw_text']}",
                    f"message_type: {row['message_type']}",
                    f"target_refs: {row['target_refs']}",
                    f"intents: {row['intents']}",
                    f"entities: {row['entities']}",
                    f"reported_results: {row['reported_results']}",
                    f"warnings: {row['warnings']}",
                    f"confidence: {row['confidence']}",
                ]
            )
        )
    return "\n\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug report for Trader A parser.")
    parser.add_argument("--db-path", default=str(default_db_path()), help="Path to parser_test sqlite DB.")
    parser.add_argument("--limit", type=int, default=50, help="Max DB rows to inspect.")
    parser.add_argument("--contains", default=None, help="Optional substring filter on raw_text.")
    parser.add_argument("--trader-a-only", action="store_true", default=True, help="Filter only likely Trader A rows.")
    parser.add_argument("--no-trader-a-only", action="store_false", dest="trader_a_only")
    parser.add_argument(
        "--fallback-synthetic",
        action="store_true",
        default=False,
        help="If DB query returns no rows, use synthetic backup cases.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", default=None, help="Optional output file path.")
    args = parser.parse_args()

    rows = generate_report_from_db(
        db_path=args.db_path,
        limit=args.limit,
        contains=args.contains,
        trader_a_only=args.trader_a_only,
    )
    if not rows and args.fallback_synthetic:
        rows = generate_report()
    output = _as_text(rows) if args.format == "text" else json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    else:
        try:
            print(output)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(output.encode("utf-8", errors="replace"))
            sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    main()
