"""MIG1 migration runner.

Applies db/migrations/*.sql in order and writes to schema_migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from src.core.timeutils import utc_now_iso


def _read_migration_files(migrations_dir: Path) -> Iterable[tuple[int, Path]]:
    for path in sorted(migrations_dir.glob("*.sql")):
        stem = path.stem
        if not stem or not stem[0:3].isdigit():
            continue
        yield int(stem[0:3]), path


def apply_migrations(db_path: str, migrations_dir: str) -> int:
    migrations_path = Path(migrations_dir)
    migrations_path.mkdir(parents=True, exist_ok=True)

    applied = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )

        known_versions = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations")
        }

        for version, migration_file in _read_migration_files(migrations_path):
            if version in known_versions:
                continue
            sql_script = migration_file.read_text(encoding="utf-8")
            conn.executescript(sql_script)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (version, utc_now_iso()),
            )
            applied += 1
        conn.commit()

    return applied
