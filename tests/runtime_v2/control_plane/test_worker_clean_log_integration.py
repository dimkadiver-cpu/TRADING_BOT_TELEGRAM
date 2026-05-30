# tests/runtime_v2/control_plane/test_worker_clean_log_integration.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
from src.runtime_v2.lifecycle.models import LifecycleEvent, TradeChain
from src.runtime_v2.lifecycle.repositories import (
    ExchangeEventRepository, ExecutionCommandRepository,
    LifecycleEventRepository, TradeChainRepository,
)
from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def test_fill_event_projects_clean_log_outbox(ops_db):
    chain_repo = TradeChainRepository(ops_db)
    chain = chain_repo.save(TradeChain(
        source_enrichment_id=1, canonical_message_id=1, raw_message_id=1,
        trader_id="trader_a", account_id="main", symbol="BTC/USDT", side="LONG",
        lifecycle_state="OPEN", entry_mode="ONE_SHOT", management_plan_json="{}",
    ))
    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=chain_repo,
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    result = EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain.trade_chain_id,
            event_type="SL_FILLED",
            source_type="exchange_event",
            payload_json="{}",
            idempotency_key=f"sl_filled:{chain.trade_chain_id}:1",
        )],
        execution_commands=[],
    )
    worker._persist_result(chain.trade_chain_id, result)

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT notification_type, destination FROM ops_notification_outbox"
    ).fetchall()
    conn.close()
    assert ("SL_FILLED", "CLEAN_LOG") in rows
