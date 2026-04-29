# TeleSignalBot — CLAUDE.md

Documento di contesto per Claude Code. Leggi questo file all'inizio di ogni sessione prima di toccare qualsiasi codice.

---

## Cos'è il progetto

Sistema di trading automatico che acquisisce segnali da canali Telegram di terzi, li parsa in formato canonico, e li esegue su exchange tramite freqtrade. Composto da due sistemi: esecuzione live (Sistema 1) e backtesting storico (Sistema 2).

## Stato attuale

La migrazione del parser alla nuova architettura canonicav1 è **completata** (Fasi 1-9 parziale).
Tutti i profili trader emettono `CanonicalMessage` nativamente tramite `parse_canonical()`.
Il router persiste `CanonicalMessage` in `parse_results_v1`. I layer downstream (operation_rules,
target_resolver, backtesting) sono ancora su modelli legacy — migrazione non ancora iniziata.

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

## Architettura parser — DESIGN ATTUALE (v1 nativa)

La migrazione è completata. Ogni profilo emette `CanonicalMessage` direttamente senza passare per il normalizer.

### Flusso interno parser

```
testo + ParserContext
      ↓
RulesEngine          legge parsing_rules.json → classificazione + confidence
      ↓
profile.py::parse_canonical()   estrae e produce CanonicalMessage direttamente
      ↓
CanonicalMessage     src/parser/canonical_v1/models.py — contratto unico
      ↓
parse_results_v1 DB  router persiste il JSON canonico
```

### Cosa NON esiste più nella nuova architettura

- `src/parser/action_builders/canonical_v2.py` — **RIMOSSO** (Fase 9)
- `pipeline.py` generico con regex universali — legacy, da eliminare
- `normalization.py` come layer separato — legacy, da eliminare
- riconciliazione tra pipeline e profilo — non esiste più

### Contratto canonico — CanonicalMessage

```python
# src/parser/canonical_v1/models.py
primary_class:    SIGNAL | UPDATE | REPORT | INFO
parse_status:     PARSED | PARTIAL | UNCLASSIFIED
trader_id:        str
raw_context:      RawContext (raw_text, reply_to_message_id, ...)
targeting:        Targeting (refs + scope)
signal:           SignalPayload | None
update:           UpdatePayload | None
report:           ReportPayload | None
warnings:         list[str]
```

### Come usare i profili v1-nativi

```python
# Ogni profilo implementa parse_canonical():
result: CanonicalMessage = parser.parse_canonical(text, context)

# Il router rileva automaticamente i profili v1-nativi:
callable(getattr(type(parser), 'parse_canonical', None))
```

### Entry model (canonico v1)

```
EntryStructure (livello signal):
  ONE_SHOT  → 1 leg
  TWO_STEP  → 2 leg discreti (averaging classico)
  RANGE     → 2 leg che definiscono [min, max] (zona di entrata)
  LADDER    → 3+ leg discreti

EntryType (livello leg):
  MARKET    → esecuzione a mercato (price opzionale e indicativo)
  LIMIT     → esecuzione a limite (price obbligatorio)
```

Matrice EntryStructure × EntryType (enforced dal validator Pydantic):

| Structure | Leg 1        | Leg 2..N |
|-----------|--------------|----------|
| ONE_SHOT  | MARKET/LIMIT | —        |
| TWO_STEP  | MARKET/LIMIT | LIMIT    |
| RANGE     | LIMIT        | LIMIT    |
| LADDER    | MARKET/LIMIT | LIMIT    |

`MARKET` ammesso solo su `sequence=1`. Le leg successive sono sempre `LIMIT`.

Mapping legacy:
```
MARKET legacy    → ONE_SHOT con leg MARKET
LIMIT legacy     → ONE_SHOT con leg LIMIT
AVERAGING (n=2)  → TWO_STEP con leg LIMIT
AVERAGING (n≥3)  → LADDER con leg LIMIT
ZONE             → RANGE con 2 leg LIMIT
```

Demozione strutturale: cardinalità insufficiente → struttura inferiore + `parse_status=PARTIAL`
+ warning `entry_structure_demoted:<from>-><to>:<reason>`.

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
├── src/
│   ├── core/                        ← utilities condivise, non toccare
│   ├── storage/                     ← storage layer, non toccare
│   ├── telegram/                    ← listener, ingestion, trader resolution
│   └── parser/
│       ├── canonical_v1/            ← CONTRATTO UNICO (non toccare struttura)
│       │   ├── __init__.py
│       │   ├── models.py            ← CanonicalMessage, Price, EntryLeg, ...
│       │   ├── normalizer.py        ← normalizer legacy→v1 (usato come fallback)
│       │   └── tests/
│       ├── adapters/
│       │   └── legacy_to_event_envelope_v1.py  ← bridge TraderParseResult→v1
│       ├── event_envelope_v1.py     ← TraderEventEnvelopeV1 (bridge minimo)
│       ├── models/                  ← LEGACY — usati da backtesting/operation_rules
│       │   ├── canonical.py         ← LEGACY Price, Intent, TargetRef (vecchio)
│       │   ├── new_signal.py        ← LEGACY NewSignalEntities
│       │   ├── update.py            ← LEGACY UpdateEntities
│       │   └── operational.py       ← LEGACY OperationalSignal, ResolvedTarget
│       ├── action_builders/         ← LEGACY svuotato (canonical_v2.py rimosso)
│       ├── rules_engine.py          ← RulesEngine
│       ├── trader_profiles/
│       │   ├── base.py              ← ParserContext, TraderParseResult dataclass, Protocols
│       │   ├── registry.py          ← registro profili
│       │   ├── trader_3/            ← ✅ v1-nativo
│       │   ├── trader_a/            ← ✅ v1-nativo
│       │   ├── trader_b/            ← ✅ v1-nativo
│       │   ├── trader_c/            ← ✅ v1-nativo
│       │   └── trader_d/            ← ✅ v1-nativo
│       ├── pipeline.py              ← LEGACY, non toccare
│       └── normalization.py         ← LEGACY, non toccare
├── parser_test/
│   ├── db/
│   ├── scripts/
│   │   ├── replay_parser.py
│   │   ├── audit_canonical_v1.py
│   │   └── generate_parser_reports.py
│   └── reporting/
├── tests/
│   └── parser_canonical_v1/         ← test schema + normalizer
└── config/
    ├── channels.yaml
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
- `pipeline.py` e `normalization.py` — legacy, eliminare solo dopo migrazione backtesting
- `src/parser/models/` (canonical.py, new_signal.py, update.py, operational.py) — LEGACY ancora in uso da backtesting, operation_rules, target_resolver; non eliminare prima di migrare quei layer
- `src/parser/canonical_v1/models.py` — contratto canonico definitivo, non modificare struttura

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

## Stato migrazione — parser

```
✅ Fase 1-8  Migrazione profili completata — tutti i profili emettono CanonicalMessage
✅ Fase 9a   canonical_v2.py rimosso — action_builders svuotato
⚠️  Fase 9b   Modelli legacy in src/parser/models/ non rimovibili:
              canonical.py, new_signal.py, update.py, operational.py
              → bloccati da: operation_rules, target_resolver
```

## Prossimi step (dopo Fase 9)

```
✅ Step A  src/backtesting/ rimosso — SignalBridgeBacktestStrategy.py eliminato
Step B  Migrare operation_rules        src/operation_rules/ → usa CanonicalMessage
Step C  Migrare target_resolver        src/target_resolver/ → usa CanonicalMessage
Step D  Rimuovere src/parser/models/   solo dopo B+C completati
Step E  Aggiornare CLAUDE.md finale
```

---

## Istruzioni automatiche fine sessione

Al termine di OGNI risposta che modifica file di codice, esegui automaticamente:

1. Aggiorna docs/AUDIT.md — segna come completato lo step appena fatto,
   aggiorna lo stato dei file toccati, aggiungi eventuali nuovi rischi emersi.

2. Esegui la skill handoff-trading-bot — produci il documento di handoff
   con: cosa fatto, file toccati, stato attuale, rischi aperti, prossimo prompt.

Non chiedere conferma — fallo sempre automaticamente.

