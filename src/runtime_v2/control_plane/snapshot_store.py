from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.control_plane.models import RuntimeSnapshot


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class SnapshotStore:
    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def save(
        self,
        *,
        control_mode: str,
        active_blocks: list[str],
        open_chain_count: int,
        pending_command_count: int,
        shutdown_reason: str | None = None,
    ) -> None:
        now = _utcnow().isoformat()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                """
                INSERT INTO ops_runtime_snapshot
                    (snapshot_at, control_mode, active_blocks_json, open_chain_count,
                     pending_command_count, shutdown_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    control_mode,
                    json.dumps(active_blocks),
                    open_chain_count,
                    pending_command_count,
                    shutdown_reason,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_latest(self) -> RuntimeSnapshot | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                """
                SELECT id, snapshot_at, control_mode, active_blocks_json,
                       open_chain_count, pending_command_count, shutdown_reason, created_at
                FROM ops_runtime_snapshot
                ORDER BY snapshot_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return RuntimeSnapshot(
            id=row[0],
            snapshot_at=_parse_dt(row[1]),
            control_mode=row[2],
            active_blocks_json=row[3],
            open_chain_count=row[4],
            pending_command_count=row[5],
            shutdown_reason=row[6],
            created_at=_parse_dt(row[7]),
        )

    def is_stale(self, snapshot_at: datetime, *, max_age_seconds: int) -> bool:
        reference = snapshot_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        age_seconds = (_utcnow() - reference.astimezone(timezone.utc)).total_seconds()
        return age_seconds > max_age_seconds


__all__ = ["SnapshotStore"]
