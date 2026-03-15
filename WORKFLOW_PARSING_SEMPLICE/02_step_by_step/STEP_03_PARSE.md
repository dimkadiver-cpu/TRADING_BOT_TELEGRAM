# Step 3 - Parsing (fino alla funzione chiave)

## Funzione centrale

La funzione principale del flusso parser e:

- `MinimalParserPipeline.parse(...)` in `src/parser/pipeline.py`

## Cosa fa in pratica

1. sceglie modalita parser (`regex_only`, `llm_only`, `hybrid_auto`)
2. invoca il dispatcher (`src/parser/dispatcher.py`)
3. usa parse regex o profilo trader-specifico (`src/parser/trader_profiles/...`)
4. normalizza output in schema comune (`src/parser/normalization.py`)
5. salva in `parse_results` (`src/storage/parse_results.py`)

## File parser importanti

- `src/parser/pipeline.py`
- `src/parser/dispatcher.py`
- `src/parser/normalization.py`
- `src/parser/intent_action_map.py`
- `src/parser/trader_profiles/base.py`
- `src/parser/trader_profiles/registry.py`
- `src/parser/trader_profiles/ta_profile.py`
- `src/parser/trader_profiles/trader_a/profile.py`

## Output semantico usato oggi

Campi centrali:

- `message_type`
- `intents`
- `actions`

Campi operativi utili:

- `entities`
- `target_refs`
- `reported_results`
- `validation_warnings`
