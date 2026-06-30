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


def _insert_chain(
    db_path: str,
    chain_id: int = 1,
    account_id: str = "acc_1",
    management_plan_json: str = "{}",
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (chain_id, 1, 10, 100, "trader_a", account_id,
         "BTC/USDT", "LONG", "WAITING_ENTRY", "ONE_SHOT", management_plan_json),
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


def _insert_cmd_with_status(
    db_path: str,
    cmd_id: int,
    *,
    status: str,
    chain_id: int = 1,
    cmd_type: str = "PLACE_ENTRY",
    payload: dict | None = None,
) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, status,
         json.dumps(payload or {}), f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def _update_chain_management_plan(db_path: str, chain_id: int, management_plan: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE ops_trade_chains SET management_plan_json=? WHERE trade_chain_id=?",
        (json.dumps(management_plan), chain_id),
    )
    conn.commit()
    conn.close()


def _rebuild_partial_tps_payload() -> dict:
    return {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "targets": [
            {
                "take_profit": 70000.0,
                "tp_size": 0.007,
                "tp_order_type": "Limit",
                "tp_limit_price": 70000.0,
                "tp_trigger_by": "MarkPrice",
                "preserve_sl": True,
            },
            {
                "take_profit": 75000.0,
                "tp_size": 0.003,
                "tp_order_type": "Limit",
                "tp_limit_price": 75000.0,
                "tp_trigger_by": "MarkPrice",
                "preserve_sl": True,
            },
        ],
    }


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

    _insert_cmd(ops_db, 1002, cmd_type="CLOSE_PARTIAL", payload={
        "symbol": "BTC/USDT", "side": "LONG", "qty": 0.01,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(
            capabilities=AdapterCapabilities(close_partial=False)
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


def test_one_way_routing_normalizes_hedge_payload_before_send(ops_db, tmp_path):
    import yaml
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {
                "default": {
                    "adapter": "fake",
                    "execution_account_id": "acc_main",
                    "position_mode": "one_way",
                }
            },
            "adapters": {
                "fake": {
                    "type": "fake",
                    "mode": "paper",
                    "connector": "fake_connector",
                }
            },
        }
    }
    config_path = tmp_path / "execution.yaml"
    config_path.write_text(yaml.dump(cfg))

    _insert_cmd(ops_db, 1004, payload={
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_type": "LIMIT",
        "price": 50000.0,
        "qty": 0.02,
        "sequence": 1,
        "leverage": 5,
        "hedge_mode": True,
        "position_idx": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    fake = FakeAdapter()
    gw = ExecutionGateway(
        config=ExecutionConfigLoader(str(config_path)).load(),
        adapter_registry={"fake": fake},
        repo=repo,
    )

    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 1004)
    gw.process(cmd, account_id="acc_1")

    set_leverage_calls = [c for c in fake.calls if c["action"] == "set_leverage"]
    place_calls = [c for c in fake.calls if c["action"] == "place_order"]
    assert set_leverage_calls
    assert place_calls
    assert set_leverage_calls[-1]["position_idx"] == 0
    assert place_calls[-1]["payload"]["hedge_mode"] is False
    assert place_calls[-1]["payload"]["position_idx"] == 0

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox "
        "WHERE destination='TECH_LOG' AND notification_type='GATEWAY_POSITION_MODE_NORMALIZED' "
        "ORDER BY notification_id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    payload = json.loads(row[1])
    assert row[0] == "GATEWAY_POSITION_MODE_NORMALIZED"
    assert payload["level"] == "WARNING"
    assert payload["from_hedge_mode"] is True
    assert payload["to_hedge_mode"] is False
    assert payload["from_position_idx"] == 1
    assert payload["to_position_idx"] == 0
    assert payload["execution_account_id"] == "acc_main"


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
    retry_row = conn.execute(
        "SELECT retry_count FROM ops_execution_commands WHERE command_id=1003"
    ).fetchone()[0]
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1003"
    ).fetchone()[0]
    conn.close()
    assert status == "SENT"
    assert retry_row == 1


def test_close_partial_uses_exit_partial_role():
    from src.runtime_v2.execution_gateway.gateway import _ROLE_MAP
    assert _ROLE_MAP["CLOSE_PARTIAL"] == "exit_partial"


def test_close_full_uses_exit_full_role():
    from src.runtime_v2.execution_gateway.gateway import _ROLE_MAP
    assert _ROLE_MAP["CLOSE_FULL"] == "exit_full"


def test_move_position_stop_uses_sl_role():
    from src.runtime_v2.execution_gateway.gateway import _ROLE_MAP
    assert _ROLE_MAP["MOVE_POSITION_STOP"] == "sl"


# ── CLOSE_FULL / CLOSE_PARTIAL deferred qty resolution ────────────────────

def _set_open_position_qty(db_path: str, chain_id: int, qty: float) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE ops_trade_chains SET open_position_qty=? WHERE trade_chain_id=?",
        (qty, chain_id),
    )
    conn.commit()
    conn.close()


def test_close_full_prefers_live_exchange_qty_over_open_position(ops_db):
    """CLOSE_FULL without qty in payload prefers live exchange qty over DB open_position_qty."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _set_open_position_qty(ops_db, chain_id=1, qty=122.3)
    _insert_cmd(ops_db, 2001, cmd_type="CLOSE_FULL", payload={
        "symbol": "BTC/USDT", "side": "LONG",
    })
    repo = GatewayCommandRepository(ops_db)
    fake = FakeAdapter(positions={"BTC/USDT:LONG": 122.456})
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo_1": fake},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2001"
    ).fetchone()
    conn.close()
    assert row[0] == "SENT"
    place_calls = [c for c in fake.calls if c["action"] == "place_order"]
    assert place_calls, "Expected at least one place_order call"
    assert abs(place_calls[-1]["payload"]["qty"] - 122.456) < 1e-9


def test_close_full_falls_back_to_open_position_when_live_qty_unavailable(ops_db):
    """CLOSE_FULL falls back to DB open_position_qty when live exchange qty is unavailable."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _set_open_position_qty(ops_db, chain_id=1, qty=122.3)
    _insert_cmd(ops_db, 2011, cmd_type="CLOSE_FULL", payload={
        "symbol": "BTC/USDT", "side": "LONG",
    })
    repo = GatewayCommandRepository(ops_db)
    fake = FakeAdapter()
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo_1": fake},
        repo=repo,
    )
    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 2011)
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2011"
    ).fetchone()
    conn.close()
    assert row[0] == "SENT"
    place_calls = [c for c in fake.calls if c["action"] == "place_order"]
    assert place_calls, "Expected at least one place_order call"
    assert abs(place_calls[-1]["payload"]["qty"] - 122.3) < 1e-9


def test_close_partial_resolves_qty_from_fraction(ops_db):
    """CLOSE_PARTIAL with fraction but no qty resolves qty = open_position_qty * fraction."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _set_open_position_qty(ops_db, chain_id=1, qty=100.0)
    _insert_cmd(ops_db, 2002, cmd_type="CLOSE_PARTIAL", payload={
        "symbol": "BTC/USDT", "side": "LONG", "fraction": 0.5,
    })
    repo = GatewayCommandRepository(ops_db)
    fake = FakeAdapter()
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": fake},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2002"
    ).fetchone()
    conn.close()
    assert row[0] == "SENT"
    # Verify fraction was stripped and qty was computed correctly
    place_calls = [c for c in fake.calls if c["action"] == "place_order"]
    assert place_calls, "Expected at least one place_order call"
    last_payload = place_calls[-1]["payload"]
    assert "fraction" not in last_payload
    assert abs(last_payload["qty"] - 50.0) < 1e-6


def test_close_full_review_required_when_no_open_position(ops_db):
    """CLOSE_FULL when open_position_qty=0 → REVIEW_REQUIRED (no qty to close)."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _set_open_position_qty(ops_db, chain_id=1, qty=0.0)
    _insert_cmd(ops_db, 2003, cmd_type="CLOSE_FULL", payload={
        "symbol": "BTC/USDT", "side": "LONG",
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
    row = conn.execute(
        "SELECT status, result_payload_json FROM ops_execution_commands WHERE command_id=2003"
    ).fetchone()
    conn.close()
    assert row[0] == "REVIEW_REQUIRED"
    result = json.loads(row[1])
    assert result["reason"] == "open_position_qty_unavailable_for_close"


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


def test_idempotency_recovery_fire_and_forget_reaches_done_and_emits_event(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.adapters.base import RawAdapterOrder
    from src.runtime_v2.execution_gateway.client_order_id import build
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway, _command_nonce
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1012, cmd_type="MOVE_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 49000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    cmd = repo.get_pending_batch()[0]
    expected_coid = build(1, 1012, "sl", 1, nonce=_command_nonce(cmd))
    adapter = FakeAdapter()
    adapter._orders[expected_coid] = RawAdapterOrder(
        client_order_id=expected_coid,
        exchange_order_id="exch_move_stop",
        adapter_order_id="hb_move_stop",
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
        "SELECT status, client_order_id FROM ops_execution_commands WHERE command_id=1012"
    ).fetchone()
    conn.close()
    assert status == "DONE"
    assert coid == expected_coid

    events = _get_exchange_events(ops_db, chain_id=1)
    assert len(events) == 1
    assert events[0][0] == "STOP_MOVED_CONFIRMED"
    assert events[0][1]["command_id"] == 1012
    assert events[0][1]["new_stop_price"] == 49000.0
    assert events[0][1]["is_breakeven"] is False


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
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    raw = yaml.safe_load(open("config/execution.yaml").read())
    raw["execution"]["adapters"]["bybit_demo"]["live_safety"]["allow_live_trading"] = True
    raw["execution"]["adapters"]["bybit_demo"]["mode"] = "live"
    config = ExecutionConfig.model_validate(raw["execution"])

    _insert_cmd(ops_db, 1004, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=config,
        adapter_registry={"bybit_demo": FakeAdapter()},
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


def test_move_position_stop_capability_missing_produces_review_required(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1005, cmd_type="MOVE_POSITION_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 47000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(
            capabilities=AdapterCapabilities(move_stop=False)
        )},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1005"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_deferred_market_resolves_qty_from_mark_price(ops_db):
    """Gateway con payload deferred_market: fetcha mark_price e calcola qty prima del place_order."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    payload = {
        "symbol": "SOL/USDT", "side": "LONG", "entry_type": "MARKET",
        "qty_mode": "deferred_market", "risk_amount": 10.0, "sl_price": 140.0,
        "leverage": 1, "hedge_mode": False, "position_idx": 0,
        "execution_strategy": "D_POSITION_TPSL", "sequence": 1,
    }
    _insert_cmd(ops_db, 2001, payload=payload)
    repo = GatewayCommandRepository(ops_db)

    adapter = FakeAdapter(mark_prices={"SOL/USDT": 150.0})
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1
    # qty = risk_amount / abs(mark_price - sl_price) = 10.0 / abs(150.0 - 140.0) = 1.0
    assert abs(adapter._last_place_qty - 1.0) < 0.001


def test_leverage_greater_than_one_calls_set_leverage(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2004, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02,
        "sequence": 1, "leverage": 5, "position_idx": 2,
    })
    repo = GatewayCommandRepository(ops_db)

    adapter = FakeAdapter()
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    set_leverage_calls = [c for c in adapter.calls if c["action"] == "set_leverage"]
    assert len(set_leverage_calls) == 1
    assert set_leverage_calls[0]["symbol"] == "BTC/USDT"
    assert set_leverage_calls[0]["leverage"] == 5
    assert set_leverage_calls[0]["position_idx"] == 2

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2004"
    ).fetchone()[0]
    conn.close()
    assert status == "SENT"


def test_waiting_partial_tp_resolves_size_from_filled_entry_qty(ops_db):
    """Gateway WAITING_POSITION partial TP calcola tp_size da filled_entry_qty reale."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    conn = sqlite3.connect(ops_db)
    conn.execute("UPDATE ops_trade_chains SET filled_entry_qty=0.006 WHERE trade_chain_id=1")
    conn.commit()
    conn.close()

    payload = {
        "symbol": "BTC/USDT:USDT",
        "side": "SHORT",
        "execution_strategy": "C_MULTI_TP",
        "position_idx": 0,
        "take_profit": 76000.26,
        "tp_qty_mode": "filled_entry_pct",
        "close_pct": 50.0,
        "tp_order_type": "Limit",
        "tp_limit_price": 76000.26,
        "tp_trigger_by": "MarkPrice",
        "preserve_sl": True,
        "sequence": 1,
    }
    _insert_cmd(ops_db, 2003, cmd_type="SET_POSITION_TPSL_PARTIAL", payload=payload)
    repo = GatewayCommandRepository(ops_db)

    adapter = FakeAdapter()
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 2003)
    gw.process(cmd, account_id="acc_1")

    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1
    sent_payload = place_calls[0]["payload"]
    assert abs(float(sent_payload["tp_size"]) - 0.003) < 0.000001
    assert "tp_qty_mode" not in sent_payload
    assert "close_pct" not in sent_payload


def test_deferred_market_no_mark_price_cancels_chain(ops_db):
    """Gateway con deferred_market e nessun mark_price: REVIEW_REQUIRED + chain CANCELLED."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    payload = {
        "symbol": "SOL/USDT", "side": "LONG", "entry_type": "MARKET",
        "qty_mode": "deferred_market", "risk_amount": 10.0, "sl_price": 140.0,
        "leverage": 1, "hedge_mode": False, "position_idx": 0,
        "execution_strategy": "D_POSITION_TPSL", "sequence": 1,
    }
    _insert_cmd(ops_db, 2002, payload=payload)
    repo = GatewayCommandRepository(ops_db)

    adapter = FakeAdapter()  # nessun mark_price configurato
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 0

    conn = sqlite3.connect(ops_db)
    cmd_row = conn.execute(
        "SELECT status, result_payload_json FROM ops_execution_commands WHERE command_id=2002"
    ).fetchone()
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert cmd_row[0] == "REVIEW_REQUIRED"
    assert "deferred_market_no_mark_price" in (cmd_row[1] or "")
    assert chain_state == "CANCELLED"


def test_deferred_market_zero_risk_distance_cancels_chain(ops_db):
    """Mark price == SL price → deferred_market_zero_risk_distance → chain CANCELLED."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    payload = {
        "symbol": "SOL/USDT", "side": "LONG", "entry_type": "MARKET",
        "qty_mode": "deferred_market", "risk_amount": 10.0, "sl_price": 150.0,
        "leverage": 1, "hedge_mode": False, "position_idx": 0,
        "execution_strategy": "D_POSITION_TPSL", "sequence": 1,
    }
    _insert_cmd(ops_db, 2010, payload=payload)
    repo = GatewayCommandRepository(ops_db)

    # mark_price == sl_price → risk_dist == 0
    adapter = FakeAdapter(mark_prices={"SOL/USDT": 150.0})
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": adapter},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    cmd_status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2010"
    ).fetchone()[0]
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert cmd_status == "REVIEW_REQUIRED"
    assert chain_state == "CANCELLED"


def test_adapter_not_found_cancels_entry_chain(ops_db):
    """Adapter non trovato su un PLACE_ENTRY → chain CANCELLED."""
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2011, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={},  # adapter vuoto → adapter_not_found
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    cmd_status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2011"
    ).fetchone()[0]
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert cmd_status == "REVIEW_REQUIRED"
    assert chain_state == "CANCELLED"


def test_capability_missing_on_entry_cancels_chain(ops_db):
    """PLACE_ENTRY_WITH_ATTACHED_TPSL con capability mancante → chain CANCELLED."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2012, cmd_type="PLACE_ENTRY", payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(
            capabilities=AdapterCapabilities(place_entry=False)
        )},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    cmd_status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2012"
    ).fetchone()[0]
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert cmd_status == "REVIEW_REQUIRED"
    assert chain_state == "CANCELLED"


def test_live_trading_gate_does_not_cancel_chain(ops_db):
    """Safety gate live_trading_env_gate_not_set: chain resta WAITING_ENTRY (hold intenzionale)."""
    import yaml
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    raw = yaml.safe_load(open("config/execution.yaml").read())
    raw["execution"]["adapters"]["bybit_demo"]["live_safety"]["allow_live_trading"] = True
    raw["execution"]["adapters"]["bybit_demo"]["mode"] = "live"
    config = ExecutionConfig.model_validate(raw["execution"])

    _insert_cmd(ops_db, 2013, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=config,
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    cmd_status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2013"
    ).fetchone()[0]
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert cmd_status == "REVIEW_REQUIRED"
    assert chain_state == "WAITING_ENTRY"  # safety gate: chain in hold, non cancellata


def test_rebuild_partial_tps_supersedes_older_pending_rebuilds_before_send(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(
        ops_db,
        3001,
        chain_id=1,
        status="PENDING",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )
    _insert_cmd_with_status(
        ops_db,
        3002,
        chain_id=1,
        status="PENDING",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )
    _insert_cmd_with_status(
        ops_db,
        3003,
        chain_id=1,
        status="PENDING",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )

    repo = GatewayCommandRepository(ops_db)
    cmd = repo.get_pending_batch()[-1]  # command 3003 (most recent)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    statuses = {
        r[0]: r[1] for r in conn.execute(
            "SELECT command_id, status FROM ops_execution_commands "
            "WHERE command_id IN (3001, 3002, 3003)"
        ).fetchall()
    }
    conn.close()
    assert statuses[3001] == "SUPERSEDED"
    assert statuses[3002] == "SUPERSEDED"
    assert statuses[3003] == "DONE"


def test_rebuild_partial_tps_does_not_supersede_set_position_tpsl_partial(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(
        ops_db,
        3101,
        chain_id=1,
        status="PENDING",
        cmd_type="SET_POSITION_TPSL_PARTIAL",
        payload={
            "symbol": "BTC/USDT",
            "side": "LONG",
            "take_profit": 70000.0,
            "tp_size": 0.007,
            "tp_order_type": "Limit",
            "tp_limit_price": 70000.0,
            "tp_trigger_by": "MarkPrice",
            "preserve_sl": True,
            "supersedes_previous": True,
        },
    )
    _insert_cmd_with_status(
        ops_db,
        3102,
        chain_id=1,
        status="PENDING",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )

    repo = GatewayCommandRepository(ops_db)
    cmd = repo.get_pending_batch()[-1]
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    statuses = {
        r[0]: r[1] for r in conn.execute(
            "SELECT command_id, status FROM ops_execution_commands "
            "WHERE command_id IN (3101, 3102)"
        ).fetchall()
    }
    conn.close()
    assert statuses[3101] == "PENDING"
    assert statuses[3102] == "DONE"


def test_rebuild_partial_tps_supersedes_older_sent_ack_done_rebuilds_after_success(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(
        ops_db,
        3111,
        chain_id=1,
        status="DONE",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )
    _insert_cmd_with_status(
        ops_db,
        3112,
        chain_id=1,
        status="SENT",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )
    _insert_cmd_with_status(
        ops_db,
        3113,
        chain_id=1,
        status="ACK",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )
    _insert_cmd_with_status(
        ops_db,
        3114,
        chain_id=1,
        status="PENDING",
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )

    repo = GatewayCommandRepository(ops_db)
    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 3114)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    statuses = {
        r[0]: r[1] for r in conn.execute(
            "SELECT command_id, status FROM ops_execution_commands "
            "WHERE command_id IN (3111, 3112, 3113, 3114)"
        ).fetchall()
    }
    conn.close()
    assert statuses[3111] == "SUPERSEDED"
    assert statuses[3112] == "SUPERSEDED"
    assert statuses[3113] == "SUPERSEDED"
    assert statuses[3114] == "DONE"


def test_superseded_tp_partials_are_not_active_for_runtime_reads(ops_db):
    """I reader runtime devono ignorare i TP partial SUPERSEDED."""
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains SET lifecycle_state='OPEN' WHERE trade_chain_id=1"
    )
    conn.commit()
    conn.close()

    _insert_cmd(ops_db, 3201, chain_id=1, cmd_type="SET_POSITION_TPSL_PARTIAL",
                payload={"symbol": "BTC/USDT", "side": "LONG", "take_profit": 70000.0,
                         "tp_size": 0.007, "tp_sequence": 1, "tp_order_type": "Limit",
                         "tp_limit_price": 70000.0, "tp_trigger_by": "MarkPrice",
                         "preserve_sl": True})
    _insert_cmd(ops_db, 3202, chain_id=1, cmd_type="SET_POSITION_TPSL_PARTIAL",
                payload={"symbol": "BTC/USDT", "side": "LONG", "take_profit": 71000.0,
                         "tp_size": 0.003, "tp_sequence": 2, "tp_order_type": "Limit",
                         "tp_limit_price": 71000.0, "tp_trigger_by": "MarkPrice",
                         "preserve_sl": True})
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_execution_commands SET status='SUPERSEDED' WHERE command_id=3201"
    )
    conn.execute(
        "UPDATE ops_execution_commands SET status='DONE' WHERE command_id=3202"
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(ops_db)
    assert repo.count_active_tps(1) == 1
    active = repo.get_active_tp_commands(1)
    assert len(active) == 1
    assert active[0]["take_profit"] == 71000.0


def test_rebuild_partial_tps_are_active_for_runtime_reads(ops_db):
    """I reader runtime devono espandere REBUILD_PARTIAL_TPS in livelli TP attivi."""
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains SET lifecycle_state='OPEN' WHERE trade_chain_id=1"
    )
    conn.commit()
    conn.close()

    _insert_cmd(
        ops_db,
        3210,
        chain_id=1,
        cmd_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "BTC/USDT",
            "side": "LONG",
            "tps": [
                {
                    "sequence": 1,
                    "price": 70000.0,
                    "qty": 0.007,
                    "order_type": "Limit",
                    "limit_price": 70000.0,
                    "trigger_by": "MarkPrice",
                },
                {
                    "sequence": 2,
                    "price": 71000.0,
                    "qty": 0.003,
                    "order_type": "Limit",
                    "limit_price": 71000.0,
                    "trigger_by": "MarkPrice",
                },
            ],
        },
    )
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_execution_commands SET status='DONE' WHERE command_id=3210"
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(ops_db)
    assert repo.count_active_tps(1) == 2
    active = sorted(repo.get_active_tp_commands(1), key=lambda item: item["tp_sequence"])
    assert len(active) == 2
    assert active[0]["take_profit"] == 70000.0
    assert active[1]["take_profit"] == 71000.0


def test_supersede_rebuild_commands_marks_pending_as_superseded(ops_db):
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(
        ops_db, 3301, status="PENDING", cmd_type="REBUILD_PARTIAL_TPS"
    )
    _insert_cmd_with_status(
        ops_db, 3302, status="PENDING", cmd_type="REBUILD_PARTIAL_TPS"
    )
    _insert_cmd_with_status(
        ops_db, 3303, status="DONE", cmd_type="REBUILD_PARTIAL_TPS"
    )

    repo = GatewayCommandRepository(ops_db)
    repo.supersede_rebuild_commands(1, 3302, statuses=("PENDING",))

    conn = sqlite3.connect(ops_db)
    statuses = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT command_id, status FROM ops_execution_commands "
            "WHERE command_id IN (3301, 3302, 3303)"
        ).fetchall()
    }
    conn.close()

    assert statuses[3301] == "SUPERSEDED"
    assert statuses[3302] == "PENDING"
    assert statuses[3303] == "DONE"


def test_supersede_rebuild_commands_does_not_touch_other_command_types(ops_db):
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(
        ops_db, 3311, status="PENDING", cmd_type="REBUILD_PARTIAL_TPS"
    )
    _insert_cmd_with_status(
        ops_db, 3312, status="PENDING", cmd_type="SET_POSITION_TPSL_PARTIAL"
    )

    repo = GatewayCommandRepository(ops_db)
    repo.supersede_rebuild_commands(1, 3311, statuses=("PENDING",))

    conn = sqlite3.connect(ops_db)
    statuses = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT command_id, status FROM ops_execution_commands "
            "WHERE command_id IN (3311, 3312)"
        ).fetchall()
    }
    conn.close()

    assert statuses[3311] == "PENDING"
    assert statuses[3312] == "PENDING"


# ── Fire-and-forget lifecycle events ─────────────────────────────────────────

def _get_exchange_events(db_path: str, chain_id: int) -> list[tuple[str, dict]]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchall()
    conn.close()
    return [(r[0], json.loads(r[1])) for r in rows]


def test_move_stop_to_be_emits_stop_moved_confirmed(ops_db):
    """MOVE_STOP_TO_BREAKEVEN con retCode=0 → STOP_MOVED_CONFIRMED con is_breakeven=True."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5001, cmd_type="MOVE_STOP_TO_BREAKEVEN", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 50000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert len(events) == 1
    assert events[0][0] == "STOP_MOVED_CONFIRMED"
    assert events[0][1]["is_breakeven"] is True
    assert events[0][1]["new_stop_price"] == 50000.0
    assert events[0][1]["command_id"] == 5001

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5001"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"


def test_move_stop_emits_stop_moved_confirmed_not_breakeven(ops_db):
    """MOVE_STOP con retCode=0 → STOP_MOVED_CONFIRMED con is_breakeven=False."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5002, cmd_type="MOVE_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 48000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert len(events) == 1
    assert events[0][0] == "STOP_MOVED_CONFIRMED"
    assert events[0][1]["is_breakeven"] is False
    assert events[0][1]["new_stop_price"] == 48000.0


def test_move_position_stop_emits_stop_moved_confirmed(ops_db):
    """MOVE_POSITION_STOP con retCode=0 → STOP_MOVED_CONFIRMED con is_breakeven=False."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5006, cmd_type="MOVE_POSITION_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 47000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert len(events) == 1
    assert events[0][0] == "STOP_MOVED_CONFIRMED"
    assert events[0][1]["is_breakeven"] is False
    assert events[0][1]["new_stop_price"] == 47000.0


def test_fire_and_forget_failed_does_not_emit_event(ops_db):
    """Se place_order() fallisce, nessun evento lifecycle viene inserito."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5004, cmd_type="MOVE_STOP_TO_BREAKEVEN", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 50000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(fail_on={"MOVE_STOP_TO_BREAKEVEN"})},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert events == [], f"Expected no events on failure, got {events}"

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5004"
    ).fetchone()[0]
    conn.close()
    assert status == "FAILED"


def test_set_tpsl_does_not_emit_direct_event(ops_db):
    """SET_POSITION_TPSL_PARTIAL non emette eventi lifecycle diretti.
    Il suo hit è rilevato separatamente da watchMyTrades/polling."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    conn = sqlite3.connect(ops_db)
    conn.execute("UPDATE ops_trade_chains SET filled_entry_qty=0.01 WHERE trade_chain_id=1")
    conn.commit()
    conn.close()

    _insert_cmd(ops_db, 5005, cmd_type="SET_POSITION_TPSL_PARTIAL", payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "take_profit": 70000.0, "tp_size": 0.005,
        "tp_sequence": 1, "tp_order_type": "Limit",
        "tp_limit_price": 70000.0, "tp_trigger_by": "MarkPrice",
        "preserve_sl": True,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert events == [], f"SET_POSITION_TPSL_PARTIAL non deve emettere eventi diretti, got {events}"

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5005"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"


def test_rebuild_partial_tps_does_not_emit_direct_event(ops_db):
    """REBUILD_PARTIAL_TPS è fire-and-forget ma non emette eventi lifecycle diretti."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(
        ops_db,
        5007,
        cmd_type="REBUILD_PARTIAL_TPS",
        payload=_rebuild_partial_tps_payload(),
    )
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert events == [], f"REBUILD_PARTIAL_TPS non deve emettere eventi diretti, got {events}"

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5007"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"


def test_run_trade_based_reconciliation_no_price_matching(ops_db):
    """trade-based reconciliation uses symbol+side correlation, not price matching.

    A fill price very far from any expected TP price (would fail ±1% check) must
    still produce a TP_FILLED event because price matching has been removed.
    """
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # Set chain to OPEN state with a filled entry so it qualifies for TP commands.
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains SET lifecycle_state='OPEN', filled_entry_qty=0.01 "
        "WHERE trade_chain_id=1"
    )
    conn.commit()
    conn.close()

    # Insert a SET_POSITION_TPSL_PARTIAL command (SENT) for chain 1.
    tp_payload = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "take_profit": 70000.0,  # expected TP price
        "tp_size": 0.005,
        "tp_sequence": 1,
        "tp_order_type": "Limit",
        "tp_limit_price": 70000.0,
        "tp_trigger_by": "MarkPrice",
        "preserve_sl": True,
    }
    _insert_cmd_with_status(
        ops_db, 6001,
        status="SENT",
        chain_id=1,
        cmd_type="SET_POSITION_TPSL_PARTIAL",
        payload=tp_payload,
    )

    repo = GatewayCommandRepository(ops_db)
    adapter = FakeAdapter()

    # Register a fill at a VERY different price (50% away — would fail ±1% check).
    # With price matching removed this must still be attributed to the chain.
    adapter.simulate_reduce_trade(
        symbol="BTC/USDT",
        side="LONG",
        price=35000.0,  # 50% below 70000 — intentionally extreme
        amount=0.005,
        trade_id="trade_abc",
    )

    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc_1",
    )
    count = worker.run_trade_based_reconciliation()

    assert count == 1, f"Expected 1 TP_FILLED inserted, got {count}"

    events = _get_exchange_events(ops_db, chain_id=1)
    tp_events = [e for e in events if e[0] == "TP_FILLED"]
    assert len(tp_events) == 1, f"Expected 1 TP_FILLED event, got {tp_events}"
    assert tp_events[0][1]["fill_price"] == 35000.0
    assert tp_events[0][1]["source"] == "trade_based_reconciliation"

    # Second call must be idempotent (no duplicate insert).
    count2 = worker.run_trade_based_reconciliation()
    assert count2 == 0, "Second run must be idempotent — 0 new events expected"


def test_deferred_market_qty_above_exchange_max_becomes_signal_rejected(ops_db):
    """Computed deferred MARKET qty above exchange max suppresses ACCEPTED and emits SIGNAL_REJECTED."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains "
        "SET plan_state_json=?, risk_snapshot_json=?, source_chat_id=?, telegram_message_id=? "
        "WHERE trade_chain_id=1",
        (
            json.dumps({
                "stop_loss": 140.0,
                "final_tp": 170.0,
                "intermediate_tps": [],
                "legs": [
                    {"sequence": 1, "entry_type": "MARKET", "price": 150.0, "status": "PENDING"},
                ],
            }),
            json.dumps({"capital": 1000.0, "risk_amount": 10.0}),
            "-1001234567890",
            456,
        ),
    )
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (1, 'SIGNAL_ACCEPTED', 'enrichment', '{}', 'sigacc:1', datetime('now'))"
    )
    project_clean_log_for_chain(conn, 1)
    conn.commit()
    conn.close()

    _insert_cmd(ops_db, 2010, cmd_type="PLACE_ENTRY_WITH_ATTACHED_TPSL", payload={
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_type": "MARKET",
        "sequence": 1,
        "qty_mode": "deferred_market",
        "risk_amount": 10.0,
        "sl_price": 140.0,
        "attached_tpsl": {"mode": "FULL", "take_profit": 170.0, "stop_loss": 140.0},
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(
            mark_prices={"BTC/USDT": 150.0},
            max_order_qty={"BTC/USDT": 0.5},
        )},
        repo=repo,
    )

    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 2010)
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    cmd_row = conn.execute(
        "SELECT status, result_payload_json FROM ops_execution_commands WHERE command_id=2010"
    ).fetchone()
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    event_types = [
        row[0] for row in conn.execute(
            "SELECT event_type FROM ops_lifecycle_events WHERE trade_chain_id=1 ORDER BY event_id"
        ).fetchall()
    ]
    outbox_rows = conn.execute(
        "SELECT notification_type, status, payload_json "
        "FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    conn.close()

    assert cmd_row[0] == "FAILED"
    assert "computed_qty_exceeds_exchange_max" in (cmd_row[1] or "")
    assert chain_state == "CANCELLED"
    assert event_types[-1] == "SIGNAL_REJECTED"
    assert outbox_rows[0][0] == "SIGNAL_ACCEPTED"
    assert outbox_rows[0][1] == "SUPPRESSED"
    assert outbox_rows[1][0] == "SIGNAL_REJECTED"
    reject_payload = json.loads(outbox_rows[1][2])
    assert reject_payload["reason"] == "computed_qty_exceeds_exchange_max"


def test_bybit_delisting_entry_failure_becomes_signal_rejected(ops_db):
    """Bybit delisting rejects pre-fill entry as SIGNAL_REJECTED, not entry_all_failed."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import AdapterResult
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    class DelistingAdapter(FakeAdapter):
        def place_order(
            self,
            *,
            command_type: str,
            payload: dict,
            client_order_id: str,
            execution_account_id: str,
            connector: str,
        ) -> AdapterResult:
            self.calls.append({
                "action": "place_order",
                "command_type": command_type,
                "client_order_id": client_order_id,
                "payload": payload,
            })
            return AdapterResult(
                success=False,
                error="retCode=30228: No new positions during delisting.",
                reason="retCode=30228: No new positions during delisting.",
            )

    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains "
        "SET plan_state_json=?, risk_snapshot_json=?, source_chat_id=?, telegram_message_id=? "
        "WHERE trade_chain_id=1",
        (
            json.dumps({
                "stop_loss": 1.63,
                "final_tp": 1.77,
                "intermediate_tps": [],
                "legs": [
                    {"sequence": 1, "entry_type": "MARKET", "price": 1.66, "status": "PENDING"},
                ],
            }),
            json.dumps({"capital": 10000.0, "risk_amount": 200.0}),
            "-1001234567890",
            456,
        ),
    )
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (1, 'SIGNAL_ACCEPTED', 'enrichment', '{}', 'sigacc:1', datetime('now'))"
    )
    project_clean_log_for_chain(conn, 1)
    conn.commit()
    conn.close()

    _insert_cmd(ops_db, 2011, cmd_type="PLACE_ENTRY_WITH_ATTACHED_TPSL", payload={
        "symbol": "TONUSDT",
        "side": "LONG",
        "entry_type": "MARKET",
        "sequence": 1,
        "qty": 6666.666666666661,
        "leverage": 5,
        "hedge_mode": True,
        "position_idx": 1,
        "attached_tpsl": {"mode": "FULL", "take_profit": 1.77, "stop_loss": 1.63},
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo_1": DelistingAdapter()},
        repo=repo,
    )

    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 2011)
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    cmd_row = conn.execute(
        "SELECT status, result_payload_json FROM ops_execution_commands WHERE command_id=2011"
    ).fetchone()
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    event_types = [
        row[0] for row in conn.execute(
            "SELECT event_type FROM ops_lifecycle_events WHERE trade_chain_id=1 ORDER BY event_id"
        ).fetchall()
    ]
    outbox_rows = conn.execute(
        "SELECT notification_type, status, payload_json "
        "FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    conn.close()

    assert cmd_row[0] == "FAILED"
    assert "30228" in (cmd_row[1] or "")
    assert chain_state == "CANCELLED"
    assert event_types[-1] == "SIGNAL_REJECTED"
    assert outbox_rows[0][0] == "SIGNAL_ACCEPTED"
    assert outbox_rows[0][1] == "SUPPRESSED"
    assert outbox_rows[1][0] == "SIGNAL_REJECTED"
    assert outbox_rows[2][0] == "GATEWAY_COMMAND_FAILED"
    reject_payload = json.loads(outbox_rows[1][2])
    assert reject_payload["reason"] == "retCode=30228: No new positions during delisting."
    tech_payload = json.loads(outbox_rows[2][2])
    assert tech_payload["chain_id"] == 1
    assert tech_payload["command_type"] == "PLACE_ENTRY_WITH_ATTACHED_TPSL"


def test_place_entry_failure_projects_gateway_command_failed_tech_log(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2101, cmd_type="PLACE_ENTRY", payload={
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_type": "LIMIT",
        "price": 50000.0,
        "qty": 0.02,
        "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo_1": FakeAdapter(fail_on={"PLACE_ENTRY"})},
        repo=repo,
    )

    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 2101)
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox "
        "WHERE destination='TECH_LOG' AND notification_type='GATEWAY_COMMAND_FAILED' "
        "ORDER BY notification_id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    payload = json.loads(row[1])
    assert row[0] == "GATEWAY_COMMAND_FAILED"
    assert payload["command_id"] == 2101
    assert payload["command_type"] == "PLACE_ENTRY"
    assert payload["chain_id"] == 1
    assert payload["trader_id"] == "trader_a"
    assert payload["execution_account_id"] == "demo_1"


def test_attached_entry_failure_can_cancel_subsequent_pending_entries(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _update_chain_management_plan(
        ops_db,
        1,
        {"cancel_subsequent_on_anchor_failure": True},
    )
    _insert_cmd(ops_db, 2201, cmd_type="PLACE_ENTRY_WITH_ATTACHED_TPSL", payload={
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_type": "LIMIT",
        "price": 50000.0,
        "qty": 0.02,
        "sequence": 1,
        "attached_tpsl": {
            "mode": "FULL",
            "take_profit": 51000.0,
            "stop_loss": 49000.0,
        },
    })
    _insert_cmd(ops_db, 2202, cmd_type="PLACE_ENTRY", payload={
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_type": "LIMIT",
        "price": 48000.0,
        "qty": 0.03,
        "sequence": 2,
    })

    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo_1": FakeAdapter(fail_on={"PLACE_ENTRY_WITH_ATTACHED_TPSL"})},
        repo=repo,
    )

    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 2201)
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT command_id, status FROM ops_execution_commands WHERE command_id IN (2201, 2202) ORDER BY command_id"
    ).fetchall()
    chain_state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert rows == [(2201, "FAILED"), (2202, "CANCELLED")]
    assert chain_state == "CANCELLED"


def test_review_required_projects_gateway_review_required_with_context(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2102, cmd_type="CLOSE_PARTIAL", payload={
        "symbol": "BTC/USDT",
        "side": "LONG",
        "qty": 0.01,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(
            capabilities=AdapterCapabilities(close_partial=False)
        )},
        repo=repo,
    )

    cmd = next(c for c in repo.get_pending_batch() if c.command_id == 2102)
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox "
        "WHERE destination='TECH_LOG' AND notification_type='GATEWAY_REVIEW_REQUIRED' "
        "ORDER BY notification_id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    payload = json.loads(row[1])
    assert row[0] == "GATEWAY_REVIEW_REQUIRED"
    assert payload["command_id"] == 2102
    assert payload["command_type"] == "CLOSE_PARTIAL"
    assert payload["chain_id"] == 1
    assert payload["trader_id"] == "trader_a"
    assert payload["execution_account_id"] == "demo_1"
