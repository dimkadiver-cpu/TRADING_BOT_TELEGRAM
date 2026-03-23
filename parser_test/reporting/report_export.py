from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config_loader import load_config
from src.parser.trader_profiles.registry import canonicalize_trader_code

from parser_test.reporting.flatteners import build_report_row
from parser_test.reporting.report_schema import schema_for_scope

REPORTS_DIR = PROJECT_ROOT / "parser_test" / "reports"
DB_PATH = PROJECT_ROOT / "parser_test" / "db" / "parser_test.sqlite3"
REPORT_SCOPES: list[str] = ["ALL", "NEW_SIGNAL", "UPDATE", "INFO_ONLY", "SETUP_INCOMPLETE", "UNCLASSIFIED"]


@dataclass(frozen=True, slots=True)
class ExportedReport:
    trader_id: str
    scope: str
    path: Path
    row_count: int


def export_reports_csv(
    *,
    db_path: str | Path,
    reports_dir: str | Path,
    trader: str | None = None,
    include_legacy_debug: bool = False,
    include_json_debug: bool = False,
) -> list[ExportedReport]:
    return export_reports_csv_v2(
        db_path=db_path,
        reports_dir=reports_dir,
        trader=trader,
        include_legacy_debug=include_legacy_debug,
        include_json_debug=include_json_debug,
    )


def export_reports_csv_v2(
    *,
    db_path: str | Path,
    reports_dir: str | Path,
    trader: str | None = None,
    include_legacy_debug: bool = False,
    include_json_debug: bool = False,
) -> list[ExportedReport]:
    db_path_resolved = _resolve_path(db_path)
    reports_dir_resolved = _resolve_path(reports_dir)
    reports_dir_resolved.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path_resolved)) as conn:
        conn.row_factory = sqlite3.Row
        trader_ids = _resolve_trader_ids(conn=conn, trader_filter=trader)
        results: list[ExportedReport] = []
        for trader_id in trader_ids:
            for scope in REPORT_SCOPES:
                report_path = _report_path(reports_dir_resolved, trader_id, scope)
                rows = _fetch_rows(
                    conn=conn,
                    trader_id=trader_id,
                    scope=scope,
                    include_legacy_debug=include_legacy_debug,
                    include_json_debug=include_json_debug,
                )
                _write_csv(
                    report_path,
                    rows,
                    scope=scope,
                    include_legacy_debug=include_legacy_debug,
                    include_json_debug=include_json_debug,
                )
                results.append(ExportedReport(trader_id=trader_id, scope=scope, path=report_path, row_count=len(rows)))
        unresolved_path = reports_dir_resolved / "unresolved_messages.csv"
        unresolved_rows = _fetch_unresolved_rows(conn=conn)
        _write_unresolved_csv(unresolved_path, unresolved_rows)
        results.append(
            ExportedReport(
                trader_id="UNRESOLVED",
                scope="UNRESOLVED_MESSAGES",
                path=unresolved_path,
                row_count=len(unresolved_rows),
            )
        )
        return results


def _resolve_trader_ids(*, conn: sqlite3.Connection, trader_filter: str | None) -> list[str]:
    if trader_filter and trader_filter.strip().lower() not in {"", "all", "trader_all", "*", "any"}:
        canonical = canonicalize_trader_code(trader_filter) or trader_filter.strip().lower()
        return [canonical]

    config = load_config(str(PROJECT_ROOT))
    configured = [str(trader_id) for trader_id in config.traders.keys()]
    db_traders = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT resolved_trader_id
            FROM parse_results
            WHERE resolved_trader_id IS NOT NULL AND TRIM(resolved_trader_id) != ''
            """
        )
    ]
    combined: list[str] = []
    seen: set[str] = set()
    for trader_id in configured + db_traders:
        canonical = canonicalize_trader_code(trader_id) or trader_id.strip().lower()
        if canonical in seen:
            continue
        seen.add(canonical)
        combined.append(canonical)
    return combined


def _report_path(reports_dir: Path, trader_id: str, scope: str) -> Path:
    folder = reports_dir / f"{trader_id}_message_types_csv"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "all_messages" if scope == "ALL" else scope.lower()
    return folder / f"{trader_id}_{suffix}.csv"


def _fetch_rows(
    *,
    conn: sqlite3.Connection,
    trader_id: str,
    scope: str,
    include_legacy_debug: bool,
    include_json_debug: bool,
) -> list[dict[str, str]]:
    sql = """
    SELECT
      rm.raw_message_id,
      pr.parse_status,
      rm.reply_to_message_id,
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
            build_report_row(
                raw_message_id=row["raw_message_id"],
                parse_status=row["parse_status"],
                reply_to_message_id=row["reply_to_message_id"],
                raw_text=row["raw_text"],
                warning_text=row["warning_text"],
                normalized=normalized,
                scope=scope,
                include_legacy_debug=include_legacy_debug,
                include_json_debug=include_json_debug,
            )
        )
    return rows


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    *,
    scope: str,
    include_legacy_debug: bool,
    include_json_debug: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = schema_for_scope(
        scope,
        include_legacy_debug=include_legacy_debug,
        include_json_debug=include_json_debug,
    ).columns
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _normalized_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _fetch_unresolved_rows(*, conn: sqlite3.Connection) -> list[dict[str, str]]:
    raw_columns = _table_columns(conn, "raw_messages")
    parse_columns = _table_columns(conn, "parse_results")
    source_chat_title_expr = "rm.source_chat_title" if "source_chat_title" in raw_columns else "''"
    eligibility_status_expr = (
        "pr.eligibility_status"
        if "eligibility_status" in parse_columns
        else "rm.acquisition_status"
        if "acquisition_status" in raw_columns
        else "''"
    )
    eligibility_reason_expr = (
        "pr.eligibility_reason"
        if "eligibility_reason" in parse_columns
        else "rm.acquisition_reason"
        if "acquisition_reason" in raw_columns
        else "''"
    )
    trader_resolution_method_expr = "pr.trader_resolution_method" if "trader_resolution_method" in parse_columns else "''"
    sql = f"""
    SELECT
      rm.raw_message_id,
      rm.source_chat_id,
      {source_chat_title_expr} AS source_chat_title,
      rm.telegram_message_id,
      COALESCE(pr.message_type, '') AS message_type,
      COALESCE({eligibility_status_expr}, '') AS eligibility_status,
      COALESCE({eligibility_reason_expr}, '') AS eligibility_reason,
      COALESCE({trader_resolution_method_expr}, '') AS trader_resolution_method,
      COALESCE(rm.raw_text, '') AS raw_text
    FROM raw_messages rm
    LEFT JOIN parse_results pr ON pr.raw_message_id = rm.raw_message_id
    WHERE pr.raw_message_id IS NULL
       OR pr.resolved_trader_id IS NULL
       OR TRIM(pr.resolved_trader_id) = ''
       OR UPPER(TRIM(pr.resolved_trader_id)) = 'UNRESOLVED'
    ORDER BY rm.message_ts ASC, rm.raw_message_id ASC
    """
    rows: list[dict[str, str]] = []
    for row in conn.execute(sql):
        rows.append(
            {
                "raw_message_id": _csv_scalar(row["raw_message_id"]),
                "source_chat_id": _csv_scalar(row["source_chat_id"]),
                "source_chat_title": _csv_scalar(row["source_chat_title"]),
                "telegram_message_id": _csv_scalar(row["telegram_message_id"]),
                "message_type": _csv_scalar(row["message_type"]),
                "eligibility_status": _csv_scalar(row["eligibility_status"]),
                "eligibility_reason": _csv_scalar(row["eligibility_reason"]),
                "trader_resolution_method": _csv_scalar(row["trader_resolution_method"]),
                "raw_text_preview": _preview_text(row["raw_text"]),
            }
        )
    return rows


def _write_unresolved_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "raw_message_id",
        "source_chat_id",
        "source_chat_title",
        "telegram_message_id",
        "message_type",
        "eligibility_status",
        "eligibility_reason",
        "trader_resolution_method",
        "raw_text_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _preview_text(value: str | None, *, limit: int = 200) -> str:
    text = _csv_scalar(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _csv_scalar(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}
