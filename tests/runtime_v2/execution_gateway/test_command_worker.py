# tests/runtime_v2/execution_gateway/test_command_worker.py
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


def _insert_chain(db_path, chain_id=1, state="WAITING_ENTRY", account_id="acc_1"):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id * 10, chain_id * 100, "trader_a",
         account_id, "BTC/USDT", "LONG", state, "ONE_SHOT", "{}", now, now),
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path, cmd_id, chain_id=1, cmd_type="PLACE_ENTRY",
                status="PENDING", payload=None):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, status,
         json.dumps(payload or {"symbol": "BTC/USDT", "side": "LONG",
                                "entry_type": "LIMIT", "price": 50000.0,
                                "qty": 0.02, "sequence": 1}),
         f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def _make_worker(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    repo = GatewayCommandRepository(ops_db)
    adapter = FakeAdapter()
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    return ExecutionCommandWorker(ops_db_path=ops_db, gateway=gw, repo=repo), adapter


def test_pending_command_gets_sent(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1001)
    worker, _ = _make_worker(ops_db)
    processed = worker.run_once()
    assert processed == 1
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1001"
    ).fetchone()[0]
    conn.close()
    assert status == "SENT"


def test_double_run_does_not_resend(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1002)
    worker, adapter = _make_worker(ops_db)
    worker.run_once()
    worker.run_once()
    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1


def test_command_with_missing_chain_gets_review_required(ops_db):
    """BUG 5: PENDING command whose chain doesn't exist must be marked REVIEW_REQUIRED,
    not silently skipped forever."""
    _insert_cmd(ops_db, 2001, chain_id=9999)  # chain 9999 does not exist
    worker, _ = _make_worker(ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2001"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_waiting_position_on_open_chain_becomes_pending(ops_db):
    _insert_chain(ops_db, state="OPEN")
    _insert_cmd(ops_db, 1003, cmd_type="SET_POSITION_TPSL_PARTIAL", status="WAITING_POSITION",
                payload={"symbol": "BTC/USDT", "side": "LONG",
                         "take_profit": 51000.0, "tp_size": 0.02,
                         "stop_loss": 49000.0, "sl_size": 0.02,
                         "position_idx": 0})
    worker, _ = _make_worker(ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1003"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"  # SET_POSITION_TPSL_PARTIAL is fire-and-forget


def test_retry_batch_is_processed_after_backoff_expires(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1004)

    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    failing_adapter = FakeAdapter(simulate_timeout=True)
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": failing_adapter},
        repo=repo,
    )
    worker = ExecutionCommandWorker(ops_db_path=ops_db, gateway=gw, repo=repo)
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_execution_commands SET next_retry_at=datetime('now','-1 second') "
        "WHERE command_id=1004"
    )
    conn.commit()
    conn.close()

    healthy_adapter = FakeAdapter()
    retry_gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": healthy_adapter},
        repo=repo,
    )
    retry_worker = ExecutionCommandWorker(ops_db_path=ops_db, gateway=retry_gw, repo=repo)
    processed = retry_worker.run_once()

    assert processed == 1
    place_calls = [c for c in healthy_adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1

    conn = sqlite3.connect(ops_db)
    status, retry_count = conn.execute(
        "SELECT status, retry_count FROM ops_execution_commands WHERE command_id=1004"
    ).fetchone()
    conn.close()
    assert status == "SENT"
    assert retry_count == 1
