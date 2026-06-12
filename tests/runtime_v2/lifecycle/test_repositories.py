# tests/runtime_v2/lifecycle/test_repositories.py
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest


def _apply_migrations(db_path: str, migrations_dir: Path) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(migrations_dir.glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path, Path("db/ops_migrations"))
    return db_path


def test_migration_creates_ops_tables(ops_db):
    conn = sqlite3.connect(ops_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "ops_trade_chains" in tables
    assert "ops_lifecycle_events" in tables
    assert "ops_execution_commands" in tables
    assert "ops_exchange_events" in tables
    assert "ops_control_state" in tables


# --- TradeChainRepository ---

def test_chain_repo_save_and_get(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=1, canonical_message_id=10, raw_message_id=100,
        trader_id="trader_a", account_id="acc_1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
        execution_mode="C_MULTI_TP",
    )
    saved = repo.save(chain)
    assert saved.trade_chain_id is not None
    fetched = repo.get_by_id(saved.trade_chain_id)
    assert fetched is not None
    assert fetched.symbol == "BTC/USDT"
    assert fetched.lifecycle_state == "WAITING_ENTRY"
    assert fetched.execution_mode == "C_MULTI_TP"


def test_chain_repo_save_and_get_preserves_explicit_roi_fields(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=3, canonical_message_id=30, raw_message_id=300,
        trader_id="trader_a", account_id="acc_1", symbol="SOL/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
        initial_risk_amount=125.5, peak_margin_used=242.25,
    )
    saved = repo.save(chain)
    fetched = repo.get_by_id(saved.trade_chain_id)
    assert fetched is not None
    assert fetched.initial_risk_amount == 125.5
    assert fetched.peak_margin_used == 242.25


def test_chain_repo_has_chain_for_raw_message(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=9, canonical_message_id=90, raw_message_id=900,
        trader_id="trader_a", account_id="acc_1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
    )
    repo.save(chain)
    assert repo.has_chain_for_raw_message(900) is True
    assert repo.has_chain_for_raw_message(901) is False


def test_chain_repo_save_idempotent(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=2, canonical_message_id=20, raw_message_id=200,
        trader_id="trader_a", account_id="acc_1", symbol="ETH/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
    )
    first = repo.save(chain)
    second = repo.save(chain)
    assert first.trade_chain_id == second.trade_chain_id


def test_chain_repo_get_active_by_trader(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    for i, state in enumerate(["WAITING_ENTRY", "OPEN", "CLOSED"]):
        repo.save(TradeChain(
            source_enrichment_id=10 + i, canonical_message_id=10 + i,
            raw_message_id=100 + i, trader_id="trader_a", account_id="acc_1",
            symbol=f"SYM{i}/USDT", side="LONG", lifecycle_state=state,
            entry_mode="ONE_SHOT", management_plan_json="{}",
        ))
    active = repo.get_active_by_trader("trader_a")
    assert len(active) == 2
    assert all(c.lifecycle_state not in ("CLOSED", "CANCELLED", "EXPIRED") for c in active)


def test_chain_repo_update_state(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = repo.save(TradeChain(
        source_enrichment_id=99, canonical_message_id=99, raw_message_id=999,
        trader_id="trader_a", account_id="acc_1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
    ))
    repo.update_state(
        chain.trade_chain_id,
        "OPEN",
        entry_avg_price=49500.0,
        initial_risk_amount=100.0,
        peak_margin_used=150.0,
    )
    updated = repo.get_by_id(chain.trade_chain_id)
    assert updated.lifecycle_state == "OPEN"
    assert updated.entry_avg_price == 49500.0
    assert updated.initial_risk_amount == 100.0
    assert updated.peak_margin_used == 150.0


def test_chain_repo_save_populates_initial_risk_amount_from_risk_amount(ops_db):
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=99,
        canonical_message_id=990,
        raw_message_id=9900,
        trader_id="trader_a",
        account_id="main",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT",
        management_plan_json="{}",
        risk_snapshot_json=json.dumps({"risk_amount": 100.0}),
    )
    saved = repo.save(chain)
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT allocated_margin, initial_risk_amount FROM ops_trade_chains WHERE trade_chain_id=?",
        (saved.trade_chain_id,),
    ).fetchone()
    conn.close()
    assert row == (100.0, 100.0)


# --- LifecycleEventRepository ---

def test_event_repo_save_idempotent(ops_db):
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import LifecycleEventRepository
    repo = LifecycleEventRepository(ops_db)
    event = LifecycleEvent(
        event_type="SIGNAL_ACCEPTED", source_type="enrichment",
        idempotency_key="sig_accepted:1",
    )
    first = repo.save(event)
    second = repo.save(event)
    assert first.event_id == second.event_id


# --- ExecutionCommandRepository ---

def test_command_repo_save_and_get_active(ops_db):
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    from src.runtime_v2.lifecycle.repositories import ExecutionCommandRepository
    repo = ExecutionCommandRepository(ops_db)
    cmd = ExecutionCommand(
        trade_chain_id=1, command_type="PLACE_ENTRY",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="place_entry:1:1",
    )
    saved = repo.save(cmd)
    assert saved.command_id is not None
    active = repo.get_active_for_chain(1)
    assert len(active) == 1
    assert active[0].command_type == "PLACE_ENTRY"


def test_command_repo_save_idempotent(ops_db):
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    from src.runtime_v2.lifecycle.repositories import ExecutionCommandRepository
    repo = ExecutionCommandRepository(ops_db)
    cmd = ExecutionCommand(
        trade_chain_id=2, command_type="PLACE_ENTRY",
        payload_json='{}', idempotency_key="place_entry:2:1",
    )
    first = repo.save(cmd)
    second = repo.save(cmd)
    assert first.command_id == second.command_id


# --- ControlStateRepository ---

def test_control_state_none_by_default(ops_db):
    from src.runtime_v2.lifecycle.repositories import ControlStateRepository
    repo = ControlStateRepository(ops_db)
    mode = repo.get_effective_mode("acc_1", "trader_a", "BTC/USDT", "LONG")
    assert mode == "NONE"


def test_control_state_global_block(ops_db):
    import sqlite3
    from datetime import datetime, timezone
    from src.runtime_v2.lifecycle.repositories import ControlStateRepository
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_control_state (scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("GLOBAL", None, "BLOCK_NEW_ENTRIES", 1, now, now),
    )
    conn.commit()
    conn.close()
    repo = ControlStateRepository(ops_db)
    mode = repo.get_effective_mode("acc_1", "trader_a", "BTC/USDT", "LONG")
    assert mode == "BLOCK_NEW_ENTRIES"


def test_control_state_most_restrictive_wins(ops_db):
    import sqlite3
    from datetime import datetime, timezone
    from src.runtime_v2.lifecycle.repositories import ControlStateRepository
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.executemany(
        "INSERT INTO ops_control_state (scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        [
            ("GLOBAL", None, "BLOCK_NEW_ENTRIES", 1, now, now),
            ("TRADER", "trader_a", "FULL_STOP", 1, now, now),
        ],
    )
    conn.commit()
    conn.close()
    repo = ControlStateRepository(ops_db)
    mode = repo.get_effective_mode("acc_1", "trader_a", "BTC/USDT", "LONG")
    assert mode == "FULL_STOP"


def test_snapshot_repo_persists_payload_json(ops_db):
    from datetime import datetime, timezone

    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
    from src.runtime_v2.lifecycle.repositories import SnapshotRepository

    repo = SnapshotRepository(ops_db)
    now = datetime.now(timezone.utc)
    repo.save_account(
        AccountStateSnapshot(
            account_id="acc_1",
            equity_usdt=1000.0,
            available_balance_usdt=900.0,
            total_open_risk_usdt=25.0,
            total_margin_used_usdt=100.0,
            source="test",
            captured_at=now,
            payload_json='{"wallet": {"equity": "1000.0"}}',
        ),
        "acc_1",
    )
    repo.save_market(
        SymbolMarketSnapshot(
            symbol="BTCUSDT",
            mark_price=50000.0,
            bid=49999.0,
            ask=50001.0,
            min_order_size=0.001,
            price_precision=1,
            qty_precision=3,
            source="test",
            captured_at=now,
            payload_json='{"ticker": {"markPrice": 50000.0}}',
        ),
        "acc_1",
    )

    conn = sqlite3.connect(ops_db)
    try:
        account_payload = conn.execute(
            "SELECT payload_json FROM ops_account_snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()[0]
        market_payload = conn.execute(
            "SELECT payload_json FROM ops_market_snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()

    assert account_payload == '{"wallet": {"equity": "1000.0"}}'
    assert market_payload == '{"ticker": {"markPrice": 50000.0}}'


# --- ExchangeEventRepository ---

def test_exchange_event_repo_get_new_and_mark(ops_db):
    import sqlite3
    from datetime import datetime, timezone
    from src.runtime_v2.lifecycle.repositories import ExchangeEventRepository
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
        (1, "TP_FILLED", '{"tp_level": 1, "is_final": false}', "NEW", "tp_filled:1:1", now),
    )
    conn.commit()
    conn.close()
    repo = ExchangeEventRepository(ops_db)
    events = repo.get_new_events(10)
    assert len(events) == 1
    assert events[0].event_type == "TP_FILLED"
    repo.mark_processed(events[0].exchange_event_id)
    assert len(repo.get_new_events(10)) == 0
