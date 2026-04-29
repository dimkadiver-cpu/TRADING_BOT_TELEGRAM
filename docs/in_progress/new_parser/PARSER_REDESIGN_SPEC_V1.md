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

    composite:          bool = False      # True se intents di categorie miste; ammesso solo UPDATE+REPORT, UPDATE+INFO, REPORT+INFO

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
    """Marker base per le entità per-intent.

    `raw_fragment` e `confidence` vivono SOLO su `IntentResult` per evitare
    duplicazione (decisione consolidata: source of truth unica).
    """

    def to_dict(self) -> dict: ...    # export per DB/JSON
```

### diagnostics — campi consigliati

```python
diagnostics: {
    "resolution_unit": "MESSAGE_WIDE" | "TARGET_ITEM_WIDE",
    "applied_disambiguation_rules": list[str],
    "trader_code": str,
    "entry_structure_demoted": dict | None,   # popolato se demozione cardinalità (vedi 5.2)
}
```

`resolution_unit`:
- `MESSAGE_WIDE` — stessa semantica per tutti i refs del messaggio
- `TARGET_ITEM_WIDE` — semantica diversa per ref diversi (caso multi-ref)
- popolato dal `runtime.py` dopo la fase di estrazione: `TARGET_ITEM_WIDE` se almeno un
  `IntentResult.targeting_override` è non-None, altrimenti `MESSAGE_WIDE`.

NB: `composite` NON è duplicato in diagnostics — vive solo su `ParsedMessage.composite`
(source of truth unica).

---

## 4. Tassonomia intents

### 4.1 UPDATE intents

| Intent | Entità |
|---|---|
| `MOVE_STOP_TO_BE` | nessuna |
| `MOVE_STOP` | `new_stop_price: Price \| None`, `stop_to_tp_level: int \| None` |
| `CLOSE_FULL` | `close_price: Price \| None` |
| `CLOSE_PARTIAL` | `fraction: float \| None`, `close_price: Price \| None` |
| `CANCEL_PENDING` | `scope: CancelScope \| None` |
| `INVALIDATE_SETUP` | nessuna |
| `REENTER` | `entries: list[Price]`, `entry_type: EntryType \| None`, `entry_structure: EntryStructure \| None` |
| `ADD_ENTRY` | `entry_price: Price`, `entry_type: EntryType \| None` |
| `UPDATE_TAKE_PROFITS` | `new_take_profits: list[Price]`, `target_tp_level: int \| None`, `mode: ModifyTargetsMode \| None` |

Note:
- `MOVE_STOP`: almeno uno tra `new_stop_price` e `stop_to_tp_level` deve essere presente
- `CANCEL_PENDING`: `scope` è tipizzato (`CancelScope` enum); `None` = scope implicito `TARGETED`
- `REENTER`: supporta multi-leg (lista prezzi) per replicare piani `AVERAGING/ZONE/LADDER`;
  `entry_structure` opzionale, popolato quando il profilo distingue esplicitamente la struttura
- `UPDATE_TAKE_PROFITS.mode`: tipizzato sui 4 modi canonici (`REPLACE_ALL/ADD/UPDATE_ONE/REMOVE_ONE`).
  `target_tp_level` richiesto per `UPDATE_ONE`/`REMOVE_ONE`. Default `REPLACE_ALL` se omesso e
  `new_take_profits` non vuoto.

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
- in messaggi composite UPDATE+INFO il `CanonicalMessage` ha `primary_class=UPDATE` e
  `INFO_ONLY` finisce in `intents[]` con `raw_fragment` preservato in `diagnostics.info_fragments`
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
    scope: CancelScope | None = None   # None = TARGETED implicito

class InvalidateSetupEntities(IntentEntities):
    pass

class ReenterEntities(IntentEntities):
    entries:         list[Price] = []
    entry_type:      EntryType | None = None
    entry_structure: EntryStructure | None = None

class AddEntryEntities(IntentEntities):
    entry_price: Price
    entry_type:  EntryType | None = None

class UpdateTakeProfitsEntities(IntentEntities):
    new_take_profits: list[Price] = []
    target_tp_level:  int | None = None
    mode:             ModifyTargetsMode | None = None  # REPLACE_ALL | ADD | UPDATE_ONE | REMOVE_ONE

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

### 5.1 Matrice EntryStructure × EntryType

Vincoli sulla composizione `EntryLeg.entry_type` per ogni `entry_structure`. Enforced
dal validator Pydantic in `SignalPayload._validate_signal_payload` e
`ModifyEntriesOperation._validate_modify_entries`.

| Structure | Leg 1 | Leg 2..N |
|---|---|---|
| `ONE_SHOT` | MARKET o LIMIT | — |
| `TWO_STEP` | MARKET o LIMIT | LIMIT |
| `RANGE` | LIMIT | LIMIT |
| `LADDER` | MARKET o LIMIT | LIMIT |

Razionale del vincolo "MARKET solo su sequence=1":
- `MARKET` = "entra adesso al prezzo corrente": esiste un solo "adesso" per messaggio
- leg successivi sono ordini in attesa → richiedono livello di prezzo → `LIMIT` obbligatorio
- `MARKET` con `price` popolato = prezzo indicativo (snapshot al momento del messaggio); sempre preservato

Convenzione `EntryLeg.role`:

| Structure | role pattern |
|---|---|
| `ONE_SHOT` | `[PRIMARY]` |
| `TWO_STEP` | `[PRIMARY, AVERAGING]` |
| `RANGE` | `[PRIMARY, PRIMARY]` |
| `LADDER` | `[PRIMARY, AVERAGING, AVERAGING, ...]` |

### 5.2 Conversione legacy entry_type

Mapping dei marker legacy (`MARKET / LIMIT / AVERAGING / ZONE`) al modello canonico:

| Legacy marker | n. prezzi | EntryStructure | EntryLeg.entry_type |
|---|---|---|---|
| `MARKET` | 0 o 1 | `ONE_SHOT` | `MARKET` |
| `LIMIT` | 1 | `ONE_SHOT` | `LIMIT` |
| `AVERAGING` | 2 | `TWO_STEP` | `LIMIT` (entrambi) |
| `AVERAGING` | ≥3 | `LADDER` | `LIMIT` (tutti) |
| `ZONE` | 2 (min/max) | `RANGE` | `LIMIT` (entrambi) |

Distinguere `RANGE` da `TWO_STEP` richiede marker semantico esplicito (es. "zona", "range",
"от ... до"); il count da solo non basta. Il profilo dichiara questi marker in
`semantic_markers.json::field_markers.entry.range_markers`.

### 5.3 Demozione strutturale (cardinalità insufficiente)

Quando l'extractor identifica un marker che dichiara struttura multi-leg ma trova un
numero di prezzi inferiore a quanto richiesto, il parser **demota** la struttura a quella
inferiore compatibile e degrada `parse_status`:

| Caso intended | n. prezzi | Demozione | parse_status | Warning |
|---|---|---|---|---|
| `AVERAGING/ZONE/RANGE/TWO_STEP/LADDER` | 1 | `ONE_SHOT`, leg `LIMIT` | `PARTIAL` | `entry_structure_demoted:<INTENDED>->ONE_SHOT:single_price` |
| `LADDER` | 2 | `TWO_STEP` | `PARTIAL` | `entry_structure_demoted:LADDER->TWO_STEP:two_prices` |
| qualsiasi (≠ MARKET/ONE_SHOT) | 0 | `entry_structure=None`, `missing_fields=["entries"]` | `PARTIAL` | `entry_structure_demoted:<INTENDED>->NONE:no_prices` |

Il warning è in formato machine-readable `<event>:<from>-><to>:<reason>` (convenzione
generale dei warning del parser). Diagnostica completa popolata in
`diagnostics.entry_structure_demoted = {"from": ..., "to": ..., "reason": ...}`.

L'helper `_demote_entry_structure(intended, legs, warnings)` in
`canonical_v1/normalizer.py` implementa la logica e va riusato dai profili e dal runtime.

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
        "priority": 0,
        "conditions": {
          "intents":  { "strong": [], "weak": [] },
          "text":     { "any": [], "none": [] },
          "message":  { "composite": null, "has_targeting": null },
          "entities": { "present": [], "absent":  [] }
        },
        "prefer": "",
        "over":   []
      },
      {
        "name": "",
        "action": "suppress",
        "priority": 0,
        "conditions": {
          "intents":  { "strong": [], "weak": [] },
          "text":     { "any": [], "none": [] },
          "message":  { "composite": null, "has_targeting": null },
          "entities": { "present": [], "absent":  [] }
        },
        "suppress": []
      }
    ]
  },

  "action_scope_groups": {
    "ALL_POSITIONS": ["ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"],
    "ALL_LONG":      ["ALL_LONGS"],
    "ALL_SHORT":     ["ALL_SHORTS"]
  }
}
```

### 9.1 classification_rules

Assegnano `primary_class` quando i campi indicati sono tutti presenti.

| campo | tipo | semantica |
|---|---|---|
| `name` | `str` | identificativo |
| `when_all_fields_present` | `list[str]` | nomi di campi del SignalPayload o categorie di intent_markers |
| `then` | `"new_signal" \| "update" \| "report" \| "info_only"` | classe assegnata |
| `score` | `float` | contributo a `confidence` (0..1), sommato e clampato |

### 9.2 combination_rules

Boost di confidence quando combinazioni di marker compaiono insieme (es. side+entry+sl+tp → segnale completo).

| campo | tipo | semantica |
|---|---|---|
| `name` | `str` | identificativo |
| `when_all_fields_present` | `list[str]` | campi richiesti |
| `then` | `str` | etichetta convenzionale dell'esito (debug) |
| `confidence_boost` | `float` | additivo a `confidence` |

### 9.3 action_scope_groups

Letto dal `intent_translator` per mappare i marker globali del trader (es. `ALL_OPEN`,
`ALL_REMAINING`) ai valori canonici di `CancelScope`. Le **chiavi** sono i valori finali di
`CancelScope` (`ALL_POSITIONS`, `ALL_LONG`, `ALL_SHORT`); i **valori** sono le liste di
sinonimi emessi dai profili in `semantic_markers.json::global_target_markers`.

NB: `action_scope_groups` è **vocabolario universale**, non trader-specifico. Il file
canonico è `src/intent_translator/scope_mapping.json`. La copia in `rules.json` del profilo
è ammessa solo per override specifici trader-per-trader (caso eccezionale).

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
| `entities_present` | `list[str]` | path `<INTENT>.<field>` — esiste un IntentResult del tipo con quel campo non-None |
| `entities_absent` | `list[str]` | path `<INTENT>.<field>` — non esiste alcun match |
| `priority` | `int` | priorità di esecuzione (default 0); più alta esegue prima |

Campi lista vuota o `null` vengono ignorati (non contribuiscono al match).

**Sintassi `entities_present`/`entities_absent`**: ogni elemento è nella forma
`<INTENT_TYPE>.<field>` (es. `MOVE_STOP.new_stop_price`, `TP_HIT.level`). Match se almeno
un `IntentResult` di quel tipo esiste tra i CONFIRMED con `entities.<field>` non-None
(per `entities_absent`: nessun match).

### Azioni

| campo | tipo | semantica |
|---|---|---|
| `action` | `"prefer"\|"suppress"` | tipo di azione |
| `prefer` | `str` | intent CONFIRMED da tenere |
| `over` | `list[str]` | intents CONFIRMED da rimuovere (solo con `action=prefer`) |
| `suppress` | `list[str]` | intents CONFIRMED da rimuovere (solo con `action=suppress`) |

### Forma unica: nested

Lo schema delle regole supporta una sola forma (nested). La forma flat è stata rimossa
per ridurre la superficie di test e la complessità del normalizer.

```json
{
  "name": "suppress_stop_move_if_close_full_strong",
  "action": "suppress",
  "priority": 0,
  "conditions": {
    "intents":  { "strong": ["CLOSE_FULL"], "weak": [] },
    "text":     { "any": [], "none": [] },
    "message":  { "composite": null, "has_targeting": null },
    "entities": { "present": [], "absent": [] }
  },
  "suppress": ["MOVE_STOP"]
}
```

**Validazione load-time:**
- `action=prefer` richiede `over` non vuoto. `over=[]` è errore di config (non valido come no-op:
  per "non sopprimere niente" semplicemente non si scrive la regola).
- `action=suppress` richiede `suppress` non vuoto.
- A load-time il motore logga warning se due regole hanno effetti opposti su una stessa
  coppia di intents (potenziale conflitto da rivedere).

### Flusso step by step

```
ParsedMessage (validation_status=VALIDATED)
  — solo intents CONFIRMED entrano
  — ogni intent porta detection_strength: "strong" | "weak"
        ↓
1. carica disambiguation_rules da rules.json del profilo
        ↓
2. ordina le regole per `priority desc`, poi per posizione nel file `asc`:
   a. verifica tutte le condizioni (AND): intents, text, message, entities
   b. se tutte matchano → applica action
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
class IntentValidator:
    def __init__(self, db_path: str, rules_path: Path) -> None: ...

    def validate(self, parsed: ParsedMessage) -> ParsedMessage:
        """Valida ogni (intent, ref) contro storia DB.
        Sync, usa sqlite3.connect(db_path) (allineato al router attuale).
        Restituisce lo stesso ParsedMessage con status/valid_refs/invalid_refs popolati.
        """
```

Decisione: validator **sync** con `sqlite3` standard library — niente migrazione async,
allineato al router esistente. Questo riduce complessità della Fase 4.5 e mantiene un
unico paradigma di concorrenza in tutto il pipeline parser.

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

### History contract

**Cosa è la "history" di un ref:** la sequenza cronologica di intents CONFIRMED collegati
allo stesso `NEW_SIGNAL` di quel ref, esclusi gli intents `INVALID` (decisione consolidata:
solo i CONFIRMED entrano in history).

**Pseudo-intent `NEW_SIGNAL`**: termine riservato delle `validation_rules` che mappa su
`primary_class=SIGNAL` con `parse_status ∈ {PARSED, PARTIAL}`. NON è un IntentType emesso
dal parser — vive solo nel vocabolario delle regole di validazione.

**Risoluzione della chain di un ref**:

```
ref_message_id (MESSAGE_ID dato dal parser)
        ↓
1. lookup raw_messages.reply_to_message_id ricorsivo → trova il NEW_SIGNAL
   (primo messaggio della chain con primary_class=SIGNAL)
        ↓
2. raccoglie TUTTI i parse_results_v1 con raw_message_id nella chain
   (ordinati per timestamp ascendente)
        ↓
3. estrae da ognuno gli intents CONFIRMED
   (campo intents_confirmed_json di parsed_messages, vedi storage layer)
        ↓
SignalLifecycle = (new_signal_id, ordered_events, is_terminal)
```

`is_terminal = True` se la history contiene già uno tra `CLOSE_FULL`, `SL_HIT`,
`INVALIDATE_SETUP` (segnale chiuso → tutti i nuovi intents su quel ref sono INVALID).

**Provider injectable** (`HistoryProvider` Protocol) per disaccoppiare validator e DB.
La query SQL canonica usa CTE ricorsiva su `raw_messages.reply_to_message_id`.

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
| `CANCEL_PENDING` | `CANCEL_PENDING` | `cancel_scope=scope` (o `"TARGETED"` se None) |
| `INVALIDATE_SETUP` | `CANCEL_PENDING` | `cancel_scope="ALL_POSITIONS"` |
| `REENTER` | `MODIFY_ENTRIES` | `mode=REENTER, entries=[...], entry_structure=...` (popolato se >1 leg) |
| `ADD_ENTRY` | `MODIFY_ENTRIES` | `mode=ADD, entries=[EntryLeg da entry_price]`, `entry_structure=None` |
| `UPDATE_TAKE_PROFITS` | `MODIFY_TARGETS` | `mode=...` (REPLACE_ALL/ADD/UPDATE_ONE/REMOVE_ONE), `take_profits=[...]`, `target_tp_level=...` |

Mapping dettagliato `UPDATE_TAKE_PROFITS`:

| `entities.mode` | `entities.new_take_profits` | `entities.target_tp_level` | output |
|---|---|---|---|
| `REPLACE_ALL` o None | non vuoto | — | `mode=REPLACE_ALL, take_profits=[...]` |
| `ADD` | non vuoto | — | `mode=ADD, take_profits=[...]` |
| `UPDATE_ONE` | non vuoto (1 elem) | richiesto | `mode=UPDATE_ONE, take_profits=[...], target_tp_level=N` |
| `REMOVE_ONE` | vuoto | richiesto | `mode=REMOVE_ONE, take_profits=[], target_tp_level=N` |

`REMOVE_ONE` con `take_profits=[]` richiede il rilassamento del validator
`ModifyTargetsOperation._validate_modify_targets` (validatore condizionale per mode).

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

### Messaggi compositi — regole

**Compositi ammessi in v1**:
- `UPDATE + REPORT`
- `UPDATE + INFO`
- `REPORT + INFO`

**Compositi vietati in v1**:
- `SIGNAL + UPDATE`
- `SIGNAL + REPORT` (rimosso da v1: era "tollerato eccezionalmente")
- `SIGNAL + INFO`

**Regola del translator** (`primary_class=SIGNAL` ha priorità assoluta):

```
se parsed.signal is not None (segnale rilevato):
    primary_class = SIGNAL
    signal = SignalPayload(...)
    update = None
    report = None
    intents non-SIGNAL CONFIRMED → soppressi con warning
        "composite_with_signal_dropped:<intent_type>"

elif intents CONFIRMED contengono almeno un UPDATE intent:
    primary_class = UPDATE
    update = UpdatePayload(operations=[... da UPDATE intents ...])
    report = ReportPayload(events=[...]) se anche REPORT presenti, altrimenti None

elif tutti gli intents CONFIRMED sono REPORT:
    primary_class = REPORT
    report = ReportPayload(events=[...])

elif solo INFO_ONLY CONFIRMED:
    primary_class = INFO
    signal/update/report = None
    diagnostics.info_fragments = [raw_fragment, ...]
```

Tutti i CONFIRMED intents compaiono in `CanonicalMessage.intents[]` (a meno che
soppressi). Il `primary_intent` segue la `primary_intent_precedence`.

**INFO_ONLY in composite UPDATE+INFO o REPORT+INFO:**
- non genera payload
- nome `"INFO_ONLY"` aggiunto a `CanonicalMessage.intents[]`
- `raw_fragment` preservato in `diagnostics.info_fragments: list[str]`

**Asimmetria del validator:**

| `primary_class` | `update` | `report` | `signal` |
|---|---|---|---|
| `SIGNAL` | ❌ vietato | ❌ vietato | ✅ richiesto |
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

**Uso di `valid_refs` (post-validator)**:

Il translator popola `TargetedAction.targeting.targets` (e `TargetedReport.targeting.targets`)
da `intent.valid_refs`, **non** da `targeting_override.refs` originale. I refs invalidati
sono già stati filtrati dal validator e restano in `intent.invalid_refs` per audit.

Edge case: `valid_refs == []` non si presenta perché il validator avrebbe già marcato
l'intent come `INVALID` (escluso prima del translator, sez. 12).

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

## 13c. Orchestratore parser

Punto di ingresso unico che incatena profilo → validator → disambiguation → translator.
Vive in `src/parser/__init__.py` e viene chiamato dal router.

### Contratto

```python
def parse_message(
    text: str,
    context: ParserContext,
    profile: TraderProfile,
    validator: IntentValidator,
    translator: IntentTranslator,
    disambiguation_engine: DisambiguationEngine,
) -> tuple[ParsedMessage, CanonicalMessage]:
    parsed = profile.parse(text, context)               # sync, stateless
    parsed = validator.validate(parsed)                  # sync, sqlite3
    parsed = disambiguation_engine.apply(parsed, profile.rules)   # sync, stateless
    canonical = translator.translate(parsed)             # sync, stateless
    return parsed, canonical
```

Tutto sync (decisione 4.3 — sqlite3, niente async). Il router invoca `parse_message()`
in un singolo step, persiste sia `parsed` (in `parsed_messages`) che `canonical`
(in `parse_results_v1`).

### Responsabilità

- istanziamento di validator/translator/disambiguation_engine: a livello applicativo
  (es. nel router al boot), iniettati come dipendenze
- gestione errori: ogni step può sollevare; il router cattura e marca il `raw_message`
  come `failed` con il `processing_status`

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
| `shared/disambiguation_engine.py` | nuovo `shared/disambiguation.py` (schema nested + priority) |
| `shared/disambiguation_rules_schema.py` | Pydantic aggiornato per nuovo schema |
| `trader_profiles/shared/profile_runtime.py` | nuovo `shared/runtime.py` |
| `trader_profiles/shared/rules_schema.py` | schema per `semantic_markers.json` + `rules.json` |
| `trader_profiles/shared/rules_schema.json` | nuovo JSON schema |
| `trader_profiles/shared/intent_taxonomy.py` | tassonomia 15 intents da spec sezione 4 |
| `trader_profiles/shared/targeting.py` | logica integrata nel nuovo `shared/runtime.py` |
| `trader_profiles/base.py` | `ParserContext` migrato in `src/parser/context.py`, `TraderParseResult` rimosso |
| `trader_x/parsing_rules.json` | split in `semantic_markers.json` + `rules.json` (per ogni profilo) |
| `trader_x/profile.py` | riscrivere ~20 righe con nuovo contratto |
| `src/telegram/router.py` | aggiornare detection profili (`parse()` invece di `parse_canonical()`) e flusso (`parse_message()`) |

### 🆕 Nuovi file

| file | scopo |
|---|---|
| `src/parser/__init__.py::parse_message()` | orchestratore (sez. 13c) |
| `src/parser/context.py` | nuova home di `ParserContext` (ex `trader_profiles/base.py`) |
| `src/parser/parsed_message.py` | modelli `ParsedMessage`, `IntentResult`, entities |
| `src/parser/intent_types.py` | enum `IntentType`, `IntentCategory` |
| `src/parser/intent_validator/validator.py` | layer separato (sez. 12) |
| `src/parser/intent_validator/validation_rules.json` | regole storia DB |
| `src/parser/intent_validator/history_provider.py` | `HistoryProvider` Protocol + impl SQLite |
| `src/intent_translator/translator.py` | layer separato (sez. 13b) |
| `src/intent_translator/scope_mapping.json` | mappatura globale ai valori canonici di `CancelScope` |
| `src/storage/parsed_messages.py` + tabella DB | persistenza ParsedMessage per debug/replay/history |

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

### Fase 4.5 — Router migration
*Aggiornare il router per usare l'orchestratore parse_message() e persistere ParsedMessage.*

- [ ] Aggiornare detection profili in `router.py`: usare `parse()` invece di `parse_canonical()`
- [ ] Wirare istanza singola di `IntentValidator`, `IntentTranslator`, `DisambiguationEngine` al boot
- [ ] Sostituire chiamata profilo con `parse_message(text, ctx, profile, validator, translator, ...)`
- [ ] Creare migrazione DB per tabella `parsed_messages`:
  - colonne: `raw_message_id, trader_id, primary_class, validation_status, composite, parsed_json, intents_confirmed_json, created_at`
- [ ] Persistere sia `ParsedMessage` (in `parsed_messages`) sia `CanonicalMessage` (in `parse_results_v1`)
- [ ] Feature flag `PARSER_USE_PARSED_MESSAGE` per dual-stack durante validazione
- [ ] Logging di divergenze legacy vs nuovo durante dual-stack
- [ ] Test integration: `test_router_parsed_message.py`

---

### Fase 5 — Intent validator
*Layer separato. Richiede DB. Testabile con DB test esistente.*

- [ ] Creare `src/parser/intent_validator/__init__.py`
- [ ] Creare `src/parser/intent_validator/validation_rules.json`:
  - [ ] regole per i 6 intents con verifica storia (compilare a mano)
- [ ] Creare `src/parser/intent_validator/history_provider.py`:
  - [ ] `HistoryProvider` Protocol
  - [ ] implementazione SQLite con CTE ricorsiva su `raw_messages.reply_to_message_id`
  - [ ] query legge `parsed_messages.intents_confirmed_json` (filtra automaticamente i CONFIRMED)
- [ ] Creare `src/parser/intent_validator/validator.py`:
  - [ ] carica `validation_rules.json`
  - [ ] sync, sqlite3 (decisione 4.3 — niente async)
  - [ ] flusso per ogni IntentResult (auto-CONFIRMED, SINGLE_SIGNAL, scope globale)
  - [ ] popola `valid_refs`, `invalid_refs`, `invalid_reason`
  - [ ] setta `validation_status = VALIDATED`
- [ ] Test con DB test (`parser_test/`):
  - [ ] verificare riduzione falsi positivi su campione reale
  - [ ] verificare che intents senza regola siano auto-CONFIRMED
  - [ ] verificare scope globale → auto-CONFIRMED
  - [ ] verificare che history consideri solo CONFIRMED storici

---

### Fase 6 — Intent translator
*Layer separato. Stateless. Testabile senza DB.*

- [ ] Creare `src/intent_translator/__init__.py`
- [ ] Creare `src/intent_translator/scope_mapping.json` (vocabolario universale, sez. 9.3)
- [ ] Creare `src/intent_translator/translator.py`:
  - [ ] lookup table `_INTENT_TO_UPDATE_OP` per i 10 UPDATE intents
  - [ ] lookup table `_INTENT_TO_REPORT_EVENT` per i 6 REPORT intents
  - [ ] logica `translate(parsed: ParsedMessage) -> CanonicalMessage`
  - [ ] regola priorità `primary_class=SIGNAL` con soppressione intents non-SIGNAL
  - [ ] gestione caso multi-ref (targeting_override → TargetedAction / TargetedReport)
  - [ ] uso di `valid_refs` (non `targeting_override.refs`) per popolare `targets`
  - [ ] mapping completo `UPDATE_TAKE_PROFITS` su tutti i 4 modi (REPLACE_ALL/ADD/UPDATE_ONE/REMOVE_ONE)
  - [ ] mapping `INVALIDATE_SETUP → cancel_scope="ALL_POSITIONS"` (no `ALL_ALL`)
  - [ ] INFO_ONLY composite → `intents[]` + `diagnostics.info_fragments`
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
  - [ ] `trader_profiles/base.py` (TraderParseResult rimosso, ParserContext migrato)
  - [ ] tutti i `parsing_rules.json` per profilo
- [ ] Rimuovere `shared/disambiguation_engine.py` (vecchio)
- [ ] Smettere di scrivere su `parse_results` (legacy); marcare la tabella come deprecata in CLAUDE.md
- [ ] Aggiornare `trader_profiles/registry.py` definitivo
- [ ] Migrare `operation_rules/engine.py`: input `CanonicalMessage` invece di `TraderParseResult`, eliminare `_coerce_entities()`
- [ ] Aggiornare `CLAUDE.md` con stato migrazione completata

---

### Fase 9 — Migrazione target_resolver
*Sblocca la rimozione finale di `src/parser/models/`.*

- [ ] Aggiornare `src/target_resolver/resolver.py`: input `CanonicalMessage` invece di
      `OperationalSignal` legacy
- [ ] Resolver consuma `CanonicalMessage.targeting` + `CanonicalMessage.targeted_actions`
- [ ] Rimuovere `src/parser/models/` (canonical.py, new_signal.py, update.py, operational.py)
- [ ] Drop tabella `parse_results` legacy (migration finale)
- [ ] Aggiornare CLAUDE.md: lista "non toccare" semplificata

---

### Dipendenze tra fasi

```
Fase 1 (cleanup)
    ↓
Fase 2 (ParsedMessage models)
    ↓
Fase 3 (shared runtime + disambiguation)
    ↓
Fase 4 (trader_a pilota)         ←── validare qui prima di procedere
    ↓
Fase 4.5 (router migration)      ←── orchestratore parse_message + tabella parsed_messages
    ↓
Fase 5 (intent_validator)        ←── richiede Fase 4.5 completata (legge parsed_messages)
    ↓
Fase 6 (intent_translator)       ←── richiede Fase 2 + CanonicalMessage invariato
    ↓
Fase 7 (altri profili)           ←── parallelizzabile per profilo
    ↓
Fase 8 (cleanup finale)          ←── solo dopo Fase 7 completa
    ↓
Fase 9 (target_resolver)         ←── sblocca rimozione models/ e parse_results legacy
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
| Forme schema | flat hardcoded | solo nested + `priority` |
| Condizioni | `when_all_detected` + `if_contains_any` | `when_strong`, `when_weak`, `text_any`, `text_none`, `message_*`, `entities_*` (path `<INTENT>.<field>`) |
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
