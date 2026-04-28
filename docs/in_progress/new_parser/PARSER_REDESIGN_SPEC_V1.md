# Parser Redesign Spec V1

Documento di specifica per il redesign del parser.
Prodotto tramite sessione di design intervistata il 2026-04-28.

---

## 1. Scopo e principi

Il parser ha un unico compito: **leggere un messaggio e restituire una struttura dati che descrive fedelmente quello che il trader ha scritto.**

Principi fondamentali:

- Il parser parla il **linguaggio del trader** (semantica), non il linguaggio operativo del sistema
- Il parser è **stateless** — nessun accesso DB, nessuna storia, nessuna decisione operativa
- Il parser è **testabile senza infrastruttura** — testo in ingresso, struttura dati in uscita
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
disambiguation_engine    → risolve conflitti tra intents (rules.json, stateless)
        ↓
ParsedMessage            ← intents con status=CANDIDATE
        ↓
intent_validator         → layer separato dentro parser, richiede DB
                           valida ogni (intent, ref) contro storia
                           popola status, valid_refs, invalid_refs per ogni intent
        ↓
ParsedMessage finale     ← validation_status=VALIDATED, OUTPUT DEL PARSER
```

### Fuori perimetro (layer successivi)

```
ParsedMessage finale (validation_status=VALIDATED)
        ↓
operation_rules          → traduce intents CONFIRMED in operazioni canoniche
        ↓
CanonicalMessage         → contratto verso downstream (invariato)
        ↓
target_resolver 
```

---

## 3. Contratto ParsedMessage

Output unico del parser. Semantica pura, nessuna operazione.

```python
class ParsedMessage(BaseModel):
    schema_version:     str = "parsed_message_v1"
    parser_profile:     str

    primary_class:      MessageClass      # SIGNAL | UPDATE | REPORT | INFO
    parse_status:       ParseStatus       # PARSED | PARTIAL | UNCLASSIFIED
    confidence:         float

    composite:          bool = False      # True se intents di categorie miste (UPDATE + REPORT)

    signal:             SignalPayload | None = None   # solo per primary_class=SIGNAL
    intents:            list[IntentResult] = []       # per UPDATE / REPORT
    primary_intent:     IntentType | None = None

    targeting:          Targeting | None = None       # targeting message-level (default)

    validation_status:  Literal["PENDING", "VALIDATED"] = "PENDING"

    warnings:           list[str] = []
    diagnostics:        dict[str, Any] = {}
    raw_context:        RawContext
```

### IntentResult

```python
class IntentResult(BaseModel):
    type:               IntentType          # enum tipizzato
    category:           IntentCategory      # UPDATE | REPORT
    entities:           IntentEntities      # modello Pydantic specifico per type
    confidence:         float
    raw_fragment:       str | None = None
    targeting_override: Targeting | None = None   # None = usa targeting message-level

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
IntentCategory = Literal["UPDATE", "REPORT"]
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
}
```

`resolution_unit`:
- `MESSAGE_WIDE` — stessa semantica per tutti i refs del messaggio
- `TARGET_ITEM_WIDE` — semantica diversa per ref diversi (caso multi-ref)

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

### 4.3 Modelli entità per intent

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
    "REPORT_FINAL_RESULT":   { "strong": [], "weak": [] }
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

  "symbol_aliases": {}
}
```

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
      "if": [],
      "then": "",
      "confidence_boost": 0.0
    }
  ],

  "disambiguation_rules": {
    "rules": [
      {
        "name": "",
        "action": "prefer",
        "when_all_detected": [],
        "prefer": "",
        "if_contains_any": []
      },
      {
        "name": "",
        "action": "suppress",
        "when_all_detected": [],
        "suppress": []
      },
      {
        "name": "",
        "action": "keep_multi",
        "when_all_detected": [],
        "keep": []
      }
    ]
  },

  "intent_compatibility": {
    "pairs": [
      {
        "intents": [],
        "relation": "specific_vs_generic",
        "preferred": "",
        "requires_resolution": true
      },
      {
        "intents": [],
        "relation": "compatible",
        "requires_resolution": false
      },
      {
        "intents": [],
        "relation": "exclusive",
        "requires_resolution": true
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

### Flusso step by step

```
ParsedMessage (candidati grezzi)
        ↓
1. carica disambiguation_rules + intent_compatibility da rules.json
        ↓
2. per ogni coppia di intents incompatibili rilevati:
   - verifica se la coppia è in intent_compatibility
   - se requires_resolution = true → applica disambiguation_rules
        ↓
3. per ogni regola disambiguation:
   - action=prefer   → rimuove il non-preferito se if_contains_any matcha
   - action=suppress → rimuove gli intents in suppress[]
   - action=keep_multi → mantiene entrambi (compatibili)
        ↓
4. aggiorna intents[], primary_intent, composite
        ↓
ParsedMessage finale
```

### Tipi di regole

| action | quando usare |
|---|---|
| `prefer` | due intents dove uno è più specifico dell'altro (es. EXIT_BE > CLOSE_FULL) |
| `suppress` | un intent che è falso positivo in presenza di un altro |
| `keep_multi` | due intents genuinamente compatibili nello stesso messaggio |

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

### Flusso per ogni IntentResult

```
per ogni intent in parsed.intents:
  refs = intent.targeting_override.refs se presente
         altrimenti parsed.targeting.refs

  per ogni ref in refs:
    controlla storia DB per quel ref
    se valido → aggiungi a valid_refs
    se non valido → aggiungi a invalid_refs con reason

  se valid_refs non vuoto → status = CONFIRMED
  se valid_refs vuoto     → status = INVALID

parsed.validation_status = VALIDATED
```

### Schema validation_rules.json

```json
{
  "rules": [
    {
      "intent": "TP_HIT",
      "requires_history": ["NEW_SIGNAL"],
      "excludes_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "SL_HIT",
      "requires_history": ["NEW_SIGNAL"],
      "excludes_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "EXIT_BE",
      "requires_history": ["NEW_SIGNAL"],
      "requires_any_history": ["MOVE_STOP", "MOVE_STOP_TO_BE"],
      "excludes_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal_or_no_stop_moved"
    },
    {
      "intent": "MOVE_STOP_TO_BE",
      "requires_history": ["NEW_SIGNAL"],
      "excludes_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "MOVE_STOP",
      "requires_history": ["NEW_SIGNAL"],
      "excludes_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "CLOSE_FULL",
      "requires_history": ["NEW_SIGNAL"],
      "excludes_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
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

### intent_validator

Riceve `ParsedMessage` finale dal parser.
Per ogni coppia `(intent, ref)` verifica la storia DB:

- `TP_HIT` valido solo se: ref ha avuto `NEW_SIGNAL` e NON ha avuto `CLOSE_FULL | EXIT_BE | INVALIDATE_SETUP | SL_HIT`
- `SL_HIT` valido solo se: ref ha avuto `NEW_SIGNAL` e NON ha avuto `CLOSE_FULL | EXIT_BE | INVALIDATE_SETUP | SL_HIT`
- `EXIT_BE` valido solo se: ref ha avuto `NEW_SIGNAL` + (`MOVE_STOP` | `MOVE_STOP_TO_BE`) e NON ha avuto `CLOSE_FULL | EXIT_BE | INVALIDATE_SETUP | SL_HIT`
- `MOVE_STOP_TO_BE` valido solo se: ref ha avuto `NEW_SIGNAL` e NON ha avuto `CLOSE_FULL | EXIT_BE | INVALIDATE_SETUP | SL_HIT`
- `MOVE_STOP` valido solo se: stesso criterio di `MOVE_STOP_TO_BE`
- `CLOSE_FULL` valido solo se: ref ha avuto `NEW_SIGNAL` e NON ha avuto `CLOSE_FULL | EXIT_BE | INVALIDATE_SETUP | SL_HIT`

Output atteso: `ValidatedIntent` con `valid_refs: list[int]` e `invalid_refs: list[int]`.

### CanonicalMessage

Rimane invariato come contratto verso i layer downstream.
Prodotto da `operation_rules` a partire dal `ParsedMessage` validato.
