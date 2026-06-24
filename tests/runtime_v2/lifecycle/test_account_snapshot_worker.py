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


@pytest.mark.asyncio
async def test_fetch_all_skip_avoids_double_fetch():
    """_fetch_all(skip=...) must not fetch accounts in the skip set."""
    port = MagicMock()
    port.get_account_state.side_effect = lambda acc: _make_snapshot(acc)
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["acc1", "acc2", "acc3"],
        interval_seconds=999,
    )
    # acc1 was already fetched this cycle (e.g. via pending drain)
    await worker._fetch_all(skip={"acc1"})
    fetched = [call[0][0] for call in port.get_account_state.call_args_list]
    assert "acc1" not in fetched
    assert "acc2" in fetched
    assert "acc3" in fetched
    assert port.get_account_state.call_count == 2


@pytest.mark.asyncio
async def test_run_no_double_fetch_on_global_refresh():
    """When all accounts are in pending, _fetch_all should make 0 additional calls."""
    port = MagicMock()
    port.get_account_state.side_effect = lambda acc: _make_snapshot(acc)
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["acc1", "acc2"],
        interval_seconds=999,
    )
    # Simulate one loop iteration manually (no asyncio.sleep)
    pending = ["acc1", "acc2"]
    for account_id in pending:
        await worker._fetch_one(account_id)
    just_fetched = set(pending)
    await worker._fetch_all(skip=just_fetched)
    # All accounts were in skip set — total calls should be exactly 2
    assert port.get_account_state.call_count == 2


# ---------------------------------------------------------------------------
# Task 5: on_snapshot_saved callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_callback_called_on_ok_snapshot():
    port = _make_port(account_id="demo_1")   # returns snapshot with status="OK"
    repo = _make_repo()
    called_with: list[str] = []
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=lambda acc_id: called_with.append(acc_id),
    )
    await worker._fetch_one("demo_1")
    assert called_with == ["demo_1"]


@pytest.mark.asyncio
async def test_callback_not_called_on_failed_snapshot():
    port = _make_port(account_id="demo_1")
    port.get_account_state.return_value = _make_snapshot("demo_1", status="FAILED")
    repo = _make_repo()
    called_with: list[str] = []
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=lambda acc_id: called_with.append(acc_id),
    )
    await worker._fetch_one("demo_1")
    assert called_with == []


@pytest.mark.asyncio
async def test_callback_not_called_on_port_exception():
    port = _make_port(raise_exc=RuntimeError("network error"))
    repo = _make_repo()
    called_with: list[str] = []
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=lambda acc_id: called_with.append(acc_id),
    )
    await worker._fetch_one("demo_1")
    assert called_with == []


@pytest.mark.asyncio
async def test_callback_error_does_not_crash_worker():
    port = _make_port(account_id="demo_1")
    repo = _make_repo()
    def _bad_callback(acc_id: str) -> None:
        raise ValueError("oops")
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=_bad_callback,
    )
    # Must not raise
    await worker._fetch_one("demo_1")
