from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(slots=True)
class ParserResultV2Record:
    run_id: int
    raw_message_id: int
    trader_id: str | None
    parser_profile: str | None
    primary_class: str | None
    parse_status: str | None
    primary_intent: str | None
    confidence: float | None
    canonical_json: str | None
    warnings_json: str | None
    diagnostics_json: str | None
    error_status: str
    error_message: str | None
    created_at: str


class ParserResultV2Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert_result(self, record: ParserResultV2Record) -> None:
        self._conn.execute(
            """
            INSERT INTO parser_results_v2 (
                run_id, raw_message_id, trader_id, parser_profile,
                primary_class, parse_status, primary_intent, confidence,
                canonical_json, warnings_json, diagnostics_json,
                error_status, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, raw_message_id) DO UPDATE SET
                trader_id        = excluded.trader_id,
                parser_profile   = excluded.parser_profile,
                primary_class    = excluded.primary_class,
                parse_status     = excluded.parse_status,
                primary_intent   = excluded.primary_intent,
                confidence       = excluded.confidence,
                canonical_json   = excluded.canonical_json,
                warnings_json    = excluded.warnings_json,
                diagnostics_json = excluded.diagnostics_json,
                error_status     = excluded.error_status,
                error_message    = excluded.error_message,
                created_at       = excluded.created_at
            """,
            (
                record.run_id, record.raw_message_id, record.trader_id, record.parser_profile,
                record.primary_class, record.parse_status, record.primary_intent, record.confidence,
                record.canonical_json, record.warnings_json, record.diagnostics_json,
                record.error_status, record.error_message, record.created_at,
            ),
        )
        self._conn.commit()

    def fetch_by_run(
        self,
        run_id: int,
        trader: str | None = None,
    ) -> list[ParserResultV2Record]:
        query = _SELECT + " WHERE run_id = ?"
        params: list[object] = [run_id]
        if trader is not None:
            query += " AND trader_id = ?"
            params.append(trader)
        return [_row(r) for r in self._conn.execute(query, params).fetchall()]

    def fetch_latest_run_results(
        self,
        trader: str | None = None,
    ) -> list[ParserResultV2Record]:
        if trader is not None:
            row = self._conn.execute(
                "SELECT MAX(run_id) FROM parser_results_v2 WHERE trader_id = ?", (trader,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT MAX(run_id) FROM parser_results_v2").fetchone()
        if row is None or row[0] is None:
            return []
        return self.fetch_by_run(row[0], trader=trader)


_SELECT = (
    "SELECT run_id, raw_message_id, trader_id, parser_profile, "
    "primary_class, parse_status, primary_intent, confidence, "
    "canonical_json, warnings_json, diagnostics_json, "
    "error_status, error_message, created_at "
    "FROM parser_results_v2"
)


def _row(r: tuple) -> ParserResultV2Record:
    return ParserResultV2Record(
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
        created_at=r[13],
    )


__all__ = ["ParserResultV2Record", "ParserResultV2Store"]
