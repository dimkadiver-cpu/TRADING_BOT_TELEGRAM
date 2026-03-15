# TeleSignalBot

Trading bot modulare che acquisisce messaggi Telegram, li normalizza in eventi canonici e li persiste in modo auditabile.

## Stato attuale (2026-03-14)

Implementato nello stato corrente:

- ingest Telegram live con Telethon (`main.py`, `src/telegram/listener.py`)
- persistenza raw message con dedup e metadata sorgente (`src/storage/raw_messages.py`, migration `006_raw_messages.sql`)
- risoluzione trader effettivo (tag nel testo -> reply inheritance -> source map) (`src/telegram/effective_trader.py`)
- eligibility + strong linking (reply/link/ref espliciti) (`src/telegram/eligibility.py`)
- parser pipeline minima con modalita `regex_only`, `llm_only`, `hybrid_auto` (`src/parser/pipeline.py`, `src/parser/dispatcher.py`)
- normalizzazione su schema canonico con `message_type + intents + actions` e campi legacy derivati (`src/parser/normalization.py`)
- mapping canonico `intent -> action` centralizzato (`src/parser/intent_action_map.py`)
- persistenza parse results legacy + payload normalizzato JSON (`src/storage/parse_results.py`, migrations `007_parse_results.sql`, `008_parse_result_normalized.sql`)
- profili trader-specific:
  - TA (`src/parser/trader_profiles/ta_profile.py`)
  - Trader A (`src/parser/trader_profiles/trader_a/profile.py`)
  - base/registry comuni (`src/parser/trader_profiles/base.py`, `src/parser/trader_profiles/registry.py`)
- planner e applier domain minimali per update (`src/execution/update_planner.py`, `src/execution/update_applier.py`)
- test parser e replay in `parser_test/tests/` + test dedicati in `src/parser/` e `src/execution/`

Parzialmente implementato / non attivo end-to-end:

- lifecycle state machine completa (`src/execution/state_machine.py`)
- risk gate / sizing completo (`src/execution/risk_gate.py`)
- execution planner runtime completo (`src/execution/planner.py`)
- automazione exchange Bybit runtime (`src/exchange/adapter.py`, `src/exchange/bybit_rest.py`, `src/exchange/bybit_ws.py`)

## Flusso operativo reale (fase corrente)

1. `main.py` carica env, config e migrazioni DB.
2. Il listener riceve `NewMessage` da Telegram.
3. Salva il raw message con stato di acquisizione.
4. Risolve trader effettivo e valuta eligibility/linking.
5. Esegue parser (regex/llm/hybrid) e produce output normalizzato.
6. Salva il risultato in `parse_results` (incluso `parse_result_normalized_json`).

Nota: il flusso live principale si ferma al parsing/persistenza. Il planner/applier esiste ma non e agganciato automaticamente al listener live.

## Event types canonici supportati

- `NEW_SIGNAL`
- `UPDATE`
- `CANCEL_PENDING`
- `MOVE_STOP`
- `TAKE_PROFIT`
- `CLOSE_POSITION`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `INVALID`

## Struttura repository

- `src/telegram/`: listener, ingest, eligibility, trader mapping
- `src/parser/`: classificazione, estrazione, normalizzazione, dispatcher LLM/regex, profili trader
- `src/storage/`: persistenza `raw_messages` e `parse_results`
- `src/execution/`: moduli lifecycle/risk/planner (da completare)
- `src/exchange/`: adapter e client exchange (da completare)
- `src/core/`: config loader, logger, migration runner, utility
- `config/`: alias trader, source map Telegram, portfolio rules
- `traders/`: regole parser/execution per singolo trader
- `db/migrations/`: schema incrementale SQLite
- `docs/`: documentazione architetturale e operativa
- `parser_test/`: replay e test parser

## Inventario file (snapshot)

- Totale file progetto rilevati: `126`
- Indice completo file: `docs/PROJECT_FILE_INDEX.md`
- Breakdown top-level:
  - `src`: 65
  - `docs`: 17
  - `parser_test`: 10
  - `traders`: 10
  - `db`: 9
  - `skills`: 7
  - `config`: 3
  - root files: 5 (`README.md`, `README_CODEX.md`, `AGENTS.md`, `main.py`, `requirements.txt`)

## Setup rapido

Prerequisiti:

- Python 3.11+
- account Telegram API (api_id/api_hash)

Installazione:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Variabili ambiente minime:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`

Variabili utili:

- `DB_PATH` (default `db/tele_signal_bot.sqlite3`)
- `LOG_PATH` (default `logs/bot.log`)
- `LOG_LEVEL` (default `INFO`)
- `TELEGRAM_SESSION` (default `tele_signal_bot`)
- `TELEGRAM_ALLOWED_CHAT_IDS` (csv chat ids consentiti)
- `TELEGRAM_SOURCE_MAP_PATH` (default `config/telegram_source_map.json`)
- `PARSER_MODE` (`regex_only` | `llm_only` | `hybrid_auto`)
- `LLM_PROVIDER` (`openai` | `gemini`)
- `LLM_MODEL` (default provider-specific)

Avvio:

```bash
python main.py --migrate
python main.py
```

## Test parser

```bash
python -m unittest discover -s parser_test/tests -p "test_*.py"
```

## Documentazione consigliata

1. `docs/README.md`
2. `docs/MASTER_PLAN.md`
3. `docs/SYSTEM_ARCHITECTURE.md`
4. `docs/PARSER_FLOW.md`
5. `docs/SESSION_HANDOFF.md`

## Limiti aperti

- Config validation ancora parziale in `src/core/config_loader.py`
- Linking update->segnale non ancora consolidato in un modulo lifecycle dedicato
- Mancano test end-to-end listener -> DB su feed Telegram reali
