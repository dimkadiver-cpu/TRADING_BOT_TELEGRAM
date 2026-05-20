# tests/runtime_v2/execution_gateway/test_gateway.py
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


def _insert_chain(db_path: str, chain_id: int = 1, account_id: str = "acc_1") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (chain_id, 1, 10, 100, "trader_a", account_id,
         "BTC/USDT", "LONG", "WAITING_ENTRY", "ONE_SHOT", "{}"),
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path: str, cmd_id: int, chain_id: int = 1,
                cmd_type: str = "PLACE_ENTRY",
                payload: dict | None = None) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, "PENDING",
         json.dumps(payload or {}), f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    _insert_chain(db)
    return db


def test_place_entry_pending_to_sent(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1001, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status, coid = conn.execute(
        "SELECT status, client_order_id FROM ops_execution_commands WHERE command_id=1001"
    ).fetchone()
    conn.close()
    assert status == "SENT"
    assert coid.startswith("tsb:1:1001:entry:1:")


def test_capability_missing_produces_review_required(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1002, cmd_type="PLACE_PROTECTIVE_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "stop_price": 49000.0, "qty": 0.02, "reduce_only": True,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(
            capabilities=AdapterCapabilities(protective_stop_native=False)
        )},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1002"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_adapter_error_sets_retry(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1003, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(simulate_timeout=True)},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    retry_count = conn.execute(
        "SELECT retry_count FROM ops_execution_commands WHERE command_id=1003"
    ).fetchone()[0]
    conn.close()
    assert retry_count == 1


def test_close_partial_uses_exit_partial_role():
    from src.runtime_v2.execution_gateway.gateway import _ROLE_MAP
    assert _ROLE_MAP["CLOSE_PARTIAL"] == "exit_partial"


def test_close_full_uses_exit_full_role():
    from src.runtime_v2.execution_gateway.gateway import _ROLE_MAP
    assert _ROLE_MAP["CLOSE_FULL"] == "exit_full"


def test_sync_protective_orders_uses_sync_role():
    from src.runtime_v2.execution_gateway.gateway import _ROLE_MAP
    assert _ROLE_MAP["SYNC_PROTECTIVE_ORDERS"] == "sync"


def test_idempotency_recovery_stores_client_order_id(ops_db):
    """BUG: when idempotency check finds existing order, client_order_id must be stored
    so ExchangeEventSyncWorker can reconcile the fill (it filters client_order_id IS NOT NULL)."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.adapters.base import RawAdapterOrder
    from src.runtime_v2.execution_gateway.client_order_id import build
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway, _command_nonce
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1010, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    cmd = repo.get_pending_batch()[0]

    # Pre-populate fake adapter so idempotency check finds an existing order
    # (simulates: process crashed after place_order but before mark_sent)
    expected_coid = build(1, 1010, "entry", 1, nonce=_command_nonce(cmd))
    adapter = FakeAdapter()
    adapter._orders[expected_coid] = RawAdapterOrder(
        client_order_id=expected_coid,
        exchange_order_id="exch_abc123",
        adapter_order_id="hb_abc123",
        status="OPEN",
    )

    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status, coid = conn.execute(
        "SELECT status, client_order_id FROM ops_execution_commands WHERE command_id=1010"
    ).fetchone()
    conn.close()
    assert status == "ACK"
    assert coid == expected_coid, f"client_order_id must be stored for reconciliation, got: {coid}"


def test_new_command_id_includes_nonce_so_db_reset_does_not_hit_stale_order(ops_db):
    """A local DB reset can reuse ids while the exchange still has old orderLinkIds.
    The current command must use a fresh id, not recover stale terminal exchange state.
    """
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.adapters.base import RawAdapterOrder
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1011, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    old_coid = "tsb:1:1011:entry:1"
    adapter = FakeAdapter()
    adapter._orders[old_coid] = RawAdapterOrder(
        client_order_id=old_coid,
        exchange_order_id="old_cancelled_order",
        status="CANCELLED",
        cancel_reason="CancelByUser|EC_PerCancelRequest",
    )

    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status, coid = conn.execute(
        "SELECT status, client_order_id FROM ops_execution_commands WHERE command_id=1011"
    ).fetchone()
    conn.close()
    assert status == "SENT"
    assert coid.startswith("tsb:1:1011:entry:1:")
    assert any(call["action"] == "place_order" for call in adapter.calls)
    assert adapter._orders[coid].status == "OPEN"


def test_live_trading_blocked(ops_db):
    import yaml
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    raw = yaml.safe_load(open("config/execution.yaml").read())
    raw["execution"]["adapters"]["bybit_paper"]["live_safety"]["allow_live_trading"] = True
    raw["execution"]["adapters"]["bybit_paper"]["mode"] = "live"

    config = ExecutionConfig.model_validate(raw["execution"])

    _insert_cmd(ops_db, 1004, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=config,
        adapter_registry={"bybit_paper": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1004"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"
