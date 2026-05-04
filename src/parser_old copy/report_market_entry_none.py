"""Report NEW_SIGNAL market entries with missing primary entry price."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any


def _safe_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_report(*, db_path: str | Path, limit: int | None = None, trader: str | None = None) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB not found: {path}")

    sql = """
        SELECT
            pr.raw_message_id,
            pr.resolved_trader_id,
            pr.symbol,
            pr.direction,
            pr.message_type,
            pr.parse_result_normalized_json,
            rm.source_chat_id,
            rm.telegram_message_id,
            rm.reply_to_message_id,
            rm.message_ts,
            rm.raw_text
        FROM parse_results pr
        LEFT JOIN raw_messages rm ON rm.raw_message_id = pr.raw_message_id
        WHERE pr.message_type = 'NEW_SIGNAL'
    """
    params: list[object] = []
    if trader:
        sql += " AND LOWER(COALESCE(pr.resolved_trader_id, '')) = LOWER(?)"
        params.append(trader)
    sql += " ORDER BY rm.message_ts ASC, pr.raw_message_id ASC"
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    rows: list[dict[str, Any]] = []
    with sqlite3.connect(str(path)) as conn:
        for row in conn.execute(sql, params):
            payload = _safe_json(row[5])
            if payload.get("message_type") != "NEW_SIGNAL":
                continue
            if payload.get("entry_mode") != "MARKET":
                continue
            if payload.get("entry_main") is not None:
                continue
            rows.append(
                {
                    "raw_message_id": row[0],
                    "resolved_trader_id": row[1],
                    "symbol": row[2],
                    "direction": row[3],
                    "source_chat_id": row[6],
                    "telegram_message_id": row[7],
                    "reply_to_message_id": row[8],
                    "message_ts": row[9],
                    "entry_mode": payload.get("entry_mode"),
                    "entry_main": payload.get("entry_main"),
                    "average_entry": payload.get("average_entry"),
                    "entry_plan_type": payload.get("entry_plan_type"),
                    "entry_structure": payload.get("entry_structure"),
                    "has_averaging_plan": payload.get("has_averaging_plan"),
                    "raw_text": row[10],
                    "entry_plan_entries": payload.get("entities", {}).get("entry_plan_entries", []),
                }
            )

    by_trader: dict[str, int] = {}
    for item in rows:
        trader_id = str(item.get("resolved_trader_id") or "UNRESOLVED")
        by_trader[trader_id] = by_trader.get(trader_id, 0) + 1

    return {
        "total": len(rows),
        "by_trader": dict(sorted(by_trader.items())),
        "samples": rows[:20],
        "rows": rows,
    }


def _print_report(report: dict[str, Any]) -> None:
    print("NEW_SIGNAL market entries with missing entry_main")
    print(f"total: {report['total']}")
    print(f"by_trader: {report['by_trader']}")
    print("samples:")
    for row in report["samples"]:
        print(
            f"- raw_message_id={row['raw_message_id']} trader={row['resolved_trader_id']} "
            f"symbol={row['symbol']} entry_mode={row['entry_mode']} average_entry={row['average_entry']} "
            f"entry_plan_type={row['entry_plan_type']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(Path(__file__).resolve().parents[2] / "parser_test" / "db" / "parser_test.sqlite3"))
    parser.add_argument("--output", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--trader", default=None)
    args = parser.parse_args()

    report = build_report(db_path=args.db_path, limit=args.limit, trader=args.trader)
    _print_report(report)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON report saved to: {output_path}")


if __name__ == "__main__":
    main()
