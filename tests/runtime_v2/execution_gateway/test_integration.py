# tests/runtime_v2/execution_gateway/test_integration.py
"""
Acceptance contract per PRD-05.
Verifica i criteri pass/fail del design definitivo usando FakeAdapter.
"""
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
    default_payload = {
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    }
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, status,
         json.dumps(payload or default_payload), f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def _make_stack(ops_db, adapter=None):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    adapter = adapter or FakeAdapter()
    config = ExecutionConfigLoader("config/execution.yaml").load()
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=config,
        adapter_registry={"hummingbot_api_paper": adapter},
        repo=repo,
    )
    worker = ExecutionCommandWorker(ops_db_path=ops_db, gateway=gw, repo=repo)
    sync = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter, repo=repo,
        execution_account_id="bybit_paper_main",
    )
    return worker, sync, adapter


# AC1: PLACE_ENTRY passa PENDING → SENT
def test_ac1_place_entry_pending_to_sent(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1001)
    worker, _, _ = _make_stack(ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1001"
    ).fetchone()[0]
    conn.close()
    assert status == "SENT"


# AC2: capability mancante → REVIEW_REQUIRED
def test_ac2_capability_missing_review_required(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1002, cmd_type="PLACE_PROTECTIVE_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "stop_price": 49000.0, "qty": 0.02, "reduce_only": True,
    })
    adapter = FakeAdapter(capabilities=AdapterCapabilities(protective_stop_native=False))
    worker, _, _ = _make_stack(ops_db, adapter)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1002"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


# AC3: doppio run non reinvia (idempotenza)
def test_ac3_double_run_no_resend(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1003)
    adapter = FakeAdapter()
    worker, _, _ = _make_stack(ops_db, adapter)
    worker.run_once()
    worker.run_once()
    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1


# AC4: adapter timeout → retry
def test_ac4_timeout_sets_retry(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1004)
    adapter = FakeAdapter(simulate_timeout=True)
    worker, _, _ = _make_stack(ops_db, adapter)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    retry_count = conn.execute(
        "SELECT retry_count FROM ops_execution_commands WHERE command_id=1004"
    ).fetchone()[0]
    conn.close()
    assert retry_count == 1


# AC5: fill entry → ops_exchange_events ENTRY_FILLED
def test_ac5_fill_produces_entry_filled_event(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1005)
    worker, sync, adapter = _make_stack(ops_db)
    worker.run_once()
    adapter.simulate_fill("tsb:1:1005:entry:1", price=50050.0, qty=0.02)
    sync.run_once()
    conn = sqlite3.connect(ops_db)
    events = conn.execute(
        "SELECT event_type FROM ops_exchange_events"
    ).fetchall()
    conn.close()
    assert any(e[0] == "ENTRY_FILLED" for e in events)


# AC6: TP WAITING_POSITION su chain non-OPEN rimane in waiting
def test_ac6_tp_waiting_position_before_fill(ops_db):
    _insert_chain(ops_db)  # state=WAITING_ENTRY (not OPEN)
    _insert_cmd(ops_db, 2001, cmd_type="PLACE_TAKE_PROFIT", status="WAITING_POSITION",
                payload={"symbol": "BTC/USDT", "side": "LONG",
                         "tp_sequence": 1, "price": 51000.0,
                         "close_pct": 50.0, "reduce_only": True})
    worker, _, _ = _make_stack(ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2001"
    ).fetchone()[0]
    conn.close()
    assert status == "WAITING_POSITION"


# AC7: live trading bloccato
def test_ac7_live_trading_blocked(ops_db):
    import yaml
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    raw = yaml.safe_load(open("config/execution.yaml").read())
    raw["execution"]["adapters"]["hummingbot_api_paper"]["mode"] = "live"
    config = ExecutionConfig.model_validate(raw["execution"])

    _insert_chain(ops_db)
    _insert_cmd(ops_db, 3001)
    adapter = FakeAdapter()
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(config=config,
                          adapter_registry={"hummingbot_api_paper": adapter}, repo=repo)
    worker = ExecutionCommandWorker(ops_db_path=ops_db, gateway=gw, repo=repo)
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=3001"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


# AC8: nessun import Hummingbot nel package execution_gateway
def test_ac8_no_hummingbot_import_in_gateway():
    import importlib
    import pkgutil
    import src.runtime_v2.execution_gateway as pkg
    for _, name, _ in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        if "hummingbot_api_paper" in name:
            continue
        try:
            mod = importlib.import_module(name)
            src_file = getattr(mod, "__file__", "") or ""
            src_code = open(src_file, encoding="utf-8").read() if src_file else ""
            assert "hummingbot" not in src_code.lower(), \
                f"{src_file} contains hummingbot import"
        except ImportError:
            pass
