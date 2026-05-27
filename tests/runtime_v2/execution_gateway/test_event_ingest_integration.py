# tests/runtime_v2/execution_gateway/test_event_ingest_integration.py
"""Integration tests for the exchange-centric event ingest pipeline.

Covers:
  1. WS + REST idempotency — same TP fill from both paths → only 1 row in ops_exchange_events
  2. Full WS pipeline with known orderLinkId → forwarded to ops_exchange_events
  3. Normalizer → Classifier → Repository pipeline (TP position-level, no chain attribution)
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


# ---------------------------------------------------------------------------
# Schema helper — copied from test_repository_extensions.py
# ---------------------------------------------------------------------------

def make_db(tmp_path) -> str:
    """Create a test DB with the required schema."""
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
            lifecycle_state TEXT
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
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_chain(db_path: str, chain_id: int = 1) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, symbol, side, lifecycle_state) VALUES (?,?,?,?)",
        (chain_id, "BTCUSDT", "LONG", "OPEN"),
    )
    conn.commit()
    conn.close()


def _insert_sent_entry_cmd(
    db_path: str,
    cmd_id: int = 1,
    chain_id: int = 1,
    client_order_id: str = "tsb:1:1:entry:1",
) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, idempotency_key, "
        "client_order_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, "PLACE_ENTRY", "SENT", "{}", f"idem:{cmd_id}", client_order_id, now, now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Minimal trade dict builder
# ---------------------------------------------------------------------------

def _trade_dict(
    exec_id: str = "exec-100",
    symbol_raw: str = "BTCUSDT",
    side: str = "Sell",
    create_type: str = "CreateByTakeProfit",
    stop_order_type: str = "TakeProfit",
    order_link_id: str = "",
    closed_size: str = "0.01",
    pos_qty: str = "0",
    exec_price: str = "45000",
) -> dict:
    return {
        "id": exec_id,
        "symbol": "BTC/USDT:USDT",
        "side": side.lower(),
        "price": float(exec_price),
        "amount": float(closed_size),
        "info": {
            "execId": exec_id,
            "symbol": symbol_raw,
            "side": side,
            "createType": create_type,
            "stopOrderType": stop_order_type,
            "execType": "Trade",
            "closedSize": closed_size,
            "posQty": pos_qty,
            "orderLinkId": order_link_id,
            "orderId": f"ord-{exec_id}",
            "seq": "99999",
            "execPrice": exec_price,
            "execQty": closed_size,
            "execValue": str(float(exec_price) * float(closed_size)),
            "execFee": "0.18",
            "feeRate": "0.0004",
            "cumExecQty": closed_size,
            "execTime": "1716800000000",
        },
    }


# ---------------------------------------------------------------------------
# Test 1: WS + REST idempotency — same TP fill from both paths
# ---------------------------------------------------------------------------

def test_ws_and_rest_tp_fill_idempotent(tmp_path):
    """WS TP fill and REST TP fill for same trade → only 1 row in ops_exchange_events."""
    # 1. Create DB and seed data
    db_path = make_db(tmp_path)
    _insert_chain(db_path, chain_id=1)
    _insert_sent_entry_cmd(db_path, cmd_id=1, chain_id=1, client_order_id="tsb:1:1:entry:1")

    repo = GatewayCommandRepository(db_path)

    # --- Simulate WS path ---
    # 4. Build raw trade dict (CreateByTakeProfit — no orderLinkId)
    trade = _trade_dict(
        exec_id="exec-ws-tp",
        create_type="CreateByTakeProfit",
        stop_order_type="TakeProfit",
        order_link_id="",
    )

    # 5. Normalize
    raw = EventNormalizer().from_trade(trade)
    assert raw is not None

    # 6-7. Classify — no known IDs (TP position-level has no orderLinkId)
    classified = EventClassifier({}).classify(raw)

    # Should be TP_FILLED / exchange_auto / no chain
    assert classified.event_type == "TP_FILLED"
    assert classified.source == "exchange_auto"
    assert classified.trade_chain_id is None

    # 8. Insert via WS path — no forwarding because chain_id=None
    assert classified.should_forward_to_lifecycle is False
    result_ws = repo.insert_raw_and_classified(classified)
    assert result_ws is True

    conn = sqlite3.connect(db_path)
    raw_count = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    ops_count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()

    assert raw_count == 1  # audit row present
    assert ops_count == 0  # NOT forwarded — no chain attribution yet

    # --- Simulate REST path attributing to chain ---
    # 9. Insert via REST (explicit chain attribution)
    first_insert = repo.insert_exchange_event(
        trade_chain_id=1,
        event_type="TP_FILLED",
        payload_json=json.dumps({"exec_price": 45000.0, "closed_size": 0.01}),
        idempotency_key="TP_FILLED:1",
    )
    # 10. First insert should succeed
    assert first_insert is True

    # 11. Second insert with same key must be idempotent
    second_insert = repo.insert_exchange_event(
        trade_chain_id=1,
        event_type="TP_FILLED",
        payload_json=json.dumps({"exec_price": 45000.0, "closed_size": 0.01}),
        idempotency_key="TP_FILLED:1",
    )
    assert second_insert is False

    # 12. Exactly 1 row in ops_exchange_events
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT event_type, trade_chain_id FROM ops_exchange_events"
    ).fetchall()
    conn.close()

    assert len(row) == 1
    assert row[0][0] == "TP_FILLED"
    assert row[0][1] == 1

    # exchange_raw_events still has exactly 1 row (from WS step)
    conn = sqlite3.connect(db_path)
    raw_count_final = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    conn.close()
    assert raw_count_final == 1


# ---------------------------------------------------------------------------
# Test 2: Full WS pipeline with known orderLinkId
# ---------------------------------------------------------------------------

def test_ws_entry_fill_with_known_order_link_id(tmp_path):
    """WS entry fill with known orderLinkId → forwarded to ops_exchange_events."""
    # 1-3. Create DB, chain, sent entry command
    db_path = make_db(tmp_path)
    _insert_chain(db_path, chain_id=1)
    _insert_sent_entry_cmd(db_path, cmd_id=1, chain_id=1, client_order_id="tsb:1:1:entry:1")

    repo = GatewayCommandRepository(db_path)

    # 4. Load known IDs
    known_ids = repo.get_known_order_link_ids()
    assert "tsb:1:1:entry:1" in known_ids
    chain_id_from_map, role_from_map, cmd_id_from_map = known_ids["tsb:1:1:entry:1"]
    assert chain_id_from_map == 1
    assert role_from_map == "entry"
    assert cmd_id_from_map == 1

    # 5. Build entry fill trade dict — CreateByUser, orderLinkId present, closedSize=0
    trade = _trade_dict(
        exec_id="exec-entry-fill",
        side="Buy",
        create_type="CreateByUser",
        stop_order_type="",
        order_link_id="tsb:1:1:entry:1",
        closed_size="0",
        pos_qty="0.01",
        exec_price="44500",
    )

    # 6. Normalize
    raw = EventNormalizer().from_trade(trade)
    assert raw is not None
    assert raw.order_link_id == "tsb:1:1:entry:1"

    # 7. Classify with known IDs
    classified = EventClassifier(known_ids).classify(raw)

    # 8. Assert classification
    assert classified.event_type == "ENTRY_FILLED"
    assert classified.source == "bot_command"
    assert classified.trade_chain_id == 1

    # 9. Insert — should forward because chain_id is set and event is actionable
    assert classified.should_forward_to_lifecycle is True
    result = repo.insert_raw_and_classified(classified)
    assert result is True

    # 10-11. Verify ops_exchange_events has the ENTRY_FILLED row
    conn = sqlite3.connect(db_path)
    ops_rows = conn.execute(
        "SELECT event_type, trade_chain_id FROM ops_exchange_events"
    ).fetchall()
    raw_rows = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    conn.close()

    assert len(ops_rows) == 1
    assert ops_rows[0][0] == "ENTRY_FILLED"
    assert ops_rows[0][1] == 1
    assert raw_rows == 1


# ---------------------------------------------------------------------------
# Test 3: Full pipeline — TP position-level (no chain attribution until REST)
# ---------------------------------------------------------------------------

def test_full_pipeline_tp_position_level(tmp_path):
    """CreateByTakeProfit trade → TP_FILLED in exchange_raw_events (no chain until REST)."""
    # 1. Create DB — no chain needed
    db_path = make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    # 2. Build raw trade dict: position-level TP, no orderLinkId
    trade = _trade_dict(
        exec_id="exec-tp-pos",
        create_type="CreateByTakeProfit",
        stop_order_type="TakeProfit",
        order_link_id="",
    )

    # 3. Normalize
    raw = EventNormalizer().from_trade(trade)
    assert raw is not None
    assert raw.create_type == "CreateByTakeProfit"

    # 4. Classify with empty known IDs
    classified = EventClassifier({}).classify(raw)
    assert classified.event_type == "TP_FILLED"
    assert classified.source == "exchange_auto"
    assert classified.trade_chain_id is None

    # 5. should_forward_to_lifecycle is False — no chain id
    assert classified.should_forward_to_lifecycle is False

    # 6. Insert
    inserted = repo.insert_raw_and_classified(classified)
    assert inserted is True

    # 7. exchange_raw_events has 1 row with correct classification + forwarded_to_lifecycle=0
    conn = sqlite3.connect(db_path)
    raw_row = conn.execute(
        "SELECT classified_event_type, forwarded_to_lifecycle FROM exchange_raw_events"
    ).fetchone()
    ops_count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()

    assert raw_row is not None
    assert raw_row[0] == "TP_FILLED"
    assert raw_row[1] == 0  # not forwarded

    # 8. ops_exchange_events has 0 rows
    assert ops_count == 0
