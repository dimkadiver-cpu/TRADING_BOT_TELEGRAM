from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_build_execution_runtime_enables_ws_watcher(monkeypatch, tmp_path):
    import main as app_main

    logger = logging.getLogger("test")
    adapter = MagicMock()
    execution_worker = MagicMock()
    sync_worker = MagicMock()
    watcher = MagicMock()

    adapter_cfg = SimpleNamespace(
        type="ccxt_bybit",
        api_key="key123",
        testnet=True,
        websocket=SimpleNamespace(
            enabled=True,
            poll_fallback_enabled=True,
            poll_fallback_period_seconds=45,
        ),
    )
    routing = SimpleNamespace(execution_account_id="master_account")
    exec_config = SimpleNamespace(
        default_adapter="bybit_demo",
        resolve_routing=lambda account_id: (routing, adapter_cfg),
    )

    monkeypatch.setattr(
        app_main,
        "ExecutionConfigLoader",
        lambda path: SimpleNamespace(load=lambda: exec_config),
    )
    monkeypatch.setattr(app_main, "build_adapter", lambda name, cfg: adapter)
    monkeypatch.setattr(
        app_main,
        "GatewayCommandRepository",
        lambda db_path: MagicMock(name="gateway_repo"),
    )
    monkeypatch.setattr(
        app_main,
        "ExecutionGateway",
        lambda **kwargs: MagicMock(name="gateway"),
    )
    monkeypatch.setattr(
        app_main,
        "ExecutionCommandWorker",
        lambda **kwargs: execution_worker,
    )
    monkeypatch.setattr(
        app_main,
        "ExchangeEventSyncWorker",
        lambda **kwargs: sync_worker,
    )
    monkeypatch.setattr(
        app_main,
        "BybitWsFillWatcher",
        lambda **kwargs: watcher,
    )
    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_DEMO", "secret123")

    runtime = app_main._build_execution_runtime(
        root_dir=tmp_path,
        ops_db_path=str(tmp_path / "ops.sqlite3"),
        logger=logger,
    )

    assert runtime is not None
    assert runtime.adapter is adapter
    assert runtime.execution_worker is execution_worker
    assert runtime.sync_worker is sync_worker
    assert runtime.ws_watcher is watcher
    assert runtime.reconciliation_interval_seconds == 45
    watcher.start.assert_called_once_with()


def test_close_execution_runtime_stops_watcher_and_closes_adapter():
    import main as app_main

    adapter = MagicMock()
    watcher = MagicMock()
    runtime = app_main.ExecutionRuntime(
        adapter=adapter,
        execution_worker=MagicMock(),
        sync_worker=MagicMock(),
        ws_watcher=watcher,
        reconciliation_interval_seconds=60,
    )

    app_main._close_execution_runtime(runtime)

    watcher.stop.assert_called_once_with()
    adapter.close.assert_called_once_with()


def test_run_reconciliation_periodically_uses_configured_interval():
    import main as app_main

    sync_worker = MagicMock()
    sleeps: list[int] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError()

    original_sleep = app_main.asyncio.sleep
    app_main.asyncio.sleep = fake_sleep
    try:
        try:
            asyncio.run(
                app_main._run_reconciliation_periodically(
                    sync_worker=sync_worker,
                    interval_seconds=45,
                    logger=logging.getLogger("test"),
                )
            )
        except asyncio.CancelledError:
            pass
    finally:
        app_main.asyncio.sleep = original_sleep

    assert sleeps == [45]
    sync_worker.run_reconciliation.assert_not_called()
