# runtime_v2 — Panoramica generale

## Cos'è

`src/runtime_v2/` è il nuovo layer di intake del bot. Riceve messaggi Telegram grezzi dal listener, li persiste, risolve il trader di riferimento e produce un `ParserDispatchCandidate` pronto per il parser.

È completamente isolato da `src/telegram/router.py` (legacy). Non importa il router in nessun punto.

## Struttura package

```
src/runtime_v2/
├── intake/
│   ├── models.py          — RawIngestItem, RawMessageEnvelope, IntakeConfig
│   ├── eligibility.py     — IntakeEligibilityCheck, EligibilityOutcome
│   └── processor.py       — RuntimeV2IntakeProcessor (orchestratore pipeline)
├── trader_resolution/
│   ├── models.py          — ResolvedTraderContext, ParserDispatchCandidate
│   ├── channel_config_resolver.py  — ChannelConfigResolver (lookup channels.yaml)
│   └── resolver.py        — RuntimeV2TraderResolver (config-first + fallback)
└── persistence/
    └── raw_messages.py    — RawMessageRepository (adapter su storage legacy)
```

## Pipeline completa

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
RuntimeV2TraderResolver             → config-driven (channels.yaml) poi EffectiveTraderResolver
      ↓
parser_profile derivato             → override channels.yaml, default = resolved_trader_id
      ↓
validazione profilo in registry     → review se profilo non esiste in parser_v2
      ↓
ParserDispatchCandidate             ← output finale, passa al parser
```

## Contratti chiave

| Tipo | Significato |
|------|-------------|
| `acquisition_status` | Immutabile — impostato una volta: `ACQUIRED`, `BLACKLISTED`, `MEDIA_ONLY_SKIPPED` |
| `processing_status` | Mutabile — traccia lo stato pipeline: `pending → processing → done / review / failed` |
| `ParserDispatchCandidate` | Output dell'intake — contiene envelope, resolved trader, parser_profile, parser_context |

## File configurabili

- `config/channels.yaml` — mappa canali Telegram → trader_id, con blacklist e topic_id opzionale
- `db/migrations/023_runtime_v2_raw_messages.sql` — migration additive per colonne runtime_v2

## Test

```
tests/runtime_v2/
├── test_intake_models.py
├── test_trader_resolution_models.py
├── test_channel_config_resolver.py
├── test_raw_message_repository.py
├── test_trader_resolver.py
├── test_intake_processor.py
└── test_acceptance.py          ← criteri PRD-01 §11.2
```

52 test, tutti passing.
