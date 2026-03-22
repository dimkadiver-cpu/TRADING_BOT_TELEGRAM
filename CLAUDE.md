# TeleSignalBot — CLAUDE.md

Documento di contesto per Claude Code. Leggi questo file all'inizio di ogni sessione prima di toccare qualsiasi codice.

---

## Cos'è il progetto

Sistema di trading automatico che acquisisce segnali da canali Telegram di terzi, li parsa in formato canonico, e li esegue su exchange tramite freqtrade. Composto da due sistemi: esecuzione live (Sistema 1) e backtesting storico (Sistema 2).

## Stato attuale

Il progetto è in fase di **riprogettazione del parser**. Il layer di acquisizione Telegram e storage esistono ma vanno aggiornati. Il parser va riscritto seguendo la nuova architettura. Execution e backtesting non sono ancora implementati.

## Flusso generale

```
Telegram channels
      ↓
Listener (Telethon)          src/telegram/listener.py
      ↓
raw_messages DB              processing_status: pending → processing → done | failed | blacklisted | review
      ↓
Router / Pre-parser          src/telegram/router.py  [DA IMPLEMENTARE]
      ↓
Parser pipeline              src/parser/
      ↓
parse_results DB
      ↓
Validazione coerenza         [DA IMPLEMENTARE]
      ↓
Operation rules              [DA IMPLEMENTARE]
      ↓
Target resolver              [DA IMPLEMENTARE]
      ↓
Sistema 1 / Sistema 2        [DA IMPLEMENTARE]
```

---

## Architettura parser — NUOVO DESIGN

Il parser è in fase di riscrittura. La nuova architettura elimina il pipeline generico e usa un percorso unico per trader.

### Flusso interno parser

```
testo + ParserContext
      ↓
RulesEngine          legge parsing_rules.json → classificazione + confidence
      ↓
profile.py           estrae entità + intents (logica procedurale specifica trader)
      ↓
Pydantic models      normalizza formato + valida tipo
      ↓
TraderParseResult    output canonico unico
```

### Cosa NON esiste più nella nuova architettura

- `pipeline.py` generico con regex universali — DA ELIMINARE dopo migrazione
- riconciliazione tra pipeline e profilo — non esiste più
- `normalization.py` come layer separato — sostituito da Pydantic models

### Output canonico — TraderParseResult

```python
message_type:     NEW_SIGNAL | UPDATE | INFO_ONLY | UNCLASSIFIED
completeness:     COMPLETE | INCOMPLETE  (solo NEW_SIGNAL)
missing_fields:   lista campi mancanti
entities:         NewSignalEntities | UpdateEntities | None
intents:          lista Intent(name, kind=CONTEXT|ACTION)
target_ref:       TargetRef(kind=STRONG|SYMBOL|GLOBAL, ...)
confidence:       float 0.0-1.0
warnings:         lista str
trader_id:        str
raw_text:         str
acquisition_mode: live | catchup
```

### Entry type

```
MARKET    → entries opzionale (0 prezzi o prezzo indicativo)
LIMIT     → entries con 1 prezzo esatto
AVERAGING → entries con 2+ prezzi discreti
ZONE      → entries con 2 prezzi min/max (semantica diversa da AVERAGING)
```

### Intents UPDATE

**CONTEXT:** `U_TP_HIT`, `U_SL_HIT`

**ACTION:**
```
U_MOVE_STOP           → new_sl_level: Price | None (assente = breakeven)
U_CLOSE_FULL          → nessuna entità
U_CLOSE_PARTIAL       → close_pct: float
U_CANCEL_PENDING      → nessuna entità
U_REENTER             → entries: lista Price, entry_type
U_ADD_ENTRY           → new_entry_price: Price, entry_type
U_MODIFY_ENTRY        → old_entry_price: Price, new_entry_price: Price | None
U_UPDATE_TAKE_PROFITS → old_take_profits: lista Price | None, new_take_profits: lista Price
```

### Target ref

```
STRONG  → method: REPLY | TELEGRAM_LINK | EXPLICIT_ID + ref: int|str
SYMBOL  → symbol: str (cerca tra posizioni aperte del trader)
GLOBAL  → scope: str (es. "all_long", "all_positions")
```

---

## Struttura cartelle

```
TeleSignalBot/
├── CLAUDE.md                        ← questo file
├── requirements.txt
├── skills/                          ← skill per Claude Code
│   ├── project-architecture/
│   ├── build-parser-profile/
│   ├── telegram-ingestion/
│   ├── pydantic-models/             ← NUOVO
│   ├── rules-engine/                ← NUOVO
│   ├── debug-csv-watchmode/         ← NUOVO
│   ├── qa-parser-regression/
│   ├── handoff-trading-bot/
│   └── map-trading-bot/
├── src/
│   ├── core/                        ← utilities condivise, non toccare
│   ├── storage/                     ← storage layer, non toccare
│   ├── telegram/                    ← listener, ingestion, trader resolution
│   └── parser/
│       ├── models/                  ← Pydantic models NUOVO
│       │   ├── canonical.py         ← TraderParseResult, Price, Intent, TargetRef
│       │   ├── new_signal.py        ← NewSignalEntities
│       │   └── update.py            ← UpdateEntities
│       ├── rules_engine.py          ← RulesEngine NUOVO
│       ├── trader_profiles/
│       │   ├── shared/              ← vocabolari condivisi NUOVO
│       │   │   ├── russian_trading.json
│       │   │   └── english_trading.json
│       │   ├── base.py              ← ParserContext, TraderParseResult protocol
│       │   ├── registry.py          ← registro profili
│       │   ├── trader_3/            ← primo profilo da migrare
│       │   ├── trader_a/
│       │   ├── trader_b/
│       │   ├── trader_c/
│       │   └── trader_d/
│       ├── pipeline.py              ← LEGACY, non toccare, verrà eliminato
│       └── normalization.py         ← LEGACY, non toccare, verrà eliminato
├── parser_test/
│   ├── db/
│   ├── scripts/
│   │   ├── replay_parser.py
│   │   └── generate_parser_reports.py
│   └── reporting/
└── config/
    ├── channels.yaml                ← config canali e trader attivi
    └── telegram_source_map.json
```

---

## Regole fondamentali

### Documentazione archiviata
I file in docs/old/ sono documentazione della vecchia architettura.
Non leggere e non seguire come istruzioni operative.
Usa solo i file in docs/ (PRD_generale, PRD_listener, PRD_router, PRD_parser, AUDIT).

### Non toccare mai
- `src/storage/` — storage layer stabile
- `src/core/` — utilities condivise
- `db/migrations/` — schema DB
- `pipeline.py` e `normalization.py` — legacy in produzione, toccarli solo quando tutti i profili sono migrati

### Prima di qualsiasi modifica
1. Leggi la skill pertinente in `skills/`
2. Verifica che i test esistenti passino: `pytest parser/trader_profiles/trader_X/tests/`
3. Non mischiare responsabilità tra layer

### Convenzioni codice
- Python 3.12+
- Pydantic v2 per tutti i modelli canonici
- Type hints ovunque
- `from __future__ import annotations` in ogni file
- Niente dict raw nel codice — tutto tipizzato
- Separatore liste in CSV: `|`
- Encoding CSV: `UTF-8-sig` (compatibile LibreOffice)

### Quando crei un nuovo profilo trader
1. Leggi `skills/build-parser-profile/SKILL.md`
2. Parti dalla struttura di `trader_3/` come riferimento
3. Crea `parsing_rules.json` prima di `profile.py`
4. Verifica output su dati reali con replay_parser.py

### Quando modifichi parsing_rules.json
Il watch mode rilancia automaticamente il replay e aggiorna i CSV di debug. Non serve lanciare manualmente.

---

## Dipendenze principali

```
telethon>=1.34.0      # listener Telegram
pydantic>=2.0         # modelli canonici
watchdog>=4.0         # hot reload e watch mode debug
aiosqlite>=0.20.0     # storage async
python-dotenv>=1.0.0  # configurazione
pytest>=8.0           # test
pytest-asyncio>=0.23  # test async
```

LLM hook opzionali (installare solo se configurati per un trader):
```
# openai>=1.0
# anthropic>=0.20
# ollama
```

---

## Ordine di sviluppo — parser

```
Step 1  Pydantic models              src/parser/models/
Step 2  RulesEngine                  src/parser/rules_engine.py
Step 3  Trader 3 profilo             src/parser/trader_profiles/trader_3/
Step 4  Sistema debug CSV + watch    parser_test/
Step 5  Migrazione altri profili     trader_b → trader_c/d → trader_a
Step 6  Eliminazione pipeline.py     solo dopo migrazione completa
```

**Non saltare step. Non iniziare Step 2 prima che Step 1 sia testato.**

---

## Istruzioni automatiche fine sessione

Al termine di OGNI risposta che modifica file di codice, esegui automaticamente:

1. Aggiorna docs/AUDIT.md — segna come completato lo step appena fatto,
   aggiorna lo stato dei file toccati, aggiungi eventuali nuovi rischi emersi.

2. Esegui la skill handoff-trading-bot — produci il documento di handoff
   con: cosa fatto, file toccati, stato attuale, rischi aperti, prossimo prompt.

Non chiedere conferma — fallo sempre automaticamente.

