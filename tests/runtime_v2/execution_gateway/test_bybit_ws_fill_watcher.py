from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for file in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(file.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path) -> str:
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _insert_command(
    db_path: str,
    *,
    command_id: int,
    trade_chain_id: int,
    command_type: str,
    status: str,
    client_order_id: str | None,
    payload: dict | None = None,
) -> None:
    now = "2026-05-19T00:00:00+00:00"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            command_id,
            trade_chain_id,
            command_type,
            status,
            json.dumps(payload or {}),
            f"idem:{command_id}",
            client_order_id,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def _wait_until(assertion, timeout: float = 1.5) -> None:
    deadline = time.time() + timeout
    last_error: AssertionError | None = None
    while time.time() < deadline:
        try:
            assertion()
            return
        except AssertionError as exc:
            last_error = exc
            time.sleep(0.02)
    if last_error is not None:
        raise last_error
    raise AssertionError("condition was not met before timeout")


def test_get_active_client_order_ids_returns_only_sent_and_ack(ops_db):
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_command(
        ops_db,
        command_id=1,
        trade_chain_id=10,
        command_type="PLACE_ENTRY",
        status="SENT",
        client_order_id="tsb:10:1:entry:1",
    )
    _insert_command(
        ops_db,
        command_id=2,
        trade_chain_id=10,
        command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
        status="ACK",
        client_order_id="tsb:10:2:tp:1",
    )
    _insert_command(
        ops_db,
        command_id=3,
        trade_chain_id=10,
        command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
        status="DONE",
        client_order_id="tsb:10:3:sl:1",
    )
    _insert_command(
        ops_db,
        command_id=4,
        trade_chain_id=10,
        command_type="PLACE_ENTRY",
        status="SENT",
        client_order_id=None,
    )

    repo = GatewayCommandRepository(ops_db)

    assert repo.get_active_client_order_ids() == {
        "tsb:10:1:entry:1",
        "tsb:10:2:tp:1",
    }




def _assert_exchange_event_count(db_path: str, *, expected: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        actual = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    finally:
        conn.close()
    assert actual == expected


def _assert_thread_stopped(watcher) -> None:
    assert watcher._thread is not None
    assert not watcher._thread.is_alive()


# ── wake_callback tests ───────────────────────────────────────────────────────

def _make_watcher_with_callback(ops_db, wake_callback=None):
    """Costruisce BybitWsFillWatcher con callback iniettato e repo reale."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    return BybitWsFillWatcher(
        api_key="key",
        api_secret="secret",
        testnet=True,
        ops_db_path=ops_db,
        repo=repo,
        wake_callback=wake_callback,
    )


def _insert_chain_open(db_path: str, chain_id: int = 1) -> None:
    import sqlite3
    now = "2026-01-01T00:00:00+00:00"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(source_enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id, "
        "symbol, side, lifecycle_state, entry_mode, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, 100 + chain_id, 200 + chain_id, "trader_a", "main", "BTCUSDT", "LONG",
         "OPEN", "TWO_STEP", now, now),
    )
    conn.commit()
    conn.close()


def test_ws_fill_payload_preserves_exec_fee(ops_db):
    """insert_raw_and_classified payload must include exec_fee from ExchangeRawEvent."""
    from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
    from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # Seed an open chain so classification can find a chain to attribute the fill to
    conn = sqlite3.connect(ops_db)
    now = "2026-05-31T00:00:00+00:00"
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (10, 1, 1, 1, "t", "main", "BTC/USDT", "LONG", "OPEN", "ONE_SHOT", "{}", "{}", "{}", now, now),
    )
    # Seed the order_link_id so classifier can attribute it
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (77, 10, "SET_POSITION_TPSL_PARTIAL", "SENT", "{}", "idem-77", "tsb:main:10:tp:1:77", now, now),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(ops_db)

    # Build a raw event that should match the order_link_id for classification
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id="exec-001",
        idempotency_key="idem-001",
        symbol="BTC/USDT",
        side="Sell",
        create_type=None,
        stop_order_type=None,
        exec_type="Trade",
        order_status="Filled",
        order_link_id="tsb:main:10:tp:1:77",
        order_id="oid-001",
        seq=None,
        exec_price=68000.0,
        exec_qty=0.002,
        closed_size=0.002,
        leaves_qty=0.0,
        pos_qty=0.008,
        exec_value=136.0,
        exec_fee=1.10,
        fee_rate=0.0001,
        cum_exec_qty=0.002,
    )

    classifier = EventClassifier(known_order_link_ids=repo.get_known_order_link_ids())
    classified = classifier.classify(raw)

    repo.insert_raw_and_classified(classified)

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=10"
    ).fetchone()
    conn.close()
    assert row is not None, "No event inserted"
    payload = json.loads(row[0])
    assert payload["exec_fee"] == 1.10
    assert payload["fill_price"] == 68000.0
    assert payload["filled_qty"] == 0.002
    assert payload["closed_size"] == 0.002


def test_ws_funding_event_resolves_raw_symbol_chain_and_forwards_to_lifecycle(ops_db):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
    from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_chain_open(ops_db, chain_id=21)
    conn = sqlite3.connect(ops_db)
    expected_chain_id = conn.execute(
        "SELECT trade_chain_id FROM ops_trade_chains WHERE symbol='BTCUSDT' AND side='LONG'"
    ).fetchone()[0]
    conn.close()
    repo = GatewayCommandRepository(ops_db)
    watcher = BybitWsFillWatcher(
        api_key="key",
        api_secret="secret",
        testnet=True,
        ops_db_path=ops_db,
        repo=repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id="funding-001",
        idempotency_key="funding-001",
        symbol="BTCUSDT",
        side="Buy",
        create_type=None,
        stop_order_type=None,
        exec_type="Funding",
        order_status=None,
        order_link_id=None,
        order_id=None,
        seq=None,
        exec_price=None,
        exec_qty=None,
        closed_size=None,
        leaves_qty=None,
        pos_qty=0.01,
        exec_value=None,
        exec_fee=0.07628025,
        fee_rate=None,
        cum_exec_qty=None,
    )

    watcher._process_batch([{"id": "funding-001"}], lambda _: raw)

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT trade_chain_id, event_type, payload_json "
        "FROM ops_exchange_events WHERE event_type='FUNDING_SETTLED'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == expected_chain_id
    assert row[1] == "FUNDING_SETTLED"
    assert json.loads(row[2])["exec_fee"] == 0.07628025


def test_build_exchange_does_not_filter_out_funding_executions(ops_db):
    # ccxt.pro bybit defaults filterExecTypes to ['Trade','AdlTrade','BustTrade','Settle'],
    # which silently drops execType=Funding from watch_my_trades. The watcher must
    # override it, otherwise FUNDING_SETTLED never reaches the lifecycle.
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher as ws_mod
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    repo = GatewayCommandRepository(ops_db)
    watcher = ws_mod.BybitWsFillWatcher(
        api_key="key",
        api_secret="secret",
        testnet=True,
        ops_db_path=ops_db,
        repo=repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )
    fake_ccxtpro = MagicMock()
    with patch.object(ws_mod, "ccxtpro", fake_ccxtpro):
        watcher._build_exchange()

    config = fake_ccxtpro.bybit.call_args[0][0]
    filter_exec_types = config["options"]["watchMyTrades"]["filterExecTypes"]
    assert "Funding" in filter_exec_types
    assert "Trade" in filter_exec_types


def test_ws_funding_event_unresolvable_chain_logs_warning(ops_db, caplog):
    """Funding execution with no (or ambiguous) open chain must log a WARNING,
    not disappear silently. The event is still persisted with NULL chain for audit."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
    from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # No open chain inserted: resolution must fail.
    repo = GatewayCommandRepository(ops_db)
    watcher = BybitWsFillWatcher(
        api_key="key",
        api_secret="secret",
        testnet=True,
        ops_db_path=ops_db,
        repo=repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id="funding-orphan-1",
        idempotency_key="funding-orphan-1",
        symbol="BTCUSDT",
        side="Buy",
        create_type=None,
        stop_order_type=None,
        exec_type="Funding",
        order_status=None,
        order_link_id=None,
        order_id=None,
        seq=None,
        exec_price=None,
        exec_qty=None,
        closed_size=None,
        leaves_qty=None,
        pos_qty=0.01,
        exec_value=None,
        exec_fee=0.07628025,
        fee_rate=None,
        cum_exec_qty=None,
    )

    with caplog.at_level(
        "WARNING",
        logger="src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher",
    ):
        watcher._process_batch([{"id": "funding-orphan-1"}], lambda _: raw)

    assert any(
        "funding" in rec.message.lower() and rec.levelname == "WARNING"
        for rec in caplog.records
    ), "expected a WARNING about unattributable funding execution"


def test_ws_funding_event_resolves_chain_for_own_account(ops_db):
    """Two chains on the same symbol+side but different accounts: the watcher,
    knowing its own account, must attribute funding to its account's chain
    instead of dropping the event as ambiguous."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
    from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_chain_open(ops_db, chain_id=31)              # account "main"
    # Second chain, same symbol+side, different account
    conn = sqlite3.connect(ops_db)
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (32, 32, 132, 232, "trader_b", "account_nuovo", "BTCUSDT", "LONG",
         "OPEN", "TWO_STEP", now, now),
    )
    conn.commit()
    main_chain_id = conn.execute(
        "SELECT trade_chain_id FROM ops_trade_chains WHERE account_id='main'"
    ).fetchone()[0]
    conn.close()

    repo = GatewayCommandRepository(ops_db)
    watcher = BybitWsFillWatcher(
        api_key="key",
        api_secret="secret",
        testnet=True,
        ops_db_path=ops_db,
        repo=repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
        account_id="main",
    )
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id="funding-acc-1",
        idempotency_key="funding-acc-1",
        symbol="BTCUSDT",
        side="Buy",
        create_type=None,
        stop_order_type=None,
        exec_type="Funding",
        order_status=None,
        order_link_id=None,
        order_id=None,
        seq=None,
        exec_price=None,
        exec_qty=None,
        closed_size=None,
        leaves_qty=None,
        pos_qty=0.01,
        exec_value=None,
        exec_fee=0.05,
        fee_rate=None,
        cum_exec_qty=None,
    )

    watcher._process_batch([{"id": "funding-acc-1"}], lambda _: raw)

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT trade_chain_id FROM ops_exchange_events WHERE event_type='FUNDING_SETTLED'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == main_chain_id
