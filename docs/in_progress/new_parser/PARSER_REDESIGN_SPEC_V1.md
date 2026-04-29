# Parser Redesign Spec V1

Documento di specifica per il redesign del parser.
Prodotto tramite sessione di design intervistata il 2026-04-28.

---

## 1. Scopo e principi

Il parser ha un unico compito: **leggere un messaggio e restituire una struttura dati che descrive fedelmente quello che il trader ha scritto.**

Principi fondamentali:

- Il parser parla il **linguaggio del trader** (semantica), non il linguaggio operativo del sistema
- L'architettura è **uniforme per tutti i profili** — stesso schema file, stesso runtime condiviso
- Tutto il vocabolario trader-specifico sta nel **JSON**, il codice è generico

### Cosa NON fa il parser

- Non valida se un intent è credibile in base alla storia (→ `intent_validator`)
- Non traduce intents in operazioni (→ `operation_rules`)
- Non risolve i target a posizioni concrete (→ `target_resolver`)
- Non decide cosa eseguire (→ `Sistema 1 / Sistema 2`)

---

## 2. Pipeline

### Dentro il perimetro del parser

```
testo raw + metadati
        ↓
classificazione          → primary_class: SIGNAL | UPDATE | REPORT | INFO
        ↓
detection intents        → lista candidati da semantic_markers.json
        ↓
estrazione entità        → prezzi, livelli, percentuali (extractors.py)
        ↓
ParsedMessage (candidati, validation_status=PENDING)
        ↓
intent_validator         → layer separato dentro parser, richiede DB
                           valida ogni (intent, ref) contro storia
                           popola status=CONFIRMED/INVALID, valid_refs, invalid_refs
        ↓
ParsedMessage (validation_status=VALIDATED)
        ↓
disambiguation_engine    → lavora SOLO sugli intents CONFIRMED
                           risolve conflitti tra intents validi (rules.json, stateless)
        ↓
ParsedMessage finale     ← OUTPUT DEL PARSER
```

### Fuori perimetro (layer successivi)

```
ParsedMessage finale (validation_status=VALIDATED)
        ↓
intent_translator        → mapping CONFIRMED intents → UpdatePayload / ReportPayload
                           stateless, universale (non per-profilo)
        ↓
CanonicalMessage         → contratto verso downstream (invariato)
        ↓
operation_rules          → gate checks + sizing (invariati come responsabilità)
                           ora legge CanonicalMessage invece di TraderParseResult
        ↓
OperationalSignal
        ↓
target_resolver / Sistema 1 / Sistema 2
```

---

## 3. Contratto ParsedMessage

Output unico del parser. Semantica pura, nessuna operazione.

```python
class ParsedMessage(BaseModel):
    schema_version:     str = "parsed_message_v1"
    parser_profile:     str

    primary_class:      MessageClass      # SIGNAL | UPDATE | REPORT | INFO
    parse_status:       ParseStatus       # PARSED | PARTIAL | UNCLASSIFIED | ERROR
    confidence:         float

    composite:          bool = False      # True se intents di categorie miste (UPDATE+REPORT, UPDATE+INFO, REPORT+INFO)

    signal:             SignalPayload | None = None   # solo per primary_class=SIGNAL
    intents:            list[IntentResult] = []       # per UPDATE / REPORT
    primary_intent:     IntentType | None = None

    targeting:          Targeting | None = None       # targeting message-level (default)

    validation_status:  Literal["PENDING", "VALIDATED"] = "PENDING"

    warnings:           list[str] = []
    diagnostics:        dict[str, Any] = {}
    raw_context:        RawContext   # riusa canonical_v1.RawContext invariato
```

`RawContext` è riusato da `src/parser/canonical_v1/models.py` senza modifiche:
`raw_text`, `reply_to_message_id`, `extracted_links`, `hashtags`, `source_chat_id`, `source_topic_id`, `acquisition_mode`.

### IntentResult

```python
class IntentResult(BaseModel):
    type:               IntentType          # enum tipizzato
    category:           IntentCategory      # UPDATE | REPORT
    entities:           IntentEntities      # modello Pydantic specifico per type
    confidence:         float
    raw_fragment:       str | None = None
    targeting_override: Targeting | None = None   # None = usa targeting message-level

    # popolato dal RulesEngine al momento del rilevamento
    detection_strength: Literal["strong", "weak"] = "weak"
    # strong = almeno un marker forte ha matchato; weak = solo marker deboli

    # popolato dall'intent_validator
    status:             Literal["CANDIDATE", "CONFIRMED", "INVALID"] = "CANDIDATE"
    valid_refs:         list[int] = []      # refs confermati dal validator
    invalid_refs:       list[int] = []      # refs scartati dal validator
    invalid_reason:     str | None = None   # motivo invalidazione
```

Il `targeting_override` per-intent gestisce il caso multi-ref dove intents diversi
puntano a refs diversi nello stesso messaggio.

`status`:
- `CANDIDATE` → rilevato dal parser, non ancora validato
- `CONFIRMED` → validato, almeno un ref valido
- `INVALID` → nessun ref valido, intent scartato

### IntentCategory

```python
IntentCategory = Literal["UPDATE", "REPORT", "INFO"]
```

### IntentEntities — base comune

```python
class IntentEntities(BaseModel):
    raw_fragment: str | None = None
    confidence:   float = 1.0

    def to_dict(self) -> dict: ...    # export per DB/JSON
```

### diagnostics — campi consigliati

```python
diagnostics: {
    "resolution_unit": "MESSAGE_WIDE" | "TARGET_ITEM_WIDE",
    "applied_disambiguation_rules": list[str],
    "trader_code": str,
    "composite": bool,   # True se intents di categorie miste — operation_rules lo legge da qui
}
```

`resolution_unit`:
- `MESSAGE_WIDE` — stessa semantica per tutti i refs del messaggio
- `TARGET_ITEM_WIDE` — semantica diversa per ref diversi (caso multi-ref)

`composite`: copiato da `ParsedMessage.composite` da operation_rules quando costruisce `CanonicalMessage`.

---

## 4. Tassonomia intents

### 4.1 UPDATE intents

| Intent | Entità |
|---|---|
| `MOVE_STOP_TO_BE` | nessuna |
| `MOVE_STOP` | `new_stop_price: Price \| None`, `stop_to_tp_level: int \| None` |
| `CLOSE_FULL` | `close_price: Price \| None` |
| `CLOSE_PARTIAL` | `fraction: float \| None`, `close_price: Price \| None` |
| `CANCEL_PENDING` | `scope: str \| None` |
| `INVALIDATE_SETUP` | nessuna |
| `REENTER` | `entry_price: Price \| None`, `entry_type: EntryType \| None` |
| `ADD_ENTRY` | `entry_price: Price`, `entry_type: EntryType \| None` |
| `UPDATE_TAKE_PROFITS` | `new_take_profits: list[Price]`, `remove_levels: list[int]`, `mode: str \| None` |

Note:
- `MOVE_STOP`: almeno uno tra `new_stop_price` e `stop_to_tp_level` deve essere presente
- `CANCEL_PENDING`: `scope` è stringa libera (es. "averaging", "entry") — il layer downstream interpreta
- `REENTER`: forma complessa con SL/TP non gestita in v1 (edge case noto)

### 4.2 REPORT intents

| Intent | Entità |
|---|---|
| `ENTRY_FILLED` | `fill_price: Price \| None`, `average_price: Price \| None`, `level: int \| None` |
| `TP_HIT` | `level: int \| None`, `price: Price \| None`, `result: ReportedResult \| None` |
| `SL_HIT` | `price: Price \| None`, `result: ReportedResult \| None` |
| `EXIT_BE` | `price: Price \| None` |
| `REPORT_PARTIAL_RESULT` | `result: ReportedResult \| None` |
| `REPORT_FINAL_RESULT` | `result: ReportedResult \| None` |

Note:
- `TP_HIT.level`: popolato solo se esplicitamente nel testo, mai inferito
- `REPORT_FINAL_RESULT.result`: può essere None se il trader dichiara chiusura senza dato numerico

### 4.3 INFO intents

| Intent | Categoria | Entità |
|---|---|---|
| `INFO_ONLY` | `INFO` | nessuna |

Note:
- `INFO_ONLY` marca porzioni non-actionable dentro messaggi misti (es. "mercato volatile oggi")
- auto-CONFIRMED dal validator (nessuna regola DB necessaria)
- può coesistere con intents UPDATE o REPORT nello stesso messaggio → `composite=True`
- i disambiguation_rules possono sopprimerlo se un intent più specifico è già CONFIRMED:

```json
{
  "name": "suppress_info_only_if_actionable",
  "action": "suppress",
  "when_strong": ["MOVE_STOP"],
  "suppress": ["INFO_ONLY"]
}
```

### 4.4 Modelli entità per intent

```python
class MoveStopToBEEntities(IntentEntities):
    pass

class MoveStopEntities(IntentEntities):
    new_stop_price:   Price | None = None
    stop_to_tp_level: int | None = None

class CloseFullEntities(IntentEntities):
    close_price: Price | None = None

class ClosePartialEntities(IntentEntities):
    fraction:    float | None = None
    close_price: Price | None = None

class CancelPendingEntities(IntentEntities):
    scope: str | None = None

class InvalidateSetupEntities(IntentEntities):
    pass

class ReenterEntities(IntentEntities):
    entry_price: Price | None = None
    entry_type:  EntryType | None = None

class AddEntryEntities(IntentEntities):
    entry_price: Price
    entry_type:  EntryType | None = None

class UpdateTakeProfitsEntities(IntentEntities):
    new_take_profits: list[Price] = []
    remove_levels:    list[int] = []
    mode:             str | None = None

class EntryFilledEntities(IntentEntities):
    fill_price:    Price | None = None
    average_price: Price | None = None
    level:         int | None = None

class TpHitEntities(IntentEntities):
    level:  int | None = None
    price:  Price | None = None
    result: ReportedResult | None = None

class SlHitEntities(IntentEntities):
    price:  Price | None = None
    result: ReportedResult | None = None

class ExitBeEntities(IntentEntities):
    price: Price | None = None

class ReportPartialResultEntities(IntentEntities):
    result: ReportedResult | None = None

class ReportFinalResultEntities(IntentEntities):
    result: ReportedResult | None = None

class InfoOnlyEntities(IntentEntities):
    pass
```

---

## 5. SignalPayload

Invariato rispetto a `canonical_v1/models.py`. Usato solo quando `primary_class = SIGNAL`.

```python
class SignalPayload(BaseModel):
    symbol:          str | None = None
    side:            Side | None = None

    entry_structure: EntryStructure | None = None   # ONE_SHOT | TWO_STEP | RANGE | LADDER
    entries:         list[EntryLeg] = []

    stop_loss:       StopLoss | None = None
    take_profits:    list[TakeProfit] = []

    leverage_hint:   float | None = None
    risk_hint:       RiskHint | None = None

    invalidation_rule: str | None = None
    conditions:        str | None = None

    completeness:    Literal["COMPLETE", "INCOMPLETE"] | None = None
    missing_fields:  list[str] = []
    raw_fragments:   dict[str, str | None] = {}
```

### Cardinalità entries per entry_structure

| entry_structure | entries |
|---|---|
| `ONE_SHOT` | esattamente 1 |
| `TWO_STEP` | esattamente 2 |
| `RANGE` | esattamente 2 |
| `LADDER` | almeno 3 |

---

## 6. Targeting

### Targeting message-level (default)

```python
class Targeting(BaseModel):
    refs:     list[TargetRef] = []
    scope:    TargetScope
    strategy: TargetingStrategy
    targeted: bool = False

class TargetRef(BaseModel):
    ref_type: TargetRefType    # REPLY | TELEGRAM_LINK | MESSAGE_ID | EXPLICIT_ID | SYMBOL
    value:    str | int

class TargetScope(BaseModel):
    kind:          TargetScopeKind    # SINGLE_SIGNAL | SYMBOL | PORTFOLIO_SIDE | ALL_OPEN | UNKNOWN
    value:         str | None = None
    side_filter:   Side | None = None
    applies_to_all: bool = False

TargetingStrategy = Literal["REPLY_OR_LINK", "SYMBOL_MATCH", "GLOBAL_SCOPE", "UNRESOLVED"]
```

### Targeting per-intent (override)

`IntentResult.targeting_override` è `None` nel caso normale — tutti gli intents usano
il targeting message-level.

Si popola solo quando intents diversi nello stesso messaggio puntano a refs diversi:

```
messaggio: "BTCUSDT стоп в бу, ETHUSDT закрываем"

targeting: { refs: [BTCUSDT, ETHUSDT] }   ← tutti i refs trovati

intents: [
  IntentResult(MOVE_STOP_TO_BE, targeting_override=Targeting(refs=[BTCUSDT])),
  IntentResult(CLOSE_FULL,      targeting_override=Targeting(refs=[ETHUSDT])),
]
```

### Nota validator (fuori perimetro)

Il validator processa ogni coppia `(intent, ref)` indipendentemente.
Output atteso: `ValidatedIntent` con `valid_refs` e `invalid_refs` separati.
Il `ParsedMessage` supporta questo pattern tramite `targeting_override` per-intent.

---

## 7. Struttura file per profilo

Ogni profilo ha esattamente questi file:

```
src/parser/
  trader_profiles/
    trader_x/
      semantic_markers.json    ← vocabolario trader-specifico
      rules.json               ← logica: combination, disambiguation
      extractors.py            ← regex + estrazione entità (sezioni interne)
      profile.py               ← orchestratore ~20 righe
      __init__.py
      tests/
        __init__.py
        test_canonical_output.py
        test_profile_real_cases.py
  shared/
    runtime.py               ← parse puro, stateless
    disambiguation.py        ← stateless
  intent_validator/          ← layer separato, dentro parser, richiede DB
    __init__.py
    validator.py             ← logica validazione per (intent, ref)
    validation_rules.json    ← regole comuni a tutti i trader
```

### profile.py — contratto

```python
class TraderXProfile:
    def __init__(self):
        self._rules = RulesEngine.load(Path(__file__).parent / "semantic_markers.json",
                                       Path(__file__).parent / "rules.json")
        self._extractors = TraderXExtractors()

    def parse(self, text: str, context: ParserContext) -> ParsedMessage:
        return shared_runtime.parse(
            trader_code="trader_x",
            text=text,
            context=context,
            rules=self._rules,
            extractors=self._extractors,
        )
```

### extractors.py — struttura interna

```python
# ─── REGEX ────────────────────────────────────────────────────────────────────
_PRICE_RE = re.compile(...)
_PERCENT_RE = re.compile(...)
_TP_LEVEL_RE = re.compile(...)
# ...

# ─── SIGNAL EXTRACTION ────────────────────────────────────────────────────────
def _extract_signal(...) -> SignalPayload | None: ...
def _extract_entries(...) -> list[EntryLeg]: ...
def _extract_stop_loss(...) -> StopLoss | None: ...
def _extract_take_profits(...) -> list[TakeProfit]: ...

# ─── UPDATE EXTRACTION ────────────────────────────────────────────────────────
def _extract_move_stop(...) -> MoveStopEntities | None: ...
def _extract_close_partial(...) -> ClosePartialEntities | None: ...
# ...

# ─── REPORT EXTRACTION ────────────────────────────────────────────────────────
def _extract_tp_hit(...) -> TpHitEntities | None: ...
def _extract_result(...) -> ReportedResult | None: ...
# ...

# ─── ORCHESTRATOR ─────────────────────────────────────────────────────────────
class TraderXExtractors:
    def extract(self, text, context, rules) -> dict[str, Any]: ...
```

---

## 8. Schema semantic_markers.json

```json
{
  "language": "ru",
  "number_format": {
    "decimal_separator": ".",
    "thousands_separator": " "
  },

  "blacklist": [],

  "classification_markers": {
    "new_signal": {
      "strong": [],
      "weak": []
    },
    "update": {
      "strong": [],
      "weak": []
    },
    "info_only": {
      "strong": [],
      "weak": []
    }
  },

  "field_markers": {
    "entry": {
      "strong": [],
      "weak": []
    },
    "stop_loss": {
      "strong": [],
      "weak": []
    },
    "take_profit": {
      "strong": [],
      "weak": []
    }
  },

  "intent_markers": {
    "MOVE_STOP_TO_BE": { "strong": [], "weak": [] },
    "MOVE_STOP":       { "strong": [], "weak": [] },
    "CLOSE_FULL":      { "strong": [], "weak": [] },
    "CLOSE_PARTIAL":   { "strong": [], "weak": [] },
    "CANCEL_PENDING":  { "strong": [], "weak": [] },
    "INVALIDATE_SETUP":{ "strong": [], "weak": [] },
    "REENTER":         { "strong": [], "weak": [] },
    "ADD_ENTRY":       { "strong": [], "weak": [] },
    "UPDATE_TAKE_PROFITS": { "strong": [], "weak": [] },
    "ENTRY_FILLED":    { "strong": [], "weak": [] },
    "TP_HIT":          { "strong": [], "weak": [] },
    "SL_HIT":          { "strong": [], "weak": [] },
    "EXIT_BE":         { "strong": [], "weak": [] },
    "REPORT_PARTIAL_RESULT": { "strong": [], "weak": [] },
    "REPORT_FINAL_RESULT":   { "strong": [], "weak": [] },
    "INFO_ONLY":             { "strong": [], "weak": [] }
  },

  "side_markers": {
    "long":  [],
    "short": []
  },

  "entry_type_markers": {
    "market": [],
    "limit":  []
  },

  "target_markers": {
    "telegram_link": [],
    "explicit_id":   [],
    "pronouns":      []
  },

  "global_target_markers": {
    "ALL_LONGS":     [],
    "ALL_SHORTS":    [],
    "ALL_POSITIONS": [],
    "ALL_OPEN":      [],
    "ALL_REMAINING": []
  },

  "symbol_aliases": {},

  "extraction_markers": {
    "risk_prefix": {
      "strong": [],
      "weak":   []
    },
    "risk_suffix": {
      "strong": [],
      "weak":   []
    },
    "leverage_prefix": {
      "strong": [],
      "weak":   []
    }
  }
}
```

`extraction_markers` — vocabolario per i pattern di estrazione entità numeriche.
Il codice in `extractors.py` li legge per costruire le regex parametrizzate:

- `risk_prefix`: parole che precedono il valore rischio (es. "риск", "rischio", "risk")
- `risk_suffix`: parole che seguono il valore (es. "от депозита", "del deposito")
- `leverage_prefix`: parole che precedono la leva (es. "x", "лев", "leverage")

`extractors.py` usa strong/weak per costruire regex con priorità:
strong prefix → match più affidabile → `RiskHint.unit` confermato
weak prefix → match tentativo → può produrre warning

---

## 9. Schema rules.json

```json
{
  "classification_rules": [
    {
      "name": "",
      "when_all_fields_present": [],
      "then": "new_signal",
      "score": 1.0
    }
  ],

  "combination_rules": [
    {
      "name": "",
      "when_all_fields_present": [],
      "then": "",
      "confidence_boost": 0.0
    }
  ],

  "primary_intent_precedence": [
    "SL_HIT",
    "EXIT_BE",
    "TP_HIT",
    "REPORT_FINAL_RESULT",
    "REPORT_PARTIAL_RESULT",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING",
    "INVALIDATE_SETUP",
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "UPDATE_TAKE_PROFITS",
    "ADD_ENTRY",
    "REENTER",
    "ENTRY_FILLED",
    "INFO_ONLY"
  ],

  "disambiguation_rules": {
    "rules": [
      {
        "name": "",
        "action": "prefer",

        "when_strong": [],
        "when_weak":   [],

        "text_any":  [],
        "text_none": [],

        "message_composite":    null,
        "message_has_targeting": null,

        "entities_present": [],
        "entities_absent":  [],

        "prefer": "",
        "over":   []
      },
      {
        "name": "",
        "action": "suppress",

        "when_strong": [],
        "when_weak":   [],

        "text_any":  [],
        "text_none": [],

        "message_composite":    null,
        "message_has_targeting": null,

        "entities_present": [],
        "entities_absent":  [],

        "suppress": []
      }
    ]
  },

  "action_scope_groups": {
    "all_positions": ["ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"],
    "all_long":      ["ALL_LONGS"],
    "all_short":     ["ALL_SHORTS"]
  }
}
```

---

## 10. Disambiguation engine

### Principio

- Lavora **solo sui CONFIRMED** (intents validati dall'intent_validator)
- `detection_strength` per-intent indica come era stato rilevato (strong/weak)
- Default: se nessuna regola matcha → tutti i CONFIRMED coesistono
- `intent_compatibility` eliminato — ridondante con le regole stesse

### `detection_strength`

Popolato dal RulesEngine al momento del rilevamento, prima della validazione:

```
se almeno un marker forte ha matchato → "strong"
solo marker deboli                    → "weak"
```

Strong prevale: se un intent matcha sia marker forti che deboli → `"strong"`.

### Condizioni di una regola (tutte opzionali, AND tra loro)

| campo | tipo | semantica |
|---|---|---|
| `when_strong` | `list[str]` | questi intents devono essere CONFIRMED e `detection_strength == "strong"` |
| `when_weak` | `list[str]` | questi intents devono essere CONFIRMED e `detection_strength == "weak"` |
| `text_any` | `list[str]` | testo contiene almeno un token |
| `text_none` | `list[str]` | testo non contiene nessun token |
| `message_composite` | `bool\|null` | ParsedMessage.composite (`null` = non valutato) |
| `message_has_targeting` | `bool\|null` | targeting message-level presente (`null` = non valutato) |
| `entities_present` | `list[str]` | questi campi entità sono non-None nel ParsedMessage |
| `entities_absent` | `list[str]` | questi campi entità sono None nel ParsedMessage |

Campi lista vuota o `null` vengono ignorati (non contribuiscono al match).

### Azioni

| campo | tipo | semantica |
|---|---|---|
| `action` | `"prefer"\|"suppress"` | tipo di azione |
| `prefer` | `str` | intent CONFIRMED da tenere |
| `over` | `list[str]` | intents CONFIRMED da rimuovere (solo con `action=prefer`) |
| `suppress` | `list[str]` | intents CONFIRMED da rimuovere (solo con `action=suppress`) |

### Forme supportate

**Flat (primaria):**
```json
{
  "name": "prefer_exit_be_over_stop_move",
  "action": "prefer",
  "when_strong": ["EXIT_BE"],
  "when_weak":   ["MOVE_STOP"],
  "text_any":    ["breakeven", "be"],
  "prefer":      "EXIT_BE",
  "over":        ["MOVE_STOP"]
}
```

**Nested (equivalente, per regole complesse):**
```json
{
  "name": "suppress_stop_move_if_close_full_strong",
  "action": "suppress",
  "conditions": {
    "intents":  { "strong": ["CLOSE_FULL"], "weak": [] },
    "text":     { "any": [], "none": [] },
    "message":  { "composite": null, "has_targeting": null },
    "entities": { "present": [], "absent": [] }
  },
  "suppress": ["MOVE_STOP"]
}
```

Il motore normalizza internamente la forma flat → nested prima del matching.

### Flusso step by step

```
ParsedMessage (validation_status=VALIDATED)
  — solo intents CONFIRMED entrano
  — ogni intent porta detection_strength: "strong" | "weak"
        ↓
1. carica disambiguation_rules da rules.json del profilo
        ↓
2. per ogni regola in ordine:
   a. normalizza flat → nested
   b. verifica tutte le condizioni (AND): intents, text, message, entities
   c. se tutte matchano → applica action
        ↓
3. action=prefer:
   - rimuove gli intents in over[]
   action=suppress:
   - rimuove gli intents in suppress[]
        ↓
4. aggiorna intents[], primary_intent, composite
        ↓
ParsedMessage finale
```

### Tipi di regole

| action | quando usare |
|---|---|
| `prefer` | un intent è più specifico dell'altro (es. EXIT_BE > CLOSE_FULL quando rilevato forte) |
| `suppress` | un intent è sempre falso positivo in presenza di un altro |

### Default

Se nessuna regola matcha → tutti i CONFIRMED coesistono nel `ParsedMessage`.

---

## 11. Esempi JSON completi ParsedMessage

### 11.1 SIGNAL puro

```json
{
  "schema_version": "parsed_message_v1",
  "parser_profile": "trader_a",
  "primary_class": "SIGNAL",
  "parse_status": "PARSED",
  "confidence": 0.96,
  "composite": false,
  "signal": {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_structure": "TWO_STEP",
    "entries": [
      { "sequence": 1, "entry_type": "LIMIT", "price": {"raw": "91500", "value": 91500.0}, "role": "PRIMARY" },
      { "sequence": 2, "entry_type": "LIMIT", "price": {"raw": "89000", "value": 89000.0}, "role": "AVERAGING" }
    ],
    "stop_loss": { "price": {"raw": "87000", "value": 87000.0} },
    "take_profits": [
      { "sequence": 1, "price": {"raw": "95000", "value": 95000.0} },
      { "sequence": 2, "price": {"raw": "98000", "value": 98000.0} }
    ]
  },
  "intents": [],
  "targeting": null,
  "warnings": [],
  "raw_context": {
    "raw_text": "BTCUSDT long\nentry a 91500 / b 89000\nsl 87000\ntp1 95000 tp2 98000",
    "reply_to_message_id": null,
    "extracted_links": []
  }
}
```

### 11.2 UPDATE puro — MOVE_STOP_TO_BE

```json
{
  "schema_version": "parsed_message_v1",
  "parser_profile": "trader_a",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "confidence": 0.95,
  "composite": false,
  "signal": null,
  "intents": [
    {
      "type": "MOVE_STOP_TO_BE",
      "category": "UPDATE",
      "entities": {},
      "confidence": 0.95,
      "raw_fragment": "стоп в бу",
      "targeting_override": null
    }
  ],
  "primary_intent": "MOVE_STOP_TO_BE",
  "targeting": {
    "refs": [{ "ref_type": "REPLY", "value": 12345 }],
    "scope": { "kind": "SINGLE_SIGNAL", "applies_to_all": false },
    "strategy": "REPLY_OR_LINK",
    "targeted": true
  },
  "warnings": [],
  "raw_context": {
    "raw_text": "стоп в бу",
    "reply_to_message_id": 12345,
    "extracted_links": []
  }
}
```

### 11.3 Composito UPDATE + REPORT

```json
{
  "schema_version": "parsed_message_v1",
  "parser_profile": "trader_a",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "confidence": 0.93,
  "composite": true,
  "signal": null,
  "intents": [
    {
      "type": "TP_HIT",
      "category": "REPORT",
      "entities": {
        "level": 1,
        "price": null,
        "result": null
      },
      "confidence": 0.88,
      "raw_fragment": "первый тейк",
      "targeting_override": null
    },
    {
      "type": "MOVE_STOP_TO_BE",
      "category": "UPDATE",
      "entities": {},
      "confidence": 0.95,
      "raw_fragment": "стоп в бу",
      "targeting_override": null
    }
  ],
  "primary_intent": "MOVE_STOP_TO_BE",
  "targeting": {
    "refs": [{ "ref_type": "REPLY", "value": 12345 }],
    "scope": { "kind": "SINGLE_SIGNAL", "applies_to_all": false },
    "strategy": "REPLY_OR_LINK",
    "targeted": true
  },
  "warnings": [],
  "raw_context": {
    "raw_text": "первый тейк, стоп в бу",
    "reply_to_message_id": 12345,
    "extracted_links": []
  }
}
```

### 11.4 Multi-ref con intents diversi per ref

```json
{
  "schema_version": "parsed_message_v1",
  "parser_profile": "trader_a",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "confidence": 0.94,
  "composite": false,
  "signal": null,
  "intents": [
    {
      "type": "MOVE_STOP_TO_BE",
      "category": "UPDATE",
      "entities": {},
      "confidence": 0.94,
      "raw_fragment": "стоп в бу",
      "targeting_override": {
        "refs": [
          { "ref_type": "MESSAGE_ID", "value": 978 },
          { "ref_type": "MESSAGE_ID", "value": 1002 },
          { "ref_type": "MESSAGE_ID", "value": 1018 }
        ],
        "scope": { "kind": "SINGLE_SIGNAL", "applies_to_all": false },
        "strategy": "REPLY_OR_LINK",
        "targeted": true
      }
    },
    {
      "type": "MOVE_STOP",
      "category": "UPDATE",
      "entities": {
        "new_stop_price": null,
        "stop_to_tp_level": 1
      },
      "confidence": 0.93,
      "raw_fragment": "стоп на 1 тейк",
      "targeting_override": {
        "refs": [{ "ref_type": "MESSAGE_ID", "value": 1005 }],
        "scope": { "kind": "SINGLE_SIGNAL", "applies_to_all": false },
        "strategy": "REPLY_OR_LINK",
        "targeted": true
      }
    }
  ],
  "primary_intent": "MOVE_STOP_TO_BE",
  "targeting": {
    "refs": [
      { "ref_type": "MESSAGE_ID", "value": 978 },
      { "ref_type": "MESSAGE_ID", "value": 1002 },
      { "ref_type": "MESSAGE_ID", "value": 1005 },
      { "ref_type": "MESSAGE_ID", "value": 1018 }
    ],
    "scope": { "kind": "SINGLE_SIGNAL", "applies_to_all": false },
    "strategy": "REPLY_OR_LINK",
    "targeted": true
  },
  "diagnostics": {
    "resolution_unit": "TARGET_ITEM_WIDE"
  },
  "warnings": [],
  "raw_context": {
    "raw_text": "978 стоп в бу\n1002 стоп в бу\n1005 стоп на 1 тейк\n1018 стоп в бу",
    "reply_to_message_id": null,
    "extracted_links": []
  }
}
```

---

## 12. Intent Validator

Layer separato dentro il package parser. Richiede accesso DB storia.

### Posizione

```
src/parser/intent_validator/
  validator.py            ← logica validazione
  validation_rules.json   ← regole comuni tutti i trader
```

### Contratto

```python
def validate(parsed: ParsedMessage, db: AsyncSession) -> ParsedMessage:
    """Valida ogni (intent, ref) contro storia DB.
    Restituisce lo stesso ParsedMessage con status/valid_refs/invalid_refs popolati.
    """
```

### Principio

**Scopo**: ridurre i falsi positivi — da N candidati rilevati dal parser, sopravvivono solo gli intents credibili in base alla storia.

**Regola di default (auto-CONFIRMED)**:
- Se l'intent NON ha una regola in `validation_rules.json` → `status = CONFIRMED` senza accesso DB
- Le regole esistono solo per intents che richiedono verifica sulla storia

**Intents con regola**: validati contro DB → CONFIRMED o INVALID
**Intents senza regola**: auto-CONFIRMED (es. ENTRY_FILLED, REPORT_FINAL_RESULT, CANCEL_PENDING)

### Flusso per ogni IntentResult

```
per ogni intent in parsed.intents:

  se intent.type NON ha regola in validation_rules.json:
    → status = CONFIRMED (auto)
    → continua al prossimo intent

  refs = intent.targeting_override.refs se presente
         altrimenti parsed.targeting.refs

  se refs vuoti O scope != SINGLE_SIGNAL:
    → status = CONFIRMED (auto)
    → risoluzione delegata all'esecutore downstream
    → continua al prossimo intent

  per ogni ref in refs:
    controlla storia DB per quel ref secondo la regola dell'intent
    se valido → aggiungi a valid_refs
    se non valido → aggiungi a invalid_refs con reason

  se valid_refs non vuoto → status = CONFIRMED
  se valid_refs vuoto     → status = INVALID

parsed.validation_status = VALIDATED
```

**Scope gestiti dal validator**: solo `SINGLE_SIGNAL` (refs per message ID).
**Scope delegati all'esecutore**: `SYMBOL`, `PORTFOLIO_SIDE`, `ALL_OPEN` → auto-CONFIRMED, risoluzione posizioni avviene downstream.

### Schema validation_rules.json

Campi disponibili per ogni regola:

| campo | logica | note |
|---|---|---|
| `intent` | a quale intent si applica | obbligatorio |
| `requires_all_history` | ALL devono essere nel passato del ref | AND |
| `requires_any_history` | ALMENO UNO deve essere nel passato | OR |
| `excludes_any_history` | se ANY è presente → INVALID | NONE |
| `excludes_all_history` | INVALID solo se TUTTI presenti | raro |
| `invalid_reason` | messaggio di errore | obbligatorio |

```json
{
  "rules": [
    {
      "intent": "TP_HIT",
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "SL_HIT",
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "EXIT_BE",
      "requires_all_history": ["NEW_SIGNAL"],
      "requires_any_history": ["MOVE_STOP", "MOVE_STOP_TO_BE"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal_or_no_stop_moved"
    },
    {
      "intent": "MOVE_STOP_TO_BE",
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "MOVE_STOP",
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "CLOSE_FULL",
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "ENTRY_FILLED",
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    }
  ]
}
```

### Esempio output dopo validazione

```
intents: [
  IntentResult(
    type=MOVE_STOP_TO_BE,
    status=CONFIRMED,
    valid_refs=[978, 1002, 1018],
    invalid_refs=[1003],
    invalid_reason="no_open_signal"
  ),
  IntentResult(
    type=TP_HIT,
    status=INVALID,
    valid_refs=[],
    invalid_refs=[12345],
    invalid_reason="no_open_signal"
  )
]
validation_status=VALIDATED
```

---

## 13. Fuori perimetro

### CanonicalMessage

Rimane invariato come contratto verso i layer downstream.
Prodotto da `intent_translator` a partire dal `ParsedMessage` validato.

---

## 13b. Intent Translator

Layer separato tra parser e operation_rules. Responsabilità singola: convertire
i CONFIRMED intents del `ParsedMessage` nel payload operazionale del `CanonicalMessage`.

### Posizione

```
src/
  intent_translator/
    __init__.py
    translator.py    ← mapping hardcoded + build UpdatePayload / ReportPayload
```

### Principio

- **Stateless** — nessun DB, nessuna storia
- **Universale** — la mapping è la stessa per tutti i profili trader
- **Non configurabile per-profilo** — appartiene al contratto di sistema, come `UpdateOperationType` in `models.py`

### Mapping intent → operazione

La mapping è hardcoded in Python (lookup table). Non va in `rules.json` perché non è
vocabolario trader-specifico: se `MOVE_STOP_TO_BE` smettesse di mappare su
`SET_STOP(ENTRY)` cambierebbe il contratto del sistema, non la configurazione di un profilo.

#### UPDATE intents → UpdateOperation

| Intent (`ParsedMessage`) | `op_type` | dettaglio |
|---|---|---|
| `MOVE_STOP_TO_BE` | `SET_STOP` | `target_type=ENTRY` |
| `MOVE_STOP` (new_stop_price) | `SET_STOP` | `target_type=PRICE, value=new_stop_price.value` |
| `MOVE_STOP` (stop_to_tp_level) | `SET_STOP` | `target_type=TP_LEVEL, value=stop_to_tp_level` |
| `CLOSE_FULL` | `CLOSE` | `close_scope="FULL"` |
| `CLOSE_PARTIAL` | `CLOSE` | `close_scope="PARTIAL", close_fraction=fraction` |
| `CANCEL_PENDING` | `CANCEL_PENDING` | `cancel_scope=scope` (o None) |
| `INVALIDATE_SETUP` | `CANCEL_PENDING` | `cancel_scope="ALL_ALL"` |
| `REENTER` | `MODIFY_ENTRIES` | `mode=REENTER, entries=[...]` |
| `ADD_ENTRY` | `MODIFY_ENTRIES` | `mode=ADD, entries=[EntryLeg da entry_price]` |
| `UPDATE_TAKE_PROFITS` | `MODIFY_TARGETS` | `mode=REPLACE_ALL, take_profits=[...]` |

#### REPORT intents → ReportEvent

| Intent (`ParsedMessage`) | `event_type` | dettaglio |
|---|---|---|
| `ENTRY_FILLED` | `ENTRY_FILLED` | `price=fill_price`, `level=level` |
| `TP_HIT` | `TP_HIT` | `level=level`, `result=result` |
| `SL_HIT` | `STOP_HIT` | `result=result` |
| `EXIT_BE` | `BREAKEVEN_EXIT` | `price=price` |
| `REPORT_FINAL_RESULT` | `FINAL_RESULT` | `result=result` |
| `REPORT_PARTIAL_RESULT` | — | contribuisce a `ReportPayload.reported_result` (nessun event_type dedicato) |

#### INFO intents

| Intent (`ParsedMessage`) | azione |
|---|---|
| `INFO_ONLY` | nessun payload prodotto — `primary_class=INFO`, signal/update/report tutti None |

### Messaggi compositi UPDATE + REPORT

Quando `ParsedMessage.composite=True` e ci sono intents di entrambe le categorie
(UPDATE + REPORT), il `CanonicalMessage` deve avere `primary_class=UPDATE` — è un
vincolo del validator Pydantic:

```python
# canonical_v1/models.py — _validate_top_level
elif self.primary_class == "REPORT":
    if self.update is not None:
        raise ValueError("primary_class=REPORT forbids signal/update payloads")
```

`primary_class=UPDATE` è l'unica variante che permette entrambi i payload contemporaneamente.

**Regola del translator per messaggi compositi UPDATE+REPORT:**

```
se intents CONFIRMED contengono almeno un UPDATE intent:
    primary_class = UPDATE   (forzato, indipendente dalla classificazione del parser)
    update = UpdatePayload(operations=[... da UPDATE intents ...])
    report = ReportPayload(events=[... da REPORT intents ...])
```

Tutti i CONFIRMED intents compaiono in `CanonicalMessage.intents[]` (a meno che
soppressi dalla disambiguation). Il `primary_intent` segue la `primary_intent_precedence`.

**Asimmetria del validator:**

| `primary_class` | `update` | `report` | `signal` |
|---|---|---|---|
| `SIGNAL` | ❌ vietato | ✅ permesso | ✅ richiesto |
| `UPDATE` | ✅ richiesto | ✅ permesso | ❌ vietato |
| `REPORT` | ❌ vietato | ✅ richiesto | ❌ vietato |
| `INFO` | ❌ vietato | ❌ vietato | ❌ vietato |

### Caso multi-ref (targeting_override per-intent)

Quando un `IntentResult` ha `targeting_override` non-None, il translator produce
`TargetedAction` / `TargetedReport` invece di `UpdatePayload.operations` / `ReportPayload.events`.

```
IntentResult(targeting_override=None)      → UpdatePayload.operations o ReportPayload.events
IntentResult(targeting_override=Targeting) → TargetedAction o TargetedReport
```

Se gli intents del messaggio sono un mix (alcuni con override, altri senza),
il translator separa i due casi e popola sia `update.operations` che `targeted_actions`.

### Contratto

```python
def translate(parsed: ParsedMessage) -> CanonicalMessage:
    """Traduce i CONFIRMED intents del ParsedMessage in CanonicalMessage.

    Non accede al DB. Non esegue gate. Non calcola sizing.
    Solleva ValueError se parsed.validation_status != VALIDATED.
    """
```

### Responsabilità di operation_rules dopo la migrazione

`operation_rules` riceve `CanonicalMessage` (non più `TraderParseResult`):
- `primary_class=SIGNAL` → gate 1–9 + sizing (logica invariata, ora legge `signal.entries` / `signal.stop_loss`)
- `primary_class=UPDATE/REPORT/INFO` → passthrough invariato
- `_coerce_entities()` eliminato — i dati sono già tipizzati in `SignalPayload`

---

## 14. Mappa file — eliminare / riscrivere / tenere

### ❌ Eliminare (obsoleti)

| file | motivo |
|---|---|
| `shared/compatibility_engine.py` | intent_compatibility eliminato dalla spec |
| `shared/intent_compatibility_schema.py` | eliminato |
| `shared/context_resolution_engine.py` | context_resolution fuori perimetro parser |
| `shared/context_resolution_schema.py` | eliminato |
| `shared/semantic_resolver.py` | dipende da tutti e 4 sopra → obsoleto |
| `event_envelope_v1.py` | TraderEventEnvelopeV1 sostituito da ParsedMessage |
| `adapters/legacy_to_event_envelope_v1.py` | TraderEventEnvelopeV1 eliminato |
| `adapters/__init__.py` | cartella vuota dopo eliminazione |
| `canonical_v1/intent_candidate.py` | sostituito da IntentResult |
| `canonical_v1/targeted_builder.py` | sostituito da intent_translator |
| `canonical_v1/normalizer.py` | fallback legacy, eliminare dopo migrazione |
| `trader_profiles/shared/envelope_builder.py` | costruiva TraderEventEnvelopeV1 |
| `trader_profiles/shared/entity_keys.py` | sostituito da modelli Pydantic tipizzati per intent |
| `intent_action_map.py` | mappatura intent→operazioni, fuori perimetro parser |
| `canonical_schema.py` | schema CSV intent, sostituito da tassonomia spec |
| `action_builders/__init__.py` | cartella già svuotata |

**File di backup da eliminare (non fanno parte dell'architettura):**

| file |
|---|
| `trader_a/extractors copy.py` |
| `trader_a/parsing_rules copy.json` |
| `trader_a/parsing_rules copy 2.json` |
| `trader_a/parsing_rules copy Ultima.json` |
| `trader_b/parsing_rules copy.json` |
| `trader_d/parsing_rules copy.json` |

### 🔄 Riscrivere / sostituire

| file attuale | sostituito da |
|---|---|
| `shared/disambiguation_engine.py` | nuovo `shared/disambiguation.py` (schema when_strong/when_weak) |
| `shared/disambiguation_rules_schema.py` | Pydantic aggiornato per nuovo schema |
| `trader_profiles/shared/profile_runtime.py` | nuovo `shared/runtime.py` |
| `trader_profiles/shared/rules_schema.py` | schema per `semantic_markers.json` + `rules.json` |
| `trader_profiles/shared/rules_schema.json` | nuovo JSON schema |
| `trader_profiles/shared/intent_taxonomy.py` | tassonomia 15 intents da spec sezione 4 |
| `trader_profiles/shared/targeting.py` | logica integrata nel nuovo `shared/runtime.py` |
| `trader_profiles/base.py` | `ParserContext` rimane, `TraderParseResult` → `ParsedMessage` |
| `trader_x/parsing_rules.json` | split in `semantic_markers.json` + `rules.json` (per ogni profilo) |
| `trader_x/profile.py` | riscrivere ~20 righe con nuovo contratto |

### ✅ Tenere invariati

| file | motivo |
|---|---|
| `canonical_v1/models.py` | CanonicalMessage — contratto downstream invariato |
| `rules_engine.py` | RulesEngine — aggiornare input, non riscrivere |
| `text_utils.py` | utilities condivise |
| `shared/resolution_unit.py` | ResolutionUnit usato in diagnostics |
| `trader_profiles/registry.py` | registro profili (piccolo update alla migrazione) |
| `trader_profiles/common_utils.py` | utilities comuni |
| `models/` (tutto) | LEGACY bloccato da operation_rules/target_resolver — non toccare |
| `report_market_entry_none.py` | script standalone, non dipende dall'architettura |
| `trader_x/extractors.py` | logica di estrazione da adattare ma non riscrivere |
| `trader_x/tests/` | aggiornare gli assert, non riscrivere la struttura |

---

## 15. Piano di implementazione

Ogni fase è autonoma e testabile prima di procedere alla successiva.
Il vecchio codice rimane attivo finché la nuova architettura non è validata.

---

### Fase 1 — Cleanup preliminare
*Nessuna modifica funzionale. Elimina il rumore prima di costruire.*

- [ ] Eliminare file di backup non versionati:
  - [ ] `trader_a/extractors copy.py`
  - [ ] `trader_a/parsing_rules copy.json`
  - [ ] `trader_a/parsing_rules copy 2.json`
  - [ ] `trader_a/parsing_rules copy Ultima.json`
  - [ ] `trader_b/parsing_rules copy.json`
  - [ ] `trader_d/parsing_rules copy.json`
- [ ] Eliminare file obsoleti senza dipendenti attivi:
  - [ ] `shared/compatibility_engine.py`
  - [ ] `shared/intent_compatibility_schema.py`
  - [ ] `shared/context_resolution_engine.py`
  - [ ] `shared/context_resolution_schema.py`
  - [ ] `shared/semantic_resolver.py`
  - [ ] `adapters/legacy_to_event_envelope_v1.py`
  - [ ] `adapters/__init__.py`
  - [ ] `intent_action_map.py`
  - [ ] `canonical_schema.py`
  - [ ] `action_builders/__init__.py`
- [ ] Verificare che i test esistenti passino ancora dopo il cleanup

---

### Fase 2 — Nuovi modelli ParsedMessage
*Additive — non rompe nulla di esistente.*

- [ ] Creare `src/parser/parsed_message.py`:
  - [ ] `ParsedMessage`
  - [ ] `IntentResult` con `detection_strength`
  - [ ] `IntentEntities` base con `to_dict()`
  - [ ] Tutti i modelli entità per i 15 intents (sezione 4.3)
  - [ ] `ReportedResult`
- [ ] Creare `src/parser/intent_types.py`:
  - [ ] `IntentType` enum (15 valori)
  - [ ] `IntentCategory` Literal
- [ ] Scrivere test unitari per ogni modello entità
- [ ] Verificare che `ParsedMessage` sia serializzabile in JSON

---

### Fase 3 — Nuova shared infrastructure
*Nuovi file affiancati ai vecchi — nessuna sostituzione ancora.*

- [ ] Creare `src/parser/shared/runtime.py`:
  - [ ] orchestrazione: classify → detect → extract → build ParsedMessage
  - [ ] popola `detection_strength` per ogni IntentResult
  - [ ] gestione `targeting_override` per-intent
- [ ] Creare `src/parser/shared/disambiguation.py`:
  - [ ] normalizzazione flat → nested
  - [ ] matching condizioni: `when_strong`, `when_weak`, `text_any`, `text_none`, `message_*`, `entities_*`
  - [ ] azioni: `prefer` + `over`, `suppress`
- [ ] Aggiornare schema Pydantic `disambiguation_rules_schema.py` per nuovo formato
- [ ] Creare JSON schema per `semantic_markers.json` in `trader_profiles/shared/`
- [ ] Creare JSON schema per `rules.json` in `trader_profiles/shared/`
- [ ] Test unitari per `disambiguation.py` con casi flat e nested

---

### Fase 4 — Migrazione trader_a (profilo pilota)
*Primo profilo sul nuovo contratto. Validare approccio prima di replicare.*

- [ ] Creare `trader_a/semantic_markers.json` (split da `parsing_rules.json`):
  - [ ] `classification_markers`
  - [ ] `field_markers`
  - [ ] `intent_markers` (15 intents)
  - [ ] `side_markers`, `entry_type_markers`
  - [ ] `target_markers`, `global_target_markers`
  - [ ] `extraction_markers` (risk_prefix, risk_suffix, leverage_prefix)
  - [ ] `symbol_aliases`, `blacklist`
- [ ] Creare `trader_a/rules.json` (split da `parsing_rules.json`):
  - [ ] `combination_rules`
  - [ ] `disambiguation_rules` (nuovo schema)
  - [ ] `action_scope_groups`
- [ ] Aggiornare `trader_a/extractors.py`:
  - [ ] parametrizzare regex su `extraction_markers`
  - [ ] output tipizzato per ogni intent (modelli da Fase 2)
- [ ] Riscrivere `trader_a/profile.py` (~20 righe, usa `shared/runtime.py`)
- [ ] Aggiornare test `trader_a/tests/`:
  - [ ] assert su `ParsedMessage` invece di `CanonicalMessage`
  - [ ] verificare `detection_strength` per i casi noti
- [ ] Eseguire replay su DB test con `replay_parser.py`
- [ ] Confrontare output ParsedMessage con output precedente su campione reale
- [ ] **Aggiornare sistema report CSV** (non compatibile con ParsedMessage out-of-the-box):
  - [ ] `parser_test/reporting/flatteners.py` — riscrivere `_derive_fields()`:
    - [ ] leggere `signal.entries`, `signal.stop_loss`, `signal.take_profits` invece dei path legacy
    - [ ] leggere `intents[].entities` per-intent invece di `entities` flat
    - [ ] leggere `targeting` invece di `target_scope`/`linking`
    - [ ] leggere `primary_class` invece di `message_type`
  - [ ] `parser_test/reporting/report_export.py`:
    - [ ] aggiornare `REPORT_SCOPES`: `["ALL", "SIGNAL", "UPDATE", "REPORT", "INFO", "UNCLASSIFIED"]`
    - [ ] aggiornare SQL filter: `pr.primary_class = ?` invece di `pr.message_type = ?`
  - [ ] `parser_test/reporting/report_schema.py` — aggiungere colonne nuove:
    - [ ] `validation_status`, `composite`
    - [ ] `intents_confirmed`, `intents_candidate`
    - [ ] `detection_strengths`

---

### Fase 5 — Intent validator
*Layer separato. Richiede DB. Testabile con DB test esistente.*

- [ ] Creare `src/parser/intent_validator/__init__.py`
- [ ] Creare `src/parser/intent_validator/validation_rules.json`:
  - [ ] regole per i 6 intents con verifica storia (compilare a mano)
- [ ] Creare `src/parser/intent_validator/validator.py`:
  - [ ] carica `validation_rules.json`
  - [ ] flusso per ogni IntentResult (auto-CONFIRMED, SINGLE_SIGNAL, scope globale)
  - [ ] popola `valid_refs`, `invalid_refs`, `invalid_reason`
  - [ ] setta `validation_status = VALIDATED`
- [ ] Test con DB test (`parser_test/`):
  - [ ] verificare riduzione falsi positivi su campione reale
  - [ ] verificare che intents senza regola siano auto-CONFIRMED
  - [ ] verificare scope globale → auto-CONFIRMED

---

### Fase 6 — Intent translator
*Layer separato. Stateless. Testabile senza DB.*

- [ ] Creare `src/intent_translator/__init__.py`
- [ ] Creare `src/intent_translator/translator.py`:
  - [ ] lookup table `_INTENT_TO_UPDATE_OP` per i 10 UPDATE intents
  - [ ] lookup table `_INTENT_TO_REPORT_EVENT` per i 6 REPORT intents
  - [ ] logica `translate(parsed: ParsedMessage) -> CanonicalMessage`
  - [ ] gestione caso multi-ref (targeting_override → TargetedAction / TargetedReport)
  - [ ] INFO_ONLY → `primary_class=INFO`, tutti i payload None
- [ ] Test unitari per ogni intent (input `IntentResult` → output operazione attesa)
- [ ] Test integrazione: `ParsedMessage` completo → `CanonicalMessage` valido
- [ ] Verificare che il `CanonicalMessage` prodotto passi i model_validator di Pydantic

---

### Fase 7 — Migrazione profili rimanenti
*Replicare Fase 4 per ogni profilo. Ordine consigliato: trader_3, trader_b, trader_c, trader_d.*

- [ ] **trader_3**:
  - [ ] `semantic_markers.json`
  - [ ] `rules.json`
  - [ ] `extractors.py` aggiornato
  - [ ] `profile.py` riscritto
  - [ ] test aggiornati + replay
- [ ] **trader_b**: stessi step
- [ ] **trader_c**: stessi step
- [ ] **trader_d**: stessi step
- [ ] Verificare che il `registry.py` carichi correttamente tutti i nuovi profili

---

### Fase 8 — Cleanup finale
*Solo dopo che tutti i profili sono migrati e i test passano.*

- [ ] Eliminare file sostituiti:
  - [ ] `event_envelope_v1.py`
  - [ ] `canonical_v1/intent_candidate.py`
  - [ ] `canonical_v1/targeted_builder.py`
  - [ ] `canonical_v1/normalizer.py`
  - [ ] `trader_profiles/shared/envelope_builder.py`
  - [ ] `trader_profiles/shared/entity_keys.py`
  - [ ] `trader_profiles/shared/profile_runtime.py`
  - [ ] `trader_profiles/shared/targeting.py` (se integrata in runtime.py)
  - [ ] `trader_profiles/shared/intent_taxonomy.py` (vecchia versione)
  - [ ] `trader_profiles/base.py` (TraderParseResult rimosso)
  - [ ] tutti i `parsing_rules.json` per profilo
- [ ] Rimuovere `shared/disambiguation_engine.py` (vecchio)
- [ ] Aggiornare `trader_profiles/registry.py` definitivo
- [ ] Migrare `operation_rules/engine.py`: input `CanonicalMessage` invece di `TraderParseResult`, eliminare `_coerce_entities()`
- [ ] Aggiornare `CLAUDE.md` con stato migrazione completata

---

### Dipendenze tra fasi

```
Fase 1 (cleanup)
    ↓
Fase 2 (ParsedMessage models)
    ↓
Fase 3 (shared runtime + disambiguation)
    ↓
Fase 4 (trader_a pilota)      ←── validare qui prima di procedere
    ↓
Fase 5 (intent_validator)     ←── richiede Fase 4 completata
    ↓
Fase 6 (intent_translator)    ←── richiede Fase 2 + CanonicalMessage invariato
    ↓
Fase 7 (altri profili)        ←── parallelizzabile per profilo
    ↓
Fase 8 (cleanup finale)       ←── solo dopo Fase 7 completa
```

### Note operative

- `parser_test/` è lo strumento principale di validazione: usare `replay_parser.py` dopo ogni fase
- Non eliminare `models/` (canonical.py, new_signal.py, update.py, operational.py) — bloccati da operation_rules/target_resolver
- `pipeline.py` e `normalization.py` rimangono invariati fino a migrazione backtesting (fuori scope)
- Aggiornare `docs/AUDIT.md` al termine di ogni fase

---

## 16. Revisione generale — confronto architettura attuale vs nuova

### 16.1 Output contract e pipeline layer

| | Attuale | Nuovo |
|---|---|---|
| Output parser | `TraderEventEnvelopeV1` | `ParsedMessage` |
| Natura output parser | Semi-operazionale (`UpdatePayloadRaw`, `op_type`) | Semantica pura (intents + entities) |
| Intents | `list[str]` flat | `list[IntentResult]` tipizzati con entities |
| Validation | Assente | `intent_validator` → CONFIRMED/INVALID/CANDIDATE |
| Detection strength | Non tracciato | `detection_strength: "strong"\|"weak"` per intent |
| Traduzione intents → ops | Nel profilo (profile.py 500+ righe) | `intent_translator` — layer separato, stateless |
| Input operation_rules | `TraderParseResult` (entities raw/Pydantic) | `CanonicalMessage` (già tipizzato) |
| `_coerce_entities()` | Necessario (normalizza dict vs Pydantic) | Eliminato — `signal.entries/stop_loss` direttamente |

### 16.2 profile.py — dimensioni e responsabilità

**Attuale** (`trader_a/profile.py`): ~500+ righe — costruisce direttamente `CanonicalMessage`,
importa da `canonical_v1.models`, `semantic_resolver`, `context_resolution_engine`, `targeted_builder`.

**Nuovo**: ~20 righe — delega tutto a `shared_runtime.parse()`.

### 16.3 Intent taxonomy — migrazioni confermate

| Attuale (Python hardcoded) | Nuovo | Azione |
|---|---|---|
| `NEW_SETUP` intent | nessun intent — coperto da `classification_markers.new_signal` | eliminare |
| `CANCEL_PENDING_ORDERS` | `CANCEL_PENDING` | rinominare in tutti i `parsing_rules.json` |
| `INFO_ONLY` come sola primary_class | `INFO_ONLY` come intent categoria `INFO` | ✅ tenere, già nello spec |
| `PRIMARY_INTENT_PRECEDENCE` lista Python | `primary_intent_precedence` in `rules.json` | spostare in JSON |
| `MUTUAL_EXCLUSIONS` dict Python | `disambiguation_rules.suppress` in `rules.json` | spostare in JSON |
| `COMPATIBLE_MULTI_INTENT` dict Python | eliminato — compatibilità emerge da validator | eliminare |
| Alias `U_*` (`U_MOVE_STOP`, ecc.) | nessun alias — nomi diretti | eliminare da taxonomy e profili |

### 16.4 Disambiguation — differenze critiche

| | Attuale | Nuovo |
|---|---|---|
| Posizione | inline in `profile_runtime.py` (`_apply_prefer_rules`) | layer separato `shared/disambiguation.py` |
| Ordine esecuzione | prima della validazione | dopo il validator (solo su CONFIRMED) |
| Azioni supportate | solo `prefer` | `prefer` + `over` + `suppress` |
| Condizioni | `when_all_detected` + `if_contains_any` | `when_strong`, `when_weak`, `text_any`, `text_none`, `message_*`, `entities_*` |
| Detection strength | non usato | `when_strong`/`when_weak` leggono `IntentResult.detection_strength` |

### 16.5 File structure per profilo

| Attuale | Nuovo |
|---|---|
| `parsing_rules.json` (vocabolario + logica insieme) | `semantic_markers.json` + `rules.json` |
| Nessun `extractors.py` separato (tutto in `profile.py`) | `extractors.py` dedicato con sezioni interne |
| `profile.py` 500+ righe | `profile.py` ~20 righe |

### 16.6 Dipendenze da eliminare da trader_a/profile.py

```python
# queste import spariscono completamente
from src.parser.shared.context_resolution_engine import ContextInput
from src.parser.shared.context_resolution_schema import ContextResolutionRulesBlock
from src.parser.shared.disambiguation_rules_schema import DisambiguationRulesBlock
from src.parser.shared.intent_compatibility_schema import IntentCompatibilityBlock
from src.parser.shared.semantic_resolver import SemanticResolver
from src.parser.canonical_v1.targeted_builder import build_targeted_actions, ...
from src.parser.intent_action_map import intent_policy_for_intent
```
