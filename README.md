# TeleSignalBot

Sistema di trading automatico che acquisisce segnali da canali Telegram di terzi, li parsa in formato canonico, e li esegue su exchange tramite freqtrade.

## Stato attuale

Il progetto ha già completato la catena core: listener live, router/pre-parser, parser nuova architettura, validazione di coerenza, operation rules, target resolver, execution live Freqtrade e base backtesting sono presenti nel repository.

Verifica eseguita il 2026-04-06:

- mixed suite su parser, telegram, validation, operation rules, target resolver, execution, backtesting e parser_test: `657 passed, 20 failed, 1 skipped`
- le regressioni viste nel workspace riguardano soprattutto `src/backtesting/tests/test_scenario_loader.py`, `src/backtesting/tests/test_runner.py::TestWindowsCommandDetection::test_win32_uses_python_module` e `src/telegram/tests/test_listener_recovery.py::test_catchup_skips_channel_with_no_last_id`

### Implementato e stabile

- persistenza raw messages con dedup e metadata sorgente (`src/storage/raw_messages.py`)
- listener live con recovery, queue e hot reload config (`src/telegram/listener.py`)
- router / pre-parser con blacklist, review queue e persistenza parse result (`src/telegram/router.py`)
- risoluzione trader effettivo (`src/telegram/effective_trader.py`)
- eligibility e strong linking (`src/telegram/eligibility.py`)
- parser nuova architettura con models Pydantic e RulesEngine (`src/parser/models/`, `src/parser/rules_engine.py`)
- profili trader migrati sulla nuova architettura (`src/parser/trader_profiles/`)
- validazione coerenza integrata nel Router (`src/validation/coherence.py`)
- operation rules applicate al flusso operativo (`src/operation_rules/`)
- target resolver integrato nel routing e nell'operational flow (`src/target_resolver/`)
- execution live / exchange-backed e reconciliation (`src/execution/`)
- backtesting base con chain builder, scenario engine, runner e report (`src/backtesting/`)
- harness replay e report CSV (`parser_test/`)

### Stato per fasi

```
Fase 1  Parser                       completata nella base architetturale
Fase 2  Listener robusto             implementata
Fase 3  Router / Pre-parser          implementata
Fase 4  Validazione + operation      implementata
Fase 4  Target resolver              implementato
Fase 5  Sistema 1 — freqtrade live   implementato e validato in dry-run
Fase 6  Sistema 2 — backtesting      base implementata
Fase 7  Scenario engine v2           presente come direzione, loader ancora da allineare
Fase 8  Report / ottimizzazione       documentata, implementazione runtime non chiusa
Fase 9  Entry plan runtime           documentata, implementazione runtime non chiusa
```

## Architettura

```
Telegram channels
      ↓
Listener (Telethon)
      ↓
raw_messages (SQLite) — processing_status: pending → done | failed | blacklisted | review
      ↓
Router / Pre-parser
      ↓
Parser — RulesEngine + profile.py per trader → TraderParseResult (Pydantic)
      ↓
parse_results (SQLite)
      ↓
Validazione coerenza → Operation rules → Target resolver
      ↓
Sistema 1 (freqtrade live)    Sistema 2 (backtesting)
```

## Nuova architettura parser

Il parser è stato riprogettato con un percorso unico per trader:

```
testo + ParserContext
      ↓
RulesEngine          legge parsing_rules.json per trader
      ↓
profile.py           estrae entità + intents
      ↓
Pydantic models      normalizza e valida
      ↓
TraderParseResult    output canonico unico
```

Output canonico:
- `message_type`: `NEW_SIGNAL | UPDATE | INFO_ONLY | UNCLASSIFIED`
- `completeness`: `COMPLETE | INCOMPLETE`
- `entities`: `NewSignalEntities | UpdateEntities`
- `intents`: lista con kind `CONTEXT | ACTION`
- `target_ref`: `STRONG | SYMBOL | GLOBAL`
- `confidence`, `warnings`, `trader_id`, `raw_text`

## Tecnologie

| Componente | Tecnologia |
|---|---|
| Listener | Telethon |
| Parser models | Pydantic v2 |
| Profili trader | profile.py + parsing_rules.json |
| DB | SQLite → Postgres in produzione |
| Execution | freqtrade + ccxt |
| UI controllo | FreqUI |
| File watching | watchdog |
| LLM hook | openai / anthropic / ollama (opzionale per trader) |

## Setup

Prerequisiti: Python 3.12+, account Telegram API

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Variabili ambiente minime (`.env`):
```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
DB_PATH=db/tele_signal_bot.sqlite3
```

Avvio:
```bash
python main.py --migrate
python main.py
```

## Operativita

Controlli rapidi utili in dry-run/live:

```powershell
# Audit sync bot <-> freqtrade sui trade aperti/pending
.\.venv\Scripts\python.exe scripts\audit_live_sync.py

# Audit mirato a un simbolo
.\.venv\Scripts\python.exe scripts\audit_live_sync.py --symbol BTCUSDT

# Ispezione completa di un attempt_key
.\.venv\Scripts\python.exe scripts\inspect_attempt.py --attempt-key T_xxx

# Ispezione dell'ultimo trade registrato
.\.venv\Scripts\python.exe scripts\inspect_attempt.py --latest-trade

# Stato TP dei trade OPEN nel DB bot
.\.venv\Scripts\python.exe scripts\tp_status.py
```

`audit_live_sync.py` segnala mismatch tra DB bot e DB `freqtrade`, ad esempio trade duplicati, `ENTRY_PENDING` gia visibili in `freqtrade`, differenze sui TP filled e warning di riconciliazione.

Il throttle del dispatcher MARKET si regola da `freqtrade/user_data/config.json` con `execution.market_dispatch_interval_s` (default `10`). Un valore intorno a `3` secondi e spesso un buon compromesso tra reattivita e rumore operativo.

## Test

Prerequisito: `.venv` attiva con `pip install -r requirements.txt` (vedi sezione Setup).
Usa sempre `.venv/Scripts/python.exe -m pytest` — mai `pytest` bare dal Python globale.

### Smoke suite — controllo rapido del sistema (212 test, ~5s)

Copre: modelli Pydantic, RulesEngine, listener/router Telegram, validazione coerenza.
Da eseguire prima di ogni commit come verifica minima.

```bash
.venv/Scripts/python.exe -m pytest \
  src/parser/models/tests/ \
  src/parser/tests/ \
  src/telegram/tests/ \
  src/validation/tests/ \
  -q
```

### Full suite — tutti i profili trader, harness e backtesting

Copre: profili trader (trader_3/a/b/c/d), harness replay, execution planner/applier.
Richiede workspace stabile e `.venv` completa.

```bash
.venv/Scripts/python.exe -m pytest \
  src/parser/trader_profiles/ \
  parser_test/tests/ \
  src/execution/test_update_planner.py \
  src/execution/test_update_applier.py \
  -q
```

Note:
- Su Windows, file SQLite nei test router possono lasciare artefatti in `.test_tmp/` — inoffensivi, ignorati da git.
- `src/execution/` usa `unittest.TestCase` — compatibile con pytest, nessuna dipendenza da fixture custom.
- `parser_test/tests/` richiede il DB di test in `parser_test/db/` per i test di integrazione.
- La verifica piu recente non e tutta verde: vedi il blocco "Verifica eseguita" sopra per i failure correnti.

### Test profilo specifico

```bash
.venv/Scripts/python.exe -m pytest src/parser/trader_profiles/trader_3/tests/

# replay su DB test
python parser_test/scripts/replay_parser.py --trader trader_3

# watch mode debug (replay automatico su modifica file)
python parser_test/scripts/watch_parser.py --trader trader_3
```

### Troubleshooting test

**Errore di ambiente** — non indica regressione del parser:

| Sintomo | Causa | Soluzione |
|---|---|---|
| `ModuleNotFoundError: No module named 'pydantic'` | pytest lanciato fuori dalla `.venv` | Usa `.venv/Scripts/python.exe -m pytest` |
| `ModuleNotFoundError: No module named 'src'` | CWD errata | Lancia pytest dalla root del progetto (`C:\TeleSignalBot`) |
| `PermissionError` su `.pytest_cache` o `.test_tmp` | Path non scrivibile | Verifica che cache e temp siano nel workspace (vedi `pytest.ini` e `conftest.py`) |
| `ERRORS` in collection, zero test raccolti | Import fallito per dipendenza mancante | `pip install -r requirements.txt` nella `.venv` |

**Errore di logica** — indica regressione del parser:

| Sintomo | Causa |
|---|---|
| `AssertionError: assert 'UPDATE' == 'NEW_SIGNAL'` | Classificazione cambiata |
| `assert result.entities.symbol == 'BTCUSDT'` | Estrazione entità rotta |
| `assert len(result.intents) == 2` | Intent mancante o aggiunto inatteso |
| `KeyError` o `ValidationError` Pydantic | Modello o struttura output cambiata |

Gli errori di ambiente non vanno mai interpretati come regressioni del parser. Risolvi l'ambiente prima di analizzare i test falliti.

## Struttura repository

```
TeleSignalBot/
├── CLAUDE.md                    → contesto per Claude Code
├── docs/
│   ├── PRD_generale.md          → architettura e tecnologie
│   ├── PRD_listener.md          → Listener dettagliato
│   ├── PRD_router.md            → Router / Pre-parser
│   ├── PRD_parser.md            → Parser + sistema debug
│   ├── AUDIT.md                 → stato progetto, file da toccare/non toccare
│   └── old/                     → documentazione vecchia architettura (archivio)
├── skills/                      → skill per Claude Code
├── src/
│   ├── core/                    → utilities condivise
│   ├── storage/                 → persistenza DB
│   ├── telegram/                → listener, ingestion, trader resolution
│   ├── parser/
│   │   ├── models/              → Pydantic models canonici [NUOVO]
│   │   ├── rules_engine.py      → RulesEngine [NUOVO]
│   │   ├── trader_profiles/
│   │   │   ├── shared/          → vocabolari condivisi [NUOVO]
│   │   │   ├── trader_3/        → primo profilo nuova arch
│   │   │   ├── trader_a/
│   │   │   ├── trader_b/
│   │   │   ├── trader_c/
│   │   │   └── trader_d/
│   │   └── trader_profiles/     → profili parser attivi
│   ├── execution/               → placeholder (Fase 5+)
│   └── exchange/                → placeholder (Fase 5+)
├── config/
│   └── channels.yaml            → canali Telegram e trader attivi
├── db/migrations/               → schema SQLite incrementale
└── parser_test/                 → replay, debug CSV, watch mode
```

## Documentazione

La documentazione autorevole è in `docs/`:
- `CLAUDE.md` — contesto per Claude Code (root del progetto)
- `CODEX.md` — contesto per CODEX APP (root del progetto)
- `docs/PRD_generale.md` — visione e architettura
- `docs/PRD_parser.md` — parser dettagliato e stato architettura parser
- `docs/PHASE_3_ROUTER_STATUS.md` — stato reale, gap e criterio di chiusura della Fase 3
- `docs/AUDIT.md` — stato progetto aggiornato
- `docs/TEST_ENV_STABILIZATION_CHECKLIST.md` — checklist operativa per chiudere la stabilizzazione ambiente test

I file in `docs/old/` sono archivio della vecchia architettura — non usare come riferimento operativo.
