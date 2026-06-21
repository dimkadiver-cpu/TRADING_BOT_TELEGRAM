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


def test_cancelled_entry_payload_includes_cancelled_order_ids_and_sequence(ops_db):
    """_handle_cancelled_order deve includere cancelled_order_ids e sequence nel payload."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    client_order_id = "tsb:10:5010:entry:2"
    _insert_sent_cmd(ops_db, 5010, 10, "PLACE_ENTRY", client_order_id)

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id=client_order_id,
        exchange_order_id="bybit-ex-0001",
        status="CANCELLED",
        filled_qty=0.0,
        average_price=None,
        cancel_reason="user_cancel",
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter,
        repo=repo, execution_account_id="main",
    )
    worker.run_reconciliation()

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=10 AND event_type='PENDING_ENTRY_CANCELLED_CONFIRMED'"
    ).fetchone()
    conn.close()
    assert row is not None, "Evento non trovato"
    payload = json.loads(row[0])
    assert "cancelled_order_ids" in payload, "cancelled_order_ids assente dal payload"
    assert payload["cancelled_order_ids"] == [client_order_id]
    assert "sequence" in payload, "sequence assente dal payload"
    assert payload["sequence"] == 2  # seq estratto da tsb:10:5010:entry:2


def test_cancelled_entry_inherits_cancel_trigger_metadata(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    import datetime as dt

    client_order_id = "tsb:10:5011:entry:1"
    _insert_sent_cmd(ops_db, 5011, 10, "PLACE_ENTRY", client_order_id)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            6011,
            10,
            "CANCEL_PENDING_ENTRY",
            "DONE",
            json.dumps({
                "entry_client_order_id": client_order_id,
                "cancel_origin": "trader_update",
                "cancel_reason": "position_closed",
            }),
            "cancel:6011",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id=client_order_id,
        exchange_order_id="bybit-ex-0002",
        status="CANCELLED",
        filled_qty=0.0,
        average_price=None,
        cancel_reason="CancelByUser|EC_PerCancelRequest",
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="main",
    )

    worker.run_reconciliation()

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=10 AND event_type='PENDING_ENTRY_CANCELLED_CONFIRMED'"
    ).fetchone()
    conn.close()
    assert row is not None
    payload = json.loads(row[0])
    assert payload["cancel_origin"] == "trader_update"
    assert payload["cancel_reason"] == "position_closed"


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


def test_bulk_position_sync_writes_snapshot_rows(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    adapter = FakeAdapter()
    adapter.set_position_live(
        [
            RawPositionLive(
                symbol="BTCUSDT",
                side="LONG",
                qty=0.5,
                mark_price=70123.4,
                unrealized_pnl=12.3,
                cum_realized_pnl=4.5,
            ),
            RawPositionLive(
                symbol="ETHUSDT",
                side="SHORT",
                qty=1.25,
                mark_price=3512.6,
                unrealized_pnl=-3.2,
                cum_realized_pnl=8.9,
            ),
        ]
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    count = worker.run_bulk_position_sync()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        "cum_realized_pnl, source "
        "FROM ops_position_snapshots ORDER BY symbol, side"
    ).fetchall()
    conn.close()
    assert rows == [
        ("acc", "BTCUSDT", "LONG", 0.5, 70123.4, 12.3, 4.5, "bulk_position_sync"),
        ("acc", "ETHUSDT", "SHORT", 1.25, 3512.6, -3.2, 8.9, "bulk_position_sync"),
    ]


def test_bulk_position_sync_returns_zero_when_adapter_returns_none(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    adapter = MagicMock()
    adapter.fetch_all_positions.return_value = None
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    count = worker.run_bulk_position_sync()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    snapshot_count = conn.execute(
        "SELECT COUNT(*) FROM ops_position_snapshots"
    ).fetchone()[0]
    event_count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events"
    ).fetchone()[0]
    conn.close()
    assert snapshot_count == 0
    assert event_count == 0


def test_bulk_position_sync_emits_close_full_filled_for_zero_qty_position(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 20, "BTC/USDT:USDT", "long", 0.01)

    adapter = FakeAdapter(positions={})  # position qty = None for unknown keys → returns None
    # Override so BTC/USDT:USDT:long returns 0.0
    adapter.set_position_live(
        [RawPositionLive(symbol="BTC/USDT:USDT", side="long", qty=0.0)]
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")

    # First run: deferred — no reduce trade confirms the close, need consecutive confirmation.
    first = worker.run_bulk_position_sync()
    assert first == 0

    # Second run: confirmed → emits CLOSE_FULL_FILLED.
    count = worker.run_bulk_position_sync()

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
    assert payload["source"] == "bulk_position_sync"


def test_bulk_position_sync_no_event_if_position_still_open(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 21, "ETH/USDT:USDT", "long", 0.5)

    adapter = FakeAdapter()
    adapter.set_position_live(
        [RawPositionLive(symbol="ETH/USDT:USDT", side="long", qty=0.5)]
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")

    count = worker.run_bulk_position_sync()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    event_count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert event_count == 0


def test_bulk_position_sync_upserts_position_snapshot_when_details_available(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 24, "BTCUSDT", "LONG", 0.5)

    adapter = MagicMock()
    adapter.fetch_all_positions.return_value = [
        RawPositionLive(symbol="BTCUSDT", side="LONG", qty=0.5)
    ]
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    count = worker.run_bulk_position_sync()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        "cum_realized_pnl, source "
        "FROM ops_position_snapshots"
    ).fetchone()
    conn.close()
    assert row == (
        "acc",
        "BTCUSDT",
        "LONG",
        0.5,
        None,
        None,
        None,
        "bulk_position_sync",
    )


def test_bulk_position_sync_idempotent(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 22, "SOL/USDT:USDT", "short", 10.0)
    adapter = FakeAdapter()
    adapter.set_position_live(
        [RawPositionLive(symbol="SOL/USDT:USDT", side="short", qty=0.0)]
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")

    worker.run_bulk_position_sync()
    worker.run_bulk_position_sync()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=22"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_bulk_position_sync_second_run_reports_zero_new_items(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 23, "XRP/USDT:USDT", "long", 100.0)
    adapter = FakeAdapter()
    adapter.set_position_live(
        [RawPositionLive(symbol="XRP/USDT:USDT", side="long", qty=0.0)]
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    first = worker.run_bulk_position_sync()   # deferred (zero_count=1)
    second = worker.run_bulk_position_sync()  # emits (zero_count=2)
    third = worker.run_bulk_position_sync()   # idempotent (already CLOSED)

    assert first == 0
    assert second == 1
    assert third == 0


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


def test_raw_adapter_trade_model():
    from src.runtime_v2.execution_gateway.models import RawAdapterTrade, RawPositionDetails
    t = RawAdapterTrade(trade_id="t1", symbol="PHAUSDT", price=0.05754, amount=3871.5)
    assert t.reduce_only is True  # default
    pos = RawPositionDetails(symbol="PHAUSDT", side="SHORT", qty=3871.5, take_profit=0.05373)
    assert pos.stop_loss is None


# ── helpers for trade-based reconciliation tests ─────────────────────────────

def _insert_open_chain_with_tp_v2(
    db_path: str,
    chain_id: int,
    symbol: str = "PHAUSDT",
    side: str = "SHORT",
    tp_price: float = 0.05754,
    tp_size: float = 3871.5,
    tp_level: int = 1,
    open_qty: float = 7743.0,
    account_id: str = "acc",
) -> None:
    """Chain OPEN (raw symbol) + active SET_POSITION_TPSL_PARTIAL command."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "t1", account_id,
         symbol, side, "OPEN", "TWO_STEP", "{}", open_qty, open_qty, now, now),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (chain_id * 100, chain_id, "SET_POSITION_TPSL_PARTIAL", "DONE",
         json.dumps({
             "symbol": symbol, "side": side,
             "take_profit": tp_price, "tp_size": tp_size, "tp_sequence": tp_level,
         }),
         f"idem_tp:{chain_id}", now, now),
    )
    conn.commit()
    conn.close()


def _insert_open_chain_with_tp_command(
    db_path: str,
    chain_id: int,
    *,
    command_type: str,
    status: str = "DONE",
    payload: dict,
    symbol: str = "PHAUSDT",
    side: str = "SHORT",
    lifecycle_state: str = "OPEN",
    open_qty: float = 7743.0,
) -> None:
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
         symbol, side, lifecycle_state, "TWO_STEP", "{}", open_qty, open_qty, now, now),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            chain_id * 100,
            chain_id,
            command_type,
            status,
            json.dumps(payload),
            f"idem_tp:{chain_id}:{command_type}",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()




def test_trade_based_reconciliation_idempotent(ops_db):
    """Calling run_trade_based_reconciliation() twice → exactly 1 TP_FILLED event."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 51, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05754, 3871.5, "t-idem")
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")

    worker.run_trade_based_reconciliation()
    worker.run_trade_based_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=51 AND event_type='TP_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_trade_based_reconciliation_does_not_reuse_one_trade_across_chains(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 56, symbol="PHAUSDT", side="SHORT", tp_price=0.05754)
    _insert_open_chain_with_tp_v2(ops_db, 57, symbol="PHAUSDT", side="SHORT", tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05754, 3871.5, "t-shared")
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    count = worker.run_trade_based_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT trade_chain_id, payload_json FROM ops_exchange_events "
        "WHERE event_type='TP_FILLED' ORDER BY trade_chain_id"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] in (56, 57)
    payload = json.loads(rows[0][1])
    assert payload["exchange_trade_id"] == "t-shared"


def test_trade_based_reconciliation_deduplicates_with_ws_insertion(ops_db):
    """If WS already inserted TP_FILLED with same idempotency key, REST poll is no-op.

    The new trade-based reconciliation uses tp_level=None (position-level TPs, no orderLinkId),
    so the idempotency key is 'TP_FILLED:<chain_id>' (no :level: suffix).
    """
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 53, tp_price=0.05754)
    # Simulate WS already inserted TP_FILLED — use the same idempotency key the
    # trade-based reconciler will generate: TP_FILLED:<chain_id> (no level suffix).
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (53, "TP_FILLED",
         '{"tp_level":null,"is_final":false,"fill_price":0.05754,"source":"watch_my_trades"}',
         "NEW", "TP_FILLED:53"),
    )
    conn.commit()
    conn.close()

    adapter = FakeAdapter()
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05754, 3871.5, "t-ws-dup")
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    worker.run_trade_based_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=53"
    ).fetchone()[0]
    conn.close()
    assert count == 1  # still just the one WS-inserted event


def test_trade_based_reconciliation_noop_when_adapter_has_no_method(ops_db):
    """If adapter lacks fetch_recent_reduce_trades → returns 0, no crash."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 54)
    adapter = MagicMock(spec=[])  # spec=[] → hasattr always False
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")

    count = worker.run_trade_based_reconciliation()
    assert count == 0


# ── protective orders reconciliation tests ────────────────────────────────────

def test_protective_orders_reconciliation_emits_event_when_tp_removed(ops_db):
    """Exchange TP is 0.0 but bot set 0.05754 and no TP_FILLED exists → PROTECTIVE_ORDER_CANCELLED."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 60, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=7743.0,
        take_profit=0.0,  # cleared on exchange (manually cancelled)
        stop_loss=0.06908,
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc"
    )
    count = worker.run_protective_orders_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id=60"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "PROTECTIVE_ORDER_CANCELLED"
    p = json.loads(rows[0][1])
    assert p["reason"] == "tp_removed_externally"
    assert p["source"] == "protective_orders_reconciliation"


def test_protective_orders_reconciliation_skips_when_tp_fill_exists(ops_db):
    """If TP_FILLED already recorded → TP triggered normally → skip detection.

    The protective-orders reconciliation uses tp_fill_exists(chain_id, None), which checks
    the idempotency key 'TP_FILLED:<chain_id>' (no :level: suffix).
    """
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 61, tp_price=0.05754)
    # Existing TP_FILLED event → means it triggered, not cancelled.
    # Use idempotency key without level suffix to match tp_fill_exists(61, None).
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (61, "TP_FILLED", '{"tp_level":null}', "DONE", "TP_FILLED:61"),
    )
    conn.commit()
    conn.close()

    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=3871.5, take_profit=0.0
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    count = worker.run_protective_orders_reconciliation()

    assert count == 0


def test_protective_orders_reconciliation_skips_when_tp_still_active(ops_db):
    """Exchange still has TP at expected level → no event."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 62, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=7743.0,
        take_profit=0.05754,  # still there
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    count = worker.run_protective_orders_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    n = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert n == 0


def test_protective_orders_reconciliation_idempotent(ops_db):
    """Two calls → exactly 1 PROTECTIVE_ORDER_CANCELLED event."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 63, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=7743.0, take_profit=0.0
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    worker.run_protective_orders_reconciliation()
    worker.run_protective_orders_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='PROTECTIVE_ORDER_CANCELLED'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_protective_orders_reconciliation_second_run_reports_zero_new_items(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 65, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=7743.0, take_profit=0.0
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    first = worker.run_protective_orders_reconciliation()
    second = worker.run_protective_orders_reconciliation()

    assert first == 1
    assert second == 0


def test_protective_orders_reconciliation_noop_when_adapter_lacks_method(ops_db):
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 64)
    adapter = MagicMock(spec=[])  # no methods
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    count = worker.run_protective_orders_reconciliation()
    assert count == 0


def test_run_reconciliation_calls_wake_callback_on_fill(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 9001, 42, "PLACE_ENTRY", "tsb:42:9001:entry:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:9001:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:9001:entry:1", price=100.0, qty=1.0)

    wake_calls = []
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
        wake_callback=lambda: wake_calls.append(1),
    )
    worker.run_reconciliation()

    assert len(wake_calls) == 1


def test_run_reconciliation_no_wake_callback_when_no_fill(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # No sent commands → nothing to reconcile
    adapter = FakeAdapter()
    wake_calls = []
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
        wake_callback=lambda: wake_calls.append(1),
    )
    worker.run_reconciliation()

    assert len(wake_calls) == 0


def test_run_reconciliation_no_wake_callback_on_duplicate(ops_db):
    """Second run_reconciliation for same fill must NOT call wake_callback again (idempotency)."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 9002, 42, "PLACE_ENTRY", "tsb:42:9002:entry:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:9002:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:9002:entry:1", price=100.0, qty=1.0)

    wake_calls = []
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
        wake_callback=lambda: wake_calls.append(1),
    )
    worker.run_reconciliation()  # first: inserts event → wake
    worker.run_reconciliation()  # second: cmd is DONE, nothing to poll

    assert len(wake_calls) == 1


def test_run_reconciliation_marks_done_even_when_ws_already_inserted_event(ops_db):
    """If WS pre-inserted the ENTRY_FILLED event, REST reconciliation must still mark command DONE.

    Regression test for P2: mark_done was gated on insert_exchange_event() returning True,
    so when WS inserted first (INSERT OR IGNORE returned False), the command stayed SENT forever.
    """
    import datetime as dt
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 9100, 77, "PLACE_ENTRY", "tsb:77:9100:entry:1")

    # Simulate WS already inserted the fill event with the same idempotency key
    # that _save_fill_event would generate: ENTRY_FILLED:<chain_id>:<exchange_order_id>
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,?)",
        (77, "ENTRY_FILLED", '{"fill_price":100.0,"filled_qty":1.0,"command_id":9100}',
         "DONE", "ENTRY_FILLED:77:ex-ws-123", now),
    )
    conn.commit()
    conn.close()

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id="tsb:77:9100:entry:1",
        exchange_order_id="ex-ws-123",
        status="FILLED",
        filled_qty=1.0,
        average_price=100.0,
    )

    wake_calls = []
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="main",
        wake_callback=lambda: wake_calls.append(1),
    )
    count = worker.run_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=9100"
    ).fetchone()[0]
    event_count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=77"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"         # command marked done despite WS pre-insert
    assert event_count == 1         # no duplicate event inserted
    assert len(wake_calls) == 1


def test_bulk_position_sync_records_fill_price_and_fee_from_rest(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 99, "BTCUSDT", "LONG", 0.131)

    adapter = FakeAdapter()
    adapter.set_position_live([RawPositionLive(symbol="BTCUSDT", side="LONG", qty=0.0)])
    adapter.simulate_reduce_trade(
        "BTCUSDT",
        "LONG",
        price=73345.8,
        amount=0.131,
        trade_id="t1",
        fee=5.76,
    )

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    n = worker.run_bulk_position_sync()
    assert n == 1

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=99 AND event_type='CLOSE_FULL_FILLED'"
    ).fetchone()
    conn.close()

    assert row is not None
    payload = json.loads(row[0])
    assert payload["fill_price"] == pytest.approx(73345.8)
    assert payload["exec_fee"] == pytest.approx(5.76)
    assert payload["fee_rate"] == pytest.approx(5.76 / (73345.8 * 0.131))
    assert payload["source"] == "bulk_position_sync"


def test_bulk_position_sync_falls_back_to_none_when_no_reduce_trades(ops_db):
    from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 100, "ETHUSDT", "LONG", 1.0)

    adapter = FakeAdapter()
    adapter.set_position_live([RawPositionLive(symbol="ETHUSDT", side="LONG", qty=0.0)])

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    # No reduce trade: first run defers, second run emits with fill_price=None.
    first = worker.run_bulk_position_sync()
    assert first == 0
    n = worker.run_bulk_position_sync()
    assert n == 1

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=100 AND event_type='CLOSE_FULL_FILLED'"
    ).fetchone()
    conn.close()

    payload = json.loads(row[0])
    assert payload["fill_price"] is None


def test_save_fill_event_includes_exec_fee_and_closed_size_for_tp(ops_db):
    """_save_fill_event must include exec_fee and closed_size in payload for TP fills."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # Insert a SENT TP command — format is tsb:chain:cmd:role:seq
    coid = "tsb:10:77:tp:1"
    _insert_sent_cmd(ops_db, 77, 10, "PLACE_TP", coid)

    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_TP",
        payload={},
        client_order_id=coid,
        execution_account_id="main",
        connector="c",
    )
    adapter.simulate_fill(coid, price=68000.0, qty=0.002)

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="main",
    )
    # Call _save_fill_event directly so we can check what it persists
    raw = adapter.get_order_status(
        client_order_id=coid,
        execution_account_id="main",
    )
    result = worker._save_fill_event(coid, raw)
    assert result is True

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()
    conn.close()
    assert row is not None, "No TP_FILLED event inserted"
    payload = json.loads(row[0])
    assert payload["fill_price"] == 68000.0
    assert payload["filled_qty"] == 0.002
    assert "closed_size" in payload
    assert payload["closed_size"] == 0.002


# ── funding reconciliation tests ──────────────────────────────────────────────

def _insert_funding_chain(
    db_path: str,
    chain_id: int,
    *,
    symbol: str = "ONDOUSDT",
    side: str = "LONG",
    open_qty: float = 1000.0,
    lifecycle_state: str = "OPEN",
    account_id: str = "acc",
) -> None:
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "t1", account_id,
         symbol, side, lifecycle_state, "TWO_STEP", "{}", open_qty, open_qty, now, now),
    )
    conn.commit()
    conn.close()


def _make_funding_worker(ops_db, adapter):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    repo = GatewayCommandRepository(ops_db)
    return ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc"
    )


def test_funding_reconciliation_inserts_funding_settled_for_open_chain(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_funding_chain(ops_db, 70, symbol="ONDOUSDT", side="LONG")
    adapter = FakeAdapter()
    adapter.simulate_funding_execution(
        "ONDOUSDT", "Buy", 0.01865106, "fund-1", "2026-06-12T08:00:00+00:00"
    )
    worker = _make_funding_worker(ops_db, adapter)

    count = worker.run_funding_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT trade_chain_id, event_type, payload_json, idempotency_key "
        "FROM ops_exchange_events"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 70
    assert row[1] == "FUNDING_SETTLED"
    # idem key must match the WS key format (fill:{execId}) so WS/REST dedup is automatic
    assert row[3] == "fill:fund-1"
    payload = json.loads(row[2])
    assert payload["exec_fee"] == 0.01865106


def test_funding_reconciliation_dedup_with_ws_and_idempotent(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_funding_chain(ops_db, 71, symbol="REZUSDT", side="LONG")
    # Simulate WS already recorded this funding execution with key fill:{execId}
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (71, "FUNDING_SETTLED", '{"exec_fee":0.07593288,"source":"exchange_auto"}',
         "DONE", "fill:fund-ws-dup"),
    )
    conn.commit()
    conn.close()

    adapter = FakeAdapter()
    adapter.simulate_funding_execution(
        "REZUSDT", "Buy", 0.07593288, "fund-ws-dup", "2026-06-12T08:00:00+00:00"
    )
    worker = _make_funding_worker(ops_db, adapter)

    first = worker.run_funding_reconciliation()
    second = worker.run_funding_reconciliation()

    assert first == 0
    assert second == 0
    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=71"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_funding_reconciliation_skips_ambiguous_chains(ops_db, caplog):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_funding_chain(ops_db, 72, symbol="ONDOUSDT", side="LONG")
    _insert_funding_chain(ops_db, 73, symbol="ONDOUSDT", side="LONG")
    adapter = FakeAdapter()
    adapter.simulate_funding_execution(
        "ONDOUSDT", "Buy", 0.0186, "fund-ambiguous", "2026-06-12T08:00:00+00:00"
    )
    worker = _make_funding_worker(ops_db, adapter)

    with caplog.at_level("WARNING", logger="src.runtime_v2.execution_gateway.event_sync"):
        count = worker.run_funding_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    rows = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert rows == 0
    assert any("funding" in rec.message.lower() for rec in caplog.records)


def test_funding_reconciliation_attributes_sell_side_to_short_chain(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_funding_chain(ops_db, 74, symbol="XAUTUSDT", side="SHORT")
    adapter = FakeAdapter()
    # Bybit side for funding is the position side: Sell = SHORT
    adapter.simulate_funding_execution(
        "XAUTUSDT", "Sell", -0.012, "fund-short", "2026-06-12T16:00:00+00:00"
    )
    worker = _make_funding_worker(ops_db, adapter)

    count = worker.run_funding_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT trade_chain_id, payload_json FROM ops_exchange_events "
        "WHERE event_type='FUNDING_SETTLED'"
    ).fetchone()
    conn.close()
    assert row[0] == 74
    assert json.loads(row[1])["exec_fee"] == -0.012


def test_funding_reconciliation_skips_zero_fee(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_funding_chain(ops_db, 75, symbol="ONDOUSDT", side="LONG")
    adapter = FakeAdapter()
    adapter.simulate_funding_execution(
        "ONDOUSDT", "Buy", 0.0, "fund-zero", "2026-06-12T08:00:00+00:00"
    )
    worker = _make_funding_worker(ops_db, adapter)

    count = worker.run_funding_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    rows = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert rows == 0


def test_funding_reconciliation_noop_when_adapter_lacks_method(ops_db):
    _insert_funding_chain(ops_db, 76, symbol="ONDOUSDT", side="LONG")
    adapter = MagicMock(spec=[])  # hasattr always False
    worker = _make_funding_worker(ops_db, adapter)

    count = worker.run_funding_reconciliation()
    assert count == 0


# ── account-aware chain resolution tests ─────────────────────────────────────

def test_resolve_chain_for_fill_account_aware(ops_db):
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_funding_chain(ops_db, 80, symbol="ONDOUSDT", side="LONG", account_id="acc")
    _insert_funding_chain(ops_db, 81, symbol="ONDOUSDT", side="LONG", account_id="other")
    repo = GatewayCommandRepository(ops_db)

    # Without account filter: ambiguous → None (legacy behavior preserved)
    assert repo.resolve_chain_for_fill("ONDOUSDT", "LONG") is None
    # With account filter: each account resolves its own chain
    assert repo.resolve_chain_for_fill("ONDOUSDT", "LONG", account_id="acc") == 80
    assert repo.resolve_chain_for_fill("ONDOUSDT", "LONG", account_id="other") == 81


def test_funding_reconciliation_resolves_cross_account_chains(ops_db):
    """Two chains on the same symbol+side but different accounts must NOT be
    ambiguous for the per-account sync worker."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_funding_chain(ops_db, 82, symbol="ONDOUSDT", side="LONG", account_id="acc")
    _insert_funding_chain(ops_db, 83, symbol="ONDOUSDT", side="LONG", account_id="other")
    adapter = FakeAdapter()
    adapter.simulate_funding_execution(
        "ONDOUSDT", "Buy", 0.0186, "fund-cross-acc", "2026-06-12T08:00:00+00:00"
    )
    worker = _make_funding_worker(ops_db, adapter)  # execution_account_id="acc"

    count = worker.run_funding_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT trade_chain_id FROM ops_exchange_events WHERE event_type='FUNDING_SETTLED'"
    ).fetchone()
    conn.close()
    assert row[0] == 82  # attributed to the worker's own account chain


def test_bulk_position_sync_ignores_other_account_chains(ops_db):
    """Worker for account 'acc' must not synthesize a close for a chain owned by
    another account: its adapter would report qty=0 for a position that lives
    on a different subaccount."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_funding_chain(ops_db, 84, symbol="ETHUSDT", side="LONG", account_id="other", open_qty=2.0)
    # acc's adapter has no ETHUSDT position → qty 0.0 (would trigger synthetic close pre-fix)
    adapter = FakeAdapter(positions={"ETHUSDT:LONG": 0.0})
    worker = _make_funding_worker(ops_db, adapter)  # execution_account_id="acc"

    count = worker.run_bulk_position_sync()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='CLOSE_FULL_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert rows == 0


def test_trade_based_reconciliation_ignores_other_account_chains(ops_db):
    """Reduce trades from acc's adapter must not be attributed to chains owned
    by another account."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    _insert_open_chain_with_tp_v2(
        ops_db, 85, symbol="PHAUSDT", side="SHORT", tp_price=0.05754, account_id="other"
    )
    adapter = FakeAdapter()
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05754, 3871.5, "t-other-acc")
    worker = _make_funding_worker(ops_db, adapter)  # execution_account_id="acc"

    count = worker.run_trade_based_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert rows == 0
