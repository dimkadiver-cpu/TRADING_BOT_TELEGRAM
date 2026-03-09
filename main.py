"""TeleSignalBot entrypoint (H1 single-process)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from src.core.config_loader import load_config
from src.core.logger import setup_logging
from src.core.migrations import apply_migrations
from src.telegram.listener import (
    build_effective_trader_resolver,
    build_eligibility_evaluator,
    build_ingestion_service,
    build_minimal_parser_pipeline,
    build_parse_results_store,
    register_message_listener,
)
from src.telegram.trader_mapping import TelegramSourceTraderMapper


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate", action="store_true", help="Apply DB migrations and exit.")
    args = parser.parse_args()

    load_dotenv()
    root_dir = Path(__file__).resolve().parent
    db_path = os.getenv("DB_PATH", str(root_dir / "db" / "tele_signal_bot.sqlite3"))
    migrations_dir = str(root_dir / "db" / "migrations")
    log_path = os.getenv("LOG_PATH", str(root_dir / "logs" / "bot.log"))
    logger = setup_logging(log_path=log_path, level=os.getenv("LOG_LEVEL", "INFO"))

    applied = apply_migrations(db_path=db_path, migrations_dir=migrations_dir)
    if applied:
        logger.info("applied %s migrations", applied)

    if args.migrate:
        print(f"Migrations applied: {applied}")
        return

    api_id = int(_required_env("TELEGRAM_API_ID"))
    api_hash = _required_env("TELEGRAM_API_HASH")
    session_name = os.getenv("TELEGRAM_SESSION", "tele_signal_bot")
    allowed_chat_ids = _parse_allowed_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
    source_map_path = os.getenv(
        "TELEGRAM_SOURCE_MAP_PATH",
        str(root_dir / "config" / "telegram_source_map.json"),
    )
    config = load_config(str(root_dir))
    trader_mapper = TelegramSourceTraderMapper.from_json_file(
        file_path=source_map_path,
        trader_aliases=config.trader_aliases,
        known_trader_ids=set(config.traders.keys()),
    )

    ingestion_service = build_ingestion_service(db_path=db_path, logger=logger)
    trader_resolver = build_effective_trader_resolver(
        db_path=db_path,
        trader_mapper=trader_mapper,
        trader_aliases=config.trader_aliases,
        known_trader_ids=set(config.traders.keys()),
    )
    eligibility_evaluator = build_eligibility_evaluator(db_path=db_path)
    parser_pipeline = build_minimal_parser_pipeline(
        trader_aliases=config.trader_aliases,
        global_parser_mode=os.getenv("PARSER_MODE", "regex_only"),
        traders=config.traders,
    )
    parse_results_store = build_parse_results_store(db_path=db_path)
    with TelegramClient(session_name, api_id, api_hash) as client:
        register_message_listener(
            client=client,
            ingestion_service=ingestion_service,
            effective_trader_resolver=trader_resolver,
            eligibility_evaluator=eligibility_evaluator,
            parser_pipeline=parser_pipeline,
            parse_results_store=parse_results_store,
            logger=logger,
            allowed_chat_ids=allowed_chat_ids,
        )
        logger.info("telegram listener started")
        client.run_until_disconnected()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_allowed_chat_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    values: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        values.add(int(token))
    return values


if __name__ == "__main__":
    main()

