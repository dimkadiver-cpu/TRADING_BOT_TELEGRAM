# tests/runtime_v2/execution_gateway/test_repository_extensions.py
from __future__ import annotations

import sqlite3

import pytest

from src.runtime_v2.execution_gateway.event_ingest.models import (
    ClassifiedEvent,
    ExchangeRawEvent,
)
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


def make_db(tmp_path):
    """Create test DB with required schema."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_execution_commands (
            command_id INTEGER PRIMARY KEY,
            trade_chain_id INTEGER,
            command_type TEXT,
            status TEXT,
            payload_json TEXT DEFAULT '{}',
            idempotency_key TEXT,
            client_order_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT,
            updated_at TEXT
        );
        CREATE TABLE exchange_raw_events (
            raw_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange_event_id TEXT NOT NULL,
            source_stream TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            create_type TEXT,
            stop_order_type TEXT,
            exec_type TEXT,
            order_status TEXT,
            order_link_id TEXT,
            order_id TEXT,
            seq INTEGER,
            exec_price REAL,
            exec_qty REAL,
            closed_size REAL,
            leaves_qty REAL,
            pos_qty REAL,
            exec_value REAL,
            exec_fee REAL,
            fee_rate REAL,
            cum_exec_qty REAL,
            position_take_profit REAL,
            position_stop_loss REAL,
            classified_event_type TEXT,
            classified_source TEXT,
            trade_chain_id INTEGER,
            tp_level INTEGER,
            forwarded_to_lifecycle INTEGER DEFAULT 0,
            forwarded_at TEXT,
            raw_info_json TEXT NOT NULL DEFAULT '{}',
            exchange_time TEXT,
            received_at TEXT NOT NULL,
            idempotency_key TEXT UNIQUE NOT NULL
        );
        CREATE TABLE ops_exchange_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER,
            event_type TEXT,
            payload_json TEXT,
            processing_status TEXT DEFAULT 'NEW',
            idempotency_key TEXT UNIQUE,
            received_at TEXT
        );
        CREATE TABLE ops_lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER,
            event_type TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT,
            previous_state TEXT,
            next_state TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL,
            destination TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'MEDIUM',
            status TEXT NOT NULL DEFAULT 'PENDING',
            dedupe_key TEXT NOT NULL UNIQUE,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _make_raw_event(idempotency_key: str = "test-key-001") -> ExchangeRawEvent:
    return ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id="exchange-evt-001",
        idempotency_key=idempotency_key,
        symbol="BTCUSDT",
        side="Buy",
        create_type=None,
        stop_order_type=None,
        exec_type="Trade",
        order_status="Filled",
        order_link_id="tsb:1:tp:1",
        order_id="order-001",
        seq=1001,
        exec_price=50000.0,
        exec_qty=0.01,
        closed_size=0.01,
        leaves_qty=0.0,
        pos_qty=0.0,
        exec_value=500.0,
        exec_fee=0.2,
        fee_rate=0.0004,
        cum_exec_qty=0.01,
        position_take_profit=None,
        position_stop_loss=None,
        exchange_time="2026-05-27T10:00:00Z",
        received_at="2026-05-27T10:00:01Z",
        raw_info={"extra": "data"},
    )


def _make_classified(
    raw: ExchangeRawEvent | None = None,
    trade_chain_id: int | None = 1,
    tp_level: int | None = 1,
    event_type: str = "TP_FILLED",
) -> ClassifiedEvent:
    if raw is None:
        raw = _make_raw_event()
    return ClassifiedEvent(
        raw=raw,
        event_type=event_type,  # type: ignore[arg-type]
        source="bot_command",
        trade_chain_id=trade_chain_id,
        tp_level=tp_level,
        is_actionable=True,
    )


# ---------------------------------------------------------------------------
# Test 1: insert_raw_and_classified — happy path
# ---------------------------------------------------------------------------

def test_insert_raw_and_classified_returns_true_on_new(tmp_path):
    db_path = make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)
    classified = _make_classified()

    result = repo.insert_raw_and_classified(classified)

    assert result is True

    conn = sqlite3.connect(db_path)
    raw_count = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    ops_count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()

    assert raw_count == 1
    assert ops_count == 1


# ---------------------------------------------------------------------------
# Test 2: insert_raw_and_classified — idempotent (second call returns False)
# ---------------------------------------------------------------------------

def test_insert_raw_and_classified_idempotent(tmp_path):
    db_path = make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)
    classified = _make_classified()

    first = repo.insert_raw_and_classified(classified)
    second = repo.insert_raw_and_classified(classified)

    assert first is True
    assert second is False

    conn = sqlite3.connect(db_path)
    raw_count = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    conn.close()

    assert raw_count == 1


# ---------------------------------------------------------------------------
# Test 3: not forwarded when trade_chain_id is None
# ---------------------------------------------------------------------------

def test_insert_raw_and_classified_not_forwarded_when_no_chain_id(tmp_path):
    db_path = make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)
    classified = _make_classified(trade_chain_id=None, tp_level=None)

    assert classified.should_forward_to_lifecycle is False

    repo.insert_raw_and_classified(classified)

    conn = sqlite3.connect(db_path)
    raw_count = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    ops_count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()

    assert raw_count == 1
    assert ops_count == 0


# ---------------------------------------------------------------------------
# Test 4: get_known_order_link_ids
# ---------------------------------------------------------------------------

def test_get_known_order_link_ids(tmp_path):
    db_path = make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, client_order_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, 10, "PLACE_ENTRY", "SENT", "tsb:1:entry:1"),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, client_order_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (2, 10, "SET_POSITION_TPSL_PARTIAL", "SENT", "tsb:1:tp:1"),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(db_path)
    mapping = repo.get_known_order_link_ids()

    assert "tsb:1:entry:1" in mapping
    assert "tsb:1:tp:1" in mapping

    chain_id_entry, role_entry, seq_entry = mapping["tsb:1:entry:1"]
    assert chain_id_entry == 10
    assert role_entry == "entry"
    assert seq_entry == 1  # command_id

    chain_id_tp, role_tp, seq_tp = mapping["tsb:1:tp:1"]
    assert chain_id_tp == 10
    assert role_tp == "tp_1"
    assert seq_tp == 2  # command_id


# ---------------------------------------------------------------------------
# Test 5: get_open_chains_with_tps
# ---------------------------------------------------------------------------

def test_get_open_chains_with_tps(tmp_path):
    db_path = make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    # Open chain with TP command
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (?, ?, ?, ?)",
        (1, "BTCUSDT", "LONG", "OPEN"),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status) VALUES (?,?,?,?)",
        (1, 1, "SET_POSITION_TPSL_PARTIAL", "SENT"),
    )
    # Closed chain — should not appear
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (?, ?, ?, ?)",
        (2, "ETHUSDT", "SHORT", "CLOSED"),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status) VALUES (?,?,?,?)",
        (2, 2, "SET_POSITION_TPSL_PARTIAL", "SENT"),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(db_path)
    chains = repo.get_open_chains_with_tps()

    assert len(chains) == 1
    assert chains[0]["trade_chain_id"] == 1
    assert chains[0]["symbol"] == "BTCUSDT"
    assert chains[0]["side"] == "LONG"


# ---------------------------------------------------------------------------
# Test 6: tp_fill_exists and protective_cancelled_exists
# ---------------------------------------------------------------------------

def test_tp_fill_exists_and_protective_cancelled_exists(tmp_path):
    db_path = make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    # Insert TP_FILLED for chain=1 level=1
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,?)",
        (1, "TP_FILLED", "{}", "NEW", "TP_FILLED:1:level:1", "2026-05-27T10:00:00Z"),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(db_path)

    assert repo.tp_fill_exists(1, 1) is True
    assert repo.tp_fill_exists(1, 2) is False

    # Insert PROTECTIVE_ORDER_CANCELLED for chain=1
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,?)",
        (1, "PROTECTIVE_ORDER_CANCELLED", "{}", "NEW", "PROTECTIVE_ORDER_CANCELLED:1:unique", "2026-05-27T10:00:00Z"),
    )
    conn.commit()
    conn.close()

    assert repo.protective_cancelled_exists(1) is True
    assert repo.protective_cancelled_exists(99) is False


# ---------------------------------------------------------------------------
# Test 7: insert_raw_and_classified — payload uses fill_price/filled_qty keys
# ---------------------------------------------------------------------------

def test_insert_raw_and_classified_payload_uses_fill_price_filled_qty(tmp_path):
    """Payload written to ops_exchange_events must use fill_price/filled_qty keys
    so event_processor._process_entry_filled / _process_tp_filled can read them."""
    import json as _json
    db_path = make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    raw = _make_raw_event()  # exec_price=50000.0, exec_qty=0.01
    classified = _make_classified(raw=raw, event_type="ENTRY_FILLED")

    repo.insert_raw_and_classified(classified)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT payload_json FROM ops_exchange_events").fetchone()
    conn.close()

    payload = _json.loads(row[0])
    assert "fill_price" in payload, f"expected fill_price, got keys: {list(payload)}"
    assert "filled_qty" in payload, f"expected filled_qty, got keys: {list(payload)}"
    assert "exec_price" not in payload, "exec_price should not be in payload"
    assert "exec_qty" not in payload, "exec_qty should not be in payload"
    assert payload["fill_price"] == 50000.0
    assert payload["filled_qty"] == 0.01


def test_insert_raw_and_classified_enriches_pending_cancel_with_trigger_metadata(tmp_path):
    import json

    db_path = make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, client_order_id) "
        "VALUES (?,?,?,?,?,?)",
        (11, 1, "PLACE_ENTRY", "DONE", "{}", "tsb:1:11:entry:1"),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            12,
            1,
            "CANCEL_PENDING_ENTRY",
            "DONE",
            json.dumps({
                "entry_client_order_id": "tsb:1:11:entry:1",
                "cancel_origin": "timeout_worker",
                "cancel_reason": "position_closed",
            }),
            "2026-06-05T13:01:00Z",
            "2026-06-05T13:01:00Z",
        ),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(db_path)
    raw = ExchangeRawEvent(
        source_stream="watch_orders",
        exchange_event_id="exchange-cancel-001",
        idempotency_key="watch-orders-cancel-001",
        symbol="BTCUSDT",
        side="Buy",
        create_type=None,
        stop_order_type=None,
        exec_type=None,
        order_status="Cancelled",
        order_link_id="tsb:1:11:entry:1",
        order_id="order-cancel-001",
        seq=1002,
        exec_price=None,
        exec_qty=None,
        closed_size=None,
        leaves_qty=0.0,
        pos_qty=0.0,
        exec_value=None,
        exec_fee=None,
        fee_rate=None,
        cum_exec_qty=0.0,
        position_take_profit=None,
        position_stop_loss=None,
        exchange_time="2026-06-05T13:01:01Z",
        received_at="2026-06-05T13:01:02Z",
        raw_info={},
    )
    classified = ClassifiedEvent(
        raw=raw,
        event_type="PENDING_ENTRY_CANCELLED",
        source="manual_command",
        trade_chain_id=1,
        tp_level=None,
        is_actionable=True,
    )

    repo.insert_raw_and_classified(classified)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE event_type='PENDING_ENTRY_CANCELLED'"
    ).fetchone()
    conn.close()
    assert row is not None
    payload = json.loads(row[0])
    assert payload["cancel_origin"] == "timeout_worker"
    assert payload["cancel_reason"] == "position_closed"


# ── resolve_chain_for_fill ────────────────────────────────────────────────────

def test_resolve_chain_for_fill_returns_chain_id_when_exactly_one(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT,
            updated_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (7, 'BTCUSDT', 'LONG', 'OPEN')"
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(db_path)
    assert repo.resolve_chain_for_fill("BTCUSDT", "LONG") == 7


def test_resolve_chain_for_fill_returns_none_when_no_open_chain(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT,
            updated_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (7, 'BTCUSDT', 'LONG', 'CLOSED')"
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(db_path)
    assert repo.resolve_chain_for_fill("BTCUSDT", "LONG") is None


# ---------------------------------------------------------------------------
# Tests: cancel_chain_if_all_entries_failed
# ---------------------------------------------------------------------------

def _setup_chain_with_commands(db_path: str, chain_state: str, cmd_statuses: list[str]) -> None:
    """Insert a chain and one PLACE_ENTRY command per status into the DB."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (1, 'FOLKUSDT', 'SHORT', ?)",
        (chain_state,),
    )
    for i, status in enumerate(cmd_statuses, start=1):
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(command_id, trade_chain_id, command_type, status, payload_json, idempotency_key, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (i, 1, "PLACE_ENTRY", status, "{}", f"idem:{i}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    conn.commit()
    conn.close()


def test_cancel_chain_if_all_entries_failed_cancels_when_all_failed(tmp_path):
    db_path = make_db(tmp_path)
    _setup_chain_with_commands(db_path, "WAITING_ENTRY", ["FAILED", "FAILED"])

    repo = GatewayCommandRepository(db_path)
    result = repo.cancel_chain_if_all_entries_failed(1, "PLACE_ENTRY", reason="symbol_not_found")

    assert result is True

    conn = sqlite3.connect(db_path)
    state = conn.execute("SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1").fetchone()[0]
    event = conn.execute(
        "SELECT event_type, previous_state, next_state, payload_json "
        "FROM ops_lifecycle_events WHERE idempotency_key='entry_all_failed:1'"
    ).fetchone()
    conn.close()

    assert state == "CANCELLED"
    assert event is not None
    assert event[0] == "PENDING_ENTRY_CANCELLED"
    assert event[1] == "WAITING_ENTRY"
    assert event[2] == "CANCELLED"
    import json
    assert json.loads(event[3])["reason"] == "symbol_not_found"


def test_cancel_chain_if_all_entries_failed_noop_when_one_still_active(tmp_path):
    db_path = make_db(tmp_path)
    _setup_chain_with_commands(db_path, "WAITING_ENTRY", ["FAILED", "SENT"])

    repo = GatewayCommandRepository(db_path)
    result = repo.cancel_chain_if_all_entries_failed(1, "PLACE_ENTRY", reason="err")

    assert result is False

    conn = sqlite3.connect(db_path)
    state = conn.execute("SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1").fetchone()[0]
    conn.close()

    assert state == "WAITING_ENTRY"


def test_cancel_chain_if_all_entries_failed_noop_for_non_entry_command_type(tmp_path):
    db_path = make_db(tmp_path)
    _setup_chain_with_commands(db_path, "WAITING_ENTRY", ["FAILED"])

    repo = GatewayCommandRepository(db_path)
    result = repo.cancel_chain_if_all_entries_failed(1, "MOVE_STOP", reason="err")

    assert result is False

    conn = sqlite3.connect(db_path)
    state = conn.execute("SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1").fetchone()[0]
    conn.close()

    assert state == "WAITING_ENTRY"


def test_cancel_chain_if_all_entries_failed_noop_when_chain_not_waiting(tmp_path):
    db_path = make_db(tmp_path)
    _setup_chain_with_commands(db_path, "OPEN", ["FAILED"])

    repo = GatewayCommandRepository(db_path)
    result = repo.cancel_chain_if_all_entries_failed(1, "PLACE_ENTRY", reason="err")

    assert result is False

    conn = sqlite3.connect(db_path)
    state = conn.execute("SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1").fetchone()[0]
    conn.close()

    assert state == "OPEN"


def test_cancel_chain_if_all_entries_failed_idempotent(tmp_path):
    db_path = make_db(tmp_path)
    _setup_chain_with_commands(db_path, "WAITING_ENTRY", ["FAILED"])

    repo = GatewayCommandRepository(db_path)
    first = repo.cancel_chain_if_all_entries_failed(1, "PLACE_ENTRY", reason="err")
    second = repo.cancel_chain_if_all_entries_failed(1, "PLACE_ENTRY", reason="err")

    assert first is True
    assert second is False  # chain is now CANCELLED, not WAITING_ENTRY

    conn = sqlite3.connect(db_path)
    event_count = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert event_count == 1


def test_cancel_chain_if_all_entries_failed_with_attached_tpsl_type(tmp_path):
    db_path = make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (1, 'BTCUSDT', 'LONG', 'WAITING_ENTRY')"
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, idempotency_key, created_at, updated_at) "
        "VALUES (1, 1, 'PLACE_ENTRY_WITH_ATTACHED_TPSL', 'FAILED', '{}', 'idem:1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(db_path)
    result = repo.cancel_chain_if_all_entries_failed(
        1, "PLACE_ENTRY_WITH_ATTACHED_TPSL", reason="adapter_error"
    )

    assert result is True

    conn = sqlite3.connect(db_path)
    state = conn.execute("SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1").fetchone()[0]
    conn.close()

    assert state == "CANCELLED"


def test_resolve_chain_for_fill_returns_none_when_multiple_open_chains(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT,
            updated_at TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) VALUES (?,?,?,?)",
        [(7, "BTCUSDT", "LONG", "OPEN"), (8, "BTCUSDT", "LONG", "OPEN")],
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(db_path)
    assert repo.resolve_chain_for_fill("BTCUSDT", "LONG") is None
