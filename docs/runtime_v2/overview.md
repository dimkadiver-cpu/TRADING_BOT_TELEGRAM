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
      lifecycle_processed=0 → eleggibile per worker PRD 04 (SIGNAL/UPDATE PASS)
      lifecycle_processed=1 → solo audit (BLOCK, REVIEW, REPORT, INFO)
```

## Come si avvia

```bash
python main.py
```

Nessuna variabile d'ambiente aggiuntiva necessaria. Il runtime_v2 è sempre attivo.

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
| `db/ops.sqlite3` | Vuoto — riservato per PRD 04+ (lifecycle, risk, execution) |

La separazione è eseguita una volta tramite `scripts/setup_parser_db_separation.py`.

### Tabelle

| Tabella | DB | Stato |
|---|---|---|
| `raw_messages` | parser.sqlite3 | Attiva — usata da listener + runtime_v2 |
| `canonical_messages` | parser.sqlite3 | Attiva — output del parser pipeline |
| `parser_runs` / `parser_results_v2` | parser.sqlite3 | Attive — audit run di parsing |
| `enriched_canonical_messages` | parser.sqlite3 | Attiva (PRD 03) — output del signal enrichment gate |
| Tutte le altre | Legacy | Droppate (migration 025) |

### Query handoff PRD 04

Il worker PRD 04 legge da `parser.sqlite3`:
```sql
SELECT * FROM enriched_canonical_messages
WHERE lifecycle_processed = 0
  AND enrichment_decision = 'PASS'
  AND primary_class IN ('SIGNAL', 'UPDATE')
ORDER BY created_at ASC
```

## File configurabili

- `config/channels.yaml` — mappa canali Telegram → trader_id, parser_profile, blacklist, topic_id
- `config/operation_config.yaml` — config globale signal enrichment: account, trader registrati, blacklist, defaults policy/risk/management
- `config/traders/<id>.yaml` — override per-trader di operation_config.yaml
- `db/migrations/023_runtime_v2_raw_messages.sql` — colonne runtime_v2 su `raw_messages`
- `db/migrations/024_runtime_v2_canonical_messages.sql` — tabella `canonical_messages`
- `db/migrations/025_drop_legacy_tables.sql` — DROP 16 tabelle legacy
- `db/migrations/026_parser_results_v2.sql` — tabelle `parser_runs` e `parser_results_v2`
- `db/migrations/027_enriched_canonical_messages.sql` — tabella `enriched_canonical_messages` (PRD 03)

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
└── signal_enrichment/
    ├── test_models.py                    ← modelli Pydantic (4 test)
    ├── test_config_loader.py             ← OperationConfigLoader (9 test)
    ├── test_repository.py                ← repository idempotenza (5 test)
    ├── test_processor_signal.py          ← SIGNAL gate (10 test)
    ├── test_processor_update.py          ← UPDATE admission gate (3 test)
    ├── test_processor_routing.py         ← REPORT/INFO routing (2 test)
    └── test_integration.py               ← end-to-end con config reale (5 test)

src/telegram/tests/
└── test_listener_process_item.py         ← _process_item con runtime_v2 pipeline
```

559 test totali passing (suite completa).

## Stato PRD

| PRD | Descrizione | Stato |
|---|---|---|
| PRD 01 | Intake pipeline (raw_messages, trader resolution) | ✅ done |
| PRD 2.a | Parser v2 gap closure (RANGE, GAP A7, round-trip) | ✅ done |
| PRD 2.b | Parser pipeline integration (canonical_messages) | ✅ done |
| PRD 2.c | Legacy elimination (router rimosso, 16 tabelle droppate) | ✅ done |
| PRD 03 | Signal Enrichment Layer — Gate 1 stateless | ✅ done |
| PRD 04 | Lifecycle Entry Gate (stateful, usa enriched_canonical_messages) | 🔜 prossimo |
