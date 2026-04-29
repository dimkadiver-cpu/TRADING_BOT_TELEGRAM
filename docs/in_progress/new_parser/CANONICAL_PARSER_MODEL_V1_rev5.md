# CANONICAL PARSER MODEL V1 - rev5 (regenerated)

## Scopo

Definire un contratto canonico unico per il parser, semplice e universale, da usare come base comune per parser trader-specifici futuri.

Questo documento descrive il modello target v1.

---

## Principi

1. Esiste un solo output canonico finale.
2. Parser trader-specifici possono avere logiche interne diverse, ma devono convergere nello stesso schema.
3. I payload canonici sono la verita business finale.
4. `intents` e `primary_intent` restano nel modello come supporto semantico, non come source of truth operativo.
5. I messaggi compositi sono ammessi solo con regole esplicite.

---

## Envelope top-level

```python
class CanonicalMessage:
    schema_version: str
    parser_profile: str

    primary_class: Literal["SIGNAL", "UPDATE", "REPORT", "INFO"]
    parse_status: Literal["PARSED", "PARTIAL", "UNCLASSIFIED", "ERROR"]
    confidence: float

    intents: list[str]
    primary_intent: str | None

    targeting: Targeting | None

    signal: SignalPayload | None
    update: UpdatePayload | None
    report: ReportPayload | None

    warnings: list[str]
    diagnostics: dict[str, Any]

    raw_context: RawContext
```

### Significato campi

- `schema_version`: versione contratto.
- `parser_profile`: parser trader-specifico che ha prodotto il risultato.
- `primary_class`: classe dominante del messaggio.
- `parse_status`: esito parsing.
- `confidence`: confidenza globale.
- `intents`: intent rilevati.
- `primary_intent`: intent principale.
- `targeting`: target normalizzato del messaggio.
- `signal`, `update`, `report`: payload canonici business.
- `warnings`: warning business-relevant leggibili.
- `diagnostics`: dettagli tecnici parser/debug.
- `raw_context`: contesto grezzo per audit e replay.

---

## Regola di precedenza

La verita business finale e rappresentata da:

- `targeting`
- `signal`
- `update`
- `report`

`intents` e `primary_intent` restano supporto semantico e non bloccano da soli la validazione business.

---

## RawContext

```python
class RawContext:
    raw_text: str
    reply_to_message_id: int | None
    extracted_links: list[str]
    hashtags: list[str]
    source_chat_id: str | None
    source_topic_id: int | None
    acquisition_mode: Literal["live", "catchup"] | None
```

Regola: `RawContext` contiene solo contesto grezzo, non semantica derivata.

---

## Targeting

```python
TargetingStrategy = Literal[
    "REPLY_OR_LINK",
    "SYMBOL_MATCH",
    "GLOBAL_SCOPE",
    "UNRESOLVED",
]

TargetScopeKind = Literal[
    "SINGLE_SIGNAL",
    "SYMBOL",
    "PORTFOLIO_SIDE",
    "ALL_OPEN",
    "UNKNOWN",
]

TargetRefType = Literal[
    "REPLY",
    "TELEGRAM_LINK",
    "MESSAGE_ID",
    "EXPLICIT_ID",
    "SYMBOL",
]

class TargetRef:
    ref_type: TargetRefType
    value: str | int

class TargetScope:
    kind: TargetScopeKind
    value: str | None
    side_filter: Literal["LONG", "SHORT"] | None
    applies_to_all: bool

class Targeting:
    refs: list[TargetRef]
    scope: TargetScope
    strategy: TargetingStrategy
    targeted: bool
```

Note:

- `EXPLICIT_ID` e parte del contratto.
- `targeted` resta descrittivo/ridondante, non e una seconda fonte di verita.
- La coerenza interna di `targeted` puo essere documentata ma non va enforced con validator duro.

---

## SignalPayload

```python
Side = Literal["LONG", "SHORT"]
EntryStructure = Literal["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"]
EntryType = Literal["MARKET", "LIMIT"]

class PriceValue:
    raw: str
    value: float

class EntryLeg:
    sequence: int
    entry_type: EntryType
    price: PriceValue | None
    role: Literal["PRIMARY", "AVERAGING", "UNKNOWN"]
    size_hint: str | None
    note: str | None
    is_optional: bool

class StopLoss:
    price: PriceValue | None

class TakeProfit:
    sequence: int
    price: PriceValue
    label: str | None
    close_fraction: float | None

class RiskHint:
    raw: str | None
    value: float | None
    unit: Literal["PERCENT", "ABSOLUTE", "UNKNOWN"]

class SignalPayload:
    symbol: str | None
    side: Side | None

    entry_structure: EntryStructure | None
    entries: list[EntryLeg]

    stop_loss: StopLoss | None
    take_profits: list[TakeProfit]

    leverage_hint: float | None
    risk_hint: RiskHint | None

    invalidation_rule: str | None
    conditions: str | None

    completeness: Literal["COMPLETE", "INCOMPLETE"] | None
    missing_fields: list[str]

    raw_fragments: dict[str, str | None]
```

### Entry model

- Non esiste `entry_type` globale.
- `entry_type` esiste solo sulle singole `entries[*]`.
- `entry_structure` descrive la forma globale del piano.

### Cardinalita finali entry

- `ONE_SHOT` = esattamente 1 leg
- `TWO_STEP` = esattamente 2 legs
- `RANGE` = esattamente 2 legs
- `LADDER` = almeno 3 legs

### Matrice EntryStructure x EntryType (enforced)

| Structure | Leg 1 | Leg 2..N |
|---|---|---|
| `ONE_SHOT` | MARKET o LIMIT | - |
| `TWO_STEP` | MARKET o LIMIT | LIMIT |
| `RANGE` | LIMIT | LIMIT |
| `LADDER` | MARKET o LIMIT | LIMIT |

Vincolo: `MARKET` ammesso solo su `sequence=1`. Le leg successive sono ordini in attesa,
richiedono livello di prezzo, quindi `LIMIT` obbligatorio.

`MARKET` con `price` popolato preserva il prezzo come indicativo (snapshot al momento del
messaggio); il prezzo non viene scartato.

Nota:

- `ONE_SHOT` con `entry_type = MARKET` puo avere `price = null`.
- La leg deve comunque esistere (quindi mai 0 legs in `ONE_SHOT`).

### Conversione legacy entry

- `SINGLE -> ONE_SHOT`
- `ZONE -> RANGE` con leg `LIMIT`
- `AVERAGING (n=2) -> TWO_STEP` con leg `LIMIT`
- `AVERAGING (n>=3) -> LADDER` con leg `LIMIT`
- `entry_plan_type` legacy -> `entry_structure` canonica + `entry_type` per leg

### Demozione strutturale (cardinalita insufficiente)

Quando l'extractor identifica un marker multi-leg ma trova un numero di prezzi inferiore,
il parser demota la struttura e degrada `parse_status` a `PARTIAL`:

| Caso intended | n. prezzi | Demozione |
|---|---|---|
| AVERAGING/ZONE/RANGE/TWO_STEP/LADDER | 1 | -> ONE_SHOT (PARTIAL + warning) |
| LADDER | 2 | -> TWO_STEP (PARTIAL + warning) |
| qualsiasi (!= MARKET/ONE_SHOT) | 0 | -> entry_structure=None (PARTIAL, missing entries) |

Warning machine-readable: `entry_structure_demoted:<INTENDED>-><TARGET>:<reason>`.

### Invalidation

La invalidation e dato descrittivo del `SIGNAL`:

- `invalidation_rule`
- oppure frammenti descrittivi in `raw_fragments`

Non e un update canonico.

---

## UpdatePayload

Update canonici v1 (solo questi 5):

- `SET_STOP`
- `CLOSE`
- `CANCEL_PENDING`
- `MODIFY_ENTRIES`
- `MODIFY_TARGETS`

```python
UpdateOperationType = Literal[
    "SET_STOP",
    "CLOSE",
    "CANCEL_PENDING",
    "MODIFY_ENTRIES",
    "MODIFY_TARGETS",
]

class StopTarget:
    target_type: Literal["PRICE", "ENTRY", "TP_LEVEL"]
    value: float | int | None

class CloseOperation:
    close_fraction: float | None
    close_price: PriceValue | None
    close_scope: str | None

class CancelPendingOperation:
    cancel_scope: str | None

class ModifyEntriesOperation:
    mode: Literal["ADD", "REENTER", "UPDATE"]
    entries: list[EntryLeg]
    entry_structure: Literal["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"] | None
    # entry_structure tipicamente popolato per REENTER multi-leg; None per ADD/UPDATE

class ModifyTargetsOperation:
    mode: Literal["REPLACE_ALL", "ADD", "UPDATE_ONE", "REMOVE_ONE"]
    take_profits: list[TakeProfit]
    target_tp_level: int | None

class UpdateOperation:
    op_type: UpdateOperationType

    set_stop: StopTarget | None
    close: CloseOperation | None
    cancel_pending: CancelPendingOperation | None
    modify_entries: ModifyEntriesOperation | None
    modify_targets: ModifyTargetsOperation | None

    raw_fragment: str | None
    confidence: float | None

class UpdatePayload:
    operations: list[UpdateOperation]
```

### Semantica update

`SET_STOP` copre:

- stop a prezzo
- stop a breakeven
- stop a TP level

`CLOSE` copre:

- full close
- partial close
- close su scope

`CANCEL_PENDING` significa:

- cancella tutto cio che e pending nel target/scope

In v1 semplificato:

- non esiste `REMOVE_PENDING_ENTRY`
- non si usa `MODIFY_ENTRIES` per remove

`MODIFY_ENTRIES` include solo:

- `ADD`
- `REENTER`
- `UPDATE`

`MODIFY_TARGETS` include:

- `REPLACE_ALL`
- `ADD`
- `UPDATE_ONE`
- `REMOVE_ONE`

### Lifecycle

`SET_SIGNAL_STATE` non esiste nel modello canonico v1.

Se un messaggio successivo ordina di annullare o chiudere:

- usare `CANCEL_PENDING`
- usare `CLOSE`

---

## ReportPayload

Il `REPORT` descrive cosa e successo, non cosa fare.

```python
ReportEventType = Literal[
    "ENTRY_FILLED",
    "TP_HIT",
    "STOP_HIT",
    "BREAKEVEN_EXIT",
    "FINAL_RESULT",
]

ResultUnit = Literal["R", "PERCENT", "TEXT", "UNKNOWN"]

class ReportedResult:
    value: float | None
    unit: ResultUnit
    text: str | None

class ReportEvent:
    event_type: ReportEventType
    level: int | None
    price: PriceValue | None
    result: ReportedResult | None
    raw_fragment: str | None
    confidence: float | None

class ReportPayload:
    events: list[ReportEvent]
    reported_result: ReportedResult | None
    notes: list[str]
```

Event types ammessi:

- `ENTRY_FILLED`
- `TP_HIT`
- `STOP_HIT`
- `BREAKEVEN_EXIT`
- `FINAL_RESULT`

---

## Messaggi compositi

Ammessi in v1:

- `UPDATE + REPORT`
- `UPDATE + INFO`
- `REPORT + INFO`

Non ammessi in v1:

- `SIGNAL + UPDATE`
- `SIGNAL + REPORT` (rimosso da v1: era "tollerato eccezionalmente" in revisioni precedenti)
- `SIGNAL + INFO`

Regola di soppressione del translator: quando `signal` è popolato, gli intents non-SIGNAL
CONFIRMED vengono soppressi con warning `composite_with_signal_dropped:<intent_type>`.

---

## Regole di validazione

Distinzione chiave:

- I validator Pydantic fanno validazione strutturale (tipo, forma, coerenza minima).
- Le regole business dipendenti da `primary_class` e `parse_status` vivono a livello top-level.

### Envelope invariants

1. `primary_class = SIGNAL`
- `signal` obbligatorio
- `update` assente
- `report` opzionale solo per casi eccezionali

2. `primary_class = UPDATE`
- `update` obbligatorio
- `signal` assente
- `report` opzionale

3. `primary_class = REPORT`
- `report` obbligatorio
- `signal` assente
- `update` assente

4. `primary_class = INFO`
- `signal`, `update`, `report` assenti

### Regole `parse_status = PARSED`

Se `primary_class = SIGNAL` e `parse_status = PARSED`, sono richiesti almeno:

- `signal.symbol`
- `signal.side`
- `signal.entry_structure`
- `signal.stop_loss`
- almeno un `signal.take_profit`
- cardinalita `entries` coerente con `entry_structure`

Se `primary_class = UPDATE` e `parse_status = PARSED`:

- almeno una operation in `update.operations`
- ogni operation deve essere strutturalmente valida

Se `primary_class = REPORT` e `parse_status = PARSED`:

- almeno un `event`
  oppure
- `reported_result` presente

`SIGNAL` con `parse_status = PARTIAL` e ammesso quando il messaggio e chiaramente un segnale ma mancano uno o piu campi richiesti per `PARSED`.

### Regole non hard-enforced

- coerenza `primary_intent in intents`: raccomandata, non validator duro
- coerenza dedotta di `targeted`: descrittiva, non validator duro

---

## Warnings vs diagnostics

- `warnings`: warning leggibili e business-relevant.
- `diagnostics`: dettagli tecnici parser/debug.

---

## Verdetto v1

Il modello canonico v1 e:

- piu semplice del modello ibrido precedente
- universale per parser trader-specifici
- stabile per evoluzioni future
- operativo senza introdurre nuova complessita
