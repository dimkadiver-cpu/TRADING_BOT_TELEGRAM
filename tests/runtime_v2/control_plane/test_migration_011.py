from __future__ import annotations

import sqlite3
from pathlib import Path


def test_migration_011_adds_outbox_aggregation_columns(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    outbox_columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_notification_outbox)")}
    conn.close()
    assert {"send_after", "aggregation_group", "source_message_id"} <= outbox_columns
