# Canonical Parser Model v1

## Stato documento

- Versione: `v1-draft-01`
- Scopo: definire un **contratto canonico unico** per il Layer 4 parser
- Obiettivo: usare questo modello come base universale per tutti i parser attuali e futuri
- Ambito: solo **semantica parser**; non include execution, policy, target resolver exchange-side

---

## 1. Problema da risolvere

L'architettura attuale del parser ha questi problemi principali:

1. convivono più contratti dati;
2. alcuni concetti uguali hanno nomi diversi;
3. `UPDATE` contiene sia azioni operative sia eventi/report;
4. `entities` è troppo eterogeneo;
5. il modello attuale è potente ma non abbastanza semplice da usare come standard universale per i futuri parser trader-specifici.

Questo documento definisce un modello più semplice, più stabile e più universale.

---

## 2. Obiettivi del nuovo modello

Il nuovo modello deve:

- essere **universale** per trader diversi;
- essere il **solo output canonico** del parser;
- separare chiaramente:
  - segnale iniziale,
  - update operativi,
  - report/eventi,
  - messaggi informativi;
- ridurre il numero di update canonici;
- permettere ai parser trader-specifici di essere liberi internamente, ma obbligarli a uscire con una struttura standard;
- essere leggibile, estendibile e auditabile.

---

## 3. Principi di design

### 3.1 Un solo contratto autorevole

Esiste un solo output canonico finale.

I parser trader-specifici possono produrre strutture intermedie, ma **prima di uscire** devono passare da un normalizer unico che converte tutto nel modello canonico v1.

### 3.2 Distinzione tra significato e azione

Il parser deve descrivere **cosa dice il messaggio**.

Non deve contenere logica exchange-specifica o policy di esecuzione.

### 3.3 Distinzione tra update e report

Un messaggio che dice:

- “sposta stop”
- “chiudi 50%”
- “cancella pending”

è un `UPDATE`.

Un messaggio che dice:

- “TP1 colpito”
- “stop preso”
- “entrata eseguita”
- “trade chiuso a +1.5R”

è un `REPORT`.

### 3.4 Nomi canonici stabili

Ogni concetto deve avere un solo nome canonico.

Gli alias sono ammessi solo in input o nella fase di normalizzazione, non nell'output canonico finale.

---

## 4. Tassonomia canonica dei messaggi

### 4.1 Message class

Il parser emette una sola tra queste classi business:

- `SIGNAL`
- `UPDATE`
- `REPORT`
- `INFO`

### 4.2 Parse status

Separatamente dal `primary_class`, il parser emette uno stato tecnico:

- `PARSED`
- `PARTIAL`
- `UNCLASSIFIED`
- `ERROR`

### 4.3 Regole

- `SIGNAL + PARSED` = setup completo
- `SIGNAL + PARTIAL` = setup incompleto ma riconosciuto
- `INFO + UNCLASSIFIED` non è corretto: se è informativo, deve essere classificato come `INFO`
- `UNCLASSIFIED` è uno **status tecnico**, non un tipo business

---

## 5. Envelope principale

```python
MessageClass = Literal["SIGNAL", "UPDATE", "REPORT", "INFO"]
ParseStatus = Literal["PARSED", "PARTIAL", "UNCLASSIFIED", "ERROR"]

class CanonicalMessage:
    schema_version: str
    parser_profile: str
    primary_class: MessageClass
    parse_status: ParseStatus
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

### 5.1 Regole envelope

- `signal` è valorizzato solo se `primary_class == SIGNAL`
- `update` è valorizzato solo se `primary_class == UPDATE`
- `report` è valorizzato solo se `primary_class == REPORT`
- per `INFO`, i tre payload business sono `None`

---

## 6. Raw context

Il raw context serve per audit, debug e linking, ma non è payload business.

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

---

## 7. Targeting unificato

Il targeting è un blocco unico, separato dal payload.

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

### 7.1 Regole targeting

- `refs` contiene le evidenze grezze viste dal parser
- `scope` rappresenta l'ambito logico del target
- `strategy` spiega il metodo di risoluzione atteso
- il parser non deve risolvere posizioni reali exchange-side; descrive solo il targeting semantico

---

## 8. SIGNAL payload

Questo payload descrive un setup/segnale iniziale.

### 8.1 Tipi base

```python
Side = Literal["LONG", "SHORT"]
EntryType = Literal["MARKET", "LIMIT"]
EntryStructure = Literal["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"]
OrderType = Literal["MARKET", "LIMIT"]

class PriceValue:
    raw: str
    value: float
```

### 8.1.1 Regola canonica per l'entry

Il modello canonico separa due concetti diversi:

- `entry_type` = **tipo ordine principale**
  - `MARKET`
  - `LIMIT`
- `entry_structure` = **struttura del piano di ingresso**
  - `ONE_SHOT`
  - `TWO_STEP`
  - `RANGE`
  - `LADDER`

Questa separazione sostituisce l'approccio precedente in cui un solo campo mescolava:
- tipo ordine
- numero di entry
- geometria del setup

### 8.1.2 Conversione dai valori legacy

| Legacy | Canonico |
|---|---|
| `SINGLE` | `entry_type=LIMIT`, `entry_structure=ONE_SHOT` |
| `ZONE` | `entry_type=LIMIT`, `entry_structure=RANGE` |
| `AVERAGING` con 2 livelli | `entry_type=LIMIT`, `entry_structure=TWO_STEP` |
| `AVERAGING` con 3+ livelli | `entry_type=LIMIT`, `entry_structure=LADDER` |
| `MARKET` | `entry_type=MARKET`, `entry_structure=ONE_SHOT` |

Regola pratica:
- `entry_plan_type` legacy non deve più uscire nel contratto canonico come campo primario
- va convertito nella coppia canonica `entry_type + entry_structure`

### 8.2 Entry / stop / target

```python
class EntryLeg:
    sequence: int
    price: PriceValue | None
    order_type: OrderType
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
```

### 8.3 Risk hint

```python
class RiskHint:
    raw: str | None
    value: float | None
    unit: Literal["PERCENT", "ABSOLUTE", "UNKNOWN"]
```

### 8.4 Payload completo

```python
class SignalPayload:
    symbol: str | None
    side: Side | None

    entry_type: EntryType | None
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

### 8.5 Regole di completezza

Per considerare un `SIGNAL` come `COMPLETE` servono:

- `symbol`
- `side`
- `entry_type`
- `entry_structure`
- `stop_loss`
- almeno un `take_profit`

In più:

- `entries` è obbligatorio se `entry_type = LIMIT`
- per `entry_type = MARKET` gli `entries` possono essere vuoti oppure contenere un riferimento indicativo non vincolante
- `entry_structure = RANGE` richiede esattamente 2 livelli logici di ingresso
- `entry_structure = TWO_STEP` richiede esattamente 2 entry leg
- `entry_structure = LADDER` richiede 3 o più entry leg
- `entry_structure = ONE_SHOT` richiede 0 o 1 entry leg, a seconda che l'ingresso sia `MARKET` o `LIMIT`

Se manca qualcosa ma il messaggio è chiaramente un setup, allora:

- `primary_class = SIGNAL`
- `parse_status = PARTIAL`
- `signal.completeness = INCOMPLETE`
- `signal.missing_fields = [...]`

---

## 9. UPDATE payload

Gli update canonici vengono ridotti a **6 operazioni**.

### 9.1 Operazioni canoniche

```python
UpdateOperationType = Literal[
    "SET_STOP",
    "CLOSE",
    "CANCEL_PENDING",
    "MODIFY_ENTRIES",
    "MODIFY_TARGETS",
    "SET_SIGNAL_STATE",
]
```

### 9.2 SET_STOP

```python
StopTargetType = Literal["PRICE", "ENTRY", "TP_LEVEL"]

class StopTarget:
    target_type: StopTargetType
    value: float | int | None
```

Regole:

- `PRICE` → stop a prezzo esplicito
- `ENTRY` → stop a breakeven / entry
- `TP_LEVEL` → stop spostato a livello tipo TP1

### 9.3 CLOSE

```python
class CloseOperation:
    close_fraction: float | None
    close_price: PriceValue | None
    close_scope: str | None
```

Regole:

- `close_fraction = 1.0` → close full
- `close_fraction < 1.0` → close partial
- `close_scope` può esprimere casi tipo `FULL`, `PARTIAL`, `ALL_LONGS`, `ALL_SHORTS`

### 9.4 CANCEL_PENDING

```python
class CancelPendingOperation:
    cancel_scope: str | None
```

Agisce sui pending entry / ordini pendenti.

### 9.5 MODIFY_ENTRIES

```python
class ModifyEntriesOperation:
    mode: Literal["ADD", "REENTER", "UPDATE", "REMOVE"]
    entries: list[EntryLeg]
    target_entry_id: str | None
```

### 9.6 MODIFY_TARGETS

```python
class ModifyTargetsOperation:
    mode: Literal["REPLACE_ALL", "ADD", "UPDATE_ONE", "REMOVE_ONE"]
    take_profits: list[TakeProfit]
    target_tp_level: int | None
```

### 9.7 SET_SIGNAL_STATE

```python

```

Nota importante:

- `SET_SIGNAL_STATE` non è semanticamente la stessa cosa di `CANCEL_PENDING`
- può coesistere con `CANCEL_PENDING`, ma non lo sostituisce

### 9.8 Wrapper operazione

```python
class UpdateOperation:
    op_type: UpdateOperationType

    set_stop: StopTarget | None
    close: CloseOperation | None
    cancel_pending: CancelPendingOperation | None
    modify_entries: ModifyEntriesOperation | None
    modify_targets: ModifyTargetsOperation | None

    raw_fragment: str | None
    confidence: float | None
```

### 9.9 Payload update completo

```python
class UpdatePayload:
    operations: list[UpdateOperation]
```

### 9.10 Regole update

- uno stesso messaggio può contenere più operazioni
- l'ordine delle operazioni deve riflettere il testo, quando possibile
- `UPDATE` non deve contenere eventi/report puri

---

## 10. REPORT payload

Il report descrive cosa è accaduto, non cosa bisogna fare.

### 10.1 Eventi report canonici

```python
ReportEventType = Literal[
    "ENTRY_FILLED",
    "TP_HIT",
    "STOP_HIT",
    "BREAKEVEN_EXIT",
    "FINAL_RESULT",
]
```

### 10.2 Risultato riportato

```python
ResultUnit = Literal["R", "PERCENT", "TEXT", "UNKNOWN"]

class ReportedResult:
    value: float | None
    unit: ResultUnit
    text: str | None
```

### 10.3 Eventi

```python
class ReportEvent:
    event_type: ReportEventType
    target_level: int | None
    fill_state: str | None
    related_price: PriceValue | None
    note: str | None
```

### 10.4 Payload completo

```python
class ReportPayload:
    events: list[ReportEvent]
    reported_result: ReportedResult | None
```

### 10.5 Regole report

Questi concetti devono andare in `REPORT`, non in `UPDATE`:

- fill / activation
- TP hit
- stop hit
- breakeven exit
- risultato finale in R / % / testo

---

## 11. INFO payload

`INFO` non ha payload business dedicato.

Regola:

- `signal = None`
- `update = None`
- `report = None`

Può avere solo:

- `warnings`
- `diagnostics`
- `raw_context`

---

## 12. Convenzioni di naming canonico

### 12.1 Nomi da usare

Usare questi nomi canonici:

- `side`
- `symbol`
- `entries`
- `stop_loss`
- `take_profits`
- `close_fraction`
- `close_price`
- `cancel_scope`
- `reported_result`
- `targeting`

### 12.2 Nomi da non usare nell'output canonico finale

Questi possono esistere in input o nella fase intermedia, ma non nell'output canonico:

- `direction`
- `new_sl_level`
- `close_pct`
- `partial_close_percent`
- `reported_rr`
- `reported_profit_r`
- `reported_profit_pct`
- `target_ref`
- `target_refs` come concetto separato da `targeting`

---

## 13. Mapping concettuale dal modello attuale al modello nuovo

### 13.1 Signal

Concetti attuali come:

- `NEW_SIGNAL`
- `SETUP_INCOMPLETE`

vengono mappati in:

- `primary_class = SIGNAL`
- `parse_status = PARSED | PARTIAL`

### 13.2 Update

Intent attuali come:

- `U_MOVE_STOP`
- `U_MOVE_STOP_TO_BE`
- `U_CLOSE_FULL`
- `U_CLOSE_PARTIAL`
- `U_CANCEL_PENDING_ORDERS`
- `U_REMOVE_PENDING_ENTRY`
- `U_REENTER`
- `U_ADD_ENTRY`
- `U_UPDATE_PENDING_ENTRY`
- `U_UPDATE_TAKE_PROFITS`
- `U_INVALIDATE_SETUP`

vengono mappati nei 6 update canonici.

### 13.3 Report

Intent/eventi attuali come:

- `U_MARK_FILLED`
- `U_ACTIVATION`
- `U_TP_HIT`
- `U_STOP_HIT`
- `U_EXIT_BE`
- `U_REPORT_FINAL_RESULT`

vengono mappati in `REPORT`.

---

## 14. Ruolo dei parser trader-specifici

Ogni parser trader-specifico deve:

1. classificare il messaggio;
2. estrarre segnali grezzi specifici del trader;
3. passare il risultato grezzo a un **Canonical Normalizer**;
4. uscire solo con `CanonicalMessage`.

### Regola fondamentale

Il parser trader-specifico **non costruisce il contratto finale a mano**.

Il contratto finale è responsabilità del normalizer canonico condiviso.

---

## 15. Ruolo del normalizer canonico

Il normalizer canonico deve:

- unificare i nomi;
- convertire alias in campi canonici;
- separare signal/update/report/info;
- validare coerenza minima;
- produrre sempre un `CanonicalMessage` valido.

---

## 16. Persistenza consigliata nel DB

### 16.1 Principio

Il JSON canonico completo deve diventare il **source of truth**.

Le colonne tabellari servono solo per:

- query rapide,
- indici,
- report sintetici.

### 16.2 Consiglio

- mantenere una colonna JSON tipo `parse_result_canonical_json`
- esporre poche colonne leggere:
  - `primary_class`
  - `parse_status`
  - `symbol`
  - `side`
  - `primary_operation` / `primary_intent` se utile

---

## 17. Decisioni architetturali già fissate in v1

Queste decisioni sono considerate parte del modello v1:

1. esiste un solo contratto canonico finale;
2. le classi business sono `SIGNAL`, `UPDATE`, `REPORT`, `INFO`;
3. `UNCLASSIFIED` è uno status tecnico, non un tipo business;
4. gli update canonici sono solo 6;
5. `CANCEL_PENDING` e `SET_SIGNAL_STATE` restano separati;
6. targeting è un blocco unico;
7. `REPORT` è separato da `UPDATE`;
8. i parser trader-specifici devono passare da un normalizer unico.

---

## 18. Punti aperti da definire nel passo successivo

Questi punti non bloccano il modello v1, ma vanno definiti nel dettaglio successivo:

1. schema Pydantic esatto dei modelli;
2. elenco valori ammessi per `close_scope`;
3. elenco valori ammessi per `cancel_scope`;
4. elenco valori ammessi per `fill_state`;
5. regole di priorità quando un messaggio contiene sia `UPDATE` sia `REPORT`;
6. lista di colonne DB sintetiche da mantenere.

---

## 19. Verdetto finale

Questo modello v1 è pensato per essere:

- più semplice del modello attuale;
- più universale;
- più stabile come contratto;
- più adatto a diventare il template standard per i parser futuri.

Non definisce tutto il downstream operativo.

Definisce il **linguaggio comune** che ogni parser deve parlare.

## Invalidation handling

Invalidation is **not** a canonical UPDATE operation in v1.

Rules such as:
- "valid until 15m closes above X"
- "without retest"
- "setup invalid above/below ..."

belong to the `SIGNAL` payload as descriptive setup metadata, typically through:
- `invalidation_rule`
- `raw_fragments` or equivalent descriptive fields

If a later message explicitly instructs to cancel remaining pending orders or close an active position, it should be represented with canonical UPDATE operations already defined in v1:
- `CANCEL_PENDING`
- `CLOSE`

So v1 keeps lifecycle/state transitions out of the parser operation model and leaves them to downstream policy/runtime logic.


## 6. REPORT payload

The `REPORT` payload describes only **what happened** on the trade, not what to do.
It must stay separate from operational updates.

### Canonical structure

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
    level: int | None                  # e.g. TP1 -> 1
    price: PriceValue | None
    result: ReportedResult | None
    raw_fragment: str | None
    confidence: float | None

class ReportPayload:
    events: list[ReportEvent]
    reported_result: ReportedResult | None
    notes: list[str]
```

### Rules

- `ENTRY_FILLED` covers activation / first fill / limit filled.
- `TP_HIT` is used for target hits, one event per target level when possible.
- `STOP_HIT` is used for stop-loss exits.
- `BREAKEVEN_EXIT` is used when the message clearly says the trade or remainder closed at breakeven.
- `FINAL_RESULT` is used only for explicitly final outcome summaries.

### Composite messages

A composite message may contain both:
- `update.operations`
- `report.events`

Example:
- "TP1 +0.5%, stop to breakeven, cancel remaining order"

should be represented as:
- `primary_class = UPDATE`
- `report.events = [TP_HIT]`
- `update.operations = [SET_STOP, CANCEL_PENDING]`

### Examples

```yaml
report:
  events:
    - event_type: TP_HIT
      level: 1
      price: null
      result:
        value: 0.5
        unit: PERCENT
        text: "+0.5%"
      raw_fragment: "TP1 +0.5%"
      confidence: 0.90
  reported_result: null
  notes: []
```

```yaml
report:
  events:
    - event_type: STOP_HIT
      level: null
      price: null
      result:
        value: -1.0
        unit: R
        text: "-1R"
      raw_fragment: "stop hit -1R"
      confidence: 0.94
  reported_result: null
  notes: []
```

```yaml
report:
  events: []
  reported_result:
    value: 1.7
    unit: R
    text: "BTC +1.7R"
  notes: []
```


## Intents in Canonical Model v1

Canonical Model v1 adopts **Option 2**:

- `intents` are kept explicitly in the top-level envelope
- `primary_intent` is kept explicitly in the top-level envelope

### Why

This preserves a clear semantic bridge with the current parser architecture, where intents are still central in the parser → router → action mapping flow.

### Rule of precedence

Even when `intents` and `primary_intent` are present, the **canonical business source of truth** is still:

- `targeting`
- `signal`
- `update`
- `report`

So:

- `intents` and `primary_intent` are semantic support fields
- canonical payloads are the final normalized meaning of the message

### Final top-level envelope

`CanonicalMessage` includes:

- `schema_version`
- `parser_profile`
- `primary_class`
- `parse_status`
- `confidence`
- `intents`
- `primary_intent`
- `targeting`
- `signal`
- `update`
- `report`
- `warnings`
- `diagnostics`
- `raw_context`

