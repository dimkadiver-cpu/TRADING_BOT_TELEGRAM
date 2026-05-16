# PRD-00 - TeleSignalBot Runtime V2 - Architettura generale

**Data originale:** 2026-05-14  
**Aggiornato:** 2026-05-16  
**Stato:** Architettura baseline aggiornata allo stato corrente PRD-01/04  
**Approccio:** Clean-core redesign in parallelo, sostituzione progressiva del runtime legacy

---

## 1. Scope e visione

### Incluso

- Runtime V2 live: Listener -> Intake -> Trader Resolution -> Parser V2 -> Signal Enrichment -> Lifecycle -> Execution Gateway.
- Contratti centrali correnti: `RawIngestItem`, `RawMessageEnvelope`, `ResolvedTraderContext`, `ParserDispatchCandidate`, `CanonicalMessage`, `CanonicalParseResult`, `EnrichedCanonicalMessage`, `TradeChain`, `LifecycleEvent`, `ExecutionCommand`, `ExchangeEvent`.
- Separazione fisica `parser_db` / `ops_db`.
- 4 flussi principali: nuovo segnale live, update operativo, eventi exchange normalizzati, replay/test parser.
- Roadmap PRD 01-06 con stato aggiornato.

### Escluso / non ancora implementato

- Execution Gateway runtime_v2 concreto e adapter Hummingbot API paper.
- Telegram bot di controllo e notifiche.
- Dashboard.
- Strategy / indicator layer.
- Migrazione dati dal runtime legacy.
- Executor secondari.

### Stato corrente

| Layer | Stato | Modulo / nota |
|---|---|---|
| Telegram Listener | Implementato | `src/telegram/listener.py` cablato su runtime_v2 |
| Raw Message Ingestion / Intake | Implementato | `src/runtime_v2/intake/`, `src/runtime_v2/persistence/raw_messages.py` |
| Trader Resolution | Implementato | `src/runtime_v2/trader_resolution/` |
| Parser V2 Runtime | Implementato | `src/parser_v2/`, `src/runtime_v2/parser_pipeline/` |
| parser_test harness | Implementato | `parser_test/`, runtime condiviso `UniversalParserRuntime` |
| Signal Enrichment PRD-03 | Implementato | `src/runtime_v2/signal_enrichment/` |
| Lifecycle PRD-04 | Implementato | `src/runtime_v2/lifecycle/` |
| Execution Gateway PRD-05 | Specificato, non implementato | spec: `docs/superpowers/specs/2026-05-15-prd05-execution-gateway-design.md` |
| Audit / Review Queue PRD-06 | Non implementato come package runtime_v2 dedicato | tracciamento parziale tramite enrichment/lifecycle events |
| Persistence runtime_v2 | Implementato | `src/runtime_v2/persistence/`, `db/migrations/`, `db/ops_migrations/` |

---

## 2. Architettura corrente

### Pipeline live

```text
[1] Telegram Listener              src/telegram/listener.py
         |
         v
[2] Runtime V2 Intake              src/runtime_v2/intake/
         | RawMessageEnvelope, processing_status
         v
[3] Trader Resolution              src/runtime_v2/trader_resolution/
         | ResolvedTraderContext -> ParserDispatchCandidate
         v
[4] Parser V2 Pipeline             src/runtime_v2/parser_pipeline/
         | CanonicalMessage schema_version="canonical_message_v2"
         v
[5] Signal Enrichment PRD-03       src/runtime_v2/signal_enrichment/
         | EnrichedCanonicalMessage PASS/BLOCK/REVIEW
         v
[6] Lifecycle PRD-04               src/runtime_v2/lifecycle/
         | TradeChain + LifecycleEvent + ExecutionCommand outbox
         v
[7] Execution Gateway PRD-05       src/runtime_v2/execution_gateway/  [non implementato]
         | futuro: adapter Hummingbot API paper + eventi normalizzati
         v
    ops_exchange_events -> LifecycleEventWorker
```

### Regola fondamentale

Ogni layer conosce solo il contratto del layer immediatamente a monte. Nessun layer importa logica di un layer a valle.

`src/telegram/router.py` resta legacy e non e il modello del runtime V2. I test `runtime_v2` verificano che i nuovi moduli non importino `src.telegram.router`.

### Package corrente

```text
src/runtime_v2/
    intake/
    trader_resolution/
    parser_pipeline/
    signal_enrichment/     <- PRD-03, Gate 1 stateless
    lifecycle/             <- PRD-04, Gate 2 stateful + command outbox
    persistence/
```

Package ancora assenti:

```text
src/runtime_v2/execution_gateway/   <- PRD-05 da implementare
src/runtime_v2/audit/               <- PRD-06 da definire/implementare
```

`src/parser_v2/` e `parser_test/` restano fuori da `runtime_v2` e sono riusati dal live.

### Confini critici tra layer

| Responsabilita | Layer corrente |
|---|---|
| Riconosce semantica testuale e intenti | Parser V2 |
| Applica policy stateless: trader registrato, blacklist, SL required, TP trim, update admission | Signal Enrichment |
| Calcola risk/capacity e crea stato operativo | Lifecycle |
| Risolve target update su chain aperte | Lifecycle |
| Produce comandi neutrali verso executor | Lifecycle, tabella `ops_execution_commands` |
| Traduce comandi neutrali verso Hummingbot/API | Execution Gateway PRD-05, non implementato |
| Normalizza eventi exchange in input lifecycle | Execution Gateway PRD-05 futuro; PRD-04 consuma gia `ops_exchange_events` |

---

## 3. Contratti centrali

I nuovi contratti runtime_v2 usano Pydantic v2 strict dove rilevante (`extra="forbid"`). Nessun layer downstream deve ricevere `dict` raw come contratto primario.

### Layer 1-3: intake e trader resolution

Contratti principali:

```python
RawIngestItem          # input normalizzato dal listener
RawMessageEnvelope     # raw persistito/letto dal parser_db
ResolvedTraderContext  # trader risolto e metodo
ParserDispatchCandidate
```

Processing lifecycle su `raw_messages.processing_status`:

```text
pending -> processing -> done | failed | blacklisted | review
```

### Layer 4: `CanonicalMessage`

Contratto blindato in `src.parser_v2.contracts.canonical_message.CanonicalMessage`.

Campi principali:

```python
schema_version: str = "canonical_message_v2"
parser_profile: str
primary_class: MessageClass        # SIGNAL | UPDATE | REPORT | INFO
parse_status: ParseStatus          # PARSED | PARTIAL | UNCLASSIFIED | ERROR
confidence: float
primary_intent: IntentType | None
intents: list[IntentType]
signal: SignalPayload | None
update: UpdatePayload | None
report: ReportPayload | None
info: InfoPayload | None
target_action_groups: list[TargetActionGroup]
target_hints: TargetHints | None
warnings: list[str]
diagnostics: dict[str, Any]
raw_context: RawContext
```

Il live e `parser_test` usano lo stesso `UniversalParserRuntime`.

### Layer 5: `EnrichedCanonicalMessage`

PRD-03 ha sostituito l'idea iniziale `OperationalDecision` con un Gate 1 stateless chiamato Signal Enrichment.

Contratto principale:

```python
enrichment_id: int | None
canonical_message_id: int
raw_message_id: int
trader_id: str
account_id: str
primary_class: MessageClass
enrichment_decision: Literal["PASS", "BLOCK", "REVIEW"]
reason_code: str | None
enriched_signal: EnrichedSignalPayload | None
enriched_actions: list[TargetActionGroup] | None
management_plan: ManagementPlanConfig | None
enrichment_log: list[EnrichmentLogEntry]
policy_snapshot: dict
policy_version: str
lifecycle_processed: bool
```

`BLOCK` e `REVIEW` non rompono la pipeline: vengono persistiti e marcati come gia processati per il lifecycle.

### Layer 6: Lifecycle PRD-04

Contratti principali:

```python
TradeChain              # stato corrente della chain operativa
LifecycleEvent          # storia append-only
ExecutionCommand        # outbox neutrale verso PRD-05
ExchangeEvent           # evento exchange normalizzato in input al lifecycle
```

Stati correnti:

```text
CREATED
WAITING_ENTRY
OPEN
PARTIALLY_CLOSED
BE_MOVE_PENDING
PROTECTED_BE
CLOSED
CANCELLED
EXPIRED
REVIEW_REQUIRED
ERROR
```

Tipi comando correnti:

```text
PLACE_ENTRY
PLACE_PROTECTIVE_STOP
PLACE_TAKE_PROFIT
MOVE_STOP_TO_BREAKEVEN
MOVE_STOP
CANCEL_PENDING_ENTRY
CLOSE_PARTIAL
CLOSE_FULL
```

### Layer 7: Execution Gateway PRD-05

Non implementato nello stato corrente.

La spec approvata prevede:

```python
ExecutionCommandWorker
ExecutionGateway
ExecutionAdapter
HummingbotApiPaperAdapter
ExchangeEventSyncWorker
```

Input previsto: record `ops_execution_commands` in stato `PENDING`. Output previsto: aggiornamento comando + eventi normalizzati in `ops_exchange_events`.

---

## 4. Data Stores

### Due database fisici distinti

**`parser_db`** (`db/parser.sqlite3`)

- Scritto da: listener/intake, parser pipeline, signal enrichment, parser_test.
- Letto da: lifecycle worker per consumare `enriched_canonical_messages`.
- Migrazioni: `db/migrations/`.

**`ops_db`** (`db/ops.sqlite3`)

- Scritto da: lifecycle PRD-04; in futuro execution gateway PRD-05.
- Letto da: lifecycle, futuro execution gateway, audit/ops tools.
- Migrazioni: `db/ops_migrations/`.

Regola fondamentale: nessuna FK cross-database. `ops_db` conserva `raw_message_id`, `canonical_message_id` ed `enrichment_id` come plain integer.

### Schema parser_db corrente

Tabelle rilevanti:

```sql
raw_messages (...)
parser_runs (...)
parser_results_v2 (...)

canonical_messages (
    canonical_message_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id        INTEGER NOT NULL,
    run_context           TEXT NOT NULL DEFAULT 'live',
    parser_profile        TEXT NOT NULL,
    schema_version        TEXT NOT NULL,
    primary_class         TEXT NOT NULL,
    parse_status          TEXT NOT NULL,
    primary_intent        TEXT,
    confidence            REAL NOT NULL,
    canonical_json        TEXT NOT NULL,
    warnings_json         TEXT NOT NULL DEFAULT '[]',
    diagnostics_json      TEXT NOT NULL DEFAULT '{}',
    parsed_at             TEXT NOT NULL,
    UNIQUE(raw_message_id, run_context)
)

enriched_canonical_messages (
    enrichment_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_message_id     INTEGER NOT NULL UNIQUE,
    raw_message_id           INTEGER NOT NULL,
    trader_id                TEXT NOT NULL,
    account_id               TEXT NOT NULL,
    primary_class            TEXT NOT NULL,
    enrichment_decision      TEXT NOT NULL,
    reason_code              TEXT,
    enriched_signal_json     TEXT,
    enriched_actions_json    TEXT,
    management_plan_json     TEXT,
    enrichment_log_json      TEXT NOT NULL DEFAULT '[]',
    policy_snapshot_json     TEXT NOT NULL DEFAULT '{}',
    policy_version           TEXT NOT NULL DEFAULT '',
    lifecycle_processed      INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

Nota di drift storico: il PRD-00 originale chiamava la tabella `canonical_messages_live`; l'implementazione corrente usa `canonical_messages` con `run_context`.

### Schema ops_db corrente

Tabelle PRD-04:

```sql
ops_trade_chains (
    trade_chain_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_enrichment_id    INTEGER NOT NULL UNIQUE,
    canonical_message_id    INTEGER NOT NULL,
    raw_message_id          INTEGER NOT NULL,
    trader_id               TEXT NOT NULL,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    lifecycle_state         TEXT NOT NULL,
    entry_mode              TEXT NOT NULL,
    entry_avg_price         REAL,
    current_stop_price      REAL,
    expected_stop_price     REAL,
    be_protection_status    TEXT NOT NULL DEFAULT 'NOT_PROTECTED',
    entry_timeout_at        TEXT,
    management_plan_json    TEXT NOT NULL DEFAULT '{}',
    risk_snapshot_json      TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
)

ops_lifecycle_events (...)
ops_execution_commands (...)
ops_account_snapshots (...)
ops_market_snapshots (...)
ops_order_snapshots (...)
ops_position_snapshots (...)
ops_exchange_events (...)
ops_control_state (...)
```

Vista:

```sql
view_active_trade_chains
```

Nota di drift storico: il PRD-00 originale ipotizzava `operational_decisions`, `signal_roots`, `signal_events`, `execution_intents`, `execution_events`, `review_queue`, `audit_events`. PRD-03/04 hanno consolidato il modello corrente in `enriched_canonical_messages` + tabelle `ops_*`.

---

## 5. Flussi principali

### Flusso 1 - Nuovo segnale live

```text
1. TelegramListener riceve evento Telegram.
2. RuntimeV2IntakeProcessor salva raw message in parser_db.
3. Trader resolver produce ResolvedTraderContext.
4. Intake produce ParserDispatchCandidate.
5. ParserPipelineProcessor usa UniversalParserRuntime.
6. CanonicalMessageRepository salva in canonical_messages.
7. SignalEnrichmentProcessor produce EnrichedCanonicalMessage:
   - BLOCK/REVIEW -> persiste e stop operativo
   - PASS -> persiste con lifecycle_processed=0
8. LifecycleGateWorker consuma PASS SIGNAL.
9. LifecycleEntryGate valida risk/capacity e crea:
   - ops_trade_chains
   - ops_lifecycle_events
   - ops_execution_commands PENDING
10. PRD-05 futuro inviera i comandi all'executor.
```

### Flusso 2 - Update operativo

```text
1-7. Identico al nuovo segnale fino a EnrichedCanonicalMessage.
8. Signal Enrichment ammette o blocca update via update_admission su source_intent.
9. LifecycleGateWorker consuma PASS UPDATE.
10. LifecycleEntryGate risolve target su chain aperte.
11. Se target risolto:
    - aggiorna stato se necessario
    - emette ops_lifecycle_events
    - emette ops_execution_commands PENDING
12. Se target non risolto o ambiguo:
    - emette REVIEW_REQUIRED in ops_lifecycle_events
```

### Flusso 3 - Eventi exchange normalizzati -> Lifecycle

```text
1. PRD-05 futuro normalizza eventi executor/exchange in ops_exchange_events.
2. LifecycleEventWorker legge eventi NEW.
3. LifecycleEventProcessor applica ENTRY_FILLED, TP_FILLED, SL_FILLED.
4. Aggiorna ops_trade_chains.
5. Registra ops_lifecycle_events.
6. Se policy lo prevede, emette nuovi ops_execution_commands.
```

### Flusso 4 - Replay / parser_test

```text
1. Import messaggi in raw_messages del DB test.
2. Trader resolution.
3. UniversalParserRuntime.parse().
4. ParserResultV2Store.insert_result() -> parser_results_v2.
5. Export CSV / report qualita.
```

Regola di parita: parser_test e live usano `UniversalParserRuntime` e `CanonicalMessage` identici. Le divergenze si confrontano su raw input, trader resolution e canonical output.

---

## 6. Roadmap PRD

| PRD | Titolo | Stato |
|---|---|---|
| PRD-00 | Architettura generale | Aggiornato da questo documento |
| PRD-01 | Intake, Raw Messages, Trader Resolution | Implementato |
| PRD-02 | Parser V2 + parser_test | Implementato |
| PRD-03 | Signal Enrichment Layer | Implementato |
| PRD-04 | Lifecycle Entry Gate + Command Outbox | Implementato |
| PRD-05 | Execution Gateway + Hummingbot API Paper Adapter | Specificato, non implementato |
| PRD-06 | Audit, Review Queue, Observability | Da scrivere e implementare |

### Vertical slice corrente

```text
raw message live/test
    -> RuntimeV2IntakeProcessor
    -> RuntimeV2TraderResolver
    -> ParserPipelineProcessor
    -> canonical_messages
    -> SignalEnrichmentProcessor
    -> enriched_canonical_messages
    -> LifecycleGateWorker
    -> ops_trade_chains / ops_lifecycle_events / ops_execution_commands
```

Criteri correnti:

1. Un raw message con trader risolto produce `CanonicalMessage` v2 persistito in `canonical_messages`.
2. Un segnale non eseguibile produce `EnrichedCanonicalMessage` `BLOCK` o `REVIEW` e non rompe la pipeline.
3. Nessun codice della slice runtime_v2 importa `src.telegram.router`.
4. `parser_test` e runtime live usano lo stesso `UniversalParserRuntime`.
5. Ogni enrichment contiene `policy_snapshot`, `policy_version`, `reason_code` quando applicabile e log tracciabile.
6. Ogni comando operativo verso executor resta neutrale in `ops_execution_commands`.

---

## 7. Criteri di qualita del design

Il ridisegno e corretto se:

1. Il runtime live usa `parser_v2` per il parsing canonico.
2. `parser_test` e live usano contratti coerenti.
3. Il parser non contiene logiche operative.
4. Signal Enrichment non interpreta testo grezzo.
5. Lifecycle non ricalcola semantica del messaggio.
6. Execution Gateway non decide policy, risk o lifecycle.
7. Ogni layer ha input/output testabili indipendentemente.
8. Ogni decisione importante e tracciabile in DB tramite enrichment/lifecycle events.
9. Si puo cambiare executor senza riscrivere parser, enrichment o lifecycle.
10. I casi non eseguibili non rompono la pipeline: vengono classificati e tracciati.
11. Replay e live sono comparabili su raw input, trader resolution e canonical output.
12. `parser_db` e `ops_db` sono fisicamente separati.

---

## 8. Stato test e validazione

Validazione piu recente collegata a questo aggiornamento:

```text
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2 -q
184 passed, 2 warnings
```

Warning noti:

- `PytestConfigWarning: Unknown config option: collect_ignore_glob`
- cache pytest non scrivibile in `.pytest_cache` in ambiente Windows sandbox

Questi warning non indicano failure funzionale del runtime_v2.

---

## 9. Future work

- Implementare PRD-05 in `src/runtime_v2/execution_gateway/`.
- Definire e implementare PRD-06 audit/review/observability.
- Allineare README e documentazione generale alla terminologia corrente (`Signal Enrichment`, `TradeChain`, `ExecutionCommand`).
- Decidere se mantenere o deprecare definitivamente i nomi storici `OperationalDecision`, `signal_roots`, `execution_intents`.
- Integrare un adapter executor reale o paper senza accoppiare lifecycle a Hummingbot.
