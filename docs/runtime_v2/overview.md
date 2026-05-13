# runtime_v2 — Panoramica generale

## Cos'è

`src/runtime_v2/` è il nuovo layer di runtime del bot. Riceve messaggi Telegram grezzi dal listener, li persiste, risolve il trader di riferimento, li parsa tramite `parser_v2`, e persiste il risultato canonico in `canonical_messages`.

Il collegamento con il listener avviene tramite `RuntimeV2ListenerSidecar`: gira in modalità shadow accanto al legacy router, attivato da `USE_RUNTIME_V2=1`.

## Struttura package

```
src/runtime_v2/
├── listener_sidecar.py    — RuntimeV2ListenerSidecar (bridge listener → runtime_v2)
├── intake/
│   ├── models.py          — RawIngestItem, RawMessageEnvelope, IntakeConfig
│   ├── eligibility.py     — IntakeEligibilityCheck, EligibilityOutcome
│   └── processor.py       — RuntimeV2IntakeProcessor (orchestratore pipeline)
├── trader_resolution/
│   ├── models.py          — ResolvedTraderContext, ParserDispatchCandidate
│   ├── channel_config_resolver.py  — ChannelConfigResolver (lookup channels.yaml)
│   └── resolver.py        — RuntimeV2TraderResolver (config-first + fallback)
├── parser_pipeline/
│   ├── __init__.py
│   ├── models.py          — CanonicalParseResult, ParserJobStatus
│   └── processor.py       — ParserPipelineProcessor (orchestratore parsing)
└── persistence/
    ├── raw_messages.py    — RawMessageRepository (adapter su storage legacy)
    └── canonical_messages.py  — CanonicalMessageRepository (store risultati parsing)
```

## Come si attiva (live)

```bash
USE_RUNTIME_V2=1 python main.py
```

Il sidecar viene istanziato in `_async_main()` e passato al `TelegramListener`. Il legacy router continua a girare invariato. Per ogni messaggio processato, il sidecar esegue la pipeline runtime_v2 in aggiunta.

## Flusso shadow (sidecar)

```
Telegram event
      ↓
TelegramListener._process_item(QueueItem)
      ↓                              ↓
legacy router.route()         sidecar.process_queue_item()   ← shadow, non blocca
      ↓                              ↓
(legacy DB)               ChannelConfigResolver.lookup()      → entry non trovata / inactive: return
                                     ↓
                          RawMessageRepository.get_by_id()    ← legge envelope già salvato
                                     ↓
                          ParserPipelineProcessor.process()   → CanonicalParseResult | ParserJobStatus
                                     ↓
                          canonical_messages (DB)
```

Il sidecar non esegue re-ingest né re-saves su `raw_messages` — legge l'envelope già persistito dal legacy listener.

## Pipeline intake completa (standalone)

Quando runtime_v2 è usato autonomamente (senza legacy router):

```
RawIngestItem (dal listener)
      ↓
RawMessageRepository.save_raw()     ← dedup per (source_chat_id, telegram_message_id)
      ↓
blacklist globale (channels.yaml)   → BLACKLISTED, return None
      ↓
media-only senza testo              → MEDIA_ONLY_SKIPPED, return None
      ↓
IntakeEligibilityCheck              → review se breve update senza link forte
      ↓
RuntimeV2TraderResolver             → config-driven (channels.yaml) poi fallback
      ↓
parser_profile derivato             → override channels.yaml, default = resolved_trader_id
      ↓
validazione profilo in registry     → review se profilo non esiste in parser_v2
      ↓
ParserDispatchCandidate             ← output PRD 01 (intake completo)
      ↓
ParserPipelineProcessor             ← chiama UniversalParserRuntime.parse()
      ↓
CanonicalMessage                    ← schema_version = "canonical_message_v2"
      ↓
canonical_messages (DB)             ← UNIQUE(raw_message_id, run_context)
      ↓
CanonicalParseResult                ← output PRD 2.b, input per PRD 03
```

## Contratti chiave

| Tipo | Significato |
|------|-------------|
| `acquisition_status` | Immutabile — impostato una volta: `ACQUIRED`, `BLACKLISTED`, `MEDIA_ONLY_SKIPPED` |
| `processing_status` | Mutabile — traccia lo stato intake: `pending → processing → done / review / failed`. Il parser pipeline non lo modifica. |
| `ParserDispatchCandidate` | Output dell'intake — contiene envelope, resolved trader, parser_profile, parser_context |
| `CanonicalParseResult` | Output del parser pipeline — contiene `CanonicalMessage` e metadata. Input per PRD 03. |
| `ParserJobStatus` | Restituito in caso di failure del parsing — `status: failed/skipped`, con `reason`. |

## File configurabili

- `config/channels.yaml` — mappa canali Telegram → trader_id, con blacklist e topic_id opzionale
- `db/migrations/023_runtime_v2_raw_messages.sql` — colonne runtime_v2 su `raw_messages`
- `db/migrations/024_runtime_v2_canonical_messages.sql` — nuova tabella `canonical_messages`

## Test

```
tests/runtime_v2/
├── test_intake_models.py
├── test_trader_resolution_models.py
├── test_channel_config_resolver.py
├── test_raw_message_repository.py
├── test_trader_resolver.py
├── test_intake_processor.py
├── test_canonical_message_repository.py  ← PRD 2.b
├── test_parser_pipeline_processor.py     ← PRD 2.b
├── test_listener_sidecar.py              ← shadow sidecar
└── test_acceptance.py                    ← criteri PRD-01 + PRD 2.b slice end-to-end
```

74 test, tutti passing.
