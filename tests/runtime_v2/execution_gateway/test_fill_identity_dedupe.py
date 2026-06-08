# tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py
from __future__ import annotations

import sqlite3

from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent, ExchangeRawEvent
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE exchange_raw_events (
            raw_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange_event_id TEXT NOT NULL,
            source_stream TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            create_type TEXT, stop_order_type TEXT, exec_type TEXT, order_status TEXT,
            order_link_id TEXT, order_id TEXT, seq INTEGER,
            exec_price REAL, exec_qty REAL, closed_size REAL, leaves_qty REAL,
            pos_qty REAL, exec_value REAL, exec_fee REAL, fee_rate REAL, cum_exec_qty REAL,
            position_take_profit REAL, position_stop_loss REAL,
            classified_event_type TEXT, classified_source TEXT,
            trade_chain_id INTEGER, tp_level INTEGER,
            forwarded_to_lifecycle INTEGER DEFAULT 0, forwarded_at TEXT,
            raw_info_json TEXT NOT NULL DEFAULT '{}',
            exchange_time TEXT, received_at TEXT NOT NULL,
            idempotency_key TEXT UNIQUE NOT NULL
        );
        CREATE TABLE ops_exchange_events (
            exchange_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, event_type TEXT, payload_json TEXT,
            processing_status TEXT DEFAULT 'NEW',
            idempotency_key TEXT UNIQUE, received_at TEXT
        );
        CREATE TABLE ops_execution_commands (
            command_id INTEGER PRIMARY KEY, trade_chain_id INTEGER,
            command_type TEXT, status TEXT, payload_json TEXT DEFAULT '{}',
            idempotency_key TEXT, client_order_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, lifecycle_state TEXT, updated_at TEXT
        );
        CREATE TABLE ops_lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, event_type TEXT NOT NULL, source_type TEXT NOT NULL,
            source_id TEXT, previous_state TEXT, next_state TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL, destination TEXT NOT NULL,
            payload_json TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'MEDIUM',
            status TEXT NOT NULL DEFAULT 'PENDING', dedupe_key TEXT NOT NULL UNIQUE,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at TEXT NOT NULL,
            sent_at TEXT, send_after TEXT, aggregation_group TEXT, source_message_id TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _make_tp_fill(exec_id: str, idem_key: str, exec_qty: float = 7070.0) -> ClassifiedEvent:
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id=exec_id,
        idempotency_key=idem_key,
        symbol="ASTERUSDT",
        side="Sell",
        create_type="CreateByTakeProfit",
        stop_order_type="TakeProfit",
        exec_type="Trade",
        order_status=None,
        order_link_id="",
        order_id=f"order-{exec_id}",
        seq=1000,
        exec_price=0.6358,
        exec_qty=exec_qty,
        closed_size=exec_qty,
        leaves_qty=0.0,
        pos_qty=None,
        exec_value=exec_qty * 0.6358,
        exec_fee=0.002,
        fee_rate=0.00055,
        cum_exec_qty=None,
        position_take_profit=None,
        position_stop_loss=None,
        exchange_time="2026-06-07T22:14:19Z",
        received_at="2026-06-07T22:14:20Z",
        raw_info={},
    )
    return ClassifiedEvent(
        raw=raw,
        event_type="TP_FILLED",
        source="exchange_auto",
        trade_chain_id=1,
        tp_level=None,
        is_actionable=True,
    )


def test_two_tp_fills_no_tp_level_both_inserted(tmp_path):
    """Regression: TP1 parziale e TP finale, entrambi tp_level=None, exchange_event_id diversi.
    Prima del fix il secondo veniva droppato da INSERT OR IGNORE sulla stessa chiave semantica."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    tp1 = _make_tp_fill("exec-aaa-001", "exec:exec-aaa-001", exec_qty=7070.0)
    tp2 = _make_tp_fill("exec-bbb-002", "exec:exec-bbb-002", exec_qty=7071.0)

    inserted1 = repo.insert_raw_and_classified(tp1)
    inserted2 = repo.insert_raw_and_classified(tp2)

    assert inserted1 is True, "first TP fill should be inserted"
    assert inserted2 is True, "second TP fill must also be inserted — different exchange_event_id"

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT idempotency_key FROM ops_exchange_events "
        "WHERE event_type='TP_FILLED' ORDER BY exchange_event_id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2, f"expected 2 TP_FILLED rows, got {len(rows)}"
    keys = {r[0] for r in rows}
    assert keys == {"fill:exec-aaa-001", "fill:exec-bbb-002"}


def test_same_tp_fill_twice_is_idempotent(tmp_path):
    """Stesso execId visto due volte (WS duplicate) — inserito una sola volta."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    tp = _make_tp_fill("exec-ccc-003", "exec:exec-ccc-003")

    inserted1 = repo.insert_raw_and_classified(tp)
    inserted2 = repo.insert_raw_and_classified(tp)

    assert inserted1 is True
    assert inserted2 is False

    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert cnt == 1


def test_tp_fill_exists_after_identity_insert(tmp_path):
    """tp_fill_exists deve trovare un TP_FILLED inserito con chiave identity-based."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    tp = _make_tp_fill("exec-ddd-004", "exec:exec-ddd-004")
    repo.insert_raw_and_classified(tp)

    assert repo.tp_fill_exists(1) is True
    assert repo.tp_fill_exists(99) is False  # wrong chain


def test_tp_fill_exists_false_when_no_tp_in_chain(tmp_path):
    """tp_fill_exists false se non ci sono TP_FILLED per quella chain."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    assert repo.tp_fill_exists(1) is False


import json


def _make_rest_reconciliation_db(tmp_path) -> str:
    """DB with open chain + active TP command, no existing TP_FILLED."""
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (1, 'ASTERUSDT', 'LONG', 'OPEN')"
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, idempotency_key, created_at, updated_at) "
        "VALUES (1, 1, 'SET_POSITION_TPSL_PARTIAL', 'SENT', '{}', 'idem:1', '2026-06-07T00:00:00Z', '2026-06-07T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    return db_path


class _FakeTrade:
    def __init__(self, trade_id: str, price: float, amount: float):
        self.trade_id = trade_id
        self.price = price
        self.amount = amount
        self.fee = 0.0


class _FakeReconciliationAdapter:
    def __init__(self, trades: list):
        self._trades = trades

    def fetch_recent_reduce_trades(self, symbol, side, execution_account_id, limit=50):
        return self._trades

    def get_order_status(self, *a, **kw):
        return None

    def get_position_qty(self, *a, **kw):
        return None

    def get_capabilities(self):
        from src.runtime_v2.execution_gateway.models import AdapterCapabilities
        return AdapterCapabilities(
            place_entry=False, protective_stop_native=False, take_profit_native=False,
            bracket_order=False, move_stop=False, close_partial=False, close_full=False,
            executor_position=False, sync_protective_orders=False,
        )


def test_trade_based_reconciliation_uses_fill_identity_key(tmp_path):
    """REST reconciliation deve inserire il fill con chiave fill:<trade_id>, non TP_FILLED:<chain>."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db_path = _make_rest_reconciliation_db(tmp_path)
    repo = GatewayCommandRepository(db_path)
    adapter = _FakeReconciliationAdapter([_FakeTrade("exec-rest-999", 0.6393, 7071.0)])
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=adapter,
        repo=repo,
        execution_account_id="test_account",
    )

    inserted_count = worker.run_trade_based_reconciliation()

    assert inserted_count == 1

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT idempotency_key, event_type FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "fill:exec-rest-999", f"expected fill:exec-rest-999, got {row[0]!r}"


def test_trade_based_reconciliation_skips_when_ws_fill_already_present(tmp_path):
    """Se il WS ha già inserito il fill, la reconciliation REST non deve inserire un duplicato."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db_path = _make_rest_reconciliation_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    # Simulate WS having already inserted the fill with identity key
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (1, 'TP_FILLED', '{}', 'NEW', 'fill:exec-rest-999', '2026-06-07T22:14:20Z')"
    )
    conn.commit()
    conn.close()

    adapter = _FakeReconciliationAdapter([_FakeTrade("exec-rest-999", 0.6393, 7071.0)])
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=adapter,
        repo=repo,
        execution_account_id="test_account",
    )

    inserted_count = worker.run_trade_based_reconciliation()

    assert inserted_count == 0

    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='TP_FILLED'").fetchone()[0]
    conn.close()
    assert cnt == 1  # still just the one from WS
