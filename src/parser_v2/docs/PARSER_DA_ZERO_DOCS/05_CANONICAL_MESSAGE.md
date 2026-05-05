# Contratto `CanonicalMessage`

## Scopo

`CanonicalMessage` è l'output finale del parser.

Deve essere pronto per il runtime successivo, ma non deve contenere validazione operativa.

```text
Parser finisce qui.
TargetResolver e Validator iniziano dopo.
```

---

## Modello proposto

```python
class CanonicalMessage(BaseModel):
    schema_version: str = "canonical_message_v2"
    parser_profile: str

    primary_class: MessageClass
    parse_status: ParseStatus
    confidence: float

    primary_intent: IntentType | None = None
    intents: list[IntentType] = []

    signal: SignalPayload | None = None
    update: UpdatePayload | None = None
    report: ReportPayload | None = None
    info: InfoPayload | None = None

    targeted_actions: list[TargetedAction] = []
    target_hints: TargetHints | None = None

    warnings: list[str] = []
    diagnostics: dict[str, Any] = {}

    raw_context: RawContext
```

> **Versione schema**: `canonical_message_v2` — incompatibile con `canonical_message_v1` del parser legacy. Differenze principali:
> - rimosso `validation_status` (era validazione DB dentro al parser)
> - rimossi `valid_refs` / `invalid_refs` / `invalid_reason`
> - rinominato `Targeting` → `TargetHints` + nuovo campo top-level `targeted_actions`
> - `intents` ora è `list[IntentType]` (solo tipi); il dettaglio per intent vive in `update.operations` / `report.events`
> - rimossi nomi legacy `U_*`

---

## Nota su targeting

Anche se il targeting risolto è fuori parser, conviene mantenere `target_hints` e `targeted_actions`.

Differenza:

```text
target_hints / targeted_actions = cosa il messaggio suggerisce
resolved_target                 = cosa il DB ha trovato (post-parser)
```

Il parser può dire:

```json
{
  "target_hints": {
    "reply_to_message_id": 123,
    "telegram_links": ["https://t.me/c/123/456"],
    "telegram_message_ids": [456],
    "symbols": ["ETHUSDT"],
    "scope_hint": "SINGLE_SIGNAL"
  }
}
```

Non deve dire:

```json
{
  "position_id": 77,
  "attempt_key": "...",
  "target_eligibility": "ELIGIBLE"
}
```

Quindi:

```text
Target hints sì.
Target resolution no.
```

---

## Quando usare `update.operations` vs `targeted_actions`

```text
update.operations:
  comandi message-wide senza target esplicito multipli
  applicabili al target generale del messaggio (reply, scope_hint, ecc.)

targeted_actions:
  comandi che si applicano a uno o più target specifici dichiarati
  (es. più link/message_id in righe diverse, gruppi t.me/.../N)
```

Vedi [08_MULTI_REF_TARGETED_ACTIONS.md](08_MULTI_REF_TARGETED_ACTIONS.md) per casi e algoritmo.

Regola di precedenza (SSoT):

```text
Se esiste almeno un target esplicito (telegram link/message id) con azione operativa:
  → tutte le azioni operative vanno in targeted_actions
  → update.operations resta vuoto

Se non esiste targeting esplicito per-azione:
  → usa update.operations
```

Non è consentito “metà in update.operations e metà in targeted_actions” nello stesso messaggio.

---

# SignalPayload

```python
class SignalPayload(BaseModel):
    symbol: str | None = None
    side: Literal["LONG", "SHORT"] | None = None

    entry_structure: EntryStructure | None = None
    entries: list[EntryLeg] = []

    stop_loss: StopLoss | None = None
    take_profits: list[TakeProfit] = []

    risk_hint: RiskHint | None = None
    leverage_hint: float | None = None

    missing_fields: list[str] = []
    completeness: Literal["COMPLETE", "INCOMPLETE"]
```

## Completezza

```text
COMPLETE   = symbol + side + entries + stop_loss + take_profits
INCOMPLETE = manca almeno uno
```

---

# UpdatePayload

```python
class UpdatePayload(BaseModel):
    operations: list[UpdateOperation] = []
```

Vincolo di validazione parser-level: `UPDATE/PARTIAL` può avere `operations` vuote solo se `warnings` non è vuoto.

```python
class InvalidateSetupOperation(BaseModel):
    reason_text: str | None = None
```

`INVALIDATE_SETUP` usa payload minimale: `reason_text` opzionale.

---

## Precedenza `UPDATE` vs `REPORT`

Regola unica:

```text
SIGNAL > UPDATE > REPORT > INFO
```

Quindi, se nel messaggio coesistono intent UPDATE e REPORT, `primary_class` è `UPDATE`.

`REPORT_RESULT` non modifica questa precedenza: resta nel payload `report.result` e non promuove `primary_class=REPORT` in presenza di UPDATE.

---

## Posizionamento `REPORT_RESULT`

`REPORT_RESULT` vive in `report.result`, non in `report.events`.

- `report.events[].event_type`: solo `ENTRY_FILLED | TP_HIT | SL_HIT | EXIT_BE`
- `report.result`: testo risultato libero (`ReportResult.raw_fragment`)

---

## UpdateOperation

```python
class UpdateOperation(BaseModel):
    op_type: Literal[
        "SET_STOP",
        "CLOSE",
        "CANCEL_PENDING",
        "MODIFY_ENTRIES",
        "MODIFY_TARGETS",
        "INVALIDATE_SETUP"
    ]

    set_stop: SetStopOperation | None = None
    close: CloseOperation | None = None
    cancel_pending: CancelPendingOperation | None = None
    modify_entries: ModifyEntriesOperation | None = None
    modify_targets: ModifyTargetsOperation | None = None
    invalidate_setup: InvalidateSetupOperation | None = None

    source_intent: IntentType
    confidence: float | None = None
    raw_fragment: str | None = None
```

Ogni operation deve avere solo il payload coerente con `op_type`.

---

## Mappa intent → update operation

| Intent              | Operation                              |
|---------------------|----------------------------------------|
| `MOVE_STOP_TO_BE`   | `SET_STOP target_type=ENTRY`           |
| `MOVE_STOP`         | `SET_STOP target_type=PRICE/TP_LEVEL`  |
| `CLOSE_FULL`        | `CLOSE close_scope=FULL`               |
| `CLOSE_PARTIAL`     | `CLOSE close_scope=PARTIAL`            |
| `CANCEL_PENDING`    | `CANCEL_PENDING`                       |
| `INVALIDATE_SETUP`  | `INVALIDATE_SETUP`                     |
| `REENTER`           | `MODIFY_ENTRIES kind=REENTER`          |
| `ADD_ENTRY`         | `MODIFY_ENTRIES kind=ADD`              |
| `MODIFY_ENTRY`      | `MODIFY_ENTRIES kind=MARKET_NOW/UPDATE_PRICE/REMOVE` |
| `MODIFY_TARGETS`    | `MODIFY_TARGETS`                       |

---

# ReportPayload minimale

Dato che non vengono usati PnL/R/%:

```python
class ReportPayload(BaseModel):
    events: list[ReportEvent] = []
    result: ReportResult | None = None
```

## ReportEvent

```python
class ReportEvent(BaseModel):
    event_type: Literal[
        "ENTRY_FILLED",
        "TP_HIT",
        "SL_HIT",
        "EXIT_BE"
    ]

    level: int | None = None
    price: Price | None = None
    source_intent: IntentType
    raw_fragment: str | None = None
```

## ReportResult

```python
class ReportResult(BaseModel):
    raw_fragment: str | None = None
```

Niente:

```text
result_value
result_percent
R
currency
pnl
```

Se un domani serviranno, si aggiungono senza rompere il contratto.

---

# InfoPayload

```python
class InfoPayload(BaseModel):
    raw_fragment: str | None = None
```

> Niente `info_type` / sottocategorie ADMIN/SCHEDULE/GREETING/etc. Vedi [03_INTENTS_ENTITIES_MINIME.md](03_INTENTS_ENTITIES_MINIME.md#info_only) per la motivazione.

---

# TargetHints

```python
class TargetHints(BaseModel):
    reply_to_message_id: int | None = None
    telegram_message_ids: list[int] = []
    telegram_links: list[str] = []
    explicit_ids: list[str] = []
    symbols: list[str] = []

    scope_hint: Literal[
        "SINGLE_SIGNAL",
        "SYMBOL",
        "ALL_LONG",
        "ALL_SHORT",
        "ALL_POSITIONS",
        "ALL_OPEN",
        "ALL_REMAINING",
        "UNKNOWN"
    ] = "UNKNOWN"
```

Questa struttura non risolve nulla. È solo input pulito per il resolver successivo.

---

# TargetedAction

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

    source_intent: IntentType
    raw_fragment: str | None = None
    confidence: float | None = None
```

Vedi [08_MULTI_REF_TARGETED_ACTIONS.md](08_MULTI_REF_TARGETED_ACTIONS.md) per i casi supportati e l'algoritmo di costruzione.

---

# Validazioni schema

## `SIGNAL`

```text
primary_class = SIGNAL
signal != None
update == None
targeted_actions vuoto
```

`report` può essere `None`.

## `UPDATE`

```text
primary_class = UPDATE
update.operations non vuoto OPPURE targeted_actions non vuoto
signal == None
```

`report` può essere presente solo se messaggio composito (vedi sezione composite).

## `REPORT`

```text
primary_class = REPORT
report != None
update == None
targeted_actions vuoto
signal == None
```

## `INFO`

```text
primary_class = INFO
signal == None
update == None
report == None
targeted_actions vuoto
info opzionale
```

---

# Messaggi compositi

Un messaggio può contenere più intenti compatibili.

## Caso 1 — UPDATE + REPORT

Esempio:

```text
первый тейк взяли, стоп в бу
```

`primary_class=UPDATE` (UPDATE domina REPORT). Il REPORT viene comunque conservato in `report.events`:

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "primary_intent": "MOVE_STOP_TO_BE",
  "intents": ["TP_HIT", "MOVE_STOP_TO_BE"],
  "update": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {"target_type": "ENTRY"},
        "source_intent": "MOVE_STOP_TO_BE"
      }
    ]
  },
  "report": {
    "events": [
      {
        "event_type": "TP_HIT",
        "level": 1,
        "source_intent": "TP_HIT"
      }
    ]
  }
}
```

## Caso 2 — REPORT + UPDATE quando il REPORT domina

Esempio:

```text
выбило по стопу, всем закрываю
```

`SL_HIT` ha precedenza alta nel `primary_intent_precedence` (vedi [06_MARKERS_RULES.md](06_MARKERS_RULES.md)). In questo caso `primary_class=REPORT` ma il `CLOSE_FULL` è ridondante (è già implicito dallo SL hit). Output:

```json
{
  "primary_class": "REPORT",
  "primary_intent": "SL_HIT",
  "intents": ["SL_HIT", "CLOSE_FULL"],
  "report": {
    "events": [
      {"event_type": "SL_HIT", "source_intent": "SL_HIT"}
    ]
  },
  "warnings": ["close_full_redundant_with_sl_hit"]
}
```

## Caso 3 — SIGNAL + UPDATE non supportato

Un messaggio non può essere `primary_class=SIGNAL` e contenere `update.operations`. Se il parser rileva entrambi, prevale il SIGNAL e gli intent UPDATE vengono soppressi con warning `update_intents_dropped_in_signal_message`.

## Regola generale composite

```text
1. UPDATE batte REPORT come primary_class.
2. SIGNAL batte tutto: se signal_payload presente, primary_class=SIGNAL.
3. Eccezione: se primary_intent_precedence dichiara un REPORT prioritario (es. SL_HIT),
   primary_class può restare REPORT e gli UPDATE finire come warning di ridondanza.
4. payload secondari (report dentro UPDATE) sono opzionali — popolare solo se il parser ha estratto entità sensate.
```

---

# Esempio UPDATE single

Input:

```text
стоп в бу
```

Canonical:

```json
{
  "schema_version": "canonical_message_v2",
  "parser_profile": "trader_a",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "primary_intent": "MOVE_STOP_TO_BE",
  "intents": ["MOVE_STOP_TO_BE"],
  "update": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {"target_type": "ENTRY"},
        "source_intent": "MOVE_STOP_TO_BE"
      }
    ]
  },
  "warnings": ["update_without_target_hint"]
}
```

---

# Esempio REPORT

Input:

```text
первый тейк взяли
```

Canonical:

```json
{
  "primary_class": "REPORT",
  "parse_status": "PARSED",
  "primary_intent": "TP_HIT",
  "intents": ["TP_HIT"],
  "report": {
    "events": [
      {
        "event_type": "TP_HIT",
        "level": 1,
        "source_intent": "TP_HIT"
      }
    ]
  }
}
```

---

# Esempio SIGNAL PARTIAL

Input:

```text
ETHUSDT LONG
Вход лимиткой 2114
Stop 2100
Тейки позже
```

Canonical:

```json
{
  "primary_class": "SIGNAL",
  "parse_status": "PARTIAL",
  "signal": {
    "symbol": "ETHUSDT",
    "side": "LONG",
    "entry_structure": "ONE_SHOT",
    "entries": [
      {
        "sequence": 1,
        "entry_type": "LIMIT",
        "price": {"raw": "2114", "value": 2114.0},
        "role": "PRIMARY"
      }
    ],
    "stop_loss": {"price": {"raw": "2100", "value": 2100.0}},
    "take_profits": [],
    "missing_fields": ["take_profits"],
    "completeness": "INCOMPLETE"
  }
}
```

---

# Esempio multi-ref TARGETED

Input:

```text
LINK https://t.me/c/123/978  стоп в бу
LINK https://t.me/c/123/1002 стоп в бу
```

Canonical:

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "primary_intent": "MOVE_STOP_TO_BE",
  "intents": ["MOVE_STOP_TO_BE"],
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {"target_type": "ENTRY"},
      "target_hints": {
        "telegram_message_ids": [978, 1002],
        "scope_hint": "SINGLE_SIGNAL"
      },
      "source_intent": "MOVE_STOP_TO_BE"
    }
  ]
}
```
