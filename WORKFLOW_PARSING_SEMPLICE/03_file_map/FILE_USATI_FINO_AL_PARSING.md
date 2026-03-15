# File Usati Fino al Parsing

Questa mappa mostra i file chiave coinvolti prima e durante il parsing.

## 1) Avvio e bootstrap

- `main.py`
- `src/core/config_loader.py`
- `src/core/migrations.py`
- `src/core/logger.py`

## 2) Telegram ingest

- `src/telegram/listener.py`
- `src/telegram/ingestion.py`
- `src/storage/raw_messages.py`

## 3) Trader resolution e eligibility

- `src/telegram/effective_trader.py`
- `src/telegram/trader_mapping.py`
- `src/telegram/eligibility.py`
- `config/trader_aliases.json`
- `config/telegram_source_map.json`

## 4) Pipeline parser

- `src/parser/pipeline.py`
- `src/parser/dispatcher.py`
- `src/parser/parser_config.py`
- `src/parser/llm_adapter.py` (solo se modalita LLM/hybrid)

## 5) Normalizzazione semantica

- `src/parser/normalization.py`
- `src/parser/intent_action_map.py`

## 6) Profili trader-specific

- `src/parser/trader_profiles/base.py`
- `src/parser/trader_profiles/registry.py`
- `src/parser/trader_profiles/ta_profile.py`
- `src/parser/trader_profiles/trader_a/profile.py`
- `src/parser/trader_profiles/common_utils.py`
- `src/parser/trader_profiles/trader_a/parsing_rules.json`
- `traders/TA/parsing_rules.json`

Per la spiegazione dettagliata di ogni file trader-specifico:

- `03_file_map/PARSER_TRADER_SPECIFICO.md`

## 7) Salvataggio parse result

- `src/storage/parse_results.py`
- `db/migrations/007_parse_results.sql`
- `db/migrations/008_parse_result_normalized.sql`
