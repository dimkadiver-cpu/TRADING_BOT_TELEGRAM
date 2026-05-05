# Intents ed entità minime

## Principio

Gli intenti devono descrivere **cosa dice il messaggio**, non cosa farà il sistema.

Quindi evitare nomi legacy operativi tipo:

```text
U_CLOSE_FULL
ACT_CLOSE_FULL
U_CANCEL_PENDING_ORDERS
```

Usare solo nomi canonici.

---

## Lista intenti canonica

> 📖 Riferimento autoritativo: [12_ENUMS_E_CONSTANTI.md](12_ENUMS_E_CONSTANTI.md). Questa sezione è informativa.

```text
MOVE_STOP_TO_BE
MOVE_STOP
CLOSE_FULL
CLOSE_PARTIAL
CANCEL_PENDING
INVALIDATE_SETUP
REENTER
ADD_ENTRY
MODIFY_ENTRY
MODIFY_TARGETS

ENTRY_FILLED
TP_HIT
SL_HIT
EXIT_BE
REPORT_RESULT

INFO_ONLY
```

> Nota: `CREATE_SIGNAL` non è un intent. Un nuovo segnale è rappresentato da `primary_class=SIGNAL` + `signal` payload, non da un intent in `intents[]`.

---

## Categorie

```text
SIGNAL
UPDATE
REPORT
INFO
```

Mappa:

| Intent              | Categoria |
|---------------------|-----------|
| `MOVE_STOP_TO_BE`   | `UPDATE`  |
| `MOVE_STOP`         | `UPDATE`  |
| `CLOSE_FULL`        | `UPDATE`  |
| `CLOSE_PARTIAL`     | `UPDATE`  |
| `CANCEL_PENDING`    | `UPDATE`  |
| `INVALIDATE_SETUP`  | `UPDATE`  |
| `REENTER`           | `UPDATE`  |
| `ADD_ENTRY`         | `UPDATE`  |
| `MODIFY_ENTRY`      | `UPDATE`  |
| `MODIFY_TARGETS`    | `UPDATE`  |
| `ENTRY_FILLED`      | `REPORT`  |
| `TP_HIT`            | `REPORT`  |
| `SL_HIT`            | `REPORT`  |
| `EXIT_BE`           | `REPORT`  |
| `REPORT_RESULT`     | `REPORT`  |
| `INFO_ONLY`         | `INFO`    |

---

# ParsedIntent

```python
class ParsedIntent(BaseModel):
    type: IntentType
    category: IntentCategory
    status: EvidenceStatus = "RESOLVED"
    confidence: float

    entities: IntentEntities
    evidence: list[MarkerEvidence] = []

    raw_fragment: str | None = None
```

## `status`

Valori:

```text
RESOLVED
AMBIGUOUS
LOW_CONFIDENCE
```

Non usare:

```text
CONFIRMED
INVALID
```

Quei valori appartengono alla validazione dopo DB/target resolver.

---

# Entità minime per intent

## `MOVE_STOP_TO_BE`

```python
class MoveStopToBEEntities(BaseModel):
    pass
```

Significato:

```text
sposta stop a entry / breakeven
```

Non serve prezzo.

---

## `MOVE_STOP`

```python
class MoveStopEntities(BaseModel):
    new_stop_price: Price | None = None
    stop_to_tp_level: int | None = None
```

Regola:

```text
Almeno uno tra new_stop_price e stop_to_tp_level dovrebbe essere presente.
Se nessuno è presente:
  parse_status resta PARSED
  warning obbligatorio: move_stop_without_level
```

Esempi:

```text
стоп на 1 тейк -> stop_to_tp_level=1
стоп на 2140  -> new_stop_price=Price(raw="2140", value=2140.0)
```

---

## `CLOSE_FULL`

```python
class CloseFullEntities(BaseModel):
    close_price: Price | None = None
```

Non mettere target qui. Target reale dopo (in `target_hints`).

---

## `CLOSE_PARTIAL`

```python
class ClosePartialEntities(BaseModel):
    fraction: float | None = None
    close_price: Price | None = None
```

Regola:

```text
fraction opzionale.
"частично" senza percentuale -> fraction=None.
"50%"      -> fraction=0.5
"half"     -> fraction=0.5
"половину" -> fraction=0.5
```

---

## `CANCEL_PENDING`

```python
class CancelPendingEntities(BaseModel):
    cancel_scope_hint: Literal[
        "TARGETED",
        "ALL_PENDING",
        "ALL_LONG",
        "ALL_SHORT",
        "ALL_POSITIONS",
        "UNKNOWN"
    ] = "UNKNOWN"
```

È solo hint linguistico.

---

## `INVALIDATE_SETUP`

```python
class InvalidateSetupEntities(BaseModel):
    reason_text: str | None = None
```

Se non serve, può essere vuoto.

---

## `REENTER`

```python
class ReenterEntities(BaseModel):
    entries: list[Price] = []
    entry_type: EntryType | None = None
    entry_structure: EntryStructure | None = None
```

---

## `ADD_ENTRY`

```python
class AddEntryEntities(BaseModel):
    entry_price: Price | None = None
    entry_type: EntryType | None = None
```

`entry_price` può essere `None` se il messaggio dice solo "aggiungo ingresso" senza valore. In quel caso warning `add_entry_without_price`.

---

## `MODIFY_ENTRY`

```python
ModifyEntryMode = Literal[
    "MARKET_NOW",
    "UPDATE_PRICE",
    "REMOVE",
    "UNKNOWN"
]

class ModifyEntryEntities(BaseModel):
    mode: ModifyEntryMode = "UNKNOWN"
    entries: list[EntryLeg] = []
    raw_mode_marker: str | None = None
```

> Vedi [09_MODIFY_ENTRY_MODE_MARKERS.md](09_MODIFY_ENTRY_MODE_MARKERS.md) per dettagli sulla discriminazione del mode e regole di precedenza con `ADD_ENTRY` / `CANCEL_PENDING` / `REENTER`.

`REPLACE` e `ADD` **non** sono modes — il primo è ridondante con `UPDATE_PRICE`, il secondo è coperto dall'intent separato `ADD_ENTRY`.

---

## `MODIFY_TARGETS`

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

---

# Report intents

## Principio

Non serve sapere PnL, R, percentuale, guadagno o perdita se non vengono usati.

Il report serve per classificare che il messaggio è informativo sullo stato/risultato.

---

## `ENTRY_FILLED`

```python
class EntryFilledEntities(BaseModel):
    level: int | None = None
    fill_price: Price | None = None
```

---

## `TP_HIT`

```python
class TpHitEntities(BaseModel):
    level: int | None = None
    price: Price | None = None
```

Niente `result`.

---

## `SL_HIT`

```python
class SlHitEntities(BaseModel):
    price: Price | None = None
```

Niente `result`.

---

## `EXIT_BE`

```python
class ExitBeEntities(BaseModel):
    price: Price | None = None
```

---

## `REPORT_RESULT`

```python
class ReportResultEntities(BaseModel):
    raw_summary: str | None = None
```

---

## `INFO_ONLY`

```python
class InfoOnlyEntities(BaseModel):
    raw_fragment: str | None = None
```

> Niente `info_type`. Le sottocategorie ADMIN / SCHEDULE / GREETING / DISCLAIMER / MARKET_COMMENT sono **rimosse** (deciso). I marker informativi finiscono in un'unica chiave `INFO` in `semantic_markers.json` e producono un singolo `INFO_ONLY` con eventuale `raw_fragment` per debug.

---

# Cosa non estrarre

Non estrarre se non serve:

```text
- pnl
- R multiple
- percentuale profitto
- currency
- result_value
- funding
- trade performance
```

Questi sono inutili per il parser operativo se il runtime non li usa.

---

# Critica pratica

Più entità estrai, più test devi mantenere.

Se il sistema deve solo capire:

```text
REPORT
TP_HIT
SL_HIT
EXIT_BE
REPORT_RESULT
```

allora non serve inseguire ogni variante numerica di risultato.
