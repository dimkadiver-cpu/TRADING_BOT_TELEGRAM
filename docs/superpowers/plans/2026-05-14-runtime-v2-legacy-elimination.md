# Runtime V2 Legacy Elimination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `main.py` avvia il listener, i messaggi Telegram vengono processati da runtime_v2, ogni messaggio produce una riga in `canonical_messages`. Nessun router legacy istanziato, nessuna tabella legacy nel DB.

**Architecture:** Il listener mantiene la propria logica di ingestion Telegram (già testata). Il worker chiama direttamente `ChannelConfigResolver` + `ParserPipelineProcessor` invece del router. `main.py` costruisce solo lo stack runtime_v2.

**Tech Stack:** Python 3.12, SQLite, Pydantic v2, Telethon, pytest.

---

## File toccati

| File | Operazione |
|---|---|
| `db/migrations/025_drop_legacy_tables.sql` | Nuovo |
| `src/telegram/listener.py` | Modifica: rimozione router, nuova interfaccia, nuovo `_process_item` |
| `src/telegram/tests/test_listener_blacklist.py` | Fix: aggiornare `_make_listener` |
| `src/telegram/tests/test_listener_media.py` | Fix: aggiornare `_make_listener` |
| `src/telegram/tests/test_listener_recovery.py` | Fix: aggiornare `_make_listener` |
| `src/telegram/tests/test_listener_recovery_topic.py` | Fix: aggiornare `_make_listener` |
| `src/telegram/tests/test_listener_topic.py` | Fix: aggiornare `_make_listener` |
| `src/telegram/tests/test_reply_chain.py` | Fix: aggiornare `_make_listener` |
| `src/telegram/tests/test_topic_integration.py` | Fix: aggiornare `_make_listener` |
| `src/telegram/tests/test_router*.py` (7 file) | Eliminati — testano `MessageRouter` direttamente |
| `src/runtime_v2/listener_sidecar.py` | Eliminato |
| `main.py` | Riscrittura — solo stack runtime_v2 |

---

## Task 1: Migration 025 — DROP tabelle legacy

**Files:**
- Create: `db/migrations/025_drop_legacy_tables.sql`

- [ ] **Step 1: Scrivi la migration**

```sql
-- db/migrations/025_drop_legacy_tables.sql
-- Eliminazione tabelle legacy: parser, operation rules, execution.
-- Tabelle runtime_v2 invariate: raw_messages, canonical_messages, schema_migrations.

DROP TABLE IF EXISTS parse_results;
DROP TABLE IF EXISTS parse_results_v1;
DROP TABLE IF EXISTS parsed_messages;
DROP TABLE IF EXISTS review_queue;
DROP TABLE IF EXISTS operational_signals;
DROP TABLE IF EXISTS signals;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS warnings;
DROP TABLE IF EXISTS trades;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS fills;
DROP TABLE IF EXISTS positions;
DROP TABLE IF EXISTS exchange_events;
DROP TABLE IF EXISTS backtest_runs;
DROP TABLE IF EXISTS backtest_trades;
DROP TABLE IF EXISTS protective_orders_mode;
```

- [ ] **Step 2: Verifica che la migration si applichi**

```bash
python -c "
from src.core.migrations import apply_migrations
n = apply_migrations(db_path='db/tele_signal_bot.sqlite3', migrations_dir='db/migrations')
print('applied:', n)
"
```

Expected: `applied: 1`

- [ ] **Step 3: Verifica che le tabelle siano sparite**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('db/tele_signal_bot.sqlite3')
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
print(tables)
conn.close()
"
```

Expected: output contiene solo `['schema_migrations', 'raw_messages', 'canonical_messages']` (più eventuali indici).

- [ ] **Step 4: Commit**

```bash
git add db/migrations/025_drop_legacy_tables.sql
git commit -m "feat(db): migration 025 — drop 16 legacy tables (parser, execution, operation_rules)"
```

---

## Task 2: Refactor `listener.py` — rimuovi dipendenze router

**Files:**
- Modify: `src/telegram/listener.py`

- [ ] **Step 1: Sostituisci gli import in cima al file**

Rimuovi queste righe:
```python
from src.storage.parse_results import ParseResultStore
from src.telegram.router import MessageRouter, QueueItem as _QueueItem, is_blacklisted_text
```

Aggiungi al loro posto (dopo gli import esistenti che rimangono):
```python
from dataclasses import dataclass

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate, ResolvedTraderContext
```

- [ ] **Step 2: Definisci `_QueueItem` localmente nel file**

Aggiungi subito dopo gli import, prima della classe `TelegramListener`:

```python
@dataclass(slots=True)
class _QueueItem:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    raw_text: str
    source_trader_id: str | None
    reply_to_message_id: int | None
    acquisition_mode: str
    source_topic_id: int | None = None
```

- [ ] **Step 3: Definisci `_is_blacklisted_text` localmente**

Aggiungi dopo `_QueueItem`:

```python
def _is_blacklisted_text(
    config: ChannelsConfig,
    raw_text: str,
    chat_id: int | None,
    topic_id: int | None = None,
) -> bool:
    text_lower = raw_text.lower()
    for tag in config.blacklist_global:
        if tag.lower() in text_lower:
            return True
    if chat_id is not None:
        entry = config.match_entry(chat_id, topic_id)
        if entry is not None:
            for tag in entry.blacklist:
                if tag.lower() in text_lower:
                    return True
    return False
```

- [ ] **Step 4: Rimuovi le factory function legacy**

Rimuovi dal file le seguenti funzioni (non più usate da main.py):
- `build_effective_trader_resolver`
- `build_eligibility_evaluator`
- `build_parse_results_store`
- `build_review_queue_store`

Tieni: `build_ingestion_service`, `build_processing_status_store`.

- [ ] **Step 5: Sostituisci `__init__` di `TelegramListener`**

```python
def __init__(
    self,
    *,
    ingestion_service: RawMessageIngestionService,
    processing_status_store: ProcessingStatusStore,
    raw_repo: RawMessageRepository,
    channel_resolver: ChannelConfigResolver,
    parser_pipeline: ParserPipelineProcessor,
    logger: logging.Logger,
    channels_config: ChannelsConfig,
    fallback_allowed_chat_ids: Iterable[int] | None = None,
) -> None:
    self._ingestion = ingestion_service
    self._status_store = processing_status_store
    self._raw_repo = raw_repo
    self._channel_resolver = channel_resolver
    self._parser_pipeline = parser_pipeline
    self._logger = logger
    self._config = channels_config
    self._fallback_ids: set[int] = set(fallback_allowed_chat_ids or [])
    self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue()
```

- [ ] **Step 6: Sostituisci `update_config`**

```python
def update_config(self, new_config: ChannelsConfig) -> None:
    self._config = new_config
    self._channel_resolver.reload()
    self._logger.info(
        "listener config updated | active_channels=%d",
        len(new_config.active_channels),
    )
```

- [ ] **Step 7: Sostituisci `_process_item`**

```python
def _process_item(self, item: _QueueItem) -> None:
    entry = self._channel_resolver.lookup(item.source_chat_id, item.source_topic_id)
    if entry is None or not entry.active:
        self._logger.debug(
            "no active channel entry | raw_message_id=%s chat=%s topic=%s",
            item.raw_message_id,
            item.source_chat_id,
            item.source_topic_id,
        )
        return

    envelope = self._raw_repo.get_by_id(item.raw_message_id)

    raw_context = RawContext(
        raw_text=envelope.raw_text or "",
        message_id=envelope.telegram_message_id,
        reply_to_message_id=envelope.reply_to_message_id,
        source_chat_id=envelope.source_chat_id,
        source_topic_id=envelope.source_topic_id,
    )
    parser_context = ParserContext(
        raw_context=raw_context,
        message_id=envelope.telegram_message_id,
        reply_to_message_id=envelope.reply_to_message_id,
        source_chat_id=envelope.source_chat_id,
        source_topic_id=envelope.source_topic_id,
    )
    resolved = ResolvedTraderContext(
        raw_message_id=item.raw_message_id,
        trader_id=entry.trader_id,
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=datetime.now(timezone.utc),
    )
    candidate = ParserDispatchCandidate(
        raw_message=envelope,
        resolved_trader=resolved,
        parser_profile=entry.parser_profile,
        parser_context=parser_context,
    )

    result = self._parser_pipeline.process(candidate)
    if isinstance(result, ParserJobStatus):
        self._logger.warning(
            "parse failed | raw_message_id=%s reason=%s",
            item.raw_message_id,
            result.reason,
        )
    else:
        self._logger.info(
            "parsed | raw_message_id=%s canonical_id=%s class=%s status=%s",
            item.raw_message_id,
            result.canonical_message_id,
            result.primary_class,
            result.parse_status,
        )
```

- [ ] **Step 8: Correggi `_is_blacklisted` per usare la funzione locale**

```python
def _is_blacklisted(self, raw_text: str, chat_id: int | None, topic_id: int | None = None) -> bool:
    return _is_blacklisted_text(self._config, raw_text, chat_id, topic_id)
```

- [ ] **Step 9: Aggiungi `datetime` agli import se mancante**

Verifica che in cima al file ci sia:
```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 10: Esegui i test per vedere cosa si rompe**

```bash
python -m pytest src/telegram/tests/ -x --tb=short -q 2>&1 | head -40
```

Expected: errori nei test che usano `router=MagicMock()` — li risolviamo nel Task 3.

- [ ] **Step 11: Commit del refactor listener**

```bash
git add src/telegram/listener.py
git commit -m "refactor(listener): remove router/sidecar, wire runtime_v2 pipeline as primary path"
```

---

## Task 3: Fix test suite `src/telegram/tests/`

**Files:**
- Delete: `src/telegram/tests/test_router.py`
- Delete: `src/telegram/tests/test_router_canonical_v1.py`
- Delete: `src/telegram/tests/test_router_integration.py`
- Delete: `src/telegram/tests/test_router_parsed_message.py`
- Delete: `src/telegram/tests/test_router_phase4.py`
- Delete: `src/telegram/tests/test_router_shadow.py`
- Delete: `src/telegram/tests/test_router_targeted_runtime.py`
- Delete: `src/telegram/tests/test_router_topic.py`
- Modify: `src/telegram/tests/test_listener_blacklist.py`
- Modify: `src/telegram/tests/test_listener_media.py`
- Modify: `src/telegram/tests/test_listener_recovery.py`
- Modify: `src/telegram/tests/test_listener_recovery_topic.py`
- Modify: `src/telegram/tests/test_listener_topic.py`
- Modify: `src/telegram/tests/test_reply_chain.py`
- Modify: `src/telegram/tests/test_topic_integration.py`

- [ ] **Step 1: Elimina i file di test router**

```bash
git rm src/telegram/tests/test_router.py \
       src/telegram/tests/test_router_canonical_v1.py \
       src/telegram/tests/test_router_integration.py \
       src/telegram/tests/test_router_parsed_message.py \
       src/telegram/tests/test_router_phase4.py \
       src/telegram/tests/test_router_shadow.py \
       src/telegram/tests/test_router_targeted_runtime.py \
       src/telegram/tests/test_router_topic.py
```

- [ ] **Step 2: Aggiorna `_make_listener` in ogni test rimanente**

In ciascuno dei seguenti file, sostituisci la funzione `_make_listener` (o il punto in cui `TelegramListener` viene costruito con `router=MagicMock()`) con la nuova signature:

```python
from unittest.mock import MagicMock
from src.telegram.listener import TelegramListener

def _make_listener(config: ChannelsConfig) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        raw_repo=MagicMock(),
        channel_resolver=MagicMock(),
        parser_pipeline=MagicMock(),
        logger=MagicMock(),
        channels_config=config,
    )
```

Applica questa modifica in:
- `src/telegram/tests/test_listener_blacklist.py`
- `src/telegram/tests/test_listener_media.py`
- `src/telegram/tests/test_listener_recovery.py`
- `src/telegram/tests/test_listener_recovery_topic.py`
- `src/telegram/tests/test_listener_topic.py`
- `src/telegram/tests/test_reply_chain.py`
- `src/telegram/tests/test_topic_integration.py`

- [ ] **Step 3: Esegui i test**

```bash
python -m pytest src/telegram/tests/ -x --tb=short -q
```

Expected: tutti i test rimanenti passano.

- [ ] **Step 4: Commit**

```bash
git add src/telegram/tests/
git commit -m "test(listener): remove router tests, update listener tests to v2 interface"
```

---

## Task 4: Scrivi test per `_process_item` con runtime_v2

**Files:**
- Create: `src/telegram/tests/test_listener_process_item.py`

- [ ] **Step 1: Scrivi i test**

```python
"""Tests for TelegramListener._process_item with runtime_v2 pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelEntry
from src.telegram.channel_config import ChannelsConfig
from src.telegram.listener import TelegramListener, _QueueItem


def _make_config() -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=[],
        channels=[],
    )


def _make_listener(
    *,
    channel_resolver: MagicMock | None = None,
    parser_pipeline: MagicMock | None = None,
    raw_repo: MagicMock | None = None,
) -> TelegramListener:
    return TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        raw_repo=raw_repo or MagicMock(),
        channel_resolver=channel_resolver or MagicMock(),
        parser_pipeline=parser_pipeline or MagicMock(),
        logger=MagicMock(),
        channels_config=_make_config(),
    )


def _make_queue_item(raw_message_id: int = 1) -> _QueueItem:
    return _QueueItem(
        raw_message_id=raw_message_id,
        source_chat_id="-100123",
        telegram_message_id=42,
        raw_text="BUY BTCUSDT",
        source_trader_id=None,
        reply_to_message_id=None,
        acquisition_mode="live",
        source_topic_id=None,
    )


def _make_channel_entry(parser_profile: str = "trader_a") -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100123",
        topic_id=None,
        label="test",
        active=True,
        trader_id="trader_a",
        parser_profile=parser_profile,
        blacklist=[],
    )


def test_process_item_no_channel_entry_skips() -> None:
    """Se il canale non è configurato, il messaggio viene ignorato."""
    resolver = MagicMock()
    resolver.lookup.return_value = None
    pipeline = MagicMock()

    listener = _make_listener(channel_resolver=resolver, parser_pipeline=pipeline)
    listener._process_item(_make_queue_item())

    pipeline.process.assert_not_called()


def test_process_item_inactive_channel_skips() -> None:
    """Se il canale è configurato ma non attivo, il messaggio viene ignorato."""
    entry = ChannelEntry(
        chat_id="-100123",
        topic_id=None,
        label="test",
        active=False,
        trader_id="trader_a",
        parser_profile="trader_a",
        blacklist=[],
    )
    resolver = MagicMock()
    resolver.lookup.return_value = entry
    pipeline = MagicMock()

    listener = _make_listener(channel_resolver=resolver, parser_pipeline=pipeline)
    listener._process_item(_make_queue_item())

    pipeline.process.assert_not_called()


def test_process_item_calls_pipeline_on_active_channel(tmp_path) -> None:
    """Un canale attivo produce una chiamata a pipeline.process()."""
    from src.runtime_v2.intake.models import RawMessageEnvelope

    entry = _make_channel_entry()
    resolver = MagicMock()
    resolver.lookup.return_value = entry

    envelope = RawMessageEnvelope(
        raw_message_id=1,
        source_chat_id="-100123",
        source_chat_title=None,
        source_type=None,
        source_topic_id=None,
        telegram_message_id=42,
        reply_to_message_id=None,
        raw_text="BUY BTCUSDT",
        message_ts=datetime.now(timezone.utc),
        acquired_at=datetime.now(timezone.utc),
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="done",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )
    raw_repo = MagicMock()
    raw_repo.get_by_id.return_value = envelope

    parse_result = MagicMock(spec=CanonicalParseResult)
    parse_result.canonical_message_id = 99
    parse_result.primary_class = "SIGNAL"
    parse_result.parse_status = "PARSED"
    pipeline = MagicMock()
    pipeline.process.return_value = parse_result

    listener = _make_listener(
        channel_resolver=resolver,
        raw_repo=raw_repo,
        parser_pipeline=pipeline,
    )
    listener._process_item(_make_queue_item(raw_message_id=1))

    raw_repo.get_by_id.assert_called_once_with(1)
    pipeline.process.assert_called_once()
    candidate = pipeline.process.call_args[0][0]
    assert candidate.parser_profile == "trader_a"
    assert candidate.raw_message.raw_message_id == 1


def test_process_item_logs_warning_on_failed_parse() -> None:
    """Un ParserJobStatus(failed) produce un log warning, non un'eccezione."""
    entry = _make_channel_entry()
    resolver = MagicMock()
    resolver.lookup.return_value = entry

    raw_repo = MagicMock()

    from src.runtime_v2.intake.models import RawMessageEnvelope
    raw_repo.get_by_id.return_value = RawMessageEnvelope(
        raw_message_id=1,
        source_chat_id="-100123",
        source_chat_title=None,
        source_type=None,
        source_topic_id=None,
        telegram_message_id=42,
        reply_to_message_id=None,
        raw_text="",
        message_ts=datetime.now(timezone.utc),
        acquired_at=datetime.now(timezone.utc),
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="done",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )

    failed = ParserJobStatus(raw_message_id=1, status="failed", reason="unknown_parser_profile")
    pipeline = MagicMock()
    pipeline.process.return_value = failed

    logger = MagicMock()
    listener = TelegramListener(
        ingestion_service=MagicMock(),
        processing_status_store=MagicMock(),
        raw_repo=raw_repo,
        channel_resolver=resolver,
        parser_pipeline=pipeline,
        logger=logger,
        channels_config=_make_config(),
    )
    listener._process_item(_make_queue_item(raw_message_id=1))

    logger.warning.assert_called_once()
    assert "parse failed" in logger.warning.call_args[0][0]
```

- [ ] **Step 2: Esegui i test per verificare che passino**

```bash
python -m pytest src/telegram/tests/test_listener_process_item.py -v
```

Expected: 4 PASSED

- [ ] **Step 3: Commit**

```bash
git add src/telegram/tests/test_listener_process_item.py
git commit -m "test(listener): add _process_item tests for runtime_v2 pipeline path"
```

---

## Task 5: Riscrittura `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Sostituisci `main.py` con la versione semplificata**

```python
"""TeleSignalBot entrypoint — runtime_v2 stack."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from src.core.logger import setup_logging
from src.core.migrations import apply_migrations
from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
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

    channels_yaml_path = str(root_dir / "config" / "channels.yaml")
    channels_config = load_channels_config(channels_yaml_path)
    fallback_ids = _parse_fallback_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
    if fallback_ids:
        logger.warning(
            "TELEGRAM_ALLOWED_CHAT_IDS fallback active (%d ids) — "
            "move channels to config/channels.yaml to remove this warning",
            len(fallback_ids),
        )

    ingestion_service = build_ingestion_service(db_path=db_path, logger=logger)
    processing_status_store = build_processing_status_store(db_path=db_path)

    raw_repo = RawMessageRepository(db_path=db_path)
    channel_resolver = ChannelConfigResolver(config_path=channels_yaml_path)
    canonical_repo = CanonicalMessageRepository(db_path=db_path)
    parser_pipeline = ParserPipelineProcessor(canonical_repo=canonical_repo)

    listener = TelegramListener(
        ingestion_service=ingestion_service,
        processing_status_store=processing_status_store,
        raw_repo=raw_repo,
        channel_resolver=channel_resolver,
        parser_pipeline=parser_pipeline,
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
```

- [ ] **Step 2: Verifica che `main.py` si importi senza errori**

```bash
python -c "import main; print('OK')"
```

Expected: `OK` senza errori di import.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat(main): riscrittura entrypoint su stack runtime_v2 — router legacy rimosso"
```

---

## Task 6: Elimina `listener_sidecar.py`

**Files:**
- Delete: `src/runtime_v2/listener_sidecar.py`

- [ ] **Step 1: Verifica che non ci siano import residui**

```bash
grep -rn "listener_sidecar" src/ main.py
```

Expected: nessun output (o solo commenti/docs).

- [ ] **Step 2: Elimina il file**

```bash
git rm src/runtime_v2/listener_sidecar.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove listener_sidecar.py — sidecar pattern replaced by direct pipeline"
```

---

## Task 7: Full test run e verifica finale

- [ ] **Step 1: Esegui tutta la suite**

```bash
python -m pytest src/ tests/ -x --tb=short -q
```

Expected: tutti i test passano. Se ci sono fallimenti residui, analizza l'output e correggi.

- [ ] **Step 2: Verifica import di `main.py` e nessun riferimento legacy attivo**

```bash
python -c "
import main
import inspect, ast, sys

src = open('main.py').read()
forbidden = ['MessageRouter', 'OperationRulesEngine', 'TargetResolver',
             'SignalsStore', 'OperationalSignalsStore', 'ParseResultStore',
             'RuntimeV2ListenerSidecar', 'DynamicPairlistManager']
found = [f for f in forbidden if f in src]
if found:
    print('FAIL — trovati import legacy:', found)
    sys.exit(1)
print('OK — nessun import legacy in main.py')
"
```

Expected: `OK — nessun import legacy in main.py`

- [ ] **Step 3: Verifica che le tabelle legacy non esistano nel DB**

```bash
python -c "
import sqlite3, os
db = os.getenv('DB_PATH', 'db/tele_signal_bot.sqlite3')
conn = sqlite3.connect(db)
tables = {r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()}
legacy = {'parse_results','parse_results_v1','parsed_messages','review_queue',
          'operational_signals','signals','orders','fills','positions',
          'exchange_events','backtest_runs','backtest_trades','protective_orders_mode'}
found = tables & legacy
print('Tabelle legacy rimanenti:', found or 'nessuna')
conn.close()
"
```

Expected: `Tabelle legacy rimanenti: nessuna`

- [ ] **Step 4: Commit finale**

```bash
git add -A
git commit -m "chore: legacy elimination complete — runtime_v2 is primary stack"
```
