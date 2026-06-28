"""TeleSignalBot entrypoint — runtime_v2 stack."""

from __future__ import annotations

import truststore
truststore.inject_into_ssl()

import argparse
import asyncio
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from src.core.logger import setup_logging
from src.core.migrations import apply_migrations
from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor
from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, LifecycleGateWorker
from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
from src.runtime_v2.lifecycle.repositories import (
    ControlStateRepository,
    ExchangeEventRepository,
    ExecutionCommandRepository,
    LifecycleEventRepository,
    SnapshotRepository,
    TradeChainRepository,
)
from src.runtime_v2.lifecycle.live_exchange_data_port import LiveExchangeDataPort
from src.runtime_v2.lifecycle.ports import ExchangeDataPort
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
from src.runtime_v2.lifecycle.workers import LifecycleEventWorker, TimeoutWorker
from src.runtime_v2.lifecycle.account_snapshot_worker import AccountSnapshotWorker
from src.runtime_v2.lifecycle.unfilled_price_watcher import UnfilledPriceWatcher
from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
from src.runtime_v2.execution_gateway.adapter_context import AdapterExecutionContext
from src.runtime_v2.control_plane.bootstrap import build_control_plane
from src.runtime_v2.control_plane.notification_dispatcher import TelegramNotificationDispatcher
from src.runtime_v2.control_plane.outbox_writer import notify_listener_edit_skipped
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.telegram_bot import TelegramControlBot
from src.storage.raw_message_revisions import RawMessageRevisionStore
from src.storage.parser_results_v2 import ParserResultV2Store
from src.storage.parser_runs import ParserRunStore
from src.telegram.channel_config import ChannelConfigWatcher, load_channels_config
from src.telegram.listener import (
    TelegramListener,
    build_ingestion_service,
    build_processing_status_store,
)
from src.telegram.pattern_extractors import TextPatternCatalog
from src.telegram.trader_resolver import TraderResolver


@dataclass
class ExecutionRuntime:
    adapter: object
    execution_worker: ExecutionCommandWorker
    sync_worker: ExchangeEventSyncWorker
    ws_watcher: BybitWsFillWatcher | None
    reconciliation_interval_seconds: int | None
    adapters: dict[str, object] | None = None
    sync_workers: dict[str, ExchangeEventSyncWorker] | None = None
    ws_watchers: dict[str, BybitWsFillWatcher] | None = None
    reconciliation_intervals: dict[str, int] | None = None
    position_live_snapshot_intervals: dict[str, int] | None = None
    poll_fallback_by_account: dict[str, bool] | None = None
    position_live_snapshot_interval_seconds: int = 600
    poll_fallback_enabled: bool = True
    adapter_contexts: dict[str, AdapterExecutionContext] | None = None


async def _wait_any(*events: asyncio.Event) -> None:
    """Ritorna appena uno qualsiasi degli eventi viene settato."""
    tasks = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_fallback_chat_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    values: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if token:
            values.add(int(token))
    return values


def _collect_runtime_known_symbols(
    runtime: ExecutionRuntime | None,
    logger,
) -> frozenset[str] | None:
    if runtime is None:
        return None
    adapters = runtime.adapters or {"default": runtime.adapter}
    merged: set[str] = set()
    any_loaded = False
    for adapter_name, adapter in adapters.items():
        if not hasattr(adapter, "load_known_symbols"):
            continue
        try:
            symbols = adapter.load_known_symbols()
        except Exception:
            logger.warning("load_known_symbols failed for adapter %s", adapter_name)
            continue
        if symbols is None:
            continue
        merged.update(symbols)
        any_loaded = True
    if not any_loaded:
        return None
    return frozenset(merged)


def _build_execution_runtime(
    *,
    root_dir: Path,
    ops_db_path: str,
    logger,
    wake_callback: Callable[[], None] | None = None,
) -> ExecutionRuntime | None:
    execution_config_path = str(root_dir / "config" / "execution.yaml")
    exec_config = ExecutionConfigLoader(execution_config_path).load()
    adapter_name = exec_config.default_adapter
    routing, adapter_cfg = exec_config.resolve_routing("default")
    adapter = build_adapter(adapter_name, adapter_cfg)

    # Build all adapters referenced in account_routing so the gateway can route per-account.
    adapter_registry = {adapter_name: adapter}
    for route in exec_config.account_routing.values():
        if route.adapter not in adapter_registry:
            try:
                adapter_registry[route.adapter] = build_adapter(
                    route.adapter, exec_config.adapters[route.adapter]
                )
                logger.info("loaded adapter: %s", route.adapter)
            except Exception:
                logger.warning("adapter '%s' failed to load — commands routed to it will fail", route.adapter)

    gateway_repo = GatewayCommandRepository(ops_db_path)
    gateway = ExecutionGateway(
        config=exec_config,
        adapter_registry=adapter_registry,
        repo=gateway_repo,
    )
    execution_worker = ExecutionCommandWorker(
        ops_db_path=ops_db_path,
        gateway=gateway,
        repo=gateway_repo,
    )
    sync_workers: dict[str, ExchangeEventSyncWorker] = {}
    ws_watchers: dict[str, BybitWsFillWatcher] = {}
    reconciliation_intervals: dict[str, int] = {}
    position_live_snapshot_intervals: dict[str, int] = {}
    poll_fallback_by_account: dict[str, bool] = {}
    account_adapter_map: dict[str, str] = {}   # account_id → adapter_name
    adapter_cfg_map: dict[str, object] = {}    # adapter_name → AdapterConfig

    route_keys = ["default", *[k for k in exec_config.account_routing.keys() if k != "default"]]

    # --- Pass 1: build sync_workers ---
    for route_key in route_keys:
        route_cfg, route_adapter_cfg = exec_config.resolve_routing(route_key)
        account_id = route_cfg.execution_account_id
        if account_id in sync_workers:
            continue
        route_adapter_name = getattr(route_cfg, "adapter", None)
        if route_adapter_name is None:
            route_adapter_name = getattr(exec_config.account_routing.get(route_key), "adapter", None)
        if route_adapter_name is None:
            route_adapter_name = adapter_name
        route_adapter = adapter_registry[route_adapter_name]
        sync_worker = ExchangeEventSyncWorker(
            ops_db_path=ops_db_path,
            adapter=route_adapter,
            repo=gateway_repo,
            execution_account_id=account_id,
            wake_callback=wake_callback,
        )
        sync_workers[account_id] = sync_worker
        account_adapter_map[account_id] = route_adapter_name
        adapter_cfg_map[route_adapter_name] = route_adapter_cfg
        poll_fallback_by_account[account_id] = route_adapter_cfg.websocket.poll_fallback_enabled
        position_live_snapshot_intervals[account_id] = (
            route_adapter_cfg.websocket.position_live_snapshot_interval_seconds
        )

    # --- Build one AdapterExecutionContext per adapter ---
    adapter_to_accounts: dict[str, list[str]] = {}
    for acc_id, adp_name in account_adapter_map.items():
        adapter_to_accounts.setdefault(adp_name, []).append(acc_id)

    adapter_contexts: dict[str, AdapterExecutionContext] = {}
    for adp_name, acc_ids in adapter_to_accounts.items():
        adp_cfg = adapter_cfg_map[adp_name]
        workers = [sync_workers[a] for a in acc_ids]

        def _make_recon(ws=workers):
            def _recon():
                for w in ws:
                    w.run_reconciliation()
            return _recon

        def _make_pos_recon(ws=workers):
            def _pos_recon():
                for w in ws:
                    # trade_based must run before bulk so that real_close_fill_exists()
                    # finds TP_FILLED and skips the redundant synthetic CLOSE_FULL_FILLED.
                    w.run_trade_based_reconciliation()
                    w.run_bulk_position_sync()
                    w.run_protective_orders_reconciliation()
                    w.run_funding_reconciliation()
            return _pos_recon

        ctx = AdapterExecutionContext(
            adp_name,
            reconciliation_fn=_make_recon(),
            position_reconciliation_fn=_make_pos_recon(),
            poll_fallback_enabled=adp_cfg.websocket.poll_fallback_enabled,
            poll_fallback_period_seconds=float(adp_cfg.websocket.poll_fallback_period_seconds),
            position_reconciliation_interval_seconds=float(
                adp_cfg.websocket.position_live_snapshot_interval_seconds
            ),
        )
        adapter_contexts[adp_name] = ctx

    # --- Pass 2: build and start ws_watchers (contexts exist now) ---
    for route_key in route_keys:
        route_cfg, route_adapter_cfg = exec_config.resolve_routing(route_key)
        account_id = route_cfg.execution_account_id
        if account_id in ws_watchers:
            continue
        if route_adapter_cfg.type != "ccxt_bybit" or not route_adapter_cfg.websocket.enabled:
            continue
        route_adapter_name = account_adapter_map.get(account_id, adapter_name)
        ctx = adapter_contexts.get(route_adapter_name)
        sw = sync_workers[account_id]

        recon_cb = (
            (lambda c=ctx, w=sw: c.submit(w.run_reconciliation))
            if ctx is not None
            else sw.run_reconciliation
        )

        api_key = (
            os.environ.get(route_adapter_cfg.api_key_env or "")
            if route_adapter_cfg.api_key_env else ""
        )
        api_secret = (
            os.environ.get(route_adapter_cfg.api_secret_env or "")
            if route_adapter_cfg.api_secret_env else ""
        )
        testnet = bool(
            getattr(route_adapter_cfg, "testnet", False)
            or route_adapter_cfg.mode == "testnet"
        )
        normalizer = EventNormalizer()
        classifier = EventClassifier(known_order_link_ids={})
        ws_watcher = BybitWsFillWatcher(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            ops_db_path=ops_db_path,
            repo=gateway_repo,
            normalizer=normalizer,
            classifier=classifier,
            reconciliation_callback=recon_cb,
            mode=route_adapter_cfg.mode,
            wake_callback=wake_callback,
            account_id=account_id,
        )
        ws_watcher.start()
        ws_watchers[account_id] = ws_watcher
        if route_adapter_cfg.websocket.poll_fallback_enabled:
            reconciliation_intervals[account_id] = (
                route_adapter_cfg.websocket.poll_fallback_period_seconds
            )

    # Start all adapter contexts
    for ctx in adapter_contexts.values():
        ctx.start()

    sync_worker = sync_workers[routing.execution_account_id]
    ws_watcher_default = ws_watchers.get(routing.execution_account_id)
    reconciliation_interval_seconds = reconciliation_intervals.get(routing.execution_account_id)

    logger.info(
        "execution gateway started | adapter=%s | account=%s",
        adapter_name, routing.execution_account_id,
    )
    return ExecutionRuntime(
        adapter=adapter,
        execution_worker=execution_worker,
        sync_worker=sync_worker,
        ws_watcher=ws_watcher_default,
        reconciliation_interval_seconds=reconciliation_interval_seconds,
        adapters=adapter_registry,
        sync_workers=sync_workers,
        ws_watchers=ws_watchers,
        reconciliation_intervals=reconciliation_intervals,
        position_live_snapshot_intervals=position_live_snapshot_intervals,
        poll_fallback_by_account=poll_fallback_by_account,
        position_live_snapshot_interval_seconds=adapter_cfg.websocket.position_live_snapshot_interval_seconds,
        poll_fallback_enabled=adapter_cfg.websocket.poll_fallback_enabled,
        adapter_contexts=adapter_contexts,
    )


def _build_lifecycle_entry_gate(
    *,
    root_dir: Path,
    risk_engine: RiskCapacityEngine,
    exchange_port: ExchangeDataPort,
) -> LifecycleEntryGate:
    execution_config_path = str(root_dir / "config" / "execution.yaml")
    exec_config = ExecutionConfigLoader(execution_config_path).load()
    _, adapter_cfg = exec_config.resolve_routing("default")
    strategy = adapter_cfg.strategy
    return LifecycleEntryGate(
        risk_engine=risk_engine,
        exchange_port=exchange_port,
        simple_attached_enabled=strategy.simple_attached_enabled,
    )


def _build_exchange_port(
    *,
    root_dir: Path,
    ops_db_path: str,
    execution_runtime: ExecutionRuntime | None,
    known_symbols: frozenset[str] | None,
):
    if execution_runtime is None or not execution_runtime.adapters:
        return StaticExchangeDataPort(known_symbols=known_symbols)
    execution_config_path = str(root_dir / "config" / "execution.yaml")
    exec_config = ExecutionConfigLoader(execution_config_path).load()
    return LiveExchangeDataPort(
        execution_config=exec_config,
        adapter_registry=execution_runtime.adapters,
        ops_db_path=ops_db_path,
        known_symbols=known_symbols,
    )


def _close_execution_runtime(runtime: ExecutionRuntime | None) -> None:
    if runtime is None:
        return
    # Stop adapter contexts first — no new REST calls will be submitted
    for ctx in (runtime.adapter_contexts or {}).values():
        ctx.stop()
    # Stop WS watchers
    stopped_watchers: set[int] = set()
    for watcher in (runtime.ws_watchers or {}).values():
        if id(watcher) in stopped_watchers:
            continue
        watcher.stop()
        stopped_watchers.add(id(watcher))
    if runtime.ws_watcher is not None and id(runtime.ws_watcher) not in stopped_watchers:
        runtime.ws_watcher.stop()
    # Close adapter REST clients
    closed_adapters: set[int] = set()
    for adapter in (runtime.adapters or {}).values():
        close = getattr(adapter, "close", None)
        if callable(close) and id(adapter) not in closed_adapters:
            close()
            closed_adapters.add(id(adapter))
    if id(runtime.adapter) not in closed_adapters:
        close = getattr(runtime.adapter, "close", None)
        if callable(close):
            close()
    # Join context threads (after REST clients closed — no in-flight calls remain)
    for ctx in (runtime.adapter_contexts or {}).values():
        ctx.join(timeout=5.0)


async def _run_lifecycle_workers(
    *,
    new_enriched_event: asyncio.Event,
    new_fill_event: asyncio.Event,
    gate_worker: LifecycleGateWorker,
    timeout_worker: TimeoutWorker,
    lifecycle_event_worker: LifecycleEventWorker,
    execution_runtime: ExecutionRuntime | None,
    logger,
) -> None:
    while True:
        try:
            await asyncio.wait_for(
                _wait_any(new_enriched_event, new_fill_event),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            pass  # fallback: gestisce timeout entry scaduti e retry

        new_enriched_event.clear()
        new_fill_event.clear()

        try:
            gate_worker.run_once()
            timeout_worker.run_once()
            lifecycle_event_worker.run_once()
            if execution_runtime is not None:
                execution_runtime.execution_worker.run_once()
        except Exception:
            logger.exception("lifecycle worker error")


async def _run_reconciliation_periodically(
    *,
    sync_worker,
    interval_seconds: int,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_reconciliation()
        except Exception:
            logger.exception("reconciliation worker error")


async def _start_control_bot(bot, logger) -> None:
    first = True
    while True:
        try:
            if first:
                logger.info("control plane: bot polling starting")
                first = False
            await bot.run()
            # run() returned normally — polling stopped without an exception
            logger.warning("control plane: polling stopped unexpectedly — restarting in 10s")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("control plane: polling error — restarting in 10s")
        await asyncio.sleep(10)


async def _async_main(
    *,
    parser_db_path: str,
    migrations_dir: str,
    ops_db_path: str,
    ops_migrations_dir: str,
    log_path: str,
    root_dir: Path,
) -> None:
    logger = setup_logging(log_path=log_path, level=os.getenv("LOG_LEVEL", "INFO"))

    applied = apply_migrations(db_path=parser_db_path, migrations_dir=migrations_dir)
    if applied:
        logger.info("applied %s parser migrations", applied)

    ops_applied = apply_migrations(db_path=ops_db_path, migrations_dir=ops_migrations_dir)
    if ops_applied:
        logger.info("applied %s ops migrations", ops_applied)

    api_id = int(_required_env("TELEGRAM_API_ID"))
    api_hash = _required_env("TELEGRAM_API_HASH")
    session_name = os.getenv("TELEGRAM_SESSION", "tele_signal_bot")

    channels_yaml_path = str(root_dir / "config" / "channels.yaml")
    channels_config = load_channels_config(channels_yaml_path)
    fallback_ids = _parse_fallback_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
    if fallback_ids:
        logger.warning(
            "TELEGRAM_ALLOWED_CHAT_IDS fallback active (%d ids) — "
            "move channels to config/channels.yaml to remove this warning",
            len(fallback_ids),
        )

    ingestion_service = build_ingestion_service(db_path=parser_db_path, logger=logger)
    processing_status_store = build_processing_status_store(db_path=parser_db_path)

    raw_repo = RawMessageRepository(db_path=parser_db_path)
    revision_store = RawMessageRevisionStore(db_path=parser_db_path)
    channel_resolver = ChannelConfigResolver(config_path=channels_yaml_path)
    canonical_repo = CanonicalMessageRepository(db_path=parser_db_path)

    live_conn = sqlite3.connect(parser_db_path, check_same_thread=False)
    run_store = ParserRunStore(live_conn)
    live_run_id = run_store.create_run(notes="live")
    result_v2_store = ParserResultV2Store(live_conn)

    parser_pipeline = ParserPipelineProcessor(
        canonical_repo=canonical_repo,
        result_v2_store=result_v2_store,
        live_run_id=live_run_id,
    )

    new_enriched_event = asyncio.Event()
    new_fill_event = asyncio.Event()
    _main_loop = asyncio.get_running_loop()

    def _fill_wake_callback() -> None:
        _main_loop.call_soon_threadsafe(new_fill_event.set)

    config_dir = str(root_dir / "config")
    enrichment_processor = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(config_dir),
        repository=EnrichedCanonicalMessageRepository(parser_db_path),
        on_pass=new_enriched_event.set,
    )
    pattern_catalog = TextPatternCatalog(root_dir / "config" / "text_patterns.yaml")

    trader_resolver = TraderResolver(
        channel_config=channel_resolver,
        raw_repo=raw_repo,
        pattern_catalog=pattern_catalog,
    )

    # PRD-04 lifecycle layer (chain_repo serve anche al listener per il gating degli edit)
    chain_repo = TradeChainRepository(ops_db_path)

    listener = TelegramListener(
        ingestion_service=ingestion_service,
        processing_status_store=processing_status_store,
        raw_repo=raw_repo,
        channel_resolver=channel_resolver,
        parser_pipeline=parser_pipeline,
        enrichment_processor=enrichment_processor,
        trader_resolver=trader_resolver,
        logger=logger,
        channels_config=channels_config,
        fallback_allowed_chat_ids=fallback_ids,
        chain_exists_for_raw=chain_repo.has_chain_for_raw_message,
        notify_edit_skipped=partial(notify_listener_edit_skipped, ops_db_path),
        revision_store=revision_store,
    )

    event_repo = LifecycleEventRepository(ops_db_path)
    command_repo = ExecutionCommandRepository(ops_db_path)
    control_repo = ControlStateRepository(ops_db_path)
    snapshot_repo = SnapshotRepository(ops_db_path)
    exchange_event_repo = ExchangeEventRepository(ops_db_path)

    # PRD-05 execution gateway layer — built first to extract known symbols for entry gate
    execution_runtime: ExecutionRuntime | None = None

    try:
        execution_runtime = _build_execution_runtime(
            root_dir=root_dir,
            ops_db_path=ops_db_path,
            logger=logger,
            wake_callback=_fill_wake_callback,
        )
    except Exception:
        logger.exception("execution gateway init failed — gateway disabled")

    # Load known symbols from exchange adapter (fail-open: None = no restriction)
    known_symbols = _collect_runtime_known_symbols(execution_runtime, logger)
    if known_symbols is not None:
        logger.info("symbol whitelist loaded: %d symbols", len(known_symbols))
    else:
        logger.info("symbol whitelist unavailable — entry gate symbol check disabled")

    exchange_port = _build_exchange_port(
        root_dir=root_dir,
        ops_db_path=ops_db_path,
        execution_runtime=execution_runtime,
        known_symbols=known_symbols,
    )
    risk_engine = RiskCapacityEngine()
    entry_gate = _build_lifecycle_entry_gate(
        root_dir=root_dir,
        risk_engine=risk_engine,
        exchange_port=exchange_port,
    )

    gate_worker = LifecycleGateWorker(
        parser_db_path=parser_db_path,
        ops_db_path=ops_db_path,
        gate=entry_gate,
        chain_repo=chain_repo,
        event_repo=event_repo,
        command_repo=command_repo,
        snapshot_repo=snapshot_repo,
        control_repo=control_repo,
        channel_resolver=channel_resolver,
    )
    timeout_worker = TimeoutWorker(ops_db_path=ops_db_path, chain_repo=chain_repo)
    lifecycle_event_worker = LifecycleEventWorker(
        ops_db_path=ops_db_path,
        processor=LifecycleEventProcessor(),
        chain_repo=chain_repo,
        event_repo=event_repo,
        command_repo=command_repo,
        exchange_event_repo=exchange_event_repo,
    )

    # Account snapshot worker — periodic balance fetch per account
    _account_ids: list[str] = (
        list(execution_runtime.sync_workers.keys())
        if execution_runtime is not None and execution_runtime.sync_workers
        else []
    )
    _account_snapshot_worker: AccountSnapshotWorker | None = None
    if _account_ids and isinstance(exchange_port, LiveExchangeDataPort):
        _account_snapshot_worker = AccountSnapshotWorker(
            port=exchange_port,
            repository=snapshot_repo,
            account_ids=_account_ids,
            interval_seconds=60,
            stale_after_seconds=180,
        )

    # Unfilled price watcher — cancel setups when price crosses TP without fill
    _unfilled_price_watcher: UnfilledPriceWatcher | None = None
    if execution_runtime is not None and _account_ids:
        _primary_account_id = _account_ids[0]
        _first_adapter_key = next(iter(execution_runtime.adapters), None)
        _primary_adapter = (
            execution_runtime.adapters[_first_adapter_key]
            if _first_adapter_key
            else execution_runtime.adapter
        )
        _op_config_loader = OperationConfigLoader(config_dir)
        _unfilled_interval = _op_config_loader.get_unfilled_price_check_interval()
        _unfilled_price_watcher = UnfilledPriceWatcher(
            ops_db_path=ops_db_path,
            chain_repo=chain_repo,
            adapter=_primary_adapter,
            execution_account_id=_primary_account_id,
            interval_seconds=_unfilled_interval,
        )

    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()

    def _position_sync_fn(account_id: str | None) -> None:
        workers = execution_runtime.sync_workers if execution_runtime else None
        if not workers:
            return
        targets = (
            [workers[account_id]] if account_id and account_id in workers
            else list(workers.values())
        )
        for w in targets:
            w.run_bulk_position_sync()
        # Trigger account snapshot refresh
        if _account_snapshot_worker is not None:
            trigger_ids = [account_id] if account_id else _account_ids
            for acc in trigger_ids:
                _account_snapshot_worker.trigger(acc)

    _cp = build_control_plane(
        config_path=str(root_dir / "config" / "telegram_control.yaml"),
        ops_db_path=ops_db_path,
        log_path=log_path,
        known_trader_ids=(
            {ch.trader_id for ch in channels_config.channels if ch.trader_id}
            | pattern_catalog.all_trader_ids
        ),
        telethon_client=client,
        position_sync_fn=_position_sync_fn,
    )
    control_bot = _cp.bot if _cp is not None else None
    cp_dispatcher = _cp.dispatcher if _cp is not None else None
    cp_service = _cp.service if _cp is not None else None

    # Wire account snapshot worker → dashboard PNL auto-refresh
    if _account_snapshot_worker is not None and _cp is not None:
        _cp_dashboard_manager = _cp.dashboard_manager

        def _on_snap(account_id: str) -> None:
            asyncio.create_task(_cp_dashboard_manager.on_snapshot_event(account_id))

        _account_snapshot_worker._on_snapshot_saved = _on_snap

    if _cp is not None and _cp.startup_plan.apply_global_block:
        cp_service.pause(scope_value=None, created_by="startup")
        logger.info("control plane: startup mode '%s' — global block applied", _cp.startup_plan.mode)
    elif _cp is not None and _cp.startup_plan.fell_back:
        logger.warning(
            "control plane: startup mode 'restore' fell back to 'auto': %s",
            _cp.startup_plan.message,
        )
    elif _cp is not None and _cp.startup_plan.mode == "restore" and not _cp.startup_plan.fell_back:
        logger.info("control plane: startup mode 'restore' — snapshot applied")

    watcher = ChannelConfigWatcher(
        path=channels_yaml_path,
        on_reload=listener.update_config,
        logger=logger,
    )
    watcher.start()

    try:
        listener.register_handlers(client)
        logger.info("telegram listener started | parser_db=%s | ops_db=%s", parser_db_path, ops_db_path)
        await listener.run_recovery(client)
        worker_task = asyncio.create_task(listener.run_worker())

        lifecycle_task = asyncio.create_task(
            _run_lifecycle_workers(
                new_enriched_event=new_enriched_event,
                new_fill_event=new_fill_event,
                gate_worker=gate_worker,
                timeout_worker=timeout_worker,
                lifecycle_event_worker=lifecycle_event_worker,
                execution_runtime=execution_runtime,
                logger=logger,
            )
        )

        account_snapshot_task = None
        if _account_snapshot_worker is not None:
            account_snapshot_task = asyncio.create_task(_account_snapshot_worker.run())
            logger.info("account snapshot worker started | accounts=%s", _account_ids)

        unfilled_watcher_task = None
        if _unfilled_price_watcher is not None:
            unfilled_watcher_task = asyncio.create_task(_unfilled_price_watcher.run())
            logger.info(
                "unfilled price watcher started | interval=%ds | account=%s",
                _unfilled_interval, _primary_account_id,
            )

        cp_dispatcher_task = None
        if cp_dispatcher is not None:
            cp_dispatcher_task = asyncio.create_task(cp_dispatcher.run())
            logger.info("control plane: notification dispatcher started")

        control_bot_task = None
        if control_bot is not None:
            control_bot_task = asyncio.create_task(_start_control_bot(control_bot, logger))

        if cp_service is not None:
            try:
                cp_service.send_startup_notification()
            except Exception:
                logger.warning("startup notification failed (non-critical)")

        if _cp is not None:
            try:
                status = cp_service.get_status()
                control = cp_service.get_control()
                _cp.snapshot_store.save(
                    control_mode=status.control_mode,
                    active_blocks=[
                        f"{b.scope_type}:{b.scope_value}" if b.scope_value else b.scope_type
                        for b in control.active_blocks
                    ],
                    open_chain_count=(
                        status.open_count + status.partial_count + status.waiting_entry_count
                    ),
                    pending_command_count=status.pending_commands,
                    shutdown_reason=None,
                )
            except Exception:
                logger.warning("startup snapshot save failed (non-critical)")

        if execution_runtime is not None:
            for worker in (execution_runtime.sync_workers or {}).values():
                try:
                    # 1. Reconcile tracked orders (fills / cancellations via client_order_id).
                    worker.run_reconciliation()
                    # 2. Recover TP_FILLED events from recent reduce trades BEFORE the bulk
                    #    position sync so that real_close_fill_exists() can skip synthetic closes
                    #    and avoid the CLOSE_FULL_FILLED vs TP_FILLED semantic mismatch.
                    worker.run_trade_based_reconciliation()
                    # 3. Bulk position snapshot — detects full closes (qty=0) and partial closes
                    #    (0 < live_qty < db_qty). Seed the consecutive-zero counter first so a
                    #    single call is sufficient to cross the confirmation threshold.
                    worker.bootstrap_zero_counts()
                    worker.run_bulk_position_sync()
                    # 4. Detect position-level TPs that were externally cancelled during downtime.
                    worker.run_protective_orders_reconciliation()
                    # 5. Recover funding executions lost during downtime.
                    worker.run_funding_reconciliation()
                except Exception:
                    logger.warning("startup reconciliation failed for worker (non-critical)", exc_info=True)

        try:
            await client.run_until_disconnected()
        finally:
            worker_task.cancel()
            lifecycle_task.cancel()
            if account_snapshot_task is not None:
                account_snapshot_task.cancel()
            if cp_dispatcher_task is not None:
                cp_dispatcher_task.cancel()
            if control_bot_task is not None:
                control_bot_task.cancel()
            if control_bot is not None:
                await control_bot.shutdown()
            if cp_service is not None:
                try:
                    cp_service.send_shutdown_notification()
                except Exception:
                    logger.warning("shutdown notification failed (non-critical)")
            if _cp is not None:
                try:
                    status = cp_service.get_status()
                    control = cp_service.get_control()
                    _cp.snapshot_store.save(
                        control_mode=status.control_mode,
                        active_blocks=[
                            f"{b.scope_type}:{b.scope_value}" if b.scope_value else b.scope_type
                            for b in control.active_blocks
                        ],
                        open_chain_count=(
                            status.open_count + status.partial_count + status.waiting_entry_count
                        ),
                        pending_command_count=status.pending_commands,
                        shutdown_reason="SIGTERM",
                    )
                except Exception:
                    logger.warning("snapshot save failed (non-critical)")
    finally:
        await client.disconnect()
        watcher.stop()
        _close_execution_runtime(execution_runtime)


_instance_lock_handle = None
_LOCK_FILE_PATH = Path(__file__).resolve().with_name(".telesignalbot.lock")


def _acquire_instance_lock() -> None:
    """
    Prevent multiple bot instances from running at the same time.

    Windows: uses msvcrt file locking.
    Linux/macOS: uses fcntl flock.
    """
    global _instance_lock_handle

    _instance_lock_handle = open(_LOCK_FILE_PATH, "w", encoding="utf-8")

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(_instance_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_instance_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(
            "ERRORE: un'altra istanza di main.py è già in esecuzione.\n"
            "Termina il processo precedente prima di avviarne uno nuovo.",
            file=sys.stderr,
        )
        sys.exit(1)

    _instance_lock_handle.seek(0)
    _instance_lock_handle.truncate()
    _instance_lock_handle.write(str(os.getpid()))
    _instance_lock_handle.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate", action="store_true", help="Apply DB migrations and exit.")
    parser.add_argument(
        "--check-config", action="store_true",
        help="Run configuration checks and exit (no bot startup).",
    )
    parser.add_argument(
        "--skip-checks", action="store_true",
        help="Skip startup configuration checks.",
    )
    args = parser.parse_args()

    load_dotenv()
    root_dir = Path(__file__).resolve().parent

    if args.check_config or not args.skip_checks:
        from src.startup_check.validator import run_startup_checks

        report = run_startup_checks(root_dir)
        if args.check_config or report.has_errors or report.warnings:
            print(report.render())
        if args.check_config:
            sys.exit(1 if report.has_errors else 0)
        if report.has_errors:
            print(
                "Avvio interrotto: correggi gli errori di configurazione segnalati sopra "
                "(oppure usa --skip-checks per forzare l'avvio).",
                file=sys.stderr,
            )
            sys.exit(1)

    _acquire_instance_lock()
    parser_db_path = os.getenv("PARSER_DB_PATH", str(root_dir / "db" / "parser.sqlite3"))
    migrations_dir = str(root_dir / "db" / "migrations")
    ops_db_path = os.getenv("OPS_DB_PATH", str(root_dir / "db" / "ops.sqlite3"))
    ops_migrations_dir = str(root_dir / "db" / "ops_migrations")
    log_path = os.getenv("LOG_PATH", str(root_dir / "logs" / "bot.log"))

    if args.migrate:
        logger = setup_logging(log_path=log_path, level=os.getenv("LOG_LEVEL", "INFO"))
        applied = apply_migrations(db_path=parser_db_path, migrations_dir=migrations_dir)
        ops_applied = apply_migrations(db_path=ops_db_path, migrations_dir=ops_migrations_dir)
        logger.info("applied %s parser migrations, %s ops migrations", applied, ops_applied)
        print(f"Parser migrations applied: {applied} | Ops migrations applied: {ops_applied}")
        return

    asyncio.run(
        _async_main(
            parser_db_path=parser_db_path,
            migrations_dir=migrations_dir,
            ops_db_path=ops_db_path,
            ops_migrations_dir=ops_migrations_dir,
            log_path=log_path,
            root_dir=root_dir,
        )
    )


if __name__ == "__main__":
    main()
