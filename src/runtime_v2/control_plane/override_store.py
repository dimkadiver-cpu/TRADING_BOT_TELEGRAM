from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _override_key(scope_type: str) -> str:
    return "symbol_blacklist.global" if scope_type == "GLOBAL" else "symbol_blacklist.trader"


class OverrideStore:
    """Persist symbol blacklist overrides in ops_config_overrides."""

    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def _fetch_row(
        self,
        conn: sqlite3.Connection,
        scope_type: str,
        scope_value: str | None,
    ) -> tuple[int, str] | None:
        if scope_value is None:
            return conn.execute(
                "SELECT id, value_json FROM ops_config_overrides "
                "WHERE override_key=? AND scope_type=? AND scope_value IS NULL AND active=1",
                (_override_key(scope_type), scope_type),
            ).fetchone()
        return conn.execute(
            "SELECT id, value_json FROM ops_config_overrides "
            "WHERE override_key=? AND scope_type=? AND scope_value=? AND active=1",
            (_override_key(scope_type), scope_type, scope_value),
        ).fetchone()

    def get_blacklist(self, scope_type: str, scope_value: str | None) -> list[str]:
        conn = sqlite3.connect(self._db)
        try:
            row = self._fetch_row(conn, scope_type, scope_value)
        finally:
            conn.close()
        return self._deserialize(row[1] if row is not None else None)

    def _deserialize(self, value_json: str | None) -> list[str]:
        if value_json is None:
            return []
        try:
            return list(json.loads(value_json or "[]"))
        except Exception:
            return []

    def _replace_symbols(
        self,
        *,
        scope_type: str,
        scope_value: str | None,
        created_by: str,
        transform,
    ) -> list[str]:
        conn = sqlite3.connect(self._db, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_row(conn, scope_type, scope_value)
            current = self._deserialize(row[1] if row is not None else None)
            updated = transform(current)
            if updated == current:
                conn.execute("COMMIT")
                return current

            now = _now()
            value_json = json.dumps(updated)
            if row is not None:
                conn.execute(
                    "UPDATE ops_config_overrides SET value_json=?, updated_at=? WHERE id=?",
                    (value_json, now, row[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO ops_config_overrides "
                    "(override_key, scope_type, scope_value, value_json, created_by, "
                    "active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        _override_key(scope_type),
                        scope_type,
                        scope_value,
                        value_json,
                        created_by,
                        now,
                        now,
                    ),
                )
            conn.execute("COMMIT")
            return updated
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def add_symbol(
        self,
        *,
        scope_type: str,
        scope_value: str | None,
        symbol: str,
        created_by: str,
    ) -> list[str]:
        normalized_symbol = symbol.upper()
        return self._replace_symbols(
            scope_type=scope_type,
            scope_value=scope_value,
            created_by=created_by,
            transform=lambda current: (
                current if normalized_symbol in current else [*current, normalized_symbol]
            ),
        )

    def remove_symbol(
        self,
        *,
        scope_type: str,
        scope_value: str | None,
        symbol: str,
    ) -> list[str]:
        normalized_symbol = symbol.upper()
        return self._replace_symbols(
            scope_type=scope_type,
            scope_value=scope_value,
            created_by="system",
            transform=lambda current: [
                value for value in current if value != normalized_symbol
            ],
        )


__all__ = ["OverrideStore"]
