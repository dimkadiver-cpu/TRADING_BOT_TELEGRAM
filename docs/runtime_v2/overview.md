# runtime_v2 — Panoramica generale

## Cos'è

`src/runtime_v2/` è lo stack di runtime attivo del bot. Riceve messaggi Telegram grezzi dal listener, li persiste in `raw_messages`, risolve il canale di provenienza tramite `channels.yaml`, li parsa tramite `parser_v2`, e persiste il risultato canonico in `canonical_messages`.

A partire da PRD 2.c, runtime_v2 è lo **stack primario e unico**. Il router legacy è stato rimosso dall'istanziazione. `main.py` costruisce solo componenti runtime_v2.

## Struttura package

```
src/runtime_v2/
├── intake/
│   ├── models.py          — RawIngestItem, RawMessageEnvelope, IntakeConfig
│   ├── eligibility.py     — IntakeEligibilityCheck, EligibilityOutcome
│   └── processor.py       — RuntimeV2IntakeProcessor (pipeline intake completa)
├── trader_resolution/
│   ├── models.py          — ResolvedTraderContext, ParserDispatchCandidate
│   ├── channel_config_resolver.py  — ChannelConfigResolver (lookup channels.yaml)
│   └── resolver.py        — RuntimeV2TraderResolver (config-first + fallback)
├── parser_pipeline/
│   ├── __init__.py
│   ├── models.py          — CanonicalParseResult, ParserJobStatus
│   └── processor.py       — ParserPipelineProcessor (orchestratore parsing)
├── signal_enrichment/
│   ├── __init__.py
│   ├── models.py          — EffectiveEnrichmentConfig, EnrichedCanonicalMessage, ManagementPlanConfig, …
│   ├── config_loader.py   — OperationConfigLoader (merge globale + per-trader, hot-reload, policy_version)
│   ├── repository.py      — EnrichedCanonicalMessageRepository (save idempotente, get_by_canonical_message_id)
│   └── processor.py       — SignalEnrichmentProcessor (Gate 1 stateless: SIGNAL / UPDATE / REPORT+INFO)
├── lifecycle/
│   ├── __init__.py
│   ├── models.py          — TradeChain, LifecycleEvent, ExecutionCommand, ExchangeEvent, ControlState, TERMINAL_STATES
│   ├── ports.py           — ExchangeDataPort ABC, AccountStateSnapshot, SymbolMarketSnapshot, …
│   ├── static_exchange_data_port.py  — StaticExchangeDataPort (test double, dati statici configurabili)
│   ├── repositories.py    — TradeChainRepository, LifecycleEventRepository, ExecutionCommandRepository,
│   │                         ControlStateRepository, SnapshotRepository, ExchangeEventRepository
│   ├── risk_capacity.py   — RiskCapacityEngine, RiskDecision (sizing + concurrent limits)
│   ├── entry_gate.py      — LifecycleEntryGate (logica pura), LifecycleGateWorker (orchestratore)
│   ├── event_processor.py — LifecycleEventProcessor, EventProcessorResult (ENTRY/TP/SL_FILLED)
│   └── workers.py         — TimeoutWorker (scadenza WAITING_ENTRY), LifecycleEventWorker (exchange events)
├── execution_gateway/
│   ├── models.py          — ExecutionConfig, AdapterConfig, AdapterCapabilities, AdapterResult
│   ├── config_loader.py   — loader `config/execution.yaml`
│   ├── gateway.py         — ExecutionGateway: dispatch comandi neutrali verso adapter
│   ├── command_worker.py  — ExecutionCommandWorker (consuma `ops_execution_commands`)
│   ├── event_sync.py      — ExchangeEventSyncWorker (fill adapter -> `ops_exchange_events`)
│   ├── repositories.py    — GatewayCommandRepository
│   ├── client_order_id.py — builder/parser `tsb:<chain>:<command>:<role>:<seq>`
│   └── adapters/
│       ├── base.py              — ExecutionAdapter ABC
│       ├── fake.py              — test double
│       ├── hummingbot_api.py    — adapter neutro Hummingbot API (capabilities da config)
│       ├── hummingbot_api_paper.py — alias retrocompatibile → HummingbotApiAdapter
│       └── factory.py           — build_adapter(name, cfg): crea adapter da AdapterConfig.type
└── persistence/
    ├── raw_messages.py    — RawMessageRepository (adapter su storage)
    └── canonical_messages.py  — CanonicalMessageRepository (store risultati)
```

## Flusso live (attivo)

```
python main.py
      ↓
TelegramListener (src/telegram/listener.py)
      ↓  evento Telegram ricevuto
_ingest_and_enqueue()
      ↓  salva in raw_messages, mette in coda
_process_item(QueueItem)
      ↓
ChannelConfigResolver.lookup(source_chat_id, topic_id)
      → entry None o inactive: skip silenzioso
      ↓ entry attiva
RawMessageRepository.get_by_id(raw_message_id)
      ↓ RawMessageEnvelope
build ParserContext + ResolvedTraderContext + ParserDispatchCandidate
      ↓
ParserPipelineProcessor.process(candidate)
      ↓
CanonicalMessage (via UniversalParserRuntime + profilo parser_v2)
      ↓
canonical_messages (parser.sqlite3)  —  UNIQUE(raw_message_id, run_context='live')
      ↓
SignalEnrichmentProcessor.process(CanonicalParseResult)        ← PRD 03
      ↓ carica config effettiva per trader (config/operation_config.yaml + config/traders/<id>.yaml)
      │
      ├── SIGNAL → gate blacklist / entry_structure / SL / TP trim / weights → PASS | BLOCK | REVIEW
      ├── UPDATE → gate update_admission per source_intent               → PASS | BLOCK | REVIEW
      └── REPORT / INFO → PASS diretto (lifecycle_processed=True, skip PRD 04)
      ↓
enriched_canonical_messages (parser.sqlite3)
      lifecycle_processed=0 → eleggibile per LifecycleGateWorker PRD 04 (SIGNAL/UPDATE PASS)
      lifecycle_processed=1 → solo audit (BLOCK, REVIEW, REPORT, INFO)
      ↓
LifecycleGateWorker.run_once()                                       ← PRD 04
      ↓ legge enriched_canonical_messages WHERE lifecycle_processed=0 AND decision=PASS
      │
      ├── SIGNAL → RiskCapacityEngine.validate() → sizing + concurrent limits
      │   ├── PASS  → ops_trade_chains (WAITING_ENTRY) + comandi PLACE_ENTRY/SL/TP
      │   └── FAIL  → REVIEW_REQUIRED event (nessuna chain creata)
      └── UPDATE → _resolve_targets() per scope → comandi MOVE_STOP / CLOSE / CANCEL
      ↓ ops-first atomicity: scrive ops.sqlite3 in transazione, poi lifecycle_processed=1
      ↓
ops.sqlite3 (ops_trade_chains, ops_lifecycle_events, ops_execution_commands, …)

TimeoutWorker.run_once()                                             ← PRD 04
      ↓ trova chains WAITING_ENTRY con entry_timeout_at scaduto
      → lifecycle_state=EXPIRED + CANCEL_PENDING_ENTRY + TIMEOUT_REACHED event

LifecycleEventWorker.run_once()                                      ← PRD 04
      ↓ legge ops_exchange_events WHERE processing_status='NEW'
      → LifecycleEventProcessor: ENTRY_FILLED→OPEN | TP_FILLED→PARTIALLY_CLOSED/CLOSED | SL_FILLED→CLOSED
      → BE automation: be_trigger da ManagementPlanConfig → MOVE_STOP_TO_BREAKEVEN se tp{N} raggiunto

ExecutionCommandWorker.run_once()                                    ← PRD 05
      ↓ legge ops_execution_commands PENDING / retry-ready / WAITING_POSITION su chain OPEN
      ↓
ExecutionGateway.process()
      ↓ risolve routing account da config/execution.yaml
      ↓ valida safety/capabilities/idempotenza
      ↓ se mode=live: controlla allow_live_trading + env TSB_ALLOW_LIVE_TRADING
      ↓
HummingbotApiAdapter (hummingbot_api_demo o hummingbot_api_paper)
      ↓ capabilities iniettate dalla config, non hardcoded
      ↓ invia ordine/cancel/leverage a Hummingbot API
      ↓ aggiorna ops_execution_commands: SENT / DONE / RETRY / REVIEW_REQUIRED / WAITING_POSITION

ExchangeEventSyncWorker.run_once()                                   ← PRD 05
      ↓ legge comandi SENT/ACK con client_order_id
      ↓ adapter.get_order_status()
      ↓ se FILLED: normalizza in ops_exchange_events ENTRY_FILLED / TP_FILLED / SL_FILLED
      ↓ LifecycleEventWorker consumerà l'evento al giro successivo
```

## Come si avvia

```bash
python main.py
```

Il runtime_v2 è sempre attivo. L'Execution Gateway PRD 05 si abilita solo se `HUMMINGBOT_BASE_URL` è presente:

```bash
HUMMINGBOT_BASE_URL=http://localhost:8000
HUMMINGBOT_SECRET=admin:admin
```

Senza `HUMMINGBOT_BASE_URL`, i worker lifecycle restano attivi e i comandi paper possono restare in coda senza essere inviati a Hummingbot.

## Contratti chiave

| Tipo | Significato |
|---|---|
| `acquisition_status` | Immutabile — impostato una volta: `ACQUIRED`, `BLACKLISTED`, `MEDIA_ONLY_SKIPPED` |
| `processing_status` | Mutabile — traccia lo stato intake: `pending → processing → done / review / failed`. Il parser pipeline non lo modifica. |
| `ParserDispatchCandidate` | Contratto tra intake e parser pipeline — envelope + resolved trader + parser_profile + parser_context |
| `CanonicalParseResult` | Output del parser pipeline — contiene `CanonicalMessage` e metadata. Input per `SignalEnrichmentProcessor`. |
| `ParserJobStatus` | Restituito in caso di failure del parsing — `status: failed/skipped`, con `reason`. |
| `target_action_groups` | Campo UPDATE di `CanonicalMessage` — lista di `TargetActionGroup` (targeting + actions). Consumato da PRD 03 gate UPDATE. |
| `EffectiveEnrichmentConfig` | Config effettiva per trader — merge di `operation_config.yaml` + `config/traders/<id>.yaml`. Contiene `signal_policy`, `update_admission`, `management_plan`, `risk`, `hedge_mode`. |
| `EnrichedCanonicalMessage` | Output di PRD 03 — `enrichment_decision` (PASS/BLOCK/REVIEW), `enriched_signal`, `enriched_actions`, `management_plan`, `lifecycle_processed`. Persistito in `enriched_canonical_messages`. |
| `lifecycle_processed` | Flag int (0/1) in DB — 0 = eleggibile per PRD 04 worker; 1 = audit only. I BLOCK/REVIEW e tutti i REPORT/INFO hanno `lifecycle_processed=1` al momento del salvataggio. |
| `TradeChain` | Unità stateful PRD 04 — rappresenta una singola operazione dal segnale alla chiusura. Stato: `WAITING_ENTRY → OPEN → PARTIALLY_CLOSED → CLOSED/EXPIRED/CANCELLED`. |
| `TERMINAL_STATES` | `frozenset({"CLOSED", "CANCELLED", "EXPIRED"})` — chains terminali non vengono più processate. |
| `RiskDecision` | Output di `RiskCapacityEngine` — `passed`, `reason`, `size_usdt`, `leverage`, `risk_snapshot`. |
| `SignalGateResult` | Output di `LifecycleEntryGate.process_signal` — `trade_chain`, `lifecycle_events`, `execution_commands`, `review_reason`. |
| `UpdateGateResult` | Output di `LifecycleEntryGate.process_update` — `chain_results` (list), `review_events`. |
| `EventProcessorResult` | Output di `LifecycleEventProcessor.process` — `new_lifecycle_state`, `new_be_protection_status`, `entry_avg_price`, `lifecycle_events`, `execution_commands`. |
| ops-first atomicity | Pattern di scrittura PRD 04: prima scrivi su `ops.sqlite3` (INSERT OR IGNORE, idempotente), poi aggiorna `lifecycle_processed=1` su `parser.sqlite3`. Su retry dopo crash, i duplicati sono silenziosi. |
| `ExecutionConfig` | Config PRD 05 caricata da `config/execution.yaml`: `default_adapter`, `account_routing`, `adapters`. |
| `AdapterCapabilities` | Contratto capabilities adapter: entry, stop nativo, TP nativo, move stop, close partial/full, executor_position. Iniettate al costruttore, non hardcoded nell'adapter. |
| `HummingbotApiAdapter` | Adapter neutro; sostituisce `HummingbotApiPaperAdapter` (ora alias). Riceve `capabilities` da `AdapterConfig`. |
| `build_adapter(name, cfg)` | Factory in `adapters/factory.py`: crea l'adapter concreto da `AdapterConfig.type`. Aggiungere qui nuovi tipi. |
| `client_order_id` | Identificatore deterministico inviato all'exchange: `tsb:<trade_chain_id>:<command_id>:<role>:<sequence>`. Serve per idempotenza e correlazione fill. |
| `GatewayCommandRepository` | Repository PRD 05 su `ops_execution_commands`: batch pending/retry, mark sent/done/retry/review/waiting. |
| `ExecutionCommandWorker` | Worker che consuma comandi neutrali PRD 04 e li passa all'`ExecutionGateway`. |
| `ExchangeEventSyncWorker` | Worker che interroga l'adapter e crea `ops_exchange_events` normalizzati quando un ordine risulta filled. |

## Wiring in main.py

```python
raw_repo        = RawMessageRepository(db_path=db_path)
channel_resolver = ChannelConfigResolver(config_path=channels_yaml_path)
canonical_repo  = CanonicalMessageRepository(db_path=db_path)
parser_pipeline = ParserPipelineProcessor(canonical_repo=canonical_repo)

listener = TelegramListener(
    ingestion_service=ingestion_service,
    processing_status_store=processing_status_store,
    raw_repo=raw_repo,
    channel_resolver=channel_resolver,
    parser_pipeline=parser_pipeline,
    ...
)
```

`TelegramListener._process_item` chiama direttamente `channel_resolver.lookup()` + `raw_repo.get_by_id()` + `parser_pipeline.process()`. Non esiste più un router legacy né un sidecar.

## DB

### File separati (da PRD 03)

| File | Scopo |
|---|---|
| `db/parser.sqlite3` | Copia di `tele_signal_bot.sqlite3` — contiene `raw_messages`, `canonical_messages`, `enriched_canonical_messages` e tutte le tabelle parser |
| `db/ops.sqlite3` | Attivo da PRD 04 — contiene tabelle lifecycle: `ops_trade_chains`, `ops_lifecycle_events`, `ops_execution_commands`, `ops_account_snapshots`, `ops_market_snapshots`, `ops_exchange_events`, `ops_control_state` |

La separazione è eseguita una volta tramite `scripts/setup_parser_db_separation.py`.

### Tabelle

| Tabella | DB | Stato |
|---|---|---|
| `raw_messages` | parser.sqlite3 | Attiva — usata da listener + runtime_v2 |
| `canonical_messages` | parser.sqlite3 | Attiva — output del parser pipeline |
| `parser_runs` / `parser_results_v2` | parser.sqlite3 | Attive — audit run di parsing |
| `enriched_canonical_messages` | parser.sqlite3 | Attiva (PRD 03) — output del signal enrichment gate |
| `ops_trade_chains` | ops.sqlite3 | Attiva (PRD 04) — catena operativa per segnale; UNIQUE su `source_enrichment_id` |
| `ops_lifecycle_events` | ops.sqlite3 | Attiva (PRD 04) — audit trail eventi; UNIQUE su `idempotency_key` |
| `ops_execution_commands` | ops.sqlite3 | Attiva (PRD 04) — comandi verso exchange; UNIQUE su `idempotency_key` |
| `ops_exchange_events` | ops.sqlite3 | Attiva (PRD 04) — eventi in ingresso da exchange (fills, ecc.) |
| `ops_control_state` | ops.sqlite3 | Attiva (PRD 04) — modalità di controllo globale (NONE / BLOCK_NEW_ENTRIES / FULL_STOP) |
| `ops_account_snapshots`, `ops_market_snapshots` | ops.sqlite3 | Attive (PRD 04) — snapshot audit per risk calculation |
| Tutte le altre | Legacy | Droppate (migration 025) |

### Query handoff PRD 04

Il `LifecycleGateWorker` legge da `parser.sqlite3`:
```sql
SELECT * FROM enriched_canonical_messages
WHERE lifecycle_processed = 0
  AND enrichment_decision = 'PASS'
  AND primary_class IN ('SIGNAL', 'UPDATE')
ORDER BY created_at ASC
LIMIT ?
```

Il `TimeoutWorker` legge da `ops.sqlite3`:
```sql
SELECT * FROM ops_trade_chains
WHERE lifecycle_state = 'WAITING_ENTRY'
  AND entry_timeout_at <= ?   -- datetime.now(utc)
LIMIT ?
```

Il `LifecycleEventWorker` legge da `ops.sqlite3`:
```sql
SELECT * FROM ops_exchange_events
WHERE processing_status = 'NEW'
ORDER BY received_at ASC
LIMIT ?
```

### Query handoff PRD 05

L'`ExecutionCommandWorker` legge da `ops.sqlite3` tramite `GatewayCommandRepository`:

```sql
SELECT * FROM ops_execution_commands
WHERE command_status = 'PENDING'
ORDER BY created_at ASC
LIMIT ?
```

Gestisce anche retry con `next_retry_at` scaduto e comandi `WAITING_POSITION` quando la chain diventa `OPEN`.

L'`ExchangeEventSyncWorker` legge i comandi già inviati con `client_order_id`, interroga l'adapter e scrive fill normalizzati in `ops_exchange_events`.

## File configurabili

- `config/channels.yaml` — mappa canali Telegram → trader_id, parser_profile, blacklist, topic_id
- `config/operation_config.yaml` — config globale signal enrichment: account, trader registrati, blacklist, defaults policy/risk/management
- `config/traders/<id>.yaml` — override per-trader di operation_config.yaml
- `config/execution.yaml` — config PRD 05: adapter Hummingbot API, routing account, capabilities, retry, live safety; default corrente: `hummingbot_api_demo` su porta 8001
- `.env` — opzionale: `HUMMINGBOT_BASE_URL` abilita l'Execution Gateway; `HUMMINGBOT_SECRET` imposta Bearer token o Basic auth `username:password`
- `.env.demo` — non versionato: credenziali stack demo (`BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET`, password Docker)
- `docker-compose.demo.yml` — stack Hummingbot demo parallelo: porta 8001, rete `hummingbot-demo-net`, volumi isolati
- `hummingbot_demo_patch/bybit_perpetual_constants.py` — patch connector: aggiunge `bybit_perpetual_demo → https://api-demo.bybit.com/`
- `db/migrations/023_runtime_v2_raw_messages.sql` — colonne runtime_v2 su `raw_messages`
- `db/migrations/024_runtime_v2_canonical_messages.sql` — tabella `canonical_messages`
- `db/migrations/025_drop_legacy_tables.sql` — DROP 16 tabelle legacy
- `db/migrations/026_parser_results_v2.sql` — tabelle `parser_runs` e `parser_results_v2`
- `db/migrations/027_enriched_canonical_messages.sql` — tabella `enriched_canonical_messages` (PRD 03)
- `db/migrations/028_ops_lifecycle_core.sql` — 9 tabelle + 6 indici + view `view_active_trade_chains` per `ops.sqlite3` (PRD 04)

## Test

```
tests/runtime_v2/
├── test_intake_models.py
├── test_trader_resolution_models.py
├── test_channel_config_resolver.py
├── test_raw_message_repository.py
├── test_trader_resolver.py
├── test_intake_processor.py
├── test_canonical_message_repository.py
├── test_parser_pipeline_processor.py
├── test_acceptance.py                    ← slice end-to-end PRD 01 + PRD 2.b
├── signal_enrichment/
│   ├── test_models.py                    ← modelli Pydantic (4 test)
│   ├── test_config_loader.py             ← OperationConfigLoader (9 test)
│   ├── test_repository.py                ← repository idempotenza (5 test)
│   ├── test_processor_signal.py          ← SIGNAL gate (10 test)
│   ├── test_processor_update.py          ← UPDATE admission gate (3 test)
│   ├── test_processor_routing.py         ← REPORT/INFO routing (2 test)
│   └── test_integration.py               ← end-to-end con config reale (5 test)
├── lifecycle/
│   ├── test_models.py                    ← modelli Pydantic (5 test)
│   ├── test_ports.py                     ← ExchangeDataPort + StaticExchangeDataPort (6 test)
│   ├── test_repositories.py              ← tutti i repository (12 test)
│   ├── test_risk_capacity.py             ← RiskCapacityEngine (9 test)
│   ├── test_entry_gate.py                ← LifecycleEntryGate SIGNAL+UPDATE (20 test)
│   ├── test_event_processor.py           ← LifecycleEventProcessor (8 test)
│   ├── test_workers.py                   ← LifecycleGateWorker + TimeoutWorker + LifecycleEventWorker (8 test)
│   └── test_integration.py               ← acceptance contract AC1–AC17 (9 test)
└── execution_gateway/
    ├── test_auth.py                      ← Bearer/Basic auth adapter Hummingbot
    ├── test_client_order_id.py           ← build/parse client_order_id
    ├── test_command_worker.py            ← ExecutionCommandWorker
    ├── test_config_loader.py             ← config/execution.yaml
    ├── test_event_sync.py                ← ExchangeEventSyncWorker
    ├── test_gateway.py                   ← ExecutionGateway
    ├── test_hummingbot_adapter.py        ← gated stack paper/testnet (`RUN_HUMMINGBOT_API_TESTS=1`)
    ├── test_hummingbot_demo_gated.py     ← gated stack demo porta 8001 (`RUN_HUMMINGBOT_DEMO_TESTS=1`)
    └── test_integration.py               ← acceptance contract PRD 05

src/telegram/tests/
└── test_listener_process_item.py         ← _process_item con runtime_v2 pipeline
```

Ultima validazione mirata: `pytest tests/runtime_v2 -v --tb=short` → 232 passed, 3 skipped.

Test gated stack paper/testnet (porta 8000):

```bash
RUN_HUMMINGBOT_API_TESTS=1 \
HUMMINGBOT_API_URL=http://localhost:8000 \
HUMMINGBOT_SECRET=admin:admin \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py -v
```

Test gated stack demo (porta 8001):

```bash
RUN_HUMMINGBOT_DEMO_TESTS=1 \
HUMMINGBOT_DEMO_API_URL=http://localhost:8001 \
HUMMINGBOT_DEMO_CONNECTOR=bybit_perpetual_demo \
HUMMINGBOT_DEMO_ACCOUNT=master_account \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py -v -s
```

## Stato PRD

| PRD | Descrizione | Stato |
|---|---|---|
| PRD 01 | Intake pipeline (raw_messages, trader resolution) | ✅ done |
| PRD 2.a | Parser v2 gap closure (RANGE, GAP A7, round-trip) | ✅ done |
| PRD 2.b | Parser pipeline integration (canonical_messages) | ✅ done |
| PRD 2.c | Legacy elimination (router rimosso, 16 tabelle droppate) | ✅ done |
| PRD 03 | Signal Enrichment Layer — Gate 1 stateless | ✅ done |
| PRD 04 | Lifecycle Entry Gate — stateful, ops-first atomicity, 3 worker | ✅ done |
| PRD 05 | Execution Gateway — adapter Hummingbot API, command worker, event sync | ✅ implementato; stack demo avviato su porta 8001 |
| Demo stack | Hummingbot parallelo + connector `bybit_perpetual_demo` → `api-demo.bybit.com` | ✅ infrastruttura ok; ordini reali bloccati finché API key Bybit Demo non configurate |
