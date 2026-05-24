# tests/runtime_v2/execution_gateway/test_event_sync.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

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


def test_run_reconciliation_processes_sent_commands(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 4001, 5, "PLACE_ENTRY", "tsb:5:4001:entry:1")

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id="tsb:5:4001:entry:1",
        exchange_order_id="ex-1",
        status="FILLED",
        filled_qty=0.01,
        average_price=50000.0,
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="main",
    )

    count = worker.run_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=4001"
    ).fetchone()
    events = conn.execute(
        "SELECT event_type FROM ops_exchange_events WHERE trade_chain_id=5"
    ).fetchall()
    conn.close()
    assert row[0] == "DONE"
    assert events == [("ENTRY_FILLED",)]


def test_run_reconciliation_skips_commands_without_client_order_id(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 4002, 6, "PLACE_ENTRY", None)

    adapter = MagicMock()
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="main",
    )

    count = worker.run_reconciliation()

    assert count == 0
    adapter.get_order_status.assert_not_called()


def test_cancelled_entry_emits_pending_entry_cancelled_confirmed(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 5001, 10, "PLACE_ENTRY", "tsb:10:5001:entry:1")

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id="tsb:10:5001:entry:1",
        exchange_order_id="ex-cancelled-1",
        status="CANCELLED",
        filled_qty=0.0,
        average_price=None,
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="main")

    count = worker.run_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5001"
    ).fetchone()
    events = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id=10"
    ).fetchall()
    conn.close()
    assert row[0] == "DONE"
    assert len(events) == 1
    assert events[0][0] == "PENDING_ENTRY_CANCELLED_CONFIRMED"
    payload = json.loads(events[0][1])
    assert payload["position_already_open"] is False


def test_cancelled_entry_partial_fill_sets_position_already_open(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 5002, 11, "PLACE_ENTRY", "tsb:11:5002:entry:1")

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id="tsb:11:5002:entry:1",
        exchange_order_id="ex-partial-cancel",
        status="CANCELLED",
        filled_qty=0.005,
        average_price=50000.0,
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="main")

    worker.run_reconciliation()

    conn = sqlite3.connect(ops_db)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=11"
    ).fetchone()[0])
    conn.close()
    assert payload["position_already_open"] is True


def test_cancelled_non_entry_marks_done_no_event(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 5003, 12, "PLACE_ENTRY_WITH_ATTACHED_TPSL", "tsb:12:5003:sl:1")

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id="tsb:12:5003:sl:1",
        exchange_order_id="ex-sl-cancel",
        status="CANCELLED",
        filled_qty=0.0,
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="main")

    count = worker.run_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5003"
    ).fetchone()[0]
    event_count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=12"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"
    assert event_count == 0


def _insert_open_chain(db_path, chain_id, symbol, side, open_qty):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "t1", "acc",
         symbol, side, "OPEN", "b_entry_stop_then_tp", "{}", open_qty, now, now),
    )
    conn.commit()
    conn.close()


def test_position_reconciliation_emits_close_full_filled(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 20, "BTC/USDT:USDT", "long", 0.01)

    adapter = FakeAdapter(positions={})  # position qty = None for unknown keys → returns None
    # Override so BTC/USDT:USDT:long returns 0.0
    adapter._positions["BTC/USDT:USDT:long"] = 0.0
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")

    count = worker.run_position_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    events = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id=20"
    ).fetchall()
    conn.close()
    assert len(events) == 1
    assert events[0][0] == "CLOSE_FULL_FILLED"
    payload = json.loads(events[0][1])
    assert payload["filled_qty"] == 0.01
    assert payload["source"] == "position_reconciliation"


def test_position_reconciliation_no_event_if_position_still_open(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 21, "ETH/USDT:USDT", "long", 0.5)

    adapter = FakeAdapter(positions={"ETH/USDT:USDT:long": 0.5})
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")

    count = worker.run_position_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    event_count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert event_count == 0


def test_position_reconciliation_idempotent(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 22, "SOL/USDT:USDT", "short", 10.0)
    adapter = FakeAdapter(positions={"SOL/USDT:USDT:short": 0.0})
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")

    worker.run_position_reconciliation()
    worker.run_position_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=22"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_run_reconciliation_continues_after_single_order_error(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 4003, 7, "PLACE_ENTRY", "tsb:7:4003:entry:1")
    _insert_sent_cmd(ops_db, 4004, 8, "PLACE_ENTRY", "tsb:8:4004:entry:1")

    adapter = MagicMock()
    adapter.get_order_status.side_effect = [
        RuntimeError("boom"),
        RawAdapterOrder(
            client_order_id="tsb:8:4004:entry:1",
            exchange_order_id="ex-2",
            status="FILLED",
            filled_qty=0.02,
            average_price=51000.0,
        ),
    ]
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="main",
    )

    count = worker.run_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT command_id, status FROM ops_execution_commands "
        "WHERE command_id IN (4003, 4004) ORDER BY command_id"
    ).fetchall()
    events = conn.execute(
        "SELECT event_type, trade_chain_id FROM ops_exchange_events "
        "ORDER BY trade_chain_id, event_type"
    ).fetchall()
    conn.close()
    assert rows == [(4003, "SENT"), (4004, "DONE")]
    assert events == [("ENTRY_FILLED", 8)]


def _insert_open_chain_with_tp(db_path, chain_id, symbol="BTC/USDT:USDT", side="LONG"):
    """Inserisce chain OPEN con un SET_POSITION_TPSL_PARTIAL DONE."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "t1", "acc",
         symbol, side, "OPEN", "ONE_SHOT", "{}", 0.01, 0.01, now, now),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (chain_id * 100, chain_id, "SET_POSITION_TPSL_PARTIAL", "DONE",
         '{"take_profit": 70000.0, "tp_size": 0.005, "tp_sequence": 1, "symbol": "BTC/USDT:USDT", "side": "LONG"}',
         f"idem_tp:{chain_id}", now, now),
    )
    conn.commit()
    conn.close()


def test_tp_filled_ws_and_polling_unified_key_no_duplicate(ops_db):
    """WS inserisce TP_FILLED con chiave level:N; poi run_tp_reconciliation()
    trova INSERT OR IGNORE → esattamente 1 riga."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp(ops_db, chain_id=30)

    # Simula: WS ha già inserito TP_FILLED con la nuova chiave unified
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (30, "TP_FILLED",
         '{"tp_level": 1, "is_final": false, "fill_price": 70000.0, "filled_qty": 0.005, "source": "watch_my_trades"}',
         "NEW", "TP_FILLED:30:level:1"),
    )
    conn.commit()
    conn.close()

    # Polling tenta di inserire lo stesso evento
    adapter = FakeAdapter(positions={"BTC/USDT:USDT:LONG": 0.005})
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter,
        repo=repo, execution_account_id="acc",
    )
    worker.run_tp_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=30 AND event_type='TP_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert count == 1, f"Expected 1 TP_FILLED event, got {count}"
