from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class ParserRunRecord:
    run_id: int
    started_at: str
    completed_at: str | None
    db_scope: str | None
    trader_filter: str | None
    parser_system: str
    parser_version: str | None
    force_reparse: bool
    notes: str | None


class ParserRunStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_run(
        self,
        *,
        parser_system: str = "parser_v2",
        trader_filter: str | None = None,
        db_scope: str | None = None,
        parser_version: str | None = None,
        force_reparse: bool = False,
        notes: str | None = None,
    ) -> int:
        started_at = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO parser_runs
                (started_at, db_scope, trader_filter, parser_system,
                 parser_version, force_reparse, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (started_at, db_scope, trader_filter, parser_system,
             parser_version, int(force_reparse), notes),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def complete_run(self, run_id: int) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE parser_runs SET completed_at = ? WHERE run_id = ?",
            (completed_at, run_id),
        )
        self._conn.commit()

    def get_latest_run(
        self,
        *,
        trader_filter: str | None = None,
        db_scope: str | None = None,
    ) -> ParserRunRecord | None:
        query = (
            "SELECT run_id, started_at, completed_at, db_scope, trader_filter, "
            "parser_system, parser_version, force_reparse, notes "
            "FROM parser_runs WHERE completed_at IS NOT NULL"
        )
        params: list[object] = []
        if trader_filter is not None:
            query += " AND trader_filter = ?"
            params.append(trader_filter)
        if db_scope is not None:
            query += " AND db_scope = ?"
            params.append(db_scope)
        query += " ORDER BY run_id DESC LIMIT 1"
        row = self._conn.execute(query, params).fetchone()
        if row is None:
            return None
        return ParserRunRecord(
            run_id=row[0],
            started_at=row[1],
            completed_at=row[2],
            db_scope=row[3],
            trader_filter=row[4],
            parser_system=row[5],
            parser_version=row[6],
            force_reparse=bool(row[7]),
            notes=row[8],
        )


__all__ = ["ParserRunRecord", "ParserRunStore"]
