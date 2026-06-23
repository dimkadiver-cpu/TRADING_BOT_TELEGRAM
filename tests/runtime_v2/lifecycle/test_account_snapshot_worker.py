# tests/runtime_v2/lifecycle/test_account_snapshot_worker.py
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.runtime_v2.lifecycle.account_snapshot_worker import AccountSnapshotWorker
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot


def _make_snapshot(account_id="demo_1", status="OK"):
    return AccountStateSnapshot(
        account_id=account_id,
        equity_usdt=1000.0,
        captured_at=datetime.now(timezone.utc),
        source="ccxt_bybit:demo",
        snapshot_status=status,
    )


def _make_port(account_id="demo_1", raise_exc=None):
    port = MagicMock()
    if raise_exc:
        port.get_account_state.side_effect = raise_exc
    else:
        port.get_account_state.return_value = _make_snapshot(account_id)
    return port


def _make_repo():
    return MagicMock()


@pytest.mark.asyncio
async def test_worker_calls_fetch_for_each_account_on_startup():
    port = MagicMock()
    port.get_account_state.side_effect = lambda acc: _make_snapshot(acc)
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1", "demo_2"],
        interval_seconds=999,
    )
    # Run one iteration manually
    await worker._fetch_all()
    assert port.get_account_state.call_count == 2
    assert repo.save_account.call_count == 2


@pytest.mark.asyncio
async def test_worker_saves_failed_record_on_exception():
    port = _make_port(raise_exc=RuntimeError("timeout"))
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
    )
    await worker._fetch_one("demo_1")
    assert repo.save_account.called
    saved_snap = repo.save_account.call_args[0][0]
    assert saved_snap.snapshot_status == "FAILED"
    assert saved_snap.error_code == "RuntimeError"


@pytest.mark.asyncio
async def test_worker_account_a_failure_does_not_stop_account_b():
    port = MagicMock()
    port.get_account_state.side_effect = lambda acc: (
        (_ for _ in ()).throw(RuntimeError("fail")) if acc == "demo_1"
        else _make_snapshot(acc)
    )
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1", "demo_2"],
        interval_seconds=999,
    )
    await worker._fetch_all()
    # demo_2 should still be saved
    saved_accounts = [call[0][1] for call in repo.save_account.call_args_list]
    assert "demo_2" in saved_accounts


@pytest.mark.asyncio
async def test_worker_trigger_deduplicates_same_account():
    port = _make_port(account_id="acc1")
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=[],
        interval_seconds=999,
    )
    # trigger same account twice before the loop drains
    worker.trigger("acc1")
    worker.trigger("acc1")
    # drain manually: drain pending and fetch
    pending = list(worker._pending_refresh)
    worker._pending_refresh.clear()
    for account_id in pending:
        await worker._fetch_one(account_id)
    # should have been fetched exactly once despite two trigger() calls
    assert port.get_account_state.call_count == 1
