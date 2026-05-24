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


def test_watcher_persists_fill_for_active_order_and_sets_testnet_sandbox(ops_db):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
        BybitWsFillWatcher,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_command(
        ops_db,
        command_id=1,
        trade_chain_id=10,
        command_type="PLACE_ENTRY",
        status="SENT",
        client_order_id="tsb:10:1:entry:1",
    )
    repo = GatewayCommandRepository(ops_db)
    filled_order = {
        "clientOrderId": "tsb:10:1:entry:1",
        "id": "exchange-order-123",
        "status": "closed",
        "filled": 0.01,
        "average": 50000.0,
    }

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.ccxtpro"
    ) as mock_ccxtpro:
        mock_exchange = MagicMock()
        mock_exchange.set_sandbox_mode = MagicMock()
        mock_exchange.close = AsyncMock()
        watch_results = iter(([filled_order], asyncio.CancelledError()))

        async def mock_watch_orders():
            result = next(watch_results)
            if isinstance(result, BaseException):
                raise result
            return result

        mock_exchange.watch_orders = mock_watch_orders
        # _watch_trades_forever also calls watch_my_trades — make it an AsyncMock
        # that raises CancelledError so the trades task exits cleanly alongside orders.
        mock_exchange.watch_my_trades = AsyncMock(side_effect=asyncio.CancelledError())
        mock_ccxtpro.bybit.return_value = mock_exchange

        watcher = BybitWsFillWatcher(
            api_key="key",
            api_secret="secret",
            testnet=True,
            ops_db_path=ops_db,
            repo=repo,
        )
        watcher.start()
        _wait_until(
            lambda: _assert_exchange_event_count(ops_db, expected=1),
        )
        watcher.stop()

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT trade_chain_id, event_type, payload_json "
        "FROM ops_exchange_events"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 10
    assert row[1] == "ENTRY_FILLED"
    payload = json.loads(row[2])
    assert payload["fill_price"] == 50000.0
    assert payload["filled_qty"] == 0.01
    assert payload["command_id"] == 1
    # Two exchange instances are built (one per parallel task), so set_sandbox_mode
    # is called once per instance — assert it was called with True at least once.
    mock_exchange.set_sandbox_mode.assert_called_with(True)
    assert mock_exchange.close.await_count >= 1


def test_watcher_discards_unknown_order(ops_db):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
        BybitWsFillWatcher,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    repo = GatewayCommandRepository(ops_db)
    unknown_order = {
        "clientOrderId": "unknown-bybit-order",
        "id": "exchange-order-123",
        "status": "closed",
        "filled": 1.0,
        "average": 100.0,
    }

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.ccxtpro"
    ) as mock_ccxtpro:
        mock_exchange = MagicMock()
        mock_exchange.set_sandbox_mode = MagicMock()
        mock_exchange.close = AsyncMock()
        watch_results = iter(([unknown_order], asyncio.CancelledError()))

        async def mock_watch_orders():
            result = next(watch_results)
            if isinstance(result, BaseException):
                raise result
            return result

        mock_exchange.watch_orders = mock_watch_orders
        # _watch_trades_forever also calls watch_my_trades — make it an AsyncMock
        # that raises CancelledError so the trades task exits cleanly alongside orders.
        mock_exchange.watch_my_trades = AsyncMock(side_effect=asyncio.CancelledError())
        mock_ccxtpro.bybit.return_value = mock_exchange

        watcher = BybitWsFillWatcher(
            api_key="key",
            api_secret="secret",
            testnet=True,
            ops_db_path=ops_db,
            repo=repo,
        )
        watcher.start()
        _wait_until(lambda: _assert_thread_stopped(watcher))
        watcher.stop()

    _assert_exchange_event_count(ops_db, expected=0)


def test_watcher_is_idempotent_on_duplicate_fill(ops_db):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
        BybitWsFillWatcher,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_command(
        ops_db,
        command_id=1,
        trade_chain_id=10,
        command_type="PLACE_ENTRY",
        status="ACK",
        client_order_id="tsb:10:1:entry:1",
    )
    repo = GatewayCommandRepository(ops_db)
    filled_order = {
        "clientOrderId": "tsb:10:1:entry:1",
        "id": "exchange-order-123",
        "status": "closed",
        "filled": 0.01,
        "average": 50000.0,
    }

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.ccxtpro"
    ) as mock_ccxtpro:
        mock_exchange = MagicMock()
        mock_exchange.set_sandbox_mode = MagicMock()
        mock_exchange.close = AsyncMock()
        watch_results = iter(([filled_order], [filled_order], asyncio.CancelledError()))

        async def mock_watch_orders():
            result = next(watch_results)
            if isinstance(result, BaseException):
                raise result
            return result

        mock_exchange.watch_orders = mock_watch_orders
        mock_ccxtpro.bybit.return_value = mock_exchange

        watcher = BybitWsFillWatcher(
            api_key="key",
            api_secret="secret",
            testnet=True,
            ops_db_path=ops_db,
            repo=repo,
        )
        watcher.start()
        _wait_until(
            lambda: _assert_exchange_event_count(ops_db, expected=1),
        )
        watcher.stop()

    _assert_exchange_event_count(ops_db, expected=1)


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
