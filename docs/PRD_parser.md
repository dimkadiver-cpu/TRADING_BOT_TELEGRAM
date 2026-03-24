# PRD Dettagliato — Parser + Sistema Test/Debug

## Obiettivo

Riprogettare il parser con architettura pulita a responsabilità singole, output canonico Pydantic, e sistema di debug CSV con watch mode.

## Stato

Implementato nella sua base architetturale. Models Pydantic, RulesEngine, profili trader migrati e sistema di replay/report sono presenti; restano fuori da questo PRD i layer downstream (`operation rules`, `target resolver`, execution).

## Architettura interna parser

```
testo + ParserContext
      ↓
RulesEngine          legge parsing_rules.json → classificazione + confidence
      ↓
profile.py           estrae entità + intents (logica specifica trader)
      ↓
Pydantic models      normalizza formato + valida tipo
      ↓
TraderParseResult    output canonico unico
```

**Niente pipeline generico. Niente riconciliazione. Un solo percorso.**

## Output canonico — TraderParseResult

```python
message_type:     NEW_SIGNAL | UPDATE | INFO_ONLY | UNCLASSIFIED
completeness:     COMPLETE | INCOMPLETE  (solo NEW_SIGNAL)
missing_fields:   lista campi mancanti (solo se INCOMPLETE)
entities:         NewSignalEntities | UpdateEntities | None
intents:          lista Intent(name, kind=CONTEXT|ACTION)
target_ref:       TargetRef(kind, method, ref, symbol, scope)
confidence:       float 0.0-1.0
warnings:         lista str
trader_id:        str
raw_text:         str
acquisition_mode: live | catchup
```

## Modello Price

```python
class Price(BaseModel):
    raw: str      # valore originale estratto — sempre preservato
    value: float  # valore normalizzato

# normalizzazione configurata per trader in parsing_rules.json:
# number_format.decimal_separator e number_format.thousands_separator
```

Casi gestiti:
- `"90 000.5"` → `90000.5`
- `"90,000.5"` → `90000.5`
- `"90.000,5"` → `90000.5`
- `"0.1772"` → `0.1772`

## Entry type

```
MARKET    → entries opzionale (0 prezzi o prezzo indicativo/range validità)
LIMIT     → entries con 1 prezzo esatto
AVERAGING → entries con 2+ prezzi discreti
ZONE      → entries con 2 prezzi min/max
```

Determinazione automatica: il profilo conta i prezzi estratti e assegna il tipo.

## NewSignalEntities

Campi obbligatori per `COMPLETE`:
```
symbol        str — normalizzato uppercase
direction     LONG | SHORT
entry_type    MARKET | LIMIT | AVERAGING | ZONE
entries       lista Price — obbligatorio per LIMIT, AVERAGING, ZONE
stop_loss     Price — sempre obbligatorio
take_profits  lista Price — almeno uno
```

Campi opzionali:
```
leverage      float | None
risk_pct      float | None
conditions    str | None — testo libero non parsato
```

## Intents UPDATE

**CONTEXT** (descrivono cosa è successo):
```
U_TP_HIT     → TP raggiunto
U_SL_HIT     → SL raggiunto
```

**ACTION** (descrivono cosa fare):
```
U_MOVE_STOP           → new_sl_level: Price | None (None = breakeven)
U_CLOSE_FULL          → nessuna entità
U_CLOSE_PARTIAL       → close_pct: float
U_CANCEL_PENDING      → nessuna entità
U_REENTER             → entries: lista Price, entry_type
U_ADD_ENTRY           → new_entry_price: Price, entry_type
U_MODIFY_ENTRY        → old_entry_price: Price, new_entry_price: Price | None
U_UPDATE_TAKE_PROFITS → old_take_profits: lista Price | None, new_take_profits: lista Price
```

## TargetRef

```python
class TargetRef(BaseModel):
    kind:   STRONG | SYMBOL | GLOBAL
    method: REPLY | TELEGRAM_LINK | EXPLICIT_ID | None  # solo se STRONG
    ref:    int | str | None    # message_id o explicit_id
    symbol: str | None          # solo se SYMBOL
    scope:  str | None          # solo se GLOBAL, es. "all_long"
```

## RulesEngine — parsing_rules.json

```json
{
  "language": "ru | en | it",
  "shared_vocabulary": "russian_trading | english_trading | null",
  "number_format": {
    "decimal_separator": ".",
    "thousands_separator": " "
  },
  "classification_markers": {
    "new_signal": { "strong": [], "weak": [] },
    "update": { "strong": [], "weak": [] },
    "info_only": { "strong": [], "weak": [] }
  },
  "combination_rules": [
    {
      "if": ["marker_a", "marker_b"],
      "then": "intent_or_override",
      "confidence_boost": 0.3
    }
  ],
  "intent_markers": {
    "U_MOVE_STOP": [],
    "U_CLOSE_FULL": [],
    "U_CLOSE_PARTIAL": [],
    "U_CANCEL_PENDING": [],
    "U_REENTER": [],
    "U_ADD_ENTRY": [],
    "U_MODIFY_ENTRY": [],
    "U_UPDATE_TAKE_PROFITS": []
  },
  "target_ref_markers": {
    "strong": {
      "telegram_link": "t\\.me/",
      "explicit_id": []
    },
    "weak": {
      "pronouns": []
    }
  },
  "blacklist": [],
  "fallback_hook": {
    "enabled": false,
    "provider": null,
    "model": null
  }
}
```

## Vocabolari condivisi

```
src/parser/trader_profiles/shared/
  russian_trading.json    → marcatori comuni per trader in russo
  english_trading.json    → marcatori comuni per trader in inglese
```

Il profilo dichiara `"shared_vocabulary": "russian_trading"` e il RulesEngine fa merge automatico. Il profilo sovrascrive il shared in caso di conflitto.

## Profili trader — struttura

```
src/parser/trader_profiles/
  shared/
    russian_trading.json
    english_trading.json
  base.py           → ParserContext, protocol TraderProfileParser
  registry.py       → registro profili (aggiornare quando si aggiunge trader)
  trader_3/
    parsing_rules.json
    profile.py
    __init__.py
    tests/
      test_profile_real_cases.py
  trader_a/
    ...
  trader_b/
    ...
  trader_c/
    ...
  trader_d/
    ...
```

## Ordine migrazione profili

```
1. trader_3   ← primo, formato strutturato inglese, riferimento per gli altri
2. trader_b   ← russo semplice, formato lineare
3. trader_c   ← 
4. trader_d   ← 
5. trader_a   ← ultimo, più complesso (russo + multi-fase + averaging)
```

## File da creare

```
src/parser/models/__init__.py
src/parser/models/canonical.py      → TraderParseResult, Price, Intent, TargetRef
src/parser/models/new_signal.py     → NewSignalEntities, EntryLevel, StopLoss, TakeProfit
src/parser/models/update.py         → UpdateEntities
src/parser/rules_engine.py          → RulesEngine
src/parser/trader_profiles/shared/russian_trading.json
src/parser/trader_profiles/shared/english_trading.json
src/parser/trader_profiles/trader_3/parsing_rules.json  ← riscrivere
src/parser/trader_profiles/trader_3/profile.py          ← riscrivere
```

## File legacy storici

```
Il cluster parser legacy (`pipeline.py`, `normalization.py`, `dispatcher.py`, `scoring.py`) risulta rimosso dal percorso attivo.
```

## Sistema test/debug

### Watch mode

```
watchdog monitora (per trader attivo):
  src/parser/trader_profiles/trader_X/parsing_rules.json
  src/parser/trader_profiles/trader_X/profile.py
      ↓ su modifica (debounce 2 secondi)
replay_parser.py --trader trader_X
      ↓
generate_parser_reports.py --trader trader_X
      ↓
CSV aggiornati in parser_test/reports/
```

Lancio:
```bash
python parser_test/scripts/watch_parser.py --trader trader_3
```

### CSV debug — colonne per scope

Separatore liste dentro cella: `|`
Encoding: `UTF-8-sig` (compatibile LibreOffice)

**NEW_SIGNAL:**
```
raw_message_id | raw_text | parse_status | confidence | warnings
message_type | completeness | missing_fields
symbol | direction | entry_type
entries | stop_loss | take_profits
leverage | risk_pct | conditions
```

**UPDATE:**
```
raw_message_id | raw_text | parse_status | confidence | warnings
message_type | intents_context | intents_action | intents_missing
target_ref_kind | target_ref_method | target_ref_value
entities_ok | entities_missing
```

**UNCLASSIFIED:**
```
raw_message_id | raw_text | warnings | possible_type_hint | confidence
```

**ALL:**
```
message_type + unione colonne principali
```

### Logging errori replay

```python
except Exception as e:
    skipped += 1
    logger.error(
        raw_message_id=row.raw_message_id,
        exception_type=type(e).__name__,
        exception_msg=str(e),
        raw_text=(row.raw_text or "")[:200],
    )
```

Log: `parser_test/logs/replay_errors.log`

## Piano di sviluppo — step

### Step 1 — Pydantic models
File: `src/parser/models/`
- `Price`, `EntryLevel`, `StopLoss`, `TakeProfit`
- `NewSignalEntities`, `UpdateEntities`
- `Intent`, `TargetRef`
- `TraderParseResult`
- `compute_completeness()` helper

Test: unit test su normalizzazione Price con tutti i formati numerici

### Step 2 — RulesEngine
File: `src/parser/rules_engine.py`
- `ClassificationResult` dataclass
- `RulesEngine.load(path)` — carica JSON + merge shared
- `RulesEngine.classify(text, context)` — applica marcatori + combination_rules
- Logica confidence: strong * 1.0 + weak * 0.4 + context boost

Test: unit test su testi reali con classificazione attesa

### Step 3 — Trader 3 profilo
File: `src/parser/trader_profiles/trader_3/`
- `parsing_rules.json` completo (inglese, SIGNAL ID esplicito)
- `profile.py` con estrazione entità e intents
- test su dati reali dal DB con replay_parser.py

Trader 3 diventa il **profilo di riferimento** per gli altri.

### Step 4 — Sistema debug
File: `parser_test/`
- `watch_parser.py` — watch mode con watchdog
- colonne CSV aggiornate per nuova architettura
- logging errori replay non silenziosi

### Step 5 — Migrazione altri profili
Ordine: trader_b → trader_c → trader_d → trader_a
Per ogni profilo: riscrivere parsing_rules.json + profile.py, verificare test esistenti.

### Step 6 — Cleanup legacy
Completato: il cluster parser legacy è stato rimosso dal percorso attivo dopo la migrazione dei profili.

## Note per Claude Code

- **Inizia sempre da Step 1** — le interfacce Pydantic sono il contratto tra tutti gli altri step
- **Non saltare step** — ogni step dipende dal precedente
- **Leggi le skill** prima di iniziare: `pydantic-models`, `rules-engine`, `build-parser-profile`
- **Trader 3 prima** — è il più semplice e diventa il riferimento
- **Nota stato attuale** — la migrazione parser è stata completata a livello di architettura di parsing; la priorità residua si sposta sui layer downstream e sulla stabilizzazione dell'ambiente di test/configurazione live.
