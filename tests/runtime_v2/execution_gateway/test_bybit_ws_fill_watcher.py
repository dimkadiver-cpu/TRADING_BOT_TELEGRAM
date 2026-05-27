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


