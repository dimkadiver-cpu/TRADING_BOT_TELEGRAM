# tests/runtime_v2/execution_gateway/test_event_sync.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


def _insert_sent_cmd(db_path, cmd_id, chain_id, cmd_type, client_order_id):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, "SENT", "{}",
         f"idem:{cmd_id}", client_order_id, now, now),
    )
    conn.commit()
    conn.close()


def test_entry_fill_writes_entry_filled_event(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 1001, 42, "PLACE_ENTRY", "tsb:42:1001:entry:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:1001:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:1001:entry:1", price=50050.0, qty=0.02)

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    events = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events"
    ).fetchall()
    conn.close()
    assert len(events) == 1
    assert events[0][0] == "ENTRY_FILLED"
    payload = json.loads(events[0][1])
    assert payload["fill_price"] == 50050.0
    assert payload["filled_qty"] == 0.02


def test_tp_fill_last_writes_is_final_true(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 2001, 42, "PLACE_TAKE_PROFIT", "tsb:42:2001:tp:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_TAKE_PROFIT",
        payload={}, client_order_id="tsb:42:2001:tp:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:2001:tp:1", price=51000.0, qty=0.02)

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()[0])
    conn.close()
    assert payload["is_final"] is True
    assert payload["tp_level"] == 1


def test_idempotency_no_duplicate_events(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 3001, 42, "PLACE_ENTRY", "tsb:42:3001:entry:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:3001:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:3001:entry:1", price=50000.0, qty=0.01)

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")
    worker.run_once()
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 1
