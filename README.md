# TeleSignalBot

Sistema di trading automatico che acquisisce segnali da canali Telegram, li parsa in formato canonico e li esegue su exchange tramite ccxt/Bybit.

## Architettura

```
Telegram channels
      ↓
TelegramListener  (Telethon)
      ↓
raw_messages  (parser.sqlite3)
      ↓
TraderResolver  →  ParserPipelineProcessor  (parser_v2)
      ↓
CanonicalMessage  (parser_results_v2)
      ↓
SignalEnrichmentProcessor  (enriched_canonical_messages)
      ↓
LifecycleGateWorker  →  LifecycleEventWorker
      ↓
ExecutionGateway  (ccxt/Bybit)  ←→  ops.sqlite3
      ↓
ControlPlane  (Telegram bot)
```

Due database separati:
- `parser.sqlite3` — raw messages, canonical messages, parser results, enriched signals
- `ops.sqlite3` — trade chains, lifecycle events, execution commands, exchange events, control plane

## Struttura repository

```
TeleSignalBot/
├── main.py                       → entrypoint runtime_v2
├── main_linux_server.py          → entrypoint per Linux
├── CLAUDE.md                     → istruzioni per Claude Code
├── config/
│   ├── channels.yaml             → canali Telegram e trader attivi
│   ├── execution.yaml            → adapter ccxt/Bybit, strategy config
│   ├── operation_config.yaml     → regole signal enrichment
│   ├── telegram_control.yaml     → control plane Telegram bot
│   └── trader_aliases.json       → mapping alias trader
├── db/
│   ├── migrations/               → schema parser.sqlite3 (incrementale)
│   └── ops_migrations/           → schema ops.sqlite3 (incrementale)
├── docs/
│   └── AUDIT.md                  → registro step completati e rischi aperti
├── scripts/                      → script operativi (inspect, reset, monitor)
├── skills/                       → skill per Claude Code
├── src/
│   ├── core/                     → logger, config_loader, migrations, trader_tags, timeutils
│   ├── storage/                  → raw_messages, processing_status, signals_store,
│   │                               parser_runs, parser_results_v2
│   ├── telegram/                 → listener, ingestion, channel_config, eligibility,
│   │                               trader_resolver, trader_mapping, effective_trader,
│   │                               topic_utils, pattern_extractors
│   ├── parser_v2/
│   │   ├── contracts/            → CanonicalMessage, ParsedMessage, enums, entities,
│   │   │                           context, markers
│   │   ├── core/                 → marker_matcher, marker_evidence_resolver,
│   │   │                           parsed_message_builder, target_binding_resolver,
│   │   │                           target_hints_extractor, local_disambiguator,
│   │   │                           text_normalizer, symbol_normalizer
│   │   ├── profiles/             → trader_3, trader_a, trader_b, trader_c, trader_d,
│   │   │                           trader_prova, strategy_parser
│   │   │   └── profili_vecchi/   → archivio vecchi profili (non usare come riferimento)
│   │   └── translation/          → canonical translator
│   └── runtime_v2/
│       ├── intake/               → processor, eligibility, models
│       ├── persistence/          → raw_messages, canonical_messages
│       ├── parser_pipeline/      → processor, models
│       ├── trader_resolution/    → TraderResolver, channel_config_resolver, models
│       ├── signal_enrichment/    → processor, repository, config_loader, models
│       ├── lifecycle/            → entry_gate, event_processor, workers, execution_plan,
│       │                           diff_engine, post_fill_rebuilder, be_move_resolver,
│       │                           breakeven_pricing, cancel_expander, entry_command_factory,
│       │                           risk_capacity, static_exchange_data_port, repositories
│       ├── execution_gateway/    → gateway, command_worker, event_sync, repositories, models,
│       │   ├── adapters/         → ccxt_bybit, fake, factory, base
│       │   └── event_ingest/     → classifier, normalizer, payload, models
│       └── control_plane/        → service, telegram_bot, bootstrap, startup, notification_dispatcher,
│           │                       outbox_writer, status_queries, topic_router, debug_controller,
│           │                       audit_store, override_store, snapshot_store
│           └── formatters/       → status, pnl, trades, health, debug, clean_log, …
└── parser_test/                  → harness replay, report CSV, scripts
    ├── scripts/                  → replay_parser_v2, watch_parser, import_history, resolve_traders
    ├── reporting/                → flatteners_v2, report_export_v2, report_schema_v2
    └── db/                       → schema harness DB
```

## Parser v2 — output canonico

```
testo raw
      ↓
profilo trader  (signal_extractor + intent_entity_extractor)
      ↓
ParsedMessage  (marker evidence)
      ↓
CanonicalMessage  (output finale)
```

Campi principali di `CanonicalMessage`:
- `primary_class`: `SIGNAL | UPDATE | REPORT | INFO`
- `parse_status`: `PARSED | PARTIAL | UNCLASSIFIED | ERROR`
- `primary_intent`: intent principale (es. `CLOSE_FULL`, `MOVE_STOP_TO_BE`)
- `intents`: lista completa di `IntentType`
- `signal`: `SignalPayload` (solo per SIGNAL)
- `target_action_groups`: `TargetActionGroup[]` (solo per UPDATE)
- `report`: `ReportPayload` (solo per REPORT)
- `confidence`, `warnings`, `parser_profile`, `raw_context`

## Tecnologie

| Componente | Tecnologia |
|---|---|
| Listener | Telethon |
| Parser models | Pydantic v2 |
| Profili trader | signal_extractor + intent_entity_extractor + rules.json |
| DB | SQLite (parser + ops) |
| Exchange | ccxt / Bybit (WebSocket fill watcher + REST) |
| Control UI | Telegram bot (control_plane) |
| File watching | watchdog |

## Setup

Prerequisiti: Python 3.12+, account Telegram API

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Variabili ambiente minime (`.env`):
```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
PARSER_DB_PATH=db/parser.sqlite3
OPS_DB_PATH=db/ops.sqlite3
```

Avvio:
```bash
# applica migrazioni ed esci
python main.py --migrate

# verifica config senza avvio
python main.py --check-config

# avvio normale
python main.py
```

## Test

Usa sempre `.venv/Scripts/python.exe -m pytest` — mai `pytest` bare dal Python globale.

### Smoke suite — parser_v2 e core (rapido)

```bash
.venv/Scripts/python.exe -m pytest \
  src/parser_v2/tests/ \
  src/parser_v2/profiles/trader_3/ \
  src/parser_v2/profiles/trader_a/ \
  src/core/tests/ \
  src/telegram/tests/ \
  -q
```

### Full suite

```bash
.venv/Scripts/python.exe -m pytest \
  src/ \
  parser_test/tests/ \
  -q
```

### Replay harness su profilo specifico

```bash
python parser_test/scripts/replay_parser_v2.py --trader trader_3

# watch mode (replay automatico su modifica file)
python parser_test/scripts/watch_parser.py --trader trader_3
```

### Troubleshooting test

| Sintomo | Causa | Soluzione |
|---|---|---|
| `ModuleNotFoundError: No module named 'pydantic'` | pytest fuori dalla `.venv` | Usa `.venv/Scripts/python.exe -m pytest` |
| `ModuleNotFoundError: No module named 'src'` | CWD errata | Lancia pytest dalla root (`C:\TeleSignalBot`) |
| `PermissionError` su `.pytest_cache` o `.test_tmp` | Path non scrivibile | Verifica `pytest.ini` e `conftest.py` |
| `ERRORS` in collection, zero test raccolti | Import fallito | `pip install -r requirements.txt` |

## Documentazione

- `CLAUDE.md` — istruzioni per Claude Code
- `docs/AUDIT.md` — log step completati, file toccati, rischi aperti
- `config/execution.yaml` — configurazione adapter e strategy
- `config/operation_config.yaml` — regole signal enrichment
- `config/channels.yaml` — canali Telegram attivi; supporta `signal_message_type` per topic (`any` default, `inline_buttons` per segnali con inline buttons)
