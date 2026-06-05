"""TeleSignalBot entrypoint — runtime_v2 stack."""

from __future__ import annotations

import truststore
truststore.inject_into_ssl()

import argparse
import asyncio
import ctypes
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
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
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
from src.runtime_v2.lifecycle.workers import LifecycleEventWorker, TimeoutWorker
from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
from src.runtime_v2.control_plane.bootstrap import build_control_plane
from src.runtime_v2.control_plane.notification_dispatcher import TelegramNotificationDispatcher
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.telegram_bot import TelegramControlBot
from src.storage.parser_results_v2 import ParserResultV2Store
from src.storage.parser_runs import ParserRunStore
from src.telegram.channel_config import ChannelConfigWatcher, load_channels_config
from src.telegram.listener import (
    TelegramListener,
    build_ingestion_service,
    build_processing_status_store,
)


@dataclass
class ExecutionRuntime:
    adapter: object
    execution_worker: ExecutionCommandWorker
    sync_worker: ExchangeEventSyncWorker
    ws_watcher: BybitWsFillWatcher | None
    reconciliation_interval_seconds: int | None
    position_reconciliation_interval_seconds: int = 600
    poll_fallback_enabled: bool = True


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
    gateway_repo = GatewayCommandRepository(ops_db_path)
    gateway = ExecutionGateway(
        config=exec_config,
        adapter_registry={adapter_name: adapter},
        repo=gateway_repo,
    )
    execution_worker = ExecutionCommandWorker(
        ops_db_path=ops_db_path,
        gateway=gateway,
        repo=gateway_repo,
    )
    sync_worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db_path,
        adapter=adapter,
        repo=gateway_repo,
        execution_account_id=routing.execution_account_id,
        wake_callback=wake_callback,
    )

    ws_watcher = None
    reconciliation_interval_seconds = None
    if adapter_cfg.type == "ccxt_bybit" and adapter_cfg.websocket.enabled:
        api_key = os.environ.get(adapter_cfg.api_key_env or "") if adapter_cfg.api_key_env else ""
        api_secret = os.environ.get(adapter_cfg.api_secret_env or "") if adapter_cfg.api_secret_env else ""
        testnet = bool(getattr(adapter_cfg, "testnet", False) or adapter_cfg.mode == "testnet")
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
            reconciliation_callback=sync_worker.run_reconciliation,
            mode=adapter_cfg.mode,
            wake_callback=wake_callback,
        )
        ws_watcher.start()
        if adapter_cfg.websocket.poll_fallback_enabled:
            reconciliation_interval_seconds = adapter_cfg.websocket.poll_fallback_period_seconds

    logger.info(
        "execution gateway started | adapter=%s | account=%s",
        adapter_name, routing.execution_account_id,
    )
    return ExecutionRuntime(
        adapter=adapter,
        execution_worker=execution_worker,
        sync_worker=sync_worker,
        ws_watcher=ws_watcher,
        reconciliation_interval_seconds=reconciliation_interval_seconds,
        position_reconciliation_interval_seconds=adapter_cfg.websocket.position_reconciliation_interval_seconds,
        poll_fallback_enabled=adapter_cfg.websocket.poll_fallback_enabled,
    )


def _build_lifecycle_entry_gate(
    *,
    root_dir: Path,
    risk_engine: RiskCapacityEngine,
    exchange_port: StaticExchangeDataPort,
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


def _close_execution_runtime(runtime: ExecutionRuntime | None) -> None:
    if runtime is None:
        return
    if runtime.ws_watcher is not None:
        runtime.ws_watcher.stop()
    close = getattr(runtime.adapter, "close", None)
    if callable(close):
        close()


async def _run_reconciliation_periodically(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_reconciliation()
        except Exception:
            logger.exception("periodic reconciliation error")


async def _run_position_reconciliation_periodically(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_position_reconciliation()
            sync_worker.run_trade_based_reconciliation()
            sync_worker.run_protective_orders_reconciliation()
        except Exception:
            logger.exception("periodic position/tp reconciliation error")


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


async def _run_sync_worker(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int = 8,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_once()
        except Exception:
            logger.exception("sync worker error")



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

    listener = TelegramListener(
        ingestion_service=ingestion_service,
        processing_status_store=processing_status_store,
        raw_repo=raw_repo,
        channel_resolver=channel_resolver,
        parser_pipeline=parser_pipeline,
        enrichment_processor=enrichment_processor,
        logger=logger,
        channels_config=channels_config,
        fallback_allowed_chat_ids=fallback_ids,
    )

    # PRD-04 lifecycle layer
    chain_repo = TradeChainRepository(ops_db_path)
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
    known_symbols: frozenset[str] | None = None
    if execution_runtime is not None:
        adapter = execution_runtime.adapter
        if hasattr(adapter, "load_known_symbols"):
            try:
                known_symbols = adapter.load_known_symbols()
                if known_symbols is not None:
                    logger.info("symbol whitelist loaded: %d symbols", len(known_symbols))
                else:
                    logger.info("symbol whitelist unavailable — entry gate symbol check disabled")
            except Exception:
                logger.warning("load_known_symbols failed — entry gate symbol check disabled")

    exchange_port = StaticExchangeDataPort(known_symbols=known_symbols)
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

    _cp = build_control_plane(
        config_path=str(root_dir / "config" / "telegram_control.yaml"),
        ops_db_path=ops_db_path,
        log_path=log_path,
    )
    control_bot = _cp.bot if _cp is not None else None
    cp_dispatcher = _cp.dispatcher if _cp is not None else None
    cp_service = _cp.service if _cp is not None else None

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

    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()
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

        if execution_runtime is not None:
            try:
                execution_runtime.sync_worker.run_reconciliation()
                execution_runtime.sync_worker.run_position_reconciliation()
            except Exception:
                logger.warning("startup reconciliation failed (non-critical)")

        sync_task = None
        if execution_runtime is not None and execution_runtime.poll_fallback_enabled:
            sync_task = asyncio.create_task(
                _run_sync_worker(
                    sync_worker=execution_runtime.sync_worker,
                    logger=logger,
                )
            )

        reconciliation_task = None
        position_reconciliation_task = None
        if (
            execution_runtime is not None
            and execution_runtime.reconciliation_interval_seconds is not None
        ):
            reconciliation_task = asyncio.create_task(
                _run_reconciliation_periodically(
                    sync_worker=execution_runtime.sync_worker,
                    interval_seconds=execution_runtime.reconciliation_interval_seconds,
                    logger=logger,
                )
            )
        if execution_runtime is not None:
            position_reconciliation_task = asyncio.create_task(
                _run_position_reconciliation_periodically(
                    sync_worker=execution_runtime.sync_worker,
                    interval_seconds=execution_runtime.position_reconciliation_interval_seconds,
                    logger=logger,
                )
            )
        try:
            await client.run_until_disconnected()
        finally:
            worker_task.cancel()
            lifecycle_task.cancel()
            if sync_task is not None:
                sync_task.cancel()
            if reconciliation_task is not None:
                reconciliation_task.cancel()
            if position_reconciliation_task is not None:
                position_reconciliation_task.cancel()
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


_instance_mutex = None
_MUTEX_NAME = "Global\\TeleSignalBot_SingleInstance"


def _acquire_instance_lock() -> None:
    global _instance_mutex
    kernel32 = ctypes.windll.kernel32
    _instance_mutex = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        print(
            "ERRORE: un'altra istanza di main.py è già in esecuzione.\n"
            "Termina il processo precedente prima di avviarne uno nuovo.",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate", action="store_true", help="Apply DB migrations and exit.")
    args = parser.parse_args()

    load_dotenv()
    root_dir = Path(__file__).resolve().parent
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
