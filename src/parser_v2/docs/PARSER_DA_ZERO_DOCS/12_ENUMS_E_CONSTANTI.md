# 12 — Enum e costanti (single source of truth)

Questo documento è l'**unico** riferimento autoritativo per tutti gli enum e i `Literal[...]` del nuovo parser. Tutti gli altri documenti devono linkare qui invece di duplicare i valori.

L'implementazione corrispondente vive in `src/parser_v2/contracts/enums.py`.

---

## `MessageClass`

```python
MessageClass = Literal[
    "SIGNAL",
    "UPDATE",
    "REPORT",
    "INFO",
]
```

Rappresenta `primary_class` di `ParsedMessage` e `CanonicalMessage`.

> ⚠️ `UNCLASSIFIED` **non** è una `MessageClass` — è un `ParseStatus`. Un messaggio non classificabile è `primary_class=INFO` con `parse_status=UNCLASSIFIED`.

---

## `ParseStatus`

```python
ParseStatus = Literal[
    "PARSED",
    "PARTIAL",
    "UNCLASSIFIED",
    "ERROR",
]
```

| Valore | Significato |
|--------|-------------|
| `PARSED` | messaggio interpretato in modo sufficiente |
| `PARTIAL` | riconosciuto ma mancano campi essenziali |
| `UNCLASSIFIED` | nessuna struttura utile |
| `ERROR` | errore tecnico o schema invalidabile |

---

## `EvidenceStatus`

```python
EvidenceStatus = Literal[
    "RESOLVED",
    "AMBIGUOUS",
    "LOW_CONFIDENCE",
]
```

`ParsedIntent.status` usa **lo stesso enum `EvidenceStatus`** (non esiste un enum separato `IntentEvidenceStatus`).

Vedi [02_CONTRATTO_PARSED_MESSAGE.md](02_CONTRATTO_PARSED_MESSAGE.md#evidence_status) per la formula di derivazione.

---

## `IntentType`

```python
IntentType = Literal[
    # UPDATE category
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING",
    "INVALIDATE_SETUP",
    "REENTER",
    "ADD_ENTRY",
    "MODIFY_ENTRY",
    "MODIFY_TARGETS",

    # REPORT category
    "ENTRY_FILLED",
    "TP_HIT",
    "SL_HIT",
    "EXIT_BE",

    # REPORT result category
    "REPORT_RESULT",

    # INFO category
    "INFO_ONLY",
]
```

> Niente `CREATE_SIGNAL` (un nuovo segnale è `primary_class=SIGNAL` + `signal` payload, non un intent).
> Niente prefissi `U_*` legacy.

---

## `IntentCategory`

```python
IntentCategory = Literal[
    "SIGNAL",
    "UPDATE",
    "REPORT",
    "INFO",
]
```

Mappa intent → category:

| Intent              | Category |
|---------------------|----------|
| `MOVE_STOP_TO_BE`   | `UPDATE` |
| `MOVE_STOP`         | `UPDATE` |
| `CLOSE_FULL`        | `UPDATE` |
| `CLOSE_PARTIAL`     | `UPDATE` |
| `CANCEL_PENDING`    | `UPDATE` |
| `INVALIDATE_SETUP`  | `UPDATE` |
| `REENTER`           | `UPDATE` |
| `ADD_ENTRY`         | `UPDATE` |
| `MODIFY_ENTRY`      | `UPDATE` |
| `MODIFY_TARGETS`    | `UPDATE` |
| `ENTRY_FILLED`      | `REPORT` |
| `TP_HIT`            | `REPORT` |
| `SL_HIT`            | `REPORT` |
| `EXIT_BE`           | `REPORT` |
| `REPORT_RESULT`     | `REPORT` |
| `INFO_ONLY`         | `INFO`   |

---

## `Side`

```python
Side = Literal["LONG", "SHORT"]
```

---

## `EntryStructure`

```python
EntryStructure = Literal[
    "ONE_SHOT",
    "TWO_STEP",
    "RANGE",
    "LADDER",
]
```

Vincoli:

| Structure | Numero entries | Note                                |
|-----------|----------------|-------------------------------------|
| `ONE_SHOT`| 1              | leg singolo MARKET o LIMIT          |
| `TWO_STEP`| 2              | A/B oppure ingresso + усреднение    |
| `RANGE`   | 2              | range esplicito, entrambi LIMIT     |
| `LADDER`  | ≥ 3            | 3+ livelli di ingresso              |

---

## `EntryType`

```python
EntryType = Literal[
    "MARKET",
    "LIMIT",
]
```

Regole:

```text
MARKET → price può essere None
LIMIT  → price obbligatorio
```

---

## `EntryRole`

```python
EntryRole = Literal[
    "PRIMARY",
    "AVERAGING",
    "UNKNOWN",
]
```

Mapping da forme testuali (Trader A):

```text
A / primo ingresso / вход     → PRIMARY
B / усреднение / averaging in → AVERAGING
```

---

## `ModifyEntryMode`

```python
ModifyEntryMode = Literal[
    "MARKET_NOW",
    "UPDATE_PRICE",
    "REMOVE",
    "UNKNOWN",
]
```

Questo enum vale solo per l'intent `MODIFY_ENTRY` (modifica di un'entry esistente).

> `REPLACE` **non** è incluso (non distinguibile affidabilmente da `UPDATE_PRICE`).
> `ADD` e `REENTER` **non** sono `ModifyEntryMode`.

Vedi [09_MODIFY_ENTRY_MODE_MARKERS.md](09_MODIFY_ENTRY_MODE_MARKERS.md).

---

## `ModifyEntriesOperationKind`

```python
ModifyEntriesOperationKind = Literal[
    "ADD",
    "REENTER",
    "MARKET_NOW",
    "UPDATE_PRICE",
    "REMOVE",
    "UNKNOWN",
]
```

Enum operativo usato nel payload canonical `update.operations[].modify_entries.kind`.

Regola SSoT:

- `ADD_ENTRY` → `MODIFY_ENTRIES kind=ADD`
- `REENTER` → `MODIFY_ENTRIES kind=REENTER`
- `MODIFY_ENTRY` → `MODIFY_ENTRIES kind=MARKET_NOW/UPDATE_PRICE/REMOVE/UNKNOWN`

---

## `ModifyTargetsMode`

```python
ModifyTargetsMode = Literal[
    "REPLACE_ALL",
    "ADD",
    "UPDATE_ONE",
    "REMOVE_ONE",
    "UNKNOWN",
]
```

---

## `ScopeHint`

```python
ScopeHint = Literal[
    "SINGLE_SIGNAL",
    "SYMBOL",
    "ALL_LONG",
    "ALL_SHORT",
    "ALL_POSITIONS",
    "ALL_OPEN",
    "ALL_REMAINING",
    "UNKNOWN",
]
```

Default: `"UNKNOWN"`.

---

## `CancelScopeHint`

```python
CancelScopeHint = Literal[
    "TARGETED",
    "ALL_PENDING",
    "ALL_LONG",
    "ALL_SHORT",
    "ALL_POSITIONS",
    "UNKNOWN",
]
```

Hint linguistico per `CancelPendingEntities.cancel_scope_hint`.

---

## `UpdateOperationType`

```python
UpdateOperationType = Literal[
    "SET_STOP",
    "CLOSE",
    "CANCEL_PENDING",
    "MODIFY_ENTRIES",
    "MODIFY_TARGETS",
    "INVALIDATE_SETUP",
]
```

Usato sia in `UpdateOperation.op_type` sia in `TargetedAction.action_type`.

---

## `SetStopTargetType`

```python
SetStopTargetType = Literal[
    "ENTRY",
    "PRICE",
    "TP_LEVEL",
]
```

Usato in `SetStopOperation.target_type`.

---

## `CloseScope`

```python
CloseScope = Literal[
    "FULL",
    "PARTIAL",
]
```

Usato in `CloseOperation.close_scope`.

---

## `ReportEventType`

```python
ReportEventType = Literal[
    "ENTRY_FILLED",
    "TP_HIT",
    "SL_HIT",
    "EXIT_BE",
]
```

Sottoinsieme di `IntentType` per la categoria REPORT, escluso `REPORT_RESULT` (che vive in `ReportPayload.result`, non in `events`).

---

## `MarkerStrength`

```python
MarkerStrength = Literal[
    "strong",
    "weak",
]
```

Lowercase per coerenza con il vocabolario JSON.

---

## `MarkerKind`

```python
MarkerKind = Literal[
    "intent",
    "field",
    "side",
    "entry_type",
    "modify_entry_mode",
    "info",
    "target_hint",
]
```

Identifica a quale sezione di `semantic_markers.json` appartiene il match.

---

## `Completeness`

```python
Completeness = Literal[
    "COMPLETE",
    "INCOMPLETE",
]
```

Per `SignalPayload.completeness`.

```text
COMPLETE = symbol + side + entries + stop_loss + take_profits
```

---

## Costanti pesi marker

```python
STRONG_WEIGHT: float = 1.0
WEAK_WEIGHT: float   = 0.4
```

Vedi [02_CONTRATTO_PARSED_MESSAGE.md](02_CONTRATTO_PARSED_MESSAGE.md#confidence) per la formula.

---

## Schema versions

```python
PARSED_MESSAGE_SCHEMA_VERSION:    str = "parsed_message_v2"
CANONICAL_MESSAGE_SCHEMA_VERSION: str = "canonical_message_v2"
```

---

## Regola di gestione

Quando si aggiunge o modifica un valore di un enum:

```text
1. Aggiornare PRIMA questo file.
2. Aggiornare `src/parser_v2/contracts/enums.py`.
3. Aggiornare i documenti che hanno tabelle/esempi (con link a questo doc).
4. Aggiornare i test.
```

Mai duplicare la lista completa altrove — solo riferimenti.
