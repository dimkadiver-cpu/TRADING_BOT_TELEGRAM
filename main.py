"""TeleSignalBot entrypoint (H1 single-process)."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from src.core.config_loader import load_config
from src.core.logger import setup_logging
from src.core.migrations import apply_migrations
from src.execution.dynamic_pairlist import DynamicPairlistManager
from src.operation_rules.engine import OperationRulesEngine
from src.storage.operational_signals_store import OperationalSignalsStore
from src.storage.signals_store import SignalsStore
from src.target_resolver.resolver import TargetResolver
from src.operation_rules.loader import validate_operation_rules_config
from src.telegram.channel_config import ChannelConfigWatcher, load_channels_config
from src.telegram.listener import (
    TelegramListener,
    build_effective_trader_resolver,
    build_eligibility_evaluator,
    build_ingestion_service,
    build_parse_results_store,
    build_processing_status_store,
    build_review_queue_store,
)
from src.telegram.router import MessageRouter
from src.telegram.trader_mapping import TelegramSourceTraderMapper


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_fallback_chat_ids(raw: str | None) -> set[int]:
    """Parse TELEGRAM_ALLOWED_CHAT_IDS env var — kept as temporary fallback."""
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
    db_path: str,
    migrations_dir: str,
    log_path: str,
    root_dir: Path,
) -> None:
    logger = setup_logging(log_path=log_path, level=os.getenv("LOG_LEVEL", "INFO"))

    applied = apply_migrations(db_path=db_path, migrations_dir=migrations_dir)
    if applied:
        logger.info("applied %s migrations", applied)

    api_id = int(_required_env("TELEGRAM_API_ID"))
    api_hash = _required_env("TELEGRAM_API_HASH")
    session_name = os.getenv("TELEGRAM_SESSION", "tele_signal_bot")

    # channels.yaml is the primary source; TELEGRAM_ALLOWED_CHAT_IDS is a fallback
    channels_yaml_path = str(root_dir / "config" / "channels.yaml")
    channels_config = load_channels_config(channels_yaml_path)
    fallback_ids = _parse_fallback_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
    if fallback_ids:
        logger.warning(
            "TELEGRAM_ALLOWED_CHAT_IDS fallback active (%d ids) — "
            "move channels to config/channels.yaml to remove this warning",
            len(fallback_ids),
        )

    source_map_path = os.getenv(
        "TELEGRAM_SOURCE_MAP_PATH",
        str(root_dir / "config" / "telegram_source_map.json"),
    )
    validate_operation_rules_config(rules_dir=str(root_dir / "config"))
    logger.info("operation rules config validation passed")
    config = load_config(str(root_dir))
    trader_mapper = TelegramSourceTraderMapper.from_json_file(
        file_path=source_map_path,
        trader_aliases=config.trader_aliases,
        known_trader_ids=set(config.traders.keys()),
    )
    dynamic_pairlist_path = os.getenv(
        'FREQTRADE_DYNAMIC_PAIRLIST_PATH',
        str(root_dir / 'freqtrade' / 'user_data' / 'dynamic_pairs.json'),
    )
    dynamic_pairlist_refresh = int(os.getenv('FREQTRADE_DYNAMIC_PAIRLIST_REFRESH_PERIOD', '10'))
    dynamic_pairlist_manager = DynamicPairlistManager(
        dynamic_pairlist_path,
        refresh_period=dynamic_pairlist_refresh,
    )

    ingestion_service = build_ingestion_service(db_path=db_path, logger=logger)
    processing_status_store = build_processing_status_store(db_path=db_path)

    listener = TelegramListener(
        ingestion_service=ingestion_service,
        processing_status_store=processing_status_store,
        router=MessageRouter(
            effective_trader_resolver=build_effective_trader_resolver(
                db_path=db_path,
                trader_mapper=trader_mapper,
                trader_aliases=config.trader_aliases,
                known_trader_ids=set(config.traders.keys()),
            ),
            eligibility_evaluator=build_eligibility_evaluator(db_path=db_path),
            parse_results_store=build_parse_results_store(db_path=db_path),
            processing_status_store=processing_status_store,
            review_queue_store=build_review_queue_store(db_path=db_path),
            raw_message_store=ingestion_service.store,
            logger=logger,
            channels_config=channels_config,
            db_path=db_path,
            operation_rules_engine=OperationRulesEngine(rules_dir=str(root_dir / "config")),
            target_resolver=TargetResolver(),
            signals_store=SignalsStore(db_path=db_path),
            operational_signals_store=OperationalSignalsStore(db_path=db_path),
            dynamic_pairlist_manager=dynamic_pairlist_manager,
        ),
        logger=logger,
        channels_config=channels_config,
        fallback_allowed_chat_ids=fallback_ids,
    )

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
        logger.info("telegram listener started")
        await listener.run_recovery(client)
        worker_task = asyncio.create_task(listener.run_worker())
        try:
            await client.run_until_disconnected()
        finally:
            worker_task.cancel()
    finally:
        await client.disconnect()
        watcher.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate", action="store_true", help="Apply DB migrations and exit.")
    args = parser.parse_args()

    load_dotenv()
    root_dir = Path(__file__).resolve().parent
    db_path = os.getenv("DB_PATH", str(root_dir / "db" / "tele_signal_bot.sqlite3"))
    migrations_dir = str(root_dir / "db" / "migrations")
    log_path = os.getenv("LOG_PATH", str(root_dir / "logs" / "bot.log"))

    if args.migrate:
        logger = setup_logging(log_path=log_path, level=os.getenv("LOG_LEVEL", "INFO"))
        applied = apply_migrations(db_path=db_path, migrations_dir=migrations_dir)
        logger.info("applied %s migrations", applied)
        print(f"Migrations applied: {applied}")
        return

    asyncio.run(
        _async_main(
            db_path=db_path,
            migrations_dir=migrations_dir,
            log_path=log_path,
            root_dir=root_dir,
        )
    )


if __name__ == "__main__":
    main()
