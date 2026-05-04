# 09 — `MODIFY_ENTRY`: mode, marker e regole di discriminazione

## Scopo

Approfondisce la gestione dell'intent `MODIFY_ENTRY` nel nuovo parser.

Obiettivo:

```text
Capire quando un messaggio sta modificando il piano di ingresso
e quale tipo di modifica richiede.
```

Il parser resta limitato a:

```text
Raw message
↓
ParsedMessage
↓
CanonicalMessage
```

La decisione operativa reale resta fuori parser:

```text
TargetResolver
ApplicabilityValidator
ExecutionPlanner
ExecutionApplier
```

---

## Decisione principale

`MODIFY_ENTRY` è l'intent.

Il `mode` è una entità dell'intent.

```text
MODIFY_ENTRY = il messaggio parla di modifica ingresso
mode         = che tipo di modifica ingresso
```

Non creare intent separati tipo:

```text
MARKET_ENTRY_NOW
ENTRY_UPDATE_PRICE
ENTRY_REPLACE
ENTRY_REMOVE
```

Meglio:

```python
ParsedIntent(
    type="MODIFY_ENTRY",
    entities=ModifyEntryEntities(
        mode="MARKET_NOW"
    )
)
```

---

## Mode canonici

```python
ModifyEntryMode = Literal[
    "MARKET_NOW",
    "UPDATE_PRICE",
    "REMOVE",
    "UNKNOWN"
]
```

`REPLACE` non è incluso: spesso è difficile distinguerlo affidabilmente da `UPDATE_PRICE`, e non cambia il comportamento operativo reale.

`ADD` non è incluso: è coperto dall'intent separato `ADD_ENTRY`.

> 📖 Riferimento autoritativo enum: [12_ENUMS_E_CONSTANTI.md](12_ENUMS_E_CONSTANTI.md).

---

## Contratto `ModifyEntryEntities`

```python
class ModifyEntryEntities(BaseModel):
    mode: ModifyEntryMode = "UNKNOWN"
    entries: list[EntryLeg] = []
    raw_mode_marker: str | None = None
```

### `mode`

Indica il tipo di modifica.

### `entries`

Contiene eventuali nuovi valori di ingresso.

Per `MARKET_NOW`:

```python
entries = [
    EntryLeg(
        sequence=1,
        entry_type="MARKET",
        price=None,
        role="PRIMARY"
    )
]
```

Per `UPDATE_PRICE`:

```python
entries = [
    EntryLeg(
        sequence=1,
        entry_type="LIMIT",
        price=Price(raw="2114", value=2114.0)
    )
]
```

Per `REMOVE`:

```python
entries = []
```

### `raw_mode_marker`

Marker testuale che ha determinato il mode.

Esempio:

```text
"входим по рынку"
```

---

## Mode 1 — `MARKET_NOW`

### Significato

Il messaggio dice di entrare subito a mercato / prezzo corrente.

Caso tipico:

```text
setup attivo con uno/due limit
↓
messaggio successivo: "входим по рынку"
```

Il parser non deve sapere se il setup è davvero attivo. Deve solo produrre:

```text
MODIFY_ENTRY / MARKET_NOW
```

> ⚠️ **Disambiguazione contestuale**: gli stessi marker `"входим по рынку"`, `"с текущих"` compaiono in `entry_type_markers.MARKET` (per signal nuovi) e qui (per update). La regola è in [06_MARKERS_RULES.md](06_MARKERS_RULES.md#regola-contestuale-market-signal-vs-modify_entrymarket_now-update). In sintesi: se nello stesso messaggio è stata estratta una struttura signal, il marker è `entry_type=MARKET`; altrimenti è `MODIFY_ENTRY/MARKET_NOW`.

### Marker forti

Vedi [06_1_SEMANTIC_MARKERS_COMPLETO.md](06_1_SEMANTIC_MARKERS_COMPLETO.md) — sezione `modify_entry_mode_markers.MARKET_NOW`.

### Output ParsedMessage

```json
{
  "primary_class": "UPDATE",
  "primary_intent": "MODIFY_ENTRY",
  "intents": [
    {
      "type": "MODIFY_ENTRY",
      "category": "UPDATE",
      "entities": {
        "mode": "MARKET_NOW",
        "entries": [
          {
            "sequence": 1,
            "entry_type": "MARKET",
            "role": "PRIMARY"
          }
        ],
        "raw_mode_marker": "входим по рынку"
      }
    }
  ]
}
```

### Output CanonicalMessage

```json
{
  "primary_class": "UPDATE",
  "primary_intent": "MODIFY_ENTRY",
  "update": {
    "operations": [
      {
        "op_type": "MODIFY_ENTRIES",
        "source_intent": "MODIFY_ENTRY",
        "modify_entries": {
          "mode": "MARKET_NOW",
          "entries": [
            {
              "sequence": 1,
              "entry_type": "MARKET",
              "role": "PRIMARY"
            }
          ]
        }
      }
    ]
  }
}
```

---

## Mode 2 — `UPDATE_PRICE`

### Significato

Il messaggio aggiorna il prezzo di ingresso. Non implica necessariamente cancellare tutto il piano precedente.

### Marker forti

Vedi [06_1_SEMANTIC_MARKERS_COMPLETO.md](06_1_SEMANTIC_MARKERS_COMPLETO.md) — sezione `modify_entry_mode_markers.UPDATE_PRICE`.

### Esempi

```text
новый вход 2114
вход теперь 2114
обновляем вход на 2114
актуальный вход 2114
```

### Output ParsedMessage

```json
{
  "type": "MODIFY_ENTRY",
  "entities": {
    "mode": "UPDATE_PRICE",
    "entries": [
      {
        "sequence": 1,
        "entry_type": "LIMIT",
        "price": {"raw": "2114", "value": 2114.0}
      }
    ],
    "raw_mode_marker": "новый вход"
  }
}
```

---

## Mode 3 — `REMOVE`

### Significato

Il messaggio rimuove un ingresso dal piano.

⚠️ Va usato solo se il testo parla chiaramente di `entry / вход`.

Se il testo parla di:

```text
лимитка
pending order
ордер
```

allora è più corretto `CANCEL_PENDING`, non `MODIFY_ENTRY / REMOVE`.

### Marker forti

Vedi [06_1_SEMANTIC_MARKERS_COMPLETO.md](06_1_SEMANTIC_MARKERS_COMPLETO.md) — sezione `modify_entry_mode_markers.REMOVE`.

### Output

```json
{
  "type": "MODIFY_ENTRY",
  "entities": {
    "mode": "REMOVE",
    "entries": [],
    "raw_mode_marker": "убираем вход"
  }
}
```

---

## Mode 4 — `UNKNOWN`

### Significato

Il messaggio sembra parlare di modifica ingresso, ma il tipo non è chiaro.

Esempio:

```text
что-то меняем по входу
```

Output:

```json
{
  "type": "MODIFY_ENTRY",
  "entities": {
    "mode": "UNKNOWN"
  },
  "warnings": [
    "modify_entry_mode_unknown"
  ]
}
```

---

## Differenza tra `MODIFY_ENTRY`, `ADD_ENTRY`, `CANCEL_PENDING`, `REENTER`

### `MODIFY_ENTRY`

Modifica il piano di ingresso esistente.

Esempi:

```text
входим по рынку
новый вход 2114
вход теперь 2114
убираем вход
```

### `ADD_ENTRY`

Aggiunge un nuovo ingresso senza sostituire quello esistente.

Esempi:

```text
добавляю вход
добавляем лимитку
добавляю усреднение
add entry
add limit
```

Output:

```json
{
  "type": "ADD_ENTRY",
  "entities": {
    "entry_type": "LIMIT",
    "entry_price": {"raw": "2114", "value": 2114.0}
  }
}
```

Non trasformarlo in `MODIFY_ENTRY / ADD`.

### `CANCEL_PENDING`

Cancella ordini pendenti / limit.

Esempi:

```text
убираем лимитки
снять лимитки
отменяем лимитку
cancel pending
cancel limit
```

Regola:

```text
лимитка / ордер / pending  → CANCEL_PENDING
вход / entry               → MODIFY_ENTRY / REMOVE
```

### `REENTER`

Rientro dopo uscita/stop/chiusura.

Esempi:

```text
перезаходим
заходим заново
входим заново
reenter
enter again
```

Non usarlo per `входим по рынку` se il setup è ancora pendente — quello è `MODIFY_ENTRY / MARKET_NOW`.

---

## Marker JSON

I marker per `modify_entry_mode_markers` sono definiti in [06_1_SEMANTIC_MARKERS_COMPLETO.md](06_1_SEMANTIC_MARKERS_COMPLETO.md). Non duplicarli qui — si rischia divergenza.

---

## Precedenza dei mode

Se più mode vengono trovati nello stesso frammento, applicare nell'ordine:

```text
MARKET_NOW > REMOVE > UPDATE_PRICE > UNKNOWN
```

Motivo:

```text
MARKET_NOW   = comando più specifico e operativo
REMOVE       = cambia il piano eliminando entry
UPDATE_PRICE = modifica valore
UNKNOWN      = fallback
```

---

## Regole di conflitto

### Regola 1 — `CANCEL_PENDING` domina `MODIFY_ENTRY / REMOVE` se ci sono marker limit/order

Se il testo contiene:

```text
лимитка | лимитки | лимитный ордер | ордер | pending | limit order
```

e contiene marker di rimozione:

```text
убираем | снимаем | отменяем | cancel | remove
```

allora:

```text
intent = CANCEL_PENDING
```

non `MODIFY_ENTRY / REMOVE`.

### Regola 2 — `ADD_ENTRY` domina `MODIFY_ENTRY / UPDATE_PRICE` se c'è marker add

Se il testo contiene `добавляю вход | добавляем вход | add entry`:

```text
intent = ADD_ENTRY
```

non `MODIFY_ENTRY`.

### Regola 3 — `REENTER` domina `MODIFY_ENTRY / MARKET_NOW` se c'è marker di rientro

Se il testo contiene `перезаходим | заходим заново | reenter | enter again`:

```text
intent = REENTER
```

non `MODIFY_ENTRY / MARKET_NOW`.

### Regola 4 — Contesto signal domina mode

Vedi [06_MARKERS_RULES.md](06_MARKERS_RULES.md). Se il messaggio ha già una struttura signal estratta, i marker MARKET sono interpretati come `entry_type` del leg, non come `MODIFY_ENTRY`.

---

## Algoritmo di estrazione

```text
1. Cerca marker principali degli intenti.
2. Se signal_payload presente nello stesso messaggio:
   - i marker MARKET diventano entry_type del leg, non intent.
   - skip MODIFY_ENTRY.
3. Se ADD_ENTRY forte:
   - intent = ADD_ENTRY
   - non emettere MODIFY_ENTRY.
4. Se CANCEL_PENDING forte con marker limit/order:
   - intent = CANCEL_PENDING
   - non emettere MODIFY_ENTRY/REMOVE.
5. Se REENTER forte:
   - intent = REENTER.
6. Altrimenti, se marker MODIFY_ENTRY o mode marker presente:
   - intent = MODIFY_ENTRY.
7. Cerca modify_entry_mode_markers.
8. Applica precedence mode:
   MARKET_NOW > REMOVE > UPDATE_PRICE > UNKNOWN.
9. Estrai eventuali prezzi.
10. Costruisci ModifyEntryEntities.
11. Se mode UNKNOWN:
   - aggiungi warning modify_entry_mode_unknown.
```

---

## Mapping verso CanonicalMessage

### `MARKET_NOW`

```json
{
  "op_type": "MODIFY_ENTRIES",
  "modify_entries": {
    "mode": "MARKET_NOW",
    "entries": [
      {"sequence": 1, "entry_type": "MARKET", "role": "PRIMARY"}
    ]
  }
}
```

### `UPDATE_PRICE`

```json
{
  "op_type": "MODIFY_ENTRIES",
  "modify_entries": {
    "mode": "UPDATE_PRICE",
    "entries": [
      {
        "sequence": 1,
        "entry_type": "LIMIT",
        "price": {"raw": "2114", "value": 2114.0}
      }
    ]
  }
}
```

### `REMOVE`

```json
{
  "op_type": "MODIFY_ENTRIES",
  "modify_entries": {
    "mode": "REMOVE",
    "entries": []
  }
}
```

> Nota: lo schema `MODIFY_ENTRIES` deve permettere `entries=[]` quando `mode=REMOVE`. È semanticamente valido.

---

## Warning consigliati

```text
modify_entry_mode_unknown
modify_entry_price_missing
modify_entry_remove_without_specific_entry
modify_entry_replace_semantics_possible
modify_entry_conflicts_with_cancel_pending
modify_entry_conflicts_with_add_entry
modify_entry_conflicts_with_reenter
```

---

## Test minimi

| Input                        | Atteso                                |
|------------------------------|---------------------------------------|
| `входим по рынку`            | `MODIFY_ENTRY / MARKET_NOW`           |
| `заходим с текущих`          | `MODIFY_ENTRY / MARKET_NOW`           |
| `новый вход 2114`            | `MODIFY_ENTRY / UPDATE_PRICE`, price=2114 |
| `вход теперь 2114`           | `MODIFY_ENTRY / UPDATE_PRICE`, price=2114 |
| `убираем вход`               | `MODIFY_ENTRY / REMOVE`               |
| `убираем лимитки`            | `CANCEL_PENDING` (non `MODIFY_ENTRY/REMOVE`) |
| `добавляю вход 2114`         | `ADD_ENTRY` (non `MODIFY_ENTRY/UPDATE_PRICE`) |
| `перезаходим по рынку`       | `REENTER` (non `MODIFY_ENTRY/MARKET_NOW`) |
| `ETHUSDT LONG, по рынку...` (signal completo) | `SIGNAL` con `entry_type=MARKET` (non `MODIFY_ENTRY`) |

---

## Decisione finale

Per la versione iniziale:

```text
MODIFY_ENTRY modes:
- MARKET_NOW
- UPDATE_PRICE
- REMOVE
- UNKNOWN
```

Non usare per ora:

```text
REPLACE
ADD
```

`ADD` resta intent separato `ADD_ENTRY`.

`REPLACE` si introdurrà solo se il runtime operativo distinguerà davvero "aggiorna prezzo entry" da "sostituisci completamente piano entry".
