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
canonical_messages (DB)  —  UNIQUE(raw_message_id, run_context='live')
      ↓
log: parsed | raw_message_id=X canonical_id=Y class=SIGNAL status=PARSED
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
| `CanonicalParseResult` | Output del parser pipeline — contiene `CanonicalMessage` e metadata. Input per PRD 03 (Operation Rules). |
| `ParserJobStatus` | Restituito in caso di failure del parsing — `status: failed/skipped`, con `reason`. |
| `target_action_groups` | Campo UPDATE di `CanonicalMessage` — sostituisce `update`/`targeted_actions`. Lista di `TargetActionGroup` (targeting + actions). Struttura consumata da PRD 03. |

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

## Tabelle DB

| Tabella | Stack | Stato |
|---|---|---|
| `raw_messages` | Condivisa | Attiva — usata da listener + runtime_v2 |
| `canonical_messages` | runtime_v2 | Attiva — output del parser pipeline |
| Tutte le altre | Legacy | Droppate (migration 025) |

## File configurabili

- `config/channels.yaml` — mappa canali Telegram → trader_id, parser_profile, blacklist, topic_id
- `db/migrations/023_runtime_v2_raw_messages.sql` — colonne runtime_v2 su `raw_messages`
- `db/migrations/024_runtime_v2_canonical_messages.sql` — tabella `canonical_messages`
- `db/migrations/025_drop_legacy_tables.sql` — DROP 16 tabelle legacy

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
└── test_acceptance.py                    ← slice end-to-end PRD 01 + PRD 2.b

src/telegram/tests/
└── test_listener_process_item.py         ← _process_item con runtime_v2 pipeline
```

68 test runtime_v2 + 113 test telegram, tutti passing.

## Stato PRD

| PRD | Descrizione | Stato |
|---|---|---|
| PRD 01 | Intake pipeline (raw_messages, trader resolution) | ✅ done |
| PRD 2.a | Parser v2 gap closure (RANGE, GAP A7, round-trip) | ✅ done |
| PRD 2.b | Parser pipeline integration (canonical_messages) | ✅ done |
| PRD 2.c | Legacy elimination (router rimosso, 16 tabelle droppate) | ✅ done |
| PRD 03 | Operation Rules Engine V2 | 🔜 prossimo |
