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


def test_position_reconciliation_second_run_reports_zero_new_items(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, 23, "XRP/USDT:USDT", "long", 100.0)
    adapter = FakeAdapter(positions={"XRP/USDT:USDT:long": 0.0})
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )

    first = worker.run_position_reconciliation()
    second = worker.run_position_reconciliation()

    assert first == 1
    assert second == 0


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
        (chain_id, chain_id, chain_id, chain_id, "t1", "acc",
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


def test_get_tp_reconciliation_entries_expands_rebuild_partial_tps(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_command(
        ops_db,
        70,
        command_type="REBUILD_PARTIAL_TPS",
        status="DONE",
        symbol="BTCUSDT",
        side="LONG",
        lifecycle_state="PARTIALLY_CLOSED",
        payload={
            "tps": [
                {"sequence": 1, "price": 70100.0, "qty": 0.003},
                {"sequence": 2, "price": 70200.0, "qty": 0.004},
            ]
        },
    )

    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=MagicMock(),
        repo=GatewayCommandRepository(ops_db),
        execution_account_id="acc",
    )

    entries = worker._get_tp_reconciliation_entries()

    assert entries == [
        {
            "cmd_id": 7000,
            "chain_id": 70,
            "tp_level": 1,
            "tp_price": 70100.0,
            "tp_size": 0.003,
            "symbol": "BTCUSDT",
            "side": "LONG",
        },
        {
            "cmd_id": 7000,
            "chain_id": 70,
            "tp_level": 2,
            "tp_price": 70200.0,
            "tp_size": 0.004,
            "symbol": "BTCUSDT",
            "side": "LONG",
        },
    ]


def test_get_tp_reconciliation_entries_keeps_legacy_partial_tp_command(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_command(
        ops_db,
        71,
        command_type="SET_POSITION_TPSL_PARTIAL",
        payload={
            "take_profit": 0.05754,
            "tp_size": 3871.5,
            "tp_sequence": 1,
        },
    )

    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=MagicMock(),
        repo=GatewayCommandRepository(ops_db),
        execution_account_id="acc",
    )

    entries = worker._get_tp_reconciliation_entries()

    assert entries == [
        {
            "cmd_id": 7100,
            "chain_id": 71,
            "tp_level": 1,
            "tp_price": 0.05754,
            "tp_size": 3871.5,
            "symbol": "PHAUSDT",
            "side": "SHORT",
        }
    ]


def test_get_tp_reconciliation_entries_skips_malformed_rebuild_tp_items(ops_db):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_command(
        ops_db,
        72,
        command_type="REBUILD_PARTIAL_TPS",
        status="SENT",
        payload={
            "tps": [
                {"sequence": 1, "price": 70100.0, "qty": 0.003},
                {"sequence": "bad", "price": 70200.0, "qty": 0.004},
                {"sequence": 3, "price": None, "qty": 0.002},
                "not-a-dict",
                {"sequence": 4, "price": 70400.0},
            ]
        },
    )

    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=MagicMock(),
        repo=GatewayCommandRepository(ops_db),
        execution_account_id="acc",
    )

    entries = worker._get_tp_reconciliation_entries()

    assert entries == [
        {
            "cmd_id": 7200,
            "chain_id": 72,
            "tp_level": 1,
            "tp_price": 70100.0,
            "tp_size": 0.003,
            "symbol": "PHAUSDT",
            "side": "SHORT",
        }
    ]


def test_trade_based_reconciliation_inserts_tp_filled_on_matching_trade(ops_db):
    """run_trade_based_reconciliation() detects TP fill via fetch_recent_reduce_trades()."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 50, symbol="PHAUSDT", side="SHORT",
                                  tp_price=0.05754, tp_size=3871.5, open_qty=7743.0)
    adapter = FakeAdapter()
    # Simulate the intermediate TP fill
    adapter.simulate_reduce_trade(
        symbol="PHAUSDT", side="SHORT",
        price=0.05754, amount=3871.5, trade_id="exch-trade-001",
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc"
    )
    count = worker.run_trade_based_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json, idempotency_key FROM ops_exchange_events WHERE trade_chain_id=50"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "TP_FILLED"
    assert rows[0][2] == "TP_FILLED:50:level:1"
    p = json.loads(rows[0][1])
    assert p["fill_price"] == 0.05754
    assert p["filled_qty"] == 3871.5
    assert p["exchange_trade_id"] == "exch-trade-001"
    assert p["source"] == "trade_based_reconciliation"


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


def test_trade_based_reconciliation_marks_final_tp_when_last_level_fills(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_command(
        ops_db,
        55,
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "tps": [
                {"sequence": 1, "price": 0.05754, "qty": 1000.0},
                {"sequence": 2, "price": 0.05600, "qty": 1000.0},
            ]
        },
        symbol="PHAUSDT",
        side="SHORT",
    )
    adapter = FakeAdapter()
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05600, 1000.0, "t-final")
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
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=55 AND event_type='TP_FILLED'"
    ).fetchone()[0])
    conn.close()
    assert payload["tp_level"] == 2
    assert payload["is_final"] is True


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


def test_trade_based_reconciliation_skips_non_matching_price(ops_db):
    """Trade price >1% away from TP → no event inserted."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 52, tp_price=0.05754)
    adapter = FakeAdapter()
    # Price 2% away from TP
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.0560, 3871.5, "t-miss")
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")

    count = worker.run_trade_based_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    n = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert n == 0


def test_trade_based_reconciliation_deduplicates_with_ws_insertion(ops_db):
    """If WS already inserted TP_FILLED with same idempotency key, REST poll is no-op."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 53, tp_price=0.05754)
    # Simulate WS already inserted
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (53, "TP_FILLED",
         '{"tp_level":1,"is_final":false,"fill_price":0.05754,"source":"watch_my_trades"}',
         "NEW", "TP_FILLED:53:level:1"),
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
    """Exchange TP is 0.0 but bot set 0.05754 and no TP_FILLED exists → PROTECTIVE_ORDERS_MISSING."""
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
    assert rows[0][0] == "PROTECTIVE_ORDERS_MISSING"
    p = json.loads(rows[0][1])
    assert p["expected_tp"] == 0.05754
    assert p["tp_level"] == 1
    assert p["reason"] == "tp_removed_externally"


def test_protective_orders_reconciliation_skips_when_tp_fill_exists(ops_db):
    """If TP_FILLED already recorded → TP triggered normally → skip detection."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 61, tp_price=0.05754)
    # Existing TP_FILLED event → means it triggered, not cancelled
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (61, "TP_FILLED", '{"tp_level":1}', "DONE", "TP_FILLED:61:level:1"),
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
    """Two calls → exactly 1 PROTECTIVE_ORDERS_MISSING event."""
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
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='PROTECTIVE_ORDERS_MISSING'"
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
