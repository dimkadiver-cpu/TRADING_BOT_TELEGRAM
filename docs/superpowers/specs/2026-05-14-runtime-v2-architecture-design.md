# PRD-00 — TeleSignalBot Runtime V2 — Architettura generale

**Data:** 2026-05-14
**Stato:** Approvato — baseline per PRD 03-06
**Approccio:** Clean-core redesign in parallelo, sostituzione progressiva

---

## 1. Scope e visione

### Incluso

- Tutti e 7 i layer del runtime V2: Listener → Intake → Trader Resolution → Parser V2 → Operation Rules V2 → Lifecycle → Execution Gateway (Hummingbot)
- Contratti centrali: `RawMessageRecord`, `StoredRawMessage`, `EffectiveTraderResult`, `CanonicalMessage`, `OperationalDecision`, `RiskDecision`, `ExecutionIntent`
- Separazione fisica `parser_db` / `ops_db`
- 4 flussi principali: nuovo segnale live, update operativo, eventi exchange, replay/test
- Roadmap PRD 01-06 con stato di avanzamento

### Escluso (future work)

- Telegram bot di controllo e notifiche
- Dashboard
- Strategy / indicator layer
- Migrazione dati dal runtime legacy
- Executor secondari (Freqtrade, exchange API diretto)

### Stato baseline

| Layer | Stato | Modulo |
|---|---|---|
| Telegram Listener | ✅ Implementato | `src/telegram/listener.py` |
| Raw Message Ingestion | ✅ Implementato | `src/telegram/ingestion.py` |
| Trader Resolution | ✅ Implementato | `src/telegram/effective_trader.py` |
| Parser V2 Runtime | ✅ Implementato | `src/parser_v2/` |
| parser_test harness | ✅ Implementato | `parser_test/` |
| Operation Rules V2 | ❌ Da costruire | `src/runtime_v2/operation_rules/` |
| Lifecycle Engine | ❌ Da costruire | `src/runtime_v2/lifecycle/` |
| Execution Gateway (Hummingbot) | ❌ Da costruire | `src/runtime_v2/execution_gateway/` |
| Audit / Review Queue | ❌ Da costruire | `src/runtime_v2/audit/` |
| Persistence runtime_v2 | ❌ Da costruire | `src/runtime_v2/persistence/` |

---

## 2. Architettura target

### Pipeline

```
[1] Telegram Listener          src/telegram/listener.py          ← ESISTENTE
         ↓ evento Telegram grezzo
[2] Raw Message Ingestion      src/telegram/ingestion.py         ← ESISTENTE
         ↓ StoredRawMessage (persistito in parser_db)
[3] Trader Resolution          src/telegram/effective_trader.py  ← ESISTENTE
         ↓ EffectiveTraderResult
[4] Parser V2 Runtime          src/parser_v2/                    ← ESISTENTE
         ↓ CanonicalMessage (schema_version="canonical_message_v2")
[5] Operation Rules Engine V2  src/runtime_v2/operation_rules/   ← DA COSTRUIRE
         ↓ OperationalDecision
[6] Lifecycle Engine           src/runtime_v2/lifecycle/         ← DA COSTRUIRE
         ↓ LifecycleCommand → ExecutionIntent
[7] Execution Gateway          src/runtime_v2/execution_gateway/ ← DA COSTRUIRE
         ↓ Hummingbot API + ExecutionEvent normalizzati
```

### Regola fondamentale

Ogni layer conosce solo il contratto del layer immediatamente a monte. Nessun layer importa logica di un layer a valle. `src/telegram/router.py` non è il modello — va decomposto e sostituito da questa pipeline.

### Package target

```
src/runtime_v2/
    operation_rules/        ← PRD-03
    lifecycle/              ← PRD-04
    execution_gateway/      ← PRD-05
    audit/                  ← PRD-06
    persistence/
        parser_db/          ← read/write canonical_messages_live, read-only raw_messages
        ops_db/             ← decisions, signals, execution, audit
        connection.py       ← due connessioni separate
```

`src/parser_v2/` e `parser_test/` restano dove sono.

### Confini critici tra layer

| Responsabilità | Layer |
|---|---|
| Riconosce MOVE_STOP dal testo | Parser V2 |
| Decide se MOVE_STOP è ammesso dalla policy | Operation Rules V2 |
| Applica MOVE_STOP allo stato operativo | Lifecycle |
| Traduce MOVE_STOP in ordine Hummingbot | Execution Gateway |
| Calcola risk/sizing | Operation Rules V2 |
| Crea/aggiorna stato segnale/posizione | Lifecycle |
| Invia comandi a exchange | Execution Gateway |

---

## 3. Contratti centrali

Tutti i contratti di layer 5-7 sono Pydantic v2 strict (`extra="forbid"`). Nessun layer passa `dict` raw tra layer.

### Layer 1-3: contratti esistenti (dataclass)

Il dato grezzo transita attraverso tre dataclass:

```
TelegramIncomingMessage   ← input a RawMessageIngestionService
        ↓
RawMessageRecord          ← scritto su parser_db
        ↓
StoredRawMessage          ← letto nei layer successivi
```

Campi chiave di `StoredRawMessage`:
```python
raw_message_id: int
source_chat_id: str
telegram_message_id: int
source_trader_id: str | None    # hint dalla source map, non resolved
raw_text: str | None
reply_to_message_id: int | None
source_topic_id: int | None
has_media: bool
```

Processing lifecycle su `raw_messages.processing_status`:
```
pending → processing → done | failed | blacklisted | review
```

Output Trader Resolution — `EffectiveTraderResult`:
```python
trader_id: str | None
method: str     # content_alias | reply_chain | source_* | unresolved | content_alias_ambiguous
detail: str | None
```

### Layer 4: `CanonicalMessage` — contratto blindato

```python
# src.parser_v2.contracts.canonical_message.CanonicalMessage
# Non modificare — usare come input dei layer a valle

schema_version: str                        # = "canonical_message_v2"
parser_profile: str
primary_class: MessageClass                # SIGNAL | UPDATE | REPORT | INFO
parse_status: ParseStatus                  # PARSED | PARTIAL | UNCLASSIFIED | ERROR
confidence: float                          # 0.0–1.0
primary_intent: IntentType | None
intents: list[IntentType]
signal: SignalPayload | None
update: UpdatePayload | None               # contiene list[UpdateOperation]
report: ReportPayload | None               # contiene list[ReportEvent]
info: InfoPayload | None
targeted_actions: list[TargetedAction]     # UPDATE con target hints espliciti
target_hints: TargetHints | None
warnings: list[str]
diagnostics: dict[str, Any]
raw_context: RawContext
```

`UpdateOperationType`: `SET_STOP | CLOSE | CANCEL_PENDING | MODIFY_ENTRIES | MODIFY_TARGETS | INVALIDATE_SETUP`

`IntentType`: `MOVE_STOP | MOVE_STOP_TO_BE | CLOSE_FULL | CLOSE_PARTIAL | CANCEL_PENDING | INVALIDATE_SETUP | REENTER | ADD_ENTRY | MODIFY_ENTRY | MODIFY_TARGETS | ENTRY_FILLED | TP_HIT | SL_HIT | EXIT_BE | REPORT_RESULT | INFO_ONLY`

### Layer 5: `OperationalDecision` — da definire in PRD-03

```python
decision_type: Literal["ACCEPT", "BLOCK", "REVIEW", "IGNORE", "LOG_ONLY"]
decision_scope: MessageClass               # SIGNAL | UPDATE | REPORT | INFO
reason_code: str | None
warnings: list[str]
applied_rules: list[str]
policy_snapshot: dict                      # snapshot immutabile delle regole usate
risk_decision: RiskDecision | None
idempotency_key: str
```

`RiskDecision` (embedded):
```python
sizing_mode: Literal["IMMEDIATE", "DEFERRED", "NOT_APPLICABLE"]
risk_pct_of_capital: float | None
position_size_usdt: float | None
leverage: float | None
deferred_reason: str | None                # per entry MARKET senza prezzo affidabile
```

### Layer 6→7: `ExecutionIntent` — da definire in PRD-05

```python
intent_type: Literal[
    "SUBMIT_NEW_SIGNAL", "PLACE_ENTRY", "PLACE_STOP", "PLACE_TAKE_PROFITS",
    "CANCEL_PENDING_ENTRY", "MOVE_STOP", "CLOSE_PARTIAL", "CLOSE_FULL", "SYNC_STATE"
]
signal_root_id: int
attempt_key: str                           # idempotenza verso Hummingbot
payload: dict
```

---

## 4. Data Stores

### Due database fisici distinti

**`parser_db`** — posseduto da intake + parser pipeline
- Scritto da: Listener, Ingestion, Trader Resolution, Parser V2, parser_test
- Letto da: Operation Rules V2 (read-only, per recuperare canonical)

**`ops_db`** — posseduto dal layer operativo
- Scritto da: Operation Rules V2, Lifecycle, Execution Gateway, Audit
- Letto da: Lifecycle, Execution Gateway, Audit, Review Queue

**Regola fondamentale:** nessuna FK cross-database. `ops_db` memorizza `raw_message_id` e `canonical_message_id` come plain integer. Accesso cross-db tramite interfaccia repository esplicita.

### Schema `parser_db`

```sql
-- esistente, non modificare
raw_messages (
    raw_message_id        INTEGER PRIMARY KEY,
    source_chat_id        TEXT,
    telegram_message_id   INTEGER,
    source_trader_id      TEXT,
    raw_text              TEXT,
    reply_to_message_id   INTEGER,
    source_topic_id       INTEGER,
    has_media             INTEGER,
    processing_status     TEXT,   -- pending|processing|done|failed|blacklisted|review
    acquisition_status    TEXT,
    message_ts            TEXT,
    acquired_at           TEXT,
    UNIQUE(source_chat_id, telegram_message_id)
)

-- esistente, non modificare
parser_runs (...)
parser_results_v2 (...)

-- nuovo
canonical_messages_live (
    canonical_message_id  INTEGER PRIMARY KEY,
    raw_message_id        INTEGER,              -- logico, no FK cross-db
    trader_id             TEXT,
    parser_profile        TEXT,
    primary_class         TEXT,
    parse_status          TEXT,
    confidence            REAL,
    canonical_json        TEXT,
    warnings_json         TEXT,
    created_at            TEXT,
    UNIQUE(raw_message_id)
)
```

### Schema `ops_db`

```sql
operational_decisions (
    decision_id           INTEGER PRIMARY KEY,
    raw_message_id        INTEGER,              -- copia da parser_db, no FK
    canonical_message_id  INTEGER,              -- copia da parser_db, no FK
    trader_id             TEXT,
    decision_type         TEXT,                 -- ACCEPT|BLOCK|REVIEW|IGNORE|LOG_ONLY
    decision_scope        TEXT,                 -- SIGNAL|UPDATE|REPORT|INFO
    reason_code           TEXT,
    warnings_json         TEXT,
    applied_rules_json    TEXT,
    policy_snapshot_json  TEXT,
    risk_decision_json    TEXT,
    idempotency_key       TEXT UNIQUE,
    created_at            TEXT
)

signal_roots (
    signal_root_id        INTEGER PRIMARY KEY,
    decision_id           INTEGER REFERENCES operational_decisions,
    trader_id             TEXT,
    symbol                TEXT,
    side                  TEXT,                 -- LONG|SHORT
    state                 TEXT,
    created_at            TEXT,
    updated_at            TEXT
)

signal_events (
    event_id              INTEGER PRIMARY KEY,
    signal_root_id        INTEGER REFERENCES signal_roots,
    event_type            TEXT,
    payload_json          TEXT,
    idempotency_key       TEXT UNIQUE,
    created_at            TEXT
)

signal_state_snapshots (
    snapshot_id           INTEGER PRIMARY KEY,
    signal_root_id        INTEGER REFERENCES signal_roots,
    state                 TEXT,
    snapshot_json         TEXT,
    created_at            TEXT
)

execution_intents (
    intent_id             INTEGER PRIMARY KEY,
    signal_root_id        INTEGER REFERENCES signal_roots,
    intent_type           TEXT,
    attempt_key           TEXT UNIQUE,
    payload_json          TEXT,
    status                TEXT,                 -- PENDING|SENT|ACK|FAILED
    created_at            TEXT
)

execution_events (
    event_id              INTEGER PRIMARY KEY,
    intent_id             INTEGER REFERENCES execution_intents,
    event_type            TEXT,
    raw_payload_json      TEXT,
    created_at            TEXT
)

review_queue (
    review_id             INTEGER PRIMARY KEY,
    raw_message_id        INTEGER,
    canonical_message_id  INTEGER,
    decision_id           INTEGER,
    reason                TEXT,
    state                 TEXT,                 -- OPEN|RESOLVED|DISMISSED
    created_at            TEXT
)

audit_events (
    audit_id              INTEGER PRIMARY KEY,
    entity_type           TEXT,
    entity_id             INTEGER,
    event_type            TEXT,
    payload_json          TEXT,
    created_at            TEXT
)
```

### Stati lifecycle (preliminari — da validare in PRD-04)

```
SETUP_RECEIVED → RULES_ACCEPTED → PENDING_ENTRY → OPEN
    → PARTIALLY_CLOSED → CLOSED
    → CANCELLED | INVALIDATED | REVIEW_REQUIRED | ERROR
```

---

## 5. Flussi principali

### Flusso 1 — Nuovo segnale live

```
1.  TelegramListener riceve evento
2.  RawMessageIngestionService.ingest()
        → raw_messages (parser_db) | processing_status = "pending"
3.  Dispatcher apre job | processing_status = "processing"
4.  EffectiveTraderResolver.resolve() → EffectiveTraderResult
5.  UniversalParserRuntime.parse() → CanonicalMessage
6.  CanonicalMessagesRepository.save()
        → canonical_messages_live (parser_db) | processing_status = "done"
7.  OperationRulesEngineV2.evaluate(CanonicalMessage) → OperationalDecision
8.  DecisionsRepository.save() → operational_decisions (ops_db)
9.  [se ACCEPT] LifecycleEngine.apply()
        → signal_root + signal_event CREATE_SETUP (ops_db)
        → ExecutionIntent "SUBMIT_NEW_SIGNAL"
10. ExecutionIntentsRepository.save() → execution_intents (ops_db)
11. ExecutionGateway.send() → API Hummingbot → execution_events (ops_db)
12. AuditRepository.record() ad ogni step significativo
```

### Flusso 2 — Update operativo (es. MOVE_STOP)

```
1-6. Identico al flusso 1 fino al canonical persistito

7.  OperationRulesEngineV2.evaluate(CanonicalMessage{UPDATE})
        → verifica ammissibilità policy → ACCEPT | BLOCK | REVIEW
8.  [se ACCEPT] LifecycleEngine.apply_update()
        → legge signal_roots aperti del trader (ops_db)
        → risolve target tramite target_hints / targeted_actions
        → signal_event APPLY_UPDATE → ExecutionIntent "MOVE_STOP"
9.  [se target non risolvibile] → SEND_TO_REVIEW → review_queue (ops_db)
10. ExecutionGateway.send() → API Hummingbot
```

### Flusso 3 — Eventi exchange → Lifecycle

```
1. Hummingbot notifica evento (fill, reject, TP hit, SL hit)
2. ExecutionGateway normalizza → ExecutionEvent
3. execution_events (ops_db)
4. LifecycleEngine.handle_execution_event()
        → aggiorna signal_root.state
        → emette signal_event
        → [se policy lo prevede] produce nuovo ExecutionIntent
          es: TP2 HIT → MOVE_STOP a BE
5. AuditRepository.record()
```

### Flusso 4 — Replay / parser_test

```
1. Import messaggi in raw_messages (parser_db di test separato)
2. EffectiveTraderResolver.resolve()   ← stessa logica del live
3. UniversalParserRuntime.parse()      ← stesso runtime del live
4. ParserResultV2Store.insert_result() → parser_results_v2
5. Export CSV / report qualità
```

**Regola di parità:** parser_test e live usano `UniversalParserRuntime` e `CanonicalMessage` identici. Divergenze tracciabili confrontando `canonical_json` sulle stesse coppie `(raw_message_id, trader_id)`.

---

## 6. Roadmap PRD

| PRD | Titolo | Stato |
|---|---|---|
| PRD-00 | Architettura generale | ✅ Questo documento |
| PRD-01 | Intake, Raw Messages, Trader Resolution | ✅ Già implementato |
| PRD-02 | Parser V2 + parser_test | ✅ Già implementato |
| PRD-03 | Operation Rules Engine V2 | ❌ Da scrivere e implementare |
| PRD-04 | Lifecycle Engine + Signal State Model | ❌ Da scrivere e implementare |
| PRD-05 | Execution Gateway + Hummingbot Adapter | ❌ Da scrivere e implementare |
| PRD-06 | Audit, Review Queue, Observability | ❌ Da scrivere e implementare |

### Prima vertical slice (dopo PRD-00)

```
raw_message già in parser_db
    → EffectiveTraderResolver  [esistente]
    → UniversalParserRuntime   [esistente]
    → canonical_messages_live  [nuovo — parser_db]
    → OperationRulesEngineV2   [nuovo — PRD-03 minimo]
    → operational_decisions    [nuovo — ops_db]
    → audit minimo             [nuovo]
```

**Criteri di accettazione della slice:**

1. Un raw message con trader risolto produce `CanonicalMessage` v2 persistito in `canonical_messages_live`
2. Un segnale non eseguibile produce `OperationalDecision` BLOCK/REVIEW/IGNORE — pipeline non si rompe
3. Nessun codice della slice importa `src/telegram/router.py`
4. `parser_test` e runtime live usano lo stesso `UniversalParserRuntime`
5. Ogni decisione operativa contiene `policy_snapshot`, `warnings` e `reason_code` tracciabili

---

## 7. Criteri di qualità del design

Il ridisegno è corretto se:

1. Il runtime live usa solo `parser_v2`
2. `parser_test` e live usano contratti coerenti
3. Il parser non contiene logiche operative
4. Operation Rules non interpreta testo grezzo
5. Lifecycle non ricalcola semantica del messaggio
6. Execution Gateway non decide policy
7. Ogni layer ha input/output testabili indipendentemente
8. Ogni decisione importante è tracciabile in DB/audit
9. Si può cambiare executor senza riscrivere parser/rules/lifecycle
10. I casi non eseguibili non rompono la pipeline — vengono classificati e tracciati
11. Replay e live sono comparabili su raw input, trader resolution, canonical output e decisione operativa
12. `parser_db` e `ops_db` sono fisicamente separati fin dall'inizio

---

## 8. Future work (fuori scope PRD-00)

- Telegram bot di controllo (blocco ordini, statistiche, notifiche apertura/chiusura)
- Dashboard
- Strategy / indicator layer come layer di controllo su SL/TP
- Migrazione dati dal runtime legacy (`src/telegram/router.py`)
- Executor secondari (Freqtrade, exchange API diretto)
