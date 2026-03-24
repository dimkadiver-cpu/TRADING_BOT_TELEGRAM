"""Export parser_test/reports CSVs from parser_test DB parse_results.

This script refreshes the existing message-type CSV files under parser_test/reports.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "parser_test" / "reports"
DB_PATH = PROJECT_ROOT / "parser_test" / "db" / "parser_test.sqlite3"

CSV_COLUMNS = [
    "raw_message_id",
    "message_type",
    "parse_status",
    "resolved_trader_id",
    "source_chat_id",
    "telegram_message_id",
    "reply_to_message_id",
    "message_ts",
    "raw_text",
    "warning_text",
    "symbol",
    "direction",
    "entry_main",
    "average_entry",
    "stop_loss_price",
    "take_profit_prices",
    "entry_plan_type",
    "entry_structure",
    "has_averaging_plan",
    "intents",
    "actions",
    "target_refs",
    "reported_results",
    "entities",
    "validation_warnings",
]


def main() -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        for csv_path in sorted(REPORTS_DIR.glob("*_message_types_csv/*.csv")):
            if csv_path.name.startswith("unresolved_"):
                continue
            trader_id, scope = _parse_report_target(csv_path.name)
            rows = _fetch_rows(conn=conn, trader_id=trader_id, scope=scope)
            _write_csv(csv_path, rows)
            print(f"updated {csv_path.relative_to(PROJECT_ROOT)} ({len(rows)} rows)")


def _parse_report_target(filename: str) -> tuple[str, str]:
    name = filename.removesuffix(".csv")
    if not name.startswith("trader_"):
        raise ValueError(f"Unexpected report filename: {filename}")
    if name.endswith("_all_messages"):
        return name[: -len("_all_messages")], "ALL"
    if name.endswith("_new_signal"):
        return name[: -len("_new_signal")], "NEW_SIGNAL"
    if name.endswith("_update"):
        return name[: -len("_update")], "UPDATE"
    if name.endswith("_info_only"):
        return name[: -len("_info_only")], "INFO_ONLY"
    if name.endswith("_unclassified"):
        return name[: -len("_unclassified")], "UNCLASSIFIED"
    if name.endswith("_setup_incomplete"):
        return name[: -len("_setup_incomplete")], "SETUP_INCOMPLETE"
    raise ValueError(f"Unexpected report scope for {filename}")


def _fetch_rows(*, conn: sqlite3.Connection, trader_id: str, scope: str) -> list[dict[str, str]]:
    sql = """
    SELECT
      rm.raw_message_id,
      pr.message_type,
      pr.parse_status,
      pr.resolved_trader_id,
      rm.source_chat_id,
      rm.telegram_message_id,
      rm.reply_to_message_id,
      rm.message_ts,
      rm.raw_text,
      pr.warning_text,
      pr.parse_result_normalized_json
    FROM raw_messages rm
    JOIN parse_results pr ON pr.raw_message_id = rm.raw_message_id
    WHERE pr.resolved_trader_id = ?
    """
    params: list[object] = [trader_id]
    if scope != "ALL":
        sql += " AND pr.message_type = ?"
        params.append(scope)
    sql += " ORDER BY rm.message_ts ASC, rm.raw_message_id ASC"

    rows: list[dict[str, str]] = []
    for row in conn.execute(sql, params):
        normalized = _normalized_obj(row["parse_result_normalized_json"])
        rows.append(
            {
                "raw_message_id": str(row["raw_message_id"]),
                "message_type": str(row["message_type"] or ""),
                "parse_status": str(row["parse_status"] or ""),
                "resolved_trader_id": str(row["resolved_trader_id"] or ""),
                "source_chat_id": str(row["source_chat_id"] or ""),
                "telegram_message_id": str(row["telegram_message_id"] or ""),
                "reply_to_message_id": "" if row["reply_to_message_id"] is None else str(row["reply_to_message_id"]),
                "message_ts": str(row["message_ts"] or ""),
                "raw_text": str(row["raw_text"] or ""),
                "warning_text": str(row["warning_text"] or ""),
                "symbol": _scalar(normalized.get("symbol")),
                "direction": _scalar(normalized.get("direction")),
                "entry_main": _scalar(normalized.get("entry_main")),
                "average_entry": _scalar(normalized.get("average_entry")),
                "stop_loss_price": _scalar(normalized.get("stop_loss_price")),
                "take_profit_prices": _json_field(normalized.get("take_profit_prices", [])),
                "entry_plan_type": _scalar(normalized.get("entry_plan_type")),
                "entry_structure": _scalar(normalized.get("entry_structure")),
                "has_averaging_plan": _scalar(normalized.get("has_averaging_plan")),
                "intents": _json_field(normalized.get("intents", [])),
                "actions": _json_field(normalized.get("actions", [])),
                "target_refs": _json_field(normalized.get("target_refs", [])),
                "reported_results": _json_field(normalized.get("reported_results", [])),
                "entities": _json_field(normalized.get("entities", {})),
                "validation_warnings": _json_field(normalized.get("validation_warnings", [])),
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _normalized_obj(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _json_field(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    main()
