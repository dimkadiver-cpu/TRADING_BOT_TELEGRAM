# tests/runtime_v2/execution_gateway/test_live_safety_gate.py
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


def _insert_chain(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (1,1,10,100,'trader_a','acc_1','BTC/USDT','LONG','WAITING_ENTRY','ONE_SHOT','{}',datetime('now'),datetime('now'))"
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path: str, cmd_id: int) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,1,'PLACE_ENTRY','PENDING',?,?,?,?)",
        (cmd_id,
         json.dumps({"symbol": "BTC/USDT", "side": "LONG", "entry_type": "LIMIT",
                     "price": 50000.0, "qty": 0.01, "sequence": 1}),
         f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def _live_config():
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    raw = {
        "default_adapter": "live_adapter",
        "account_routing": {"default": {"adapter": "live_adapter", "execution_account_id": "acc_1"}},
        "adapters": {
            "live_adapter": {
                "type": "hummingbot_api",
                "mode": "live",
                "base_url": "http://localhost:8002",
                "connector": "bybit_perpetual_main",
                "live_safety": {"allow_live_trading": True},
            }
        },
    }
    return ExecutionConfig.model_validate(raw)


def _live_config_not_allowed():
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    raw = {
        "default_adapter": "live_adapter",
        "account_routing": {"default": {"adapter": "live_adapter", "execution_account_id": "acc_1"}},
        "adapters": {
            "live_adapter": {
                "type": "hummingbot_api",
                "mode": "live",
                "base_url": "http://localhost:8002",
                "connector": "bybit_perpetual_main",
                "live_safety": {"allow_live_trading": False},
            }
        },
    }
    return ExecutionConfig.model_validate(raw)


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    _insert_chain(db)
    return db


def test_live_mode_without_env_var_blocked(ops_db, monkeypatch):
    monkeypatch.delenv("TSB_ALLOW_LIVE_TRADING", raising=False)
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2001)
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=_live_config(),
        adapter_registry={"live_adapter": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_execution_commands WHERE command_id=2001").fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_live_mode_with_env_var_but_config_false_blocked(ops_db, monkeypatch):
    monkeypatch.setenv("TSB_ALLOW_LIVE_TRADING", "YES_I_UNDERSTAND")
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2002)
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=_live_config_not_allowed(),
        adapter_registry={"live_adapter": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_execution_commands WHERE command_id=2002").fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_demo_mode_is_not_blocked_by_live_gate(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2003)
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_execution_commands WHERE command_id=2003").fetchone()[0]
    conn.close()
    assert status == "SENT"
