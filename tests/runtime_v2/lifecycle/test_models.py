# tests/runtime_v2/lifecycle/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_trade_chain_requires_mandatory_fields():
    from src.runtime_v2.lifecycle.models import TradeChain
    with pytest.raises(ValidationError):
        TradeChain()  # mancano campi obbligatori


def test_trade_chain_valid():
    from src.runtime_v2.lifecycle.models import TradeChain
    chain = TradeChain(
        source_enrichment_id=1,
        canonical_message_id=10,
        raw_message_id=100,
        trader_id="trader_a",
        account_id="acc_1",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT",
        management_plan_json="{}",
    )
    assert chain.be_protection_status == "NOT_PROTECTED"
    assert chain.trade_chain_id is None


def test_lifecycle_event_valid():
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    event = LifecycleEvent(
        event_type="SIGNAL_ACCEPTED",
        source_type="enrichment",
        idempotency_key="sig_accepted:1",
    )
    assert event.trade_chain_id is None
    assert event.payload_json == "{}"


def test_execution_command_valid():
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    cmd = ExecutionCommand(
        trade_chain_id=1,
        command_type="PLACE_ENTRY",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="place_entry:1:1",
    )
    assert cmd.status == "PENDING"


def test_terminal_states():
    from src.runtime_v2.lifecycle.models import TERMINAL_STATES
    assert "CLOSED" in TERMINAL_STATES
    assert "CANCELLED" in TERMINAL_STATES
    assert "EXPIRED" in TERMINAL_STATES
    assert "OPEN" not in TERMINAL_STATES


def test_command_status_includes_prd05_states():
    from src.runtime_v2.lifecycle.models import CommandStatus
    import typing
    args = typing.get_args(CommandStatus)
    assert "WAITING_POSITION" in args
    assert "REVIEW_REQUIRED" in args


def test_trade_chain_has_qty_runtime_fields():
    from src.runtime_v2.lifecycle.models import TradeChain
    chain = TradeChain(
        source_enrichment_id=1, canonical_message_id=2, raw_message_id=3,
        trader_id="t1", account_id="acc1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT",
        management_plan_json="{}",
        planned_entry_qty=0.01,
        filled_entry_qty=0.005,
        open_position_qty=0.005,
        closed_position_qty=0.0,
        last_position_sync_at=None,
        execution_mode="a_sequential",
    )
    assert chain.planned_entry_qty == 0.01
    assert chain.filled_entry_qty == 0.005
    assert chain.open_position_qty == 0.005
    assert chain.closed_position_qty == 0.0
    assert chain.last_position_sync_at is None
    assert chain.execution_mode == "a_sequential"


def test_trade_chain_qty_defaults_to_zero():
    from src.runtime_v2.lifecycle.models import TradeChain
    chain = TradeChain(
        source_enrichment_id=1, canonical_message_id=2, raw_message_id=3,
        trader_id="t1", account_id="acc1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT",
        management_plan_json="{}",
    )
    assert chain.planned_entry_qty == 0.0
    assert chain.filled_entry_qty == 0.0
    assert chain.open_position_qty == 0.0
    assert chain.closed_position_qty == 0.0
    assert chain.execution_mode == "a_sequential"


import sqlite3, os, tempfile


def test_migration_003_creates_qty_columns():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE ops_trade_chains (
                trade_chain_id INTEGER PRIMARY KEY,
                source_enrichment_id INTEGER NOT NULL,
                lifecycle_state TEXT NOT NULL,
                management_plan_json TEXT NOT NULL DEFAULT '{}',
                risk_snapshot_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.commit()
        migration = open("db/ops_migrations/003_ops_quantity_runtime.sql").read()
        for stmt in migration.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_trade_chains)")}
        assert "planned_entry_qty" in cols
        assert "filled_entry_qty" in cols
        assert "open_position_qty" in cols
        assert "closed_position_qty" in cols
        assert "last_position_sync_at" in cols
        assert "execution_mode" in cols
        conn.close()
    finally:
        os.unlink(db_path)
