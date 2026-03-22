# TeleSignalBot

Sistema di trading automatico che acquisisce segnali da canali Telegram di terzi, li parsa in formato canonico, e li esegue su exchange tramite freqtrade.

## Stato attuale

Il progetto è in fase di **riprogettazione del parser** seguendo una nuova architettura più semplice e manutenibile. Il layer di acquisizione Telegram e storage esistono e funzionano. L'execution è pianificata per le fasi successive.

### Implementato e stabile

- persistenza raw messages con dedup e metadata sorgente (`src/storage/raw_messages.py`)
- risoluzione trader effettivo (`src/telegram/effective_trader.py`)
- eligibility e strong linking (`src/telegram/eligibility.py`)
- parser pipeline esistente — **in fase di riscrittura** (`src/parser/pipeline.py`)
- profili trader esistenti — **in fase di migrazione** (`src/parser/trader_profiles/`)
- harness replay e report CSV (`parser_test/`)

### In sviluppo ora — Fase 1: Parser

```
Step 1  Pydantic models canonici     src/parser/models/
Step 2  RulesEngine                  src/parser/rules_engine.py
Step 3  Trader 3 profilo             primo profilo nuova architettura
Step 4  Watch mode + CSV debug       parser_test/
Step 5  Migrazione altri profili     trader_b → trader_c/d → trader_a
Step 6  Cleanup legacy               eliminazione pipeline.py e normalization.py
```

### Pianificato — Fasi successive

```
Fase 2  Listener robusto             asyncio.Queue, recovery, hot reload
Fase 3  Router / Pre-parser          blacklist, trader resolution, review queue
Fase 4  Validazione + Operation rules + Target resolver
Fase 5  Sistema 1 — freqtrade live
Fase 6  Sistema 2 — backtesting
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

## Test parser

```bash
# test profilo specifico
pytest src/parser/trader_profiles/trader_3/tests/

# replay su DB test
python parser_test/scripts/replay_parser.py --trader trader_3

# watch mode debug (replay automatico su modifica file)
python parser_test/scripts/watch_parser.py --trader trader_3
```

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
│   │   ├── pipeline.py          → LEGACY (eliminare dopo migrazione)
│   │   └── normalization.py     → LEGACY (eliminare dopo migrazione)
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
- `docs/PRD_parser.md` — parser dettagliato (priorità attuale)
- `docs/AUDIT.md` — stato progetto aggiornato

I file in `docs/old/` sono archivio della vecchia architettura — non usare come riferimento operativo.
