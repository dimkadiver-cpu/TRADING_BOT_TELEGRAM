"""Esporta parser_results_v2 in CSV per scope."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from parser_test.reporting.flatteners_v2 import ReportRow, flatten_for_scope
from parser_test.reporting.report_schema_v2 import SCOPE_COLUMNS

_SCOPE_FILTERS: dict[str, str] = {
    "ALL":             "r.error_status = 'OK'",
    "NEW_SIGNAL":      "r.error_status = 'OK' AND r.primary_class = 'SIGNAL' AND r.parse_status = 'PARSED'",
    "SETUP_INCOMPLETE":"r.error_status = 'OK' AND r.primary_class = 'SIGNAL' AND r.parse_status = 'PARTIAL'",
    "UPDATE":          "r.error_status = 'OK' AND r.primary_class = 'UPDATE'",
    "REPORT":          "r.error_status = 'OK' AND r.primary_class = 'REPORT'",
    "INFO_ONLY":       "r.error_status = 'OK' AND r.primary_class = 'INFO'",
    "UNCLASSIFIED":    "r.error_status = 'OK' AND r.parse_status = 'UNCLASSIFIED'",
    "ERRORS":          "(r.error_status != 'OK' OR r.parse_status = 'ERROR')",
}

_SCOPE_FILENAMES: dict[str, str] = {
    "ALL":             "all_messages",
    "NEW_SIGNAL":      "new_signal",
    "SETUP_INCOMPLETE":"setup_incomplete",
    "UPDATE":          "update",
    "REPORT":          "report",
    "INFO_ONLY":       "info_only",
    "UNCLASSIFIED":    "unclassified",
    "ERRORS":          "errors",
}

_SELECT_JOIN = """
    SELECT
        r.run_id, r.raw_message_id, r.trader_id, r.parser_profile,
        r.primary_class, r.parse_status, r.primary_intent, r.confidence,
        r.canonical_json, r.warnings_json, r.diagnostics_json,
        r.error_status, r.error_message,
        m.telegram_message_id, m.source_chat_id, m.source_topic_id,
        m.reply_to_message_id, m.message_ts, m.raw_text
    FROM parser_results_v2 r
    JOIN raw_messages m ON r.raw_message_id = m.raw_message_id
    WHERE r.run_id = ? AND {filter}{trader_filter}
    ORDER BY m.message_ts ASC
"""


def export_all(
    conn: sqlite3.Connection,
    run_id: int,
    trader: str | None,
    reports_dir: Path,
) -> list[Path]:
    if trader is None:
        traders = _list_run_traders(conn, run_id)
        if traders:
            generated: list[Path] = []
            for trader_id in traders:
                generated.extend(_export_for_trader(conn, run_id, trader_id, reports_dir))
            return generated

    return _export_for_trader(conn, run_id, trader, reports_dir)


def _list_run_traders(conn: sqlite3.Connection, run_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT trader_id
        FROM parser_results_v2
        WHERE run_id = ? AND trader_id IS NOT NULL AND TRIM(trader_id) != ''
        ORDER BY trader_id ASC
        """,
        (run_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _export_for_trader(
    conn: sqlite3.Connection,
    run_id: int,
    trader: str | None,
    reports_dir: Path,
) -> list[Path]:
    run_dir = reports_dir / f"run_{run_id}"
    trader_name = trader or "all_traders"
    csv_dir = run_dir / f"{trader_name}_message_types_csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    trader_filter = " AND r.trader_id = ?" if trader is not None else ""
    generated: list[Path] = []
    for scope, where_filter in _SCOPE_FILTERS.items():
        query = _SELECT_JOIN.format(filter=where_filter, trader_filter=trader_filter)
        params: list[Any] = [run_id]
        if trader is not None:
            params.append(trader)
        rows = [_build_report_row(r) for r in conn.execute(query, params).fetchall()]
        filename = f"{trader_name}_{_SCOPE_FILENAMES[scope]}.csv"
        out_path = csv_dir / filename
        _write_csv(out_path, scope, rows)
        generated.append(out_path)
        print(f"  {filename}: {len(rows)} righe")
    return generated


def _build_report_row(r: tuple) -> ReportRow:
    return ReportRow(
        run_id=r[0],
        raw_message_id=r[1],
        trader_id=r[2],
        parser_profile=r[3],
        primary_class=r[4],
        parse_status=r[5],
        primary_intent=r[6],
        confidence=r[7],
        canonical_json=r[8],
        warnings_json=r[9],
        diagnostics_json=r[10],
        error_status=r[11],
        error_message=r[12],
        telegram_message_id=r[13],
        source_chat_id=r[14],
        source_topic_id=r[15],
        reply_to_message_id=r[16],
        message_ts=r[17],
        raw_text=r[18],
    )


def _write_csv(path: Path, scope: str, rows: list[ReportRow]) -> None:
    columns = SCOPE_COLUMNS[scope]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(flatten_for_scope(scope, row))


__all__ = ["export_all"]
