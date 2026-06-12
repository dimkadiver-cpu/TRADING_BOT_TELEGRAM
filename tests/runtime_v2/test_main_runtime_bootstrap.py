from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_build_execution_runtime_enables_ws_watcher(monkeypatch, tmp_path):
    import main as app_main

    logger = logging.getLogger("test")
    adapter = MagicMock()
    execution_worker = MagicMock()
    sync_worker = MagicMock()
    watcher = MagicMock()

    adapter_cfg = SimpleNamespace(
        type="ccxt_bybit",
        mode="demo",
        api_key_env="BYBIT_API_KEY_BYBIT_DEMO",
        api_secret_env="BYBIT_API_SECRET_BYBIT_DEMO",
        websocket=SimpleNamespace(
            enabled=True,
            poll_fallback_enabled=True,
            poll_fallback_period_seconds=45,
            position_reconciliation_interval_seconds=600,
        ),
    )
    routing = SimpleNamespace(execution_account_id="master_account")
    exec_config = SimpleNamespace(
        default_adapter="bybit_demo",
        account_routing={},
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
    watcher_kwargs = {}

    def fake_ws_watcher(**kwargs):
        watcher_kwargs.update(kwargs)
        return watcher

    monkeypatch.setattr(app_main, "BybitWsFillWatcher", fake_ws_watcher)
    monkeypatch.setenv("BYBIT_API_KEY_BYBIT_DEMO", "key123")
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
    assert watcher_kwargs["api_key"] == "key123"
    assert watcher_kwargs["api_secret"] == "secret123"
    assert watcher_kwargs["testnet"] is False
    assert watcher_kwargs["mode"] == "demo"
    watcher.start.assert_called_once_with()


def test_build_execution_runtime_creates_sync_workers_and_watchers_for_all_routed_accounts(
    monkeypatch, tmp_path
):
    import main as app_main

    logger = logging.getLogger("test")
    default_adapter = MagicMock(name="default_adapter")
    routed_adapter = MagicMock(name="routed_adapter")
    built_adapters = {
        "bybit_demo": default_adapter,
        "bybit_nuovo": routed_adapter,
    }
    sync_workers: list[dict] = []
    watcher_calls: list[dict] = []
    watchers: list[MagicMock] = []

    default_cfg = SimpleNamespace(
        type="ccxt_bybit",
        mode="demo",
        api_key_env="BYBIT_API_KEY_BYBIT_DEMO",
        api_secret_env="BYBIT_API_SECRET_BYBIT_DEMO",
        websocket=SimpleNamespace(
            enabled=True,
            poll_fallback_enabled=True,
            poll_fallback_period_seconds=45,
            position_reconciliation_interval_seconds=600,
        ),
    )
    routed_cfg = SimpleNamespace(
        type="ccxt_bybit",
        mode="demo",
        api_key_env="BYBIT_API_KEY_ACCOUNT_NUOVO",
        api_secret_env="BYBIT_API_SECRET_ACCOUNT_NUOVO",
        websocket=SimpleNamespace(
            enabled=True,
            poll_fallback_enabled=True,
            poll_fallback_period_seconds=60,
            position_reconciliation_interval_seconds=900,
        ),
    )
    default_routing = SimpleNamespace(adapter="bybit_demo", execution_account_id="main")
    routed_routing = SimpleNamespace(adapter="bybit_nuovo", execution_account_id="account_nuovo")
    exec_config = SimpleNamespace(
        default_adapter="bybit_demo",
        adapters={
            "bybit_demo": default_cfg,
            "bybit_nuovo": routed_cfg,
        },
        account_routing={
            "default": default_routing,
            "main": default_routing,
            "account_nuovo": routed_routing,
        },
        resolve_routing=lambda account_id: (
            default_routing if account_id == "default" else routed_routing,
            default_cfg if account_id == "default" else routed_cfg,
        ),
    )

    monkeypatch.setattr(
        app_main,
        "ExecutionConfigLoader",
        lambda path: SimpleNamespace(load=lambda: exec_config),
    )
    monkeypatch.setattr(app_main, "build_adapter", lambda name, cfg: built_adapters[name])
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
        lambda **kwargs: MagicMock(name="execution_worker"),
    )

    def fake_sync_worker(**kwargs):
        sync_workers.append(kwargs)
        worker = MagicMock(name=f"sync_{kwargs['execution_account_id']}")
        worker.execution_account_id = kwargs["execution_account_id"]
        return worker

    monkeypatch.setattr(app_main, "ExchangeEventSyncWorker", fake_sync_worker)

    def fake_ws_watcher(**kwargs):
        watcher_calls.append(kwargs)
        watcher = MagicMock(name=f"watcher_{len(watcher_calls)}")
        watchers.append(watcher)
        return watcher

    monkeypatch.setattr(app_main, "BybitWsFillWatcher", fake_ws_watcher)
    monkeypatch.setenv("BYBIT_API_KEY_BYBIT_DEMO", "demo_key")
    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_DEMO", "demo_secret")
    monkeypatch.setenv("BYBIT_API_KEY_ACCOUNT_NUOVO", "nuovo_key")
    monkeypatch.setenv("BYBIT_API_SECRET_ACCOUNT_NUOVO", "nuovo_secret")

    runtime = app_main._build_execution_runtime(
        root_dir=tmp_path,
        ops_db_path=str(tmp_path / "ops.sqlite3"),
        logger=logger,
    )

    assert runtime is not None
    assert runtime.sync_workers.keys() == {"main", "account_nuovo"}
    assert runtime.ws_watchers.keys() == {"main", "account_nuovo"}
    assert runtime.reconciliation_intervals == {
        "main": 45,
        "account_nuovo": 60,
    }
    assert runtime.position_reconciliation_intervals == {
        "main": 600,
        "account_nuovo": 900,
    }
    assert [call["execution_account_id"] for call in sync_workers] == ["main", "account_nuovo"]
    assert [call["mode"] for call in watcher_calls] == ["demo", "demo"]
    assert [call["api_key"] for call in watcher_calls] == ["demo_key", "nuovo_key"]
    for watcher in watchers:
        watcher.start.assert_called_once_with()


def test_linux_build_execution_runtime_loads_routed_adapters(monkeypatch, tmp_path):
    import main_linux_server as app_main

    logger = logging.getLogger("test")
    default_adapter = MagicMock(name="default_adapter")
    routed_adapter = MagicMock(name="routed_adapter")
    gateway_kwargs = {}

    adapter_cfg = SimpleNamespace(
        type="ccxt_bybit",
        mode="demo",
        api_key_env="BYBIT_API_KEY_BYBIT_DEMO",
        api_secret_env="BYBIT_API_SECRET_BYBIT_DEMO",
        websocket=SimpleNamespace(
            enabled=False,
            poll_fallback_enabled=False,
            poll_fallback_period_seconds=45,
            position_reconciliation_interval_seconds=600,
        ),
    )
    routing = SimpleNamespace(execution_account_id="master_account")
    exec_config = SimpleNamespace(
        default_adapter="bybit_demo",
        adapters={
            "bybit_demo": SimpleNamespace(),
            "bybit_night": SimpleNamespace(),
        },
        account_routing={
            "night": SimpleNamespace(adapter="bybit_night"),
        },
        resolve_routing=lambda account_id: (routing, adapter_cfg),
    )

    monkeypatch.setattr(
        app_main,
        "ExecutionConfigLoader",
        lambda path: SimpleNamespace(load=lambda: exec_config),
    )

    def fake_build_adapter(name, cfg):
        return default_adapter if name == "bybit_demo" else routed_adapter

    monkeypatch.setattr(app_main, "build_adapter", fake_build_adapter)
    monkeypatch.setattr(
        app_main,
        "GatewayCommandRepository",
        lambda db_path: MagicMock(name="gateway_repo"),
    )

    def fake_execution_gateway(**kwargs):
        gateway_kwargs.update(kwargs)
        return MagicMock(name="gateway")

    monkeypatch.setattr(app_main, "ExecutionGateway", fake_execution_gateway)
    monkeypatch.setattr(
        app_main,
        "ExecutionCommandWorker",
        lambda **kwargs: MagicMock(name="execution_worker"),
    )
    monkeypatch.setattr(
        app_main,
        "ExchangeEventSyncWorker",
        lambda **kwargs: MagicMock(name="sync_worker"),
    )

    runtime = app_main._build_execution_runtime(
        root_dir=tmp_path,
        ops_db_path=str(tmp_path / "ops.sqlite3"),
        logger=logger,
    )

    assert runtime is not None
    assert gateway_kwargs["adapter_registry"] == {
        "bybit_demo": default_adapter,
        "bybit_night": routed_adapter,
    }


def test_linux_async_main_passes_text_pattern_catalog(monkeypatch, tmp_path):
    import main_linux_server as app_main

    captured = {}

    class DummyPatternCatalog:
        def __init__(self, path):
            captured["pattern_catalog_path"] = Path(path)

        @property
        def all_trader_ids(self):
            return {"pattern_trader"}

    class StopBootstrap(Exception):
        pass

    monkeypatch.setattr(app_main, "setup_logging", lambda **kwargs: logging.getLogger("test"))
    monkeypatch.setattr(app_main, "apply_migrations", lambda **kwargs: 0)
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abcde")
    monkeypatch.setattr(app_main, "load_channels_config", lambda path: SimpleNamespace(channels=[]))
    monkeypatch.setattr(app_main, "build_ingestion_service", lambda **kwargs: MagicMock(name="ingestion"))
    monkeypatch.setattr(app_main, "build_processing_status_store", lambda **kwargs: MagicMock(name="status"))
    monkeypatch.setattr(app_main, "RawMessageRepository", lambda **kwargs: MagicMock(name="raw_repo"))
    monkeypatch.setattr(app_main, "ChannelConfigResolver", lambda **kwargs: MagicMock(name="channel_resolver"))
    monkeypatch.setattr(app_main, "CanonicalMessageRepository", lambda **kwargs: MagicMock(name="canonical_repo"))
    monkeypatch.setattr(app_main.sqlite3, "connect", lambda *args, **kwargs: MagicMock(name="sqlite_conn"))
    monkeypatch.setattr(app_main, "ParserRunStore", lambda conn: SimpleNamespace(create_run=lambda **kwargs: 1))
    monkeypatch.setattr(app_main, "ParserResultV2Store", lambda conn: MagicMock(name="result_v2_store"))
    monkeypatch.setattr(app_main, "ParserPipelineProcessor", lambda **kwargs: MagicMock(name="parser_pipeline"))
    monkeypatch.setattr(app_main, "SignalEnrichmentProcessor", lambda **kwargs: MagicMock(name="enrichment_processor"))
    monkeypatch.setattr(app_main, "OperationConfigLoader", lambda path: MagicMock(name="config_loader"))
    monkeypatch.setattr(app_main, "EnrichedCanonicalMessageRepository", lambda *args, **kwargs: MagicMock(name="enriched_repo"))
    monkeypatch.setattr(app_main, "TextPatternCatalog", DummyPatternCatalog, raising=False)

    def fake_trader_resolver(**kwargs):
        captured["trader_resolver_kwargs"] = kwargs
        raise StopBootstrap

    monkeypatch.setattr(app_main, "TraderResolver", fake_trader_resolver)

    with pytest.raises(StopBootstrap):
        asyncio.run(
            app_main._async_main(
                parser_db_path=str(tmp_path / "parser.sqlite3"),
                migrations_dir=str(tmp_path / "migrations"),
                ops_db_path=str(tmp_path / "ops.sqlite3"),
                ops_migrations_dir=str(tmp_path / "ops_migrations"),
                log_path=str(tmp_path / "bot.log"),
                root_dir=tmp_path,
            )
        )

    assert captured["pattern_catalog_path"] == tmp_path / "config" / "text_patterns.yaml"
    assert captured["trader_resolver_kwargs"]["pattern_catalog"].all_trader_ids == {"pattern_trader"}


def test_build_lifecycle_entry_gate_uses_simple_attached_strategy(monkeypatch, tmp_path):
    import main as app_main

    adapter_cfg = SimpleNamespace(
        strategy=SimpleNamespace(
            default_mode="D_POSITION_TPSL",
            simple_attached_enabled=True,
        )
    )
    routing = SimpleNamespace(execution_account_id="master_account")
    exec_config = SimpleNamespace(
        resolve_routing=lambda account_id: (routing, adapter_cfg),
    )

    monkeypatch.setattr(
        app_main,
        "ExecutionConfigLoader",
        lambda path: SimpleNamespace(load=lambda: exec_config),
    )

    gate = app_main._build_lifecycle_entry_gate(
        root_dir=tmp_path,
        risk_engine=MagicMock(),
        exchange_port=MagicMock(),
    )

    assert gate._simple_attached_enabled is True


def test_build_exchange_port_uses_live_port_when_runtime_has_adapters(monkeypatch, tmp_path):
    import main as app_main
    from src.runtime_v2.execution_gateway.models import (
        AccountRoutingEntry,
        AdapterConfig,
        ExecutionConfig,
    )
    from src.runtime_v2.lifecycle.live_exchange_data_port import LiveExchangeDataPort

    exec_config = ExecutionConfig(
        default_adapter="bybit_demo",
        account_routing={
            "default": AccountRoutingEntry(adapter="bybit_demo", execution_account_id="main"),
        },
        adapters={"bybit_demo": AdapterConfig(type="ccxt_bybit", mode="demo", connector="bybit")},
    )
    monkeypatch.setattr(
        app_main,
        "ExecutionConfigLoader",
        lambda path: SimpleNamespace(load=lambda: exec_config),
    )

    runtime = app_main.ExecutionRuntime(
        adapter=MagicMock(name="default_adapter"),
        execution_worker=MagicMock(),
        sync_worker=MagicMock(),
        ws_watcher=None,
        reconciliation_interval_seconds=None,
        adapters={"bybit_demo": MagicMock(name="default_adapter")},
    )

    port = app_main._build_exchange_port(
        root_dir=tmp_path,
        execution_runtime=runtime,
        known_symbols=frozenset({"BTC/USDT:USDT"}),
    )

    assert isinstance(port, LiveExchangeDataPort)


def test_build_exchange_port_falls_back_to_static_port_without_runtime(tmp_path):
    import main as app_main
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    port = app_main._build_exchange_port(
        root_dir=tmp_path,
        execution_runtime=None,
        known_symbols=frozenset({"BTC/USDT:USDT"}),
    )

    assert isinstance(port, StaticExchangeDataPort)


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


def test_close_execution_runtime_stops_all_watchers_and_closes_all_adapters():
    import main as app_main

    default_adapter = MagicMock(name="default_adapter")
    routed_adapter = MagicMock(name="routed_adapter")
    main_watcher = MagicMock(name="main_watcher")
    routed_watcher = MagicMock(name="routed_watcher")
    runtime = app_main.ExecutionRuntime(
        adapter=default_adapter,
        execution_worker=MagicMock(),
        sync_worker=MagicMock(),
        ws_watcher=main_watcher,
        reconciliation_interval_seconds=45,
        sync_workers={
            "main": MagicMock(name="sync_main"),
            "account_nuovo": MagicMock(name="sync_nuovo"),
        },
        ws_watchers={
            "main": main_watcher,
            "account_nuovo": routed_watcher,
        },
        adapters={
            "bybit_demo": default_adapter,
            "bybit_nuovo": routed_adapter,
        },
        reconciliation_intervals={"main": 45, "account_nuovo": 60},
        position_reconciliation_intervals={"main": 600, "account_nuovo": 900},
        poll_fallback_by_account={"main": True, "account_nuovo": True},
    )

    app_main._close_execution_runtime(runtime)

    main_watcher.stop.assert_called_once_with()
    routed_watcher.stop.assert_called_once_with()
    default_adapter.close.assert_called_once_with()
    routed_adapter.close.assert_called_once_with()


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


def test_execution_runtime_has_position_reconciliation_interval():
    from main import ExecutionRuntime
    from unittest.mock import MagicMock
    rt = ExecutionRuntime(
        adapter=MagicMock(),
        execution_worker=MagicMock(),
        sync_worker=MagicMock(),
        ws_watcher=None,
        reconciliation_interval_seconds=None,
        position_reconciliation_interval_seconds=120,
        poll_fallback_enabled=False,
    )
    assert rt.position_reconciliation_interval_seconds == 120
    assert rt.poll_fallback_enabled is False


def test_collect_runtime_known_symbols_unions_all_adapters():
    import main as app_main

    adapter_a = MagicMock(name="adapter_a")
    adapter_b = MagicMock(name="adapter_b")
    adapter_a.load_known_symbols.return_value = frozenset({"BTC/USDT:USDT", "ETH/USDT:USDT"})
    adapter_b.load_known_symbols.return_value = frozenset({"HOME/USDT:USDT"})

    runtime = app_main.ExecutionRuntime(
        adapter=adapter_a,
        execution_worker=MagicMock(),
        sync_worker=MagicMock(),
        ws_watcher=None,
        reconciliation_interval_seconds=None,
        adapters={
            "bybit_demo": adapter_a,
            "bybit_nuovo": adapter_b,
        },
    )

    assert app_main._collect_runtime_known_symbols(runtime, logging.getLogger("test")) == frozenset(
        {"BTC/USDT:USDT", "ETH/USDT:USDT", "HOME/USDT:USDT"}
    )


def test_collect_runtime_known_symbols_ignores_none_and_failures():
    import main as app_main

    adapter_ok = MagicMock(name="adapter_ok")
    adapter_none = MagicMock(name="adapter_none")
    adapter_fail = MagicMock(name="adapter_fail")
    adapter_ok.load_known_symbols.return_value = frozenset({"BTC/USDT:USDT"})
    adapter_none.load_known_symbols.return_value = None
    adapter_fail.load_known_symbols.side_effect = RuntimeError("boom")

    runtime = app_main.ExecutionRuntime(
        adapter=adapter_ok,
        execution_worker=MagicMock(),
        sync_worker=MagicMock(),
        ws_watcher=None,
        reconciliation_interval_seconds=None,
        adapters={
            "ok": adapter_ok,
            "none": adapter_none,
            "fail": adapter_fail,
        },
    )

    assert app_main._collect_runtime_known_symbols(runtime, logging.getLogger("test")) == frozenset(
        {"BTC/USDT:USDT"}
    )


def test_websocket_config_exposes_position_reconciliation_interval():
    from src.runtime_v2.execution_gateway.models import WebsocketConfig
    cfg = WebsocketConfig(position_reconciliation_interval_seconds=120)
    assert cfg.position_reconciliation_interval_seconds == 120


def test_websocket_config_default_position_reconciliation_interval():
    from src.runtime_v2.execution_gateway.models import WebsocketConfig
    cfg = WebsocketConfig()
    assert cfg.position_reconciliation_interval_seconds == 600
