"""TeleSignalBot entrypoint — runtime_v2 stack."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

import sqlite3

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
from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter
from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
from src.storage.parser_results_v2 import ParserResultV2Store
from src.storage.parser_runs import ParserRunStore
from src.telegram.channel_config import ChannelConfigWatcher, load_channels_config
from src.telegram.listener import (
    TelegramListener,
    build_ingestion_service,
    build_processing_status_store,
)


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

    config_dir = str(root_dir / "config")
    enrichment_processor = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(config_dir),
        repository=EnrichedCanonicalMessageRepository(parser_db_path),
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

    exchange_port = StaticExchangeDataPort()
    risk_engine = RiskCapacityEngine()
    entry_gate = LifecycleEntryGate(risk_engine=risk_engine, exchange_port=exchange_port)

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

    # PRD-05 execution gateway layer (abilitato solo se HUMMINGBOT_BASE_URL è configurato)
    hummingbot_url = os.getenv("HUMMINGBOT_BASE_URL", "")
    execution_worker: ExecutionCommandWorker | None = None
    sync_worker: ExchangeEventSyncWorker | None = None

    if hummingbot_url:
        execution_config_path = str(root_dir / "config" / "execution.yaml")
        exec_config = ExecutionConfigLoader(execution_config_path).load()
        adapter_name = exec_config.default_adapter
        adapter_cfg = exec_config.adapters[adapter_name]
        hb_adapter = HummingbotApiPaperAdapter(
            base_url=hummingbot_url,
            connector=adapter_cfg.connector,
        )
        gateway_repo = GatewayCommandRepository(ops_db_path)
        gateway = ExecutionGateway(
            config=exec_config,
            adapter_registry={adapter_name: hb_adapter},
            repo=gateway_repo,
        )
        routing, _ = exec_config.resolve_routing("default")
        execution_worker = ExecutionCommandWorker(
            ops_db_path=ops_db_path,
            gateway=gateway,
            repo=gateway_repo,
        )
        sync_worker = ExchangeEventSyncWorker(
            ops_db_path=ops_db_path,
            adapter=hb_adapter,
            repo=gateway_repo,
            execution_account_id=routing.execution_account_id,
        )
        logger.info(
            "execution gateway started | adapter=%s | url=%s | account=%s",
            adapter_name, hummingbot_url, routing.execution_account_id,
        )
    else:
        logger.warning("HUMMINGBOT_BASE_URL not set — execution gateway disabled (paper commands will queue but not be sent)")

    async def _run_lifecycle_workers() -> None:
        while True:
            try:
                gate_worker.run_once()
                timeout_worker.run_once()
                lifecycle_event_worker.run_once()
                if execution_worker is not None:
                    execution_worker.run_once()
                if sync_worker is not None:
                    sync_worker.run_once()
            except Exception:
                logger.exception("lifecycle worker error")
            await asyncio.sleep(10)

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
        lifecycle_task = asyncio.create_task(_run_lifecycle_workers())
        try:
            await client.run_until_disconnected()
        finally:
            worker_task.cancel()
            lifecycle_task.cancel()
    finally:
        await client.disconnect()
        watcher.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate", action="store_true", help="Apply DB migrations and exit.")
    args = parser.parse_args()

    load_dotenv()
    root_dir = Path(__file__).resolve().parent
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
