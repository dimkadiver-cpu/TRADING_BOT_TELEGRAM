# 10 — Funzionalità utili da recuperare dal parser attuale

## Scopo

Questo documento raccoglie le parti del parser attuale che vale la pena portare nella riscrittura da zero.

Il criterio non è “copiare il vecchio parser”, ma recuperare solo ciò che:

```text
- è già utile nei dati reali
- è semplice da testare
- non introduce validazione DB dentro il parser
- non aumenta inutilmente la complessità del contratto
```

Il nuovo parser resta limitato a:

```text
Raw Telegram message
↓
ParsedMessage
↓
CanonicalMessage
```

Restano fuori:

```text
TargetResolver
ApplicabilityValidator
ExecutionPlanner
ExecutionApplier
DB lifecycle validation
```

---

## 1. Normalizzazione testo minimale

## Da recuperare

Il parser attuale normalizza già alcuni caratteri utili:

```text
ё -> е
– — − -> -
lowercase
```

Questa logica va portata nel nuovo `TextNormalizer`.

## Da migliorare

Aggiungere:

```text
collapse spazi multipli
trim righe
conservazione sempre del raw_text originale
```

## Contratto consigliato

```python
class NormalizedText(BaseModel):
    raw_text: str
    normalized_text: str
    lines: list[str]
```

## Regola

Il matching usa `normalized_text`.

L’estrazione numerica può usare anche `raw_text`, per non perdere formati particolari.

---

## 2. Normalizzazione prezzi robusta

## Da recuperare

Il modello canonical attuale ha già una logica utile per normalizzare prezzi in formati diversi.

Formati da supportare:

```text
90 000.5
90,000.5
90.000,5
1 234,56
0.1772
```

## Contratto consigliato

```python
class Price(BaseModel):
    raw: str
    value: float
```

## Regola

Il parser deve sempre conservare:

```text
raw = valore originale nel testo
value = float normalizzato
```

Questo è utile per audit e debug.

---

## 3. Entry A/B e ruoli PRIMARY / AVERAGING

## Da recuperare

Il parser attuale riconosce strutture tipo:

```text
A (с текущих): ...
B (лимит): ...
```

oppure:

```text
вход ...
усреднение ...
```

Questa è una funzionalità utile per Trader A.

## Contratto consigliato

```python
class EntryLeg(BaseModel):
    sequence: int
    entry_type: Literal["MARKET", "LIMIT"]
    price: Price | None = None
    role: Literal["PRIMARY", "AVERAGING", "UNKNOWN"] = "UNKNOWN"
    is_optional: bool = False
```

## Regole

```text
A / primo ingresso      -> role = PRIMARY
B / усреднение          -> role = AVERAGING
AVERAGING               -> is_optional = True
MARKET entry            -> price può essere None
LIMIT entry             -> price obbligatorio
```

## Esempio concettuale

```text
A (с текущих): 2114
B (лимит): 2090
```

Output:

```json
{
  "entries": [
    {
      "sequence": 1,
      "entry_type": "MARKET",
      "price": {"raw": "2114", "value": 2114.0},
      "role": "PRIMARY",
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": {"raw": "2090", "value": 2090.0},
      "role": "AVERAGING",
      "is_optional": true
    }
  ]
}
```

---

## 4. Entry structure più precisa

## Da recuperare

Il parser attuale deduce già la struttura dagli ingressi.

## Da migliorare

Non basta:

```text
1 entry  -> ONE_SHOT
2 entries -> TWO_STEP
3+ entries -> LADDER
```

Serve una distinzione più semantica.

## Regole consigliate

```text
1 prezzo/entry singola                -> ONE_SHOT
A/B oppure ingresso + усреднение       -> TWO_STEP
range esplicito 2110 - 2120            -> RANGE
3+ livelli di ingresso                 -> LADDER
```

## Contratto

```python
EntryStructure = Literal[
    "ONE_SHOT",
    "TWO_STEP",
    "RANGE",
    "LADDER"
]
```

## Validazioni

```text
ONE_SHOT -> esattamente 1 entry
TWO_STEP -> esattamente 2 entry
RANGE    -> esattamente 2 entry, entrambe LIMIT
LADDER   -> almeno 3 entry
```

---

## 5. Completezza del segnale

## Problema attuale

Il parser attuale considera missing fields soprattutto:

```text
entries
stop_loss
take_profits
```

Nel nuovo parser va corretto.

## Regola nuova

Un segnale completo richiede:

```text
symbol
side
entries
stop_loss
take_profits
```

## Output

Se manca qualcosa:

```json
{
  "primary_class": "SIGNAL",
  "parse_status": "PARTIAL",
  "signal": {
    "missing_fields": ["take_profits"],
    "completeness": "INCOMPLETE"
  }
}
```

## Regola importante

Un messaggio con struttura da setup incompleto resta:

```text
SIGNAL / PARTIAL
```

Non deve diventare `UPDATE` o `INFO`.

---

## 6. MOVE_STOP: prezzo o TP level

## Da recuperare

Il parser attuale distingue bene due casi:

```text
стоп на 1 тейк -> stop_to_tp_level = 1
стоп на 2140  -> new_stop_price = 2140
```

## Contratto

```python
class MoveStopEntities(BaseModel):
    new_stop_price: Price | None = None
    stop_to_tp_level: int | None = None
```

## Mapping canonical

### Stop a prezzo

```json
{
  "op_type": "SET_STOP",
  "set_stop": {
    "target_type": "PRICE",
    "price": {
      "raw": "2140",
      "value": 2140.0
    }
  }
}
```

### Stop a TP level

```json
{
  "op_type": "SET_STOP",
  "set_stop": {
    "target_type": "TP_LEVEL",
    "value": 1
  }
}
```

## Warning

Se viene rilevato `MOVE_STOP` senza prezzo o TP level:

```text
move_stop_without_level
```

---

## 7. CLOSE_PARTIAL con percentuale o “half/pоловину”

## Da recuperare

Il parser attuale estrae:

```text
50%      -> fraction = 0.5
half     -> fraction = 0.5
половину -> fraction = 0.5
```

## Contratto

```python
class ClosePartialEntities(BaseModel):
    fraction: float | None = None
    close_price: Price | None = None
```

## Regole

```text
percentuale 0-100 -> fraction 0.0-1.0
half / половину   -> fraction = 0.5
частично senza %   -> fraction = None
```

## Warning opzionale

```text
close_partial_without_fraction
```

Non è necessariamente errore. Può restare valido.

---

## 8. Raw fragment

## Da recuperare

Il parser attuale conserva `raw_fragment` sugli intenti.

Da mantenere.

## Perché serve

```text
- debug
- audit
- report qualità parser
- review umana
- ricostruzione del motivo di parsing
```

## Contratto

```python
class ParsedIntent(BaseModel):
    type: IntentType
    category: IntentCategory
    entities: IntentEntities
    confidence: float
    raw_fragment: str | None = None
```

Anche le operation canonical possono conservarlo:

```python
class UpdateOperation(BaseModel):
    op_type: UpdateOperationType
    raw_fragment: str | None = None
```

---

## 9. Diagnostics e warnings

## Da recuperare

Il parser attuale usa `diagnostics` e `warnings`.

Da mantenere, ma rendere più strutturato.

## Diagnostics consigliati

```json
{
  "matched_markers": [],
  "suppressed_markers": [],
  "applied_marker_rules": [],
  "applied_disambiguation_rules": [],
  "classification_reasons": [],
  "signal_missing_fields": [],
  "signal_entry_count": 0,
  "signal_tp_count": 0
}
```

## Warning consigliati

```text
partial_signal_missing_fields
update_without_target_hint
move_stop_without_level
modify_entry_mode_unknown
ambiguous_multi_ref_intent_mapping
multi_ref_mixed_intents_not_supported
weak_only_intent
```

---

## 10. Targeted actions

## Da recuperare

Il parser attuale ha già una gestione utile di:

```text
EXPLICIT_TARGETS
TARGET_GROUP
SELECTOR
```

Da portare nel nuovo parser, ma in forma più semplice.

## Contratto nuovo

```python
class TargetHints(BaseModel):
    reply_to_message_id: int | None = None
    telegram_message_ids: list[int] = []
    telegram_links: list[str] = []
    explicit_ids: list[str] = []
    symbols: list[str] = []
    scope_hint: str = "UNKNOWN"
```

```python
class TargetedAction(BaseModel):
    action_type: Literal[
        "SET_STOP",
        "CLOSE",
        "CANCEL_PENDING",
        "MODIFY_ENTRIES",
        "MODIFY_TARGETS",
        "INVALIDATE_SETUP"
    ]
    params: dict
    target_hints: TargetHints
    raw_fragment: str | None = None
    confidence: float | None = None
```

## Casi da supportare

```text
multi-line stesso comando
multi-link comando condiviso
selector globale
```

## Casi da non supportare per ora

```text
link 111 стоп в бу
link 222 закрываю
link 333 лимитки убираем
```

Output:

```text
parse_status = PARTIAL
warning = multi_ref_mixed_intents_not_supported
```

---

## 11. Schema validation Pydantic

## Da recuperare

Il modello canonical attuale valida la coerenza tra:

```text
primary_class
parse_status
business payload
```

Questa è una buona pratica.

## Regole consigliate

### SIGNAL

```text
primary_class = SIGNAL
signal != None
update == None
```

### UPDATE

```text
primary_class = UPDATE
update.operations non vuoto oppure targeted_actions non vuoto
signal == None
```

### REPORT

```text
primary_class = REPORT
report != None
signal == None
update == None
```

### INFO

```text
primary_class = INFO
signal == None
update == None
report == None
```

### PARSED SIGNAL

Richiede:

```text
symbol
side
entries
stop_loss
take_profits
```

---

## 12. MODIFY_TARGETS

## Da recuperare con cautela

Il parser attuale ha già entità tipo:

```python
class UpdateTakeProfitsEntities(BaseModel):
    new_take_profits: list[Price]
    target_tp_level: int | None
    mode: ModifyTargetsMode | None
```

Questa parte può essere utile.

## Contratto consigliato

Rinominare in:

```python
class ModifyTargetsEntities(BaseModel):
    take_profits: list[Price] = []
    target_tp_level: int | None = None
    mode: Literal[
        "REPLACE_ALL",
        "ADD",
        "UPDATE_ONE",
        "REMOVE_ONE",
        "UNKNOWN"
    ] = "UNKNOWN"
```

## Marker

```text
новые тейки
обновляем тейки
добавлю тейк
убираю 2 тейк
первый тейк убираем
```

## Nota

Non implementarlo se per ora non è usato dal runtime. È utile ma non prioritario.

---

## 13. REENTER con entry structure

## Da recuperare con cautela

Il parser attuale per `REENTER` estrae:

```text
entries
entry_type
entry_structure
```

Questo è sensato.

## Contratto

```python
class ReenterEntities(BaseModel):
    entries: list[Price] = []
    entry_type: EntryType | None = None
    entry_structure: EntryStructure | None = None
```

## Regola

`REENTER` non è uguale a `MODIFY_ENTRY / MARKET_NOW`.

```text
перезаходим / заходим заново -> REENTER
входим по рынку su setup pendente -> MODIFY_ENTRY / MARKET_NOW
```

---

# Cose da non portare

## 1. Report dettagliato

Non portare:

```text
ReportedResult
result_value
result_percent
result_currency
REPORT_FINAL_RESULT
REPORT_PARTIAL_RESULT
```

Hai deciso che non serve.

Nel nuovo parser basta:

```python
class ReportResultEntities(BaseModel):
    pass
```

oppure:

```python
class ReportResultEntities(BaseModel):
    raw_fragment: str | None = None
```

---

## 2. InfoPayload dettagliato

Non portare:

```text
ADMIN
SCHEDULE
GREETING
DISCLAIMER
MARKET_COMMENT
```

Nel nuovo parser basta:

```text
primary_class = INFO
primary_intent = INFO_ONLY
```

---

## 3. Validazione DB dentro parser

Non portare:

```text
validation_status = VALIDATED
status = CONFIRMED / INVALID
valid_refs
invalid_refs
invalid_reason
```

Questi campi appartengono al validator post-parser.

Nel parser nuovo usare:

```text
evidence_status = RESOLVED / AMBIGUOUS / LOW_CONFIDENCE
```

---

## 4. Translator che richiede VALIDATED

Non portare la regola:

```text
IntentTranslator requires ParsedMessage.validation_status=VALIDATED
```

Nel nuovo parser il translator deve produrre `CanonicalMessage` anche prima della validazione operativa.

---

## 5. INVALIDATE_SETUP mappato a CANCEL_PENDING

Non portare questa semplificazione.

Nel nuovo parser `INVALIDATE_SETUP` deve restare distinto.

## Contratto consigliato

```python
UpdateOperationType = Literal[
    "SET_STOP",
    "CLOSE",
    "CANCEL_PENDING",
    "MODIFY_ENTRIES",
    "MODIFY_TARGETS",
    "INVALIDATE_SETUP"
]
```

---

# Priorità di implementazione

Ordine consigliato:

```text
1. Normalizzazione testo
2. Normalizzazione prezzi
3. EntryLeg con role/is_optional
4. Signal completeness corretta
5. MOVE_STOP price vs TP level
6. CLOSE_PARTIAL fraction
7. Raw fragment + diagnostics
8. Targeted actions minime
9. Schema validation
10. MODIFY_TARGETS / REENTER solo dopo test reali
```

---

# Decisione finale

Da recuperare subito:

```text
- TextNormalizer minimale
- Price normalization
- EntryLeg role/is_optional
- EntryStructure
- MoveStop price/TP-level
- ClosePartial fraction
- raw_fragment
- warnings/diagnostics
- targeted_actions
- schema validation
```

Da scartare:

```text
- Report dettagliato
- InfoPayload dettagliato
- validation_status dentro parser
- valid_refs/invalid_refs dentro parser
- translator bloccato su VALIDATED
- INVALIDATE_SETUP convertito in CANCEL_PENDING
```
