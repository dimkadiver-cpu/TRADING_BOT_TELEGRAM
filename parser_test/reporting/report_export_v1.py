"""Export CSV reports from the parsed_messages (parsed_message_v1) table."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.reporting.flatteners_v1 import build_report_row_v1
from parser_test.reporting.report_schema_v1 import REPORT_SCOPES_V1, schema_for_scope_v1

# SQL filter per scope -------------------------------------------------------
# Maps scope name → (primary_class filter, parse_status filter) or None for no filter.
# None value means "no restriction on that column".
_SCOPE_FILTER: dict[str, tuple[str | None, str | None]] = {
    "ALL": (None, None),
    "NEW_SIGNAL": ("SIGNAL", "PARSED"),
    "UPDATE": ("UPDATE", None),
    "REPORT": ("REPORT", None),
    "INFO_ONLY": ("INFO", None),
    "SETUP_INCOMPLETE": ("SIGNAL", "PARTIAL"),
    "UNCLASSIFIED": (None, "UNCLASSIFIED"),
}


@dataclass(frozen=True, slots=True)
class ExportedReportV1:
    trader_id: str
    scope: str
    path: Path
    row_count: int


def export_reports_csv_v1(
    *,
    db_path: str | Path,
    reports_dir: str | Path,
    trader: str | None = None,
) -> list[ExportedReportV1]:
    """Read from parsed_messages and write per-trader, per-scope CSV files."""
    db_resolved = _resolve_path(db_path)
    reports_resolved = _resolve_path(reports_dir)
    reports_resolved.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_resolved)) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "parsed_messages"):
            raise RuntimeError(
                "Table 'parsed_messages' not found. "
                "Run the parser with --parser-system=parsed_message first, "
                "or apply migration 022_parsed_messages.sql."
            )
        trader_ids = _resolve_trader_ids(conn=conn, trader_filter=trader)
        results: list[ExportedReportV1] = []
        for trader_id in trader_ids:
            for scope in REPORT_SCOPES_V1:
                rows = _fetch_rows(conn=conn, trader_id=trader_id, scope=scope)
                path = _report_path(reports_resolved, trader_id, scope)
                _write_csv(path, rows, scope=scope)
                results.append(
                    ExportedReportV1(
                        trader_id=trader_id,
                        scope=scope,
                        path=path,
                        row_count=len(rows),
                    )
                )
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_trader_ids(*, conn: sqlite3.Connection, trader_filter: str | None) -> list[str]:
    if trader_filter and trader_filter.strip().lower() not in {"", "all", "*", "any"}:
        return [trader_filter.strip()]

    rows = conn.execute(
        "SELECT DISTINCT trader_id FROM parsed_messages WHERE trader_id IS NOT NULL AND TRIM(trader_id) != '' ORDER BY trader_id"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _fetch_rows(
    *,
    conn: sqlite3.Connection,
    trader_id: str,
    scope: str,
) -> list[dict[str, str]]:
    class_filter, status_filter = _SCOPE_FILTER[scope]

    sql = """
    SELECT
        pm.raw_message_id,
        pm.parsed_json
    FROM parsed_messages pm
    WHERE pm.trader_id = ?
    """
    params: list[object] = [trader_id]

    if class_filter is not None:
        sql += " AND pm.primary_class = ?"
        params.append(class_filter)

    if status_filter is not None:
        # parse_status lives inside the JSON; also stored in validation_status column
        # We use a JSON extract approach for portability, falling back to a LIKE guard.
        # SQLite's json_extract is available from 3.38+; for older versions we filter in Python.
        sql += " AND pm.parsed_json LIKE ?"
        params.append(f'%"parse_status": "{status_filter}"%')

    sql += " ORDER BY pm.raw_message_id ASC"

    rows: list[dict[str, str]] = []
    for row in conn.execute(sql, params):
        parsed_message = _parse_json(row["parsed_json"])
        # Double-check parse_status in Python (LIKE above may have false positives)
        if status_filter is not None:
            actual_status = str(parsed_message.get("parse_status", ""))
            if actual_status != status_filter:
                continue
        rows.append(
            build_report_row_v1(
                raw_message_id=row["raw_message_id"],
                parsed_message=parsed_message,
                scope=scope,
            )
        )
    return rows


def _report_path(reports_dir: Path, trader_id: str, scope: str) -> Path:
    folder = reports_dir / f"{trader_id}_message_types_csv"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = _scope_to_filename(scope)
    return folder / f"{trader_id}_{suffix}.csv"


def _scope_to_filename(scope: str) -> str:
    mapping = {
        "ALL": "all_messages",
        "NEW_SIGNAL": "new_signal",
        "UPDATE": "update",
        "REPORT": "report",
        "INFO_ONLY": "info_only",
        "SETUP_INCOMPLETE": "setup_incomplete",
        "UNCLASSIFIED": "unclassified",
    }
    return mapping.get(scope, scope.lower())


def _write_csv(path: Path, rows: list[dict[str, str]], *, scope: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = schema_for_scope_v1(scope).columns
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row is not None


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
