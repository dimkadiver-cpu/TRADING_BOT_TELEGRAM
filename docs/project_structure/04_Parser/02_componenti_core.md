# Parser - Componenti Core

## 1. `RulesEngine` (`src/parser/rules_engine.py`)

Responsabilita:

- carica `parsing_rules.json` del profilo;
- merge opzionale con vocabolario condiviso (`trader_profiles/shared/*.json`);
- classifica il testo (`NEW_SIGNAL|UPDATE|INFO_ONLY|UNCLASSIFIED`);
- rileva intents via `intent_markers`;
- espone `number_format`, `language`, `fallback_hook_enabled`.

Note tecniche:

- supporta formato marker nuovo (`strong/weak`) e legacy (lista flat);
- score marker: `strong=1.0`, `weak=0.4`;
- `combination_rules` puo aumentare lo score categoria;
- contiene anche `is_blacklisted(text)` lato parser-profile.

## 2. Profili trader (`src/parser/trader_profiles/*`)

Ogni profilo implementa parsing trader-specifico:

- preprocess testo;
- classifica messaggio;
- estrae intents, target refs, entita, warning, confidence;
- produce `TraderParseResult`.

Profili registrati:

- `TraderAProfileParser`
- `TraderBProfileParser`
- `TraderCProfileParser` (base su B, override dedicati)
- `TraderDProfileParser` (base su B, override dedicati)
- `Trader3ProfileParser` (base su B, override dedicati)

Tutti i profili principali espongono anche `parse_canonical(...)` per output v1 nativo.

## 3. Registry e canonicalizzazione trader

`src/parser/trader_profiles/registry.py` gestisce:

- alias trader (`ta`, `a`, `trader_a`, ecc.);
- factory parser per trader canonicale;
- fallback `None` se non registrato.

Impatto: il Router delega qui il binding `trader_id -> profile parser`.

## 4. Contratti parser interni

`src/parser/trader_profiles/base.py` definisce:

- `ParserContext`: contesto di parsing (testo, reply, link, hashtag, chat);
- `TraderParseResult` (dataclass legacy);
- protocol `TraderProfileParser` (`parse_message`);
- protocol `TraderProfileParserV1` (`parse_canonical`).

## 5. Bridge verso Canonical v1

Componenti:

- `src/parser/event_envelope_v1.py`: envelope parser-side intermedio;
- `src/parser/adapters/legacy_to_event_envelope_v1.py`: mapping da legacy a envelope;
- `src/parser/canonical_v1/normalizer.py`: mapping envelope -> `CanonicalMessage`.

Scopo: migrazione progressiva verso v1 senza rompere i consumer legacy.

## 6. Mappatura intents -> azioni

`src/parser/intent_action_map.py`:

- normalizza intent names;
- applica policy `state_change`;
- produce azioni usate dal runtime update planner/applier;
- espone funzioni helper per `primary_intent` e payload azione strutturato.

Questa mappa e usata dal Router quando applica UPDATE a runtime.

