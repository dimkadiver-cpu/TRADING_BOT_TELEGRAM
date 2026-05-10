# PRD Finale Corretto — Parser V2: gestione robusta di `MODIFY\_ENTRY`

## 1\. Scopo

Questo PRD definisce l’intervento finale su `parser\_v2` per migliorare la gestione degli update che modificano una entry esistente.

L’intervento riguarda solo:

```text
MODIFY\_ENTRY
```

e non deve fondere, riscrivere o alterare gli intent già separati:

```text
ADD\_ENTRY
REENTER
```

Il parser deve produrre un output canonico più preciso, ma non deve applicare direttamente modifiche allo stato operativo del segnale, della posizione o degli ordini.

\---

## 2\. Decisione finale

### 2.1 Intent separati

La separazione semantica corretta è:

```text
MODIFY\_ENTRY = modifica una entry già prevista
ADD\_ENTRY    = aggiunge una nuova entry al setup/signal esistente
REENTER      = rientro operativo dopo precedente uscita/chiusura/consumo del setup
```

Questi intent devono restare separati sia nei marker sia nella traduzione.

Non bisogna trasformare `ADD\_ENTRY` o `REENTER` in sottocasi di `MODIFY\_ENTRY`.

\---

## 3\. Stato attuale

Nel profilo `trader\_a`, i marker sono attualmente separati.

### 3.1 `MODIFY\_ENTRY`

```json
"MODIFY\_ENTRY": {
  "strong": \[
    "входим по рынку",
    "убираем вход",
    "новый вход"
  ],
  "weak": \[]
}
```

### 3.2 `ADD\_ENTRY`

```json
"ADD\_ENTRY": {
  "strong": \[
    "добавляю вход"
  ],
  "weak": \[]
}
```

### 3.3 `REENTER`

```json
"REENTER": {
  "strong": \[
    "перезайдем",
    "перезаходим",
    "заходим по текущим заново"
  ],
  "weak": \[]
}
```

La separazione è corretta e va mantenuta.

\---

## 4\. Come vengono trovati oggi nel testo

Il parser V2 usa un flusso di questo tipo:

```text
raw text
→ TextNormalizer
→ MarkerMatcher
→ MarkerEvidenceResolver
→ IntentEntityExtractor
→ LocalDisambiguator
→ Target binding
→ ParsedMessage
→ CanonicalTranslator
→ CanonicalMessage
```

La ricerca dei marker è letterale.

Il testo viene normalizzato:

```text
lowercase
ё → е
trattini lunghi normalizzati
spazi multipli compressi
righe vuote rimosse
```

Poi il matcher cerca ogni marker nel testo normalizzato con una logica equivalente a:

```python
text.find(marker)
```

Quindi oggi non c’è una classificazione linguistica avanzata per `MODIFY\_ENTRY`: il sistema dipende molto dai marker espliciti.

\---

## 5\. Problema

La gestione attuale di `MODIFY\_ENTRY` è troppo fragile per casi reali.

### 5.1 Pochi marker

Oggi riconosce bene solo casi come:

```text
новый вход 2114
входим по рынку
убираем вход
```

ma può perdere frasi operative realistiche:

```text
вход теперь 2114
переносим вход на 2114
вход меняем на 2114
новая точка входа 2114
лимитку входа переносим на 2114
основной вход переносим на 2114
усреднение переносим на 2114
```

### 5.2 Prezzi estratti in modo troppo semplice

Attualmente la logica prende il primo prezzo dopo il marker.

Questo non copre bene:

```text
вход теперь 2114-2120
вход теперь 2114 2100 2080
```

### 5.3 Mancanza di selector dell’entry

Il parser oggi non distingue in modo robusto:

```text
основной вход
первый вход
усреднение
вход A
вход B
entry A
entry B
```

Quindi non è chiaro quale entry debba essere modificata.

\---

## 6\. Obiettivi

L’intervento deve permettere a `MODIFY\_ENTRY` di produrre entità più informative:

```text
mode
entry\_selector
entries
entry\_structure
raw\_mode\_marker
raw\_selector\_marker
```

Il risultato canonico deve restare nella famiglia operativa già esistente:

```text
MODIFY\_ENTRY → MODIFY\_ENTRIES
```

ma con `kind`, `entry\_selector` ed `entry\_structure` più precisi.

\---

## 7\. Non obiettivi

Questo PRD non deve:

```text
- fondere ADD\_ENTRY dentro MODIFY\_ENTRY
- fondere REENTER dentro MODIFY\_ENTRY
- cambiare la semantica di ADD\_ENTRY
- cambiare la semantica di REENTER
- cambiare il database
- cambiare il parser legacy
- applicare direttamente modifiche allo stato operativo
- riscrivere l’engine downstream
- introdurre nuovi mode di rimozione
- rimuovere il comportamento legacy esistente
```

\---

## 8\. Regola importante sul legacy `REMOVE`

Nel codice attuale esiste già il mode:

```text
REMOVE
```

e può essere prodotto da `MODIFY\_ENTRY` per marker già presenti, per esempio:

```text
убираем вход
```

Questo PRD non deve eliminare `REMOVE`, perché rimuoverlo potrebbe rompere path esistenti e validazioni Pydantic.

Decisione finale:

```text
- mantenere REMOVE come legacy
- non introdurre REMOVE\_ONE
- non introdurre REMOVE\_ALL
- non introdurre REMOVE\_PENDING
- non ampliare la logica REMOVE in questo intervento
- non usare REMOVE come focus del redesign
```

Il redesign riguarda:

```text
UPDATE\_PRICE
UPDATE\_RANGE
MARKET\_NOW
REPLACE\_ENTRY
entry\_selector
entry\_structure
```

`REMOVE` resta compatibile, ma fuori dal perimetro di miglioramento.

\---

## 9\. Modello concettuale finale

### 9.1 Livello semantico

```text
MODIFY\_ENTRY
```

significa:

```text
il messaggio modifica una entry già prevista
```

### 9.2 Livello entità

L’intent deve descrivere:

```text
come viene modificata l’entry
quale entry viene modificata
quali nuovi prezzi/strutture sono indicati
```

### 9.3 Livello canonico

La traduzione resta:

```text
MODIFY\_ENTRY → UpdateOperation(op\_type="MODIFY\_ENTRIES")
```

ma il payload deve essere più informativo.

\---

## 10\. Schema proposto

### 10.1 `EntrySelector`

Aggiungere un nuovo modello:

```python
class EntrySelector(ContractModel):
    role: EntryRole | None = None
    sequence: int | None = Field(default=None, ge=1)
    label: str | None = None
    raw: str | None = None
```

Esempi:

```text
основной вход → role=PRIMARY
первый вход   → sequence=1
второй вход   → sequence=2
усреднение    → role=AVERAGING
вход A        → label="A"
вход B        → label="B"
entry A       → label="A"
entry B       → label="B"
```

\---

### 10.2 `ModifyEntryEntities`

Estendere il modello attuale:

```python
class ModifyEntryEntities(IntentEntities):
    mode: ModifyEntryMode = "UNKNOWN"
    entry\_selector: EntrySelector | None = None
    entries: list\[EntryLeg] = Field(default\_factory=list)
    entry\_structure: EntryStructure | None = None
    raw\_mode\_marker: str | None = None
    raw\_selector\_marker: str | None = None
```

\---

### 10.3 `ModifyEntryMode`

Versione finale corretta:

```python
ModifyEntryMode = Literal\[
    "MARKET\_NOW",
    "UPDATE\_PRICE",
    "UPDATE\_RANGE",
    "REPLACE\_ENTRY",
    "REMOVE",      # legacy: mantenere, non espandere
    "UNKNOWN",
]
```

Motivo:

```text
REMOVE esiste già nel codice attuale.
Va mantenuto per compatibilità.
Non va esteso in questo PRD.
```

\---

### 10.4 `ModifyEntriesOperationKind`

Versione finale corretta:

```python
ModifyEntriesOperationKind = Literal\[
    "ADD",
    "REENTER",
    "MARKET\_NOW",
    "UPDATE\_PRICE",
    "UPDATE\_RANGE",
    "REPLACE\_ENTRY",
    "REMOVE",      # legacy: mantenere, non espandere
    "UNKNOWN",
]
```

\---

### 10.5 `ModifyEntriesOperation`

Estendere il payload canonico:

```python
class ModifyEntriesOperation(CanonicalModel):
    kind: ModifyEntriesOperationKind
    entries: list\[EntryLeg] = Field(default\_factory=list)
    entry\_structure: EntryStructure | None = None
    entry\_selector: EntrySelector | None = None
```

\---

## 11\. Traduzione intent → azione canonica

### 11.1 Mapping finale

|Intent|Operation|Kind|
|-|-|-|
|`MODIFY\_ENTRY`|`MODIFY\_ENTRIES`|`UPDATE\_PRICE`|
|`MODIFY\_ENTRY`|`MODIFY\_ENTRIES`|`UPDATE\_RANGE`|
|`MODIFY\_ENTRY`|`MODIFY\_ENTRIES`|`MARKET\_NOW`|
|`MODIFY\_ENTRY`|`MODIFY\_ENTRIES`|`REPLACE\_ENTRY`|
|`MODIFY\_ENTRY`|`MODIFY\_ENTRIES`|`REMOVE` legacy|
|`ADD\_ENTRY`|`MODIFY\_ENTRIES`|`ADD`|
|`REENTER`|`MODIFY\_ENTRIES`|`REENTER`|

Nota: usare la stessa operation family `MODIFY\_ENTRIES` è accettabile perché tutte queste azioni lavorano sulle entries. La distinzione semantica resta nell’intent sorgente e nel `kind`.

\---

## 12\. Detection proposta

### 12.1 Marker `MODIFY\_ENTRY`

Estendere i marker forti:

```json
"MODIFY\_ENTRY": {
  "strong": \[
    "входим по рынку",
    "вход по рынку",
    "новый вход",
    "новая точка входа",
    "вход теперь",
    "вход меняем",
    "меняем вход",
    "переносим вход",
    "вход переносим",
    "лимитку входа переносим",
    "основной вход переносим",
    "усреднение переносим"
  ],
  "weak": \[
    "точка входа"
  ]
}
```

Cautela:

```text
Non aggiungere marker troppo generici come "вход" da solo.
```

\---

### 12.2 Marker `ADD\_ENTRY`

Lasciare separati.

Esempio attuale:

```json
"ADD\_ENTRY": {
  "strong": \[
    "добавляю вход"
  ],
  "weak": \[]
}
```

Eventuali espansioni future devono restare in `ADD\_ENTRY`, non in `MODIFY\_ENTRY`.

Esempi da NON mettere in `MODIFY\_ENTRY`:

```text
добавляю вход
добавим вход
добавляю лимитку
```

\---

### 12.3 Marker `REENTER`

Lasciare separati.

Esempio attuale:

```json
"REENTER": {
  "strong": \[
    "перезайдем",
    "перезаходим",
    "заходим по текущим заново"
  ],
  "weak": \[]
}
```

Eventuali espansioni future devono restare in `REENTER`, non in `MODIFY\_ENTRY`.

Esempi da NON mettere in `MODIFY\_ENTRY`:

```text
перезаходим
перезайдем
заходим заново
повторный вход
```

\---

### 12.4 Marker di mode

Usare i marker di mode come supporto reale all’estrazione:

```json
"modify\_entry\_mode\_markers": {
  "MARKET\_NOW": {
    "strong": \[
      "входим по рынку",
      "вход по рынку",
      "по текущим",
      "с текущих"
    ],
    "weak": \[]
  },
  "UPDATE\_PRICE": {
    "strong": \[
      "новый вход",
      "новая точка входа",
      "вход теперь",
      "вход меняем",
      "меняем вход",
      "переносим вход",
      "вход переносим"
    ],
    "weak": \[]
  },
  "UPDATE\_RANGE": {
    "strong": \[
      "диапазон входа",
      "вход в диапазон"
    ],
    "weak": \[]
  },
  "REPLACE\_ENTRY": {
    "strong": \[
      "заменяем вход",
      "полностью меняем вход"
    ],
    "weak": \[]
  }
}
```

Nota tecnica: se il codice attuale non usa davvero `modify\_entry\_mode\_markers` per costruire l’entità, l’estrattore deve essere aggiornato.

\---

## 13\. Entry selector



Aggiungere una sezione dedicata o un extractor dedicato per selector.

Proposta concettuale:

```json
"entry\\\_selector\\\_markers": {
  "PRIMARY": {
    "strong": \\\[
      "основной вход",
      "первый вход",
      "entry a",
      "вход a"
    ],
    "weak": \\\[]
  },
  "AVERAGING": {
    "strong": \\\[
      "усреднение",
      "лимитка на усреднение",
      "вход b",
      "entry b"
    ],
    "weak": \\\[]
  }
}
```



### 13.1 Selector `PRIMARY`

Riconoscere:

```text
основной вход
первый вход
entry a
вход a
```

Output:

```python
EntrySelector(role="PRIMARY", sequence=1, raw=...)
```

\---

### 13.2 Selector `AVERAGING`

Riconoscere:

```text
усреднение
лимитка на усреднение
вход b
entry b
```

Output:

```python
EntrySelector(role="AVERAGING", raw=...)
```

Se il testo indica chiaramente `B`, si può valorizzare anche:

```python
label="B"
```

\---

### 13.3 Selector sconosciuto

Se non c’è selector chiaro:

```python
entry\_selector = None
```

oppure:

```python
EntrySelector(raw=None)
```

Preferenza: usare `None` se non c’è evidenza reale.

\---

## 14\. Estrazione prezzi

### 14.1 Prezzo singolo

Input:

```text
новый вход 2114
```

Output:

```text
mode = UPDATE\_PRICE
entry\_structure = ONE\_SHOT
entries = \[LIMIT 2114]
```

\---

### 14.2 Range

Input:

```text
вход теперь 2114-2120
```

Output:

```text
mode = UPDATE\_RANGE
entry\_structure = RANGE
entries = \[LIMIT 2114, LIMIT 2120]
```

Regola:

```text
due prezzi separati da "-" / "–" / "—" indicano RANGE
```

\---

### 14.3 Ladder

Input:

```text
вход теперь 2114 2100 2080
```

Output:

```text
mode = UPDATE\_PRICE
entry\_structure = LADDER
entries = \[LIMIT 2114, LIMIT 2100, LIMIT 2080]
```

Nota: se non c’è marker esplicito di sostituzione totale, non usare `REPLACE\_ENTRY`.

\---

### 14.4 Replace entry

Input:

```text
полностью меняем вход на 2114 2100
```

Output:

```text
mode = REPLACE\_ENTRY
entry\_structure = LADDER
entries = \[LIMIT 2114, LIMIT 2100]
```

`REPLACE\_ENTRY` deve essere usato solo con marker espliciti di sostituzione completa.

\---

### 14.5 Market now

Input:

```text
входим по рынку
```

Output:

```text
mode = MARKET\_NOW
entries = \[MARKET]
entry\_structure = ONE\_SHOT
```

\---

## 15\. Regole anti-collisione

### 15.1 `ADD\_ENTRY` prevale sui marker simili

Se il testo contiene marker chiaro di `ADD\_ENTRY`, non deve essere interpretato come `MODIFY\_ENTRY`.

Esempio:

```text
добавляю вход 2114
```

Atteso:

```text
intent = ADD\_ENTRY
not MODIFY\_ENTRY
```

\---

### 15.2 `REENTER` prevale sui marker simili

Se il testo contiene marker chiaro di `REENTER`, non deve essere interpretato come `MODIFY\_ENTRY`.

Esempio:

```text
перезаходим 2114
```

Atteso:

```text
intent = REENTER
not MODIFY\_ENTRY
```

\---

### 15.3 Signal payload prevale sul contesto di setup

Se il messaggio è un nuovo signal completo o parziale, frasi come:

```text
вход с текущих
```

non devono diventare automaticamente `MODIFY\_ENTRY`.

Devono restare parte del payload del segnale.

\---

## 16\. Modifiche file-by-file

### 16.1 `src/parser\_v2/contracts/enums.py`

Aggiornare:

```python
ModifyEntryMode = Literal\[
    "MARKET\_NOW",
    "UPDATE\_PRICE",
    "UPDATE\_RANGE",
    "REPLACE\_ENTRY",
    "REMOVE",
    "UNKNOWN",
]
```

Aggiornare:

```python
ModifyEntriesOperationKind = Literal\[
    "ADD",
    "REENTER",
    "MARKET\_NOW",
    "UPDATE\_PRICE",
    "UPDATE\_RANGE",
    "REPLACE\_ENTRY",
    "REMOVE",
    "UNKNOWN",
]
```

\---

### 16.2 `src/parser\_v2/contracts/entities.py`

Aggiungere:

```python
class EntrySelector(ContractModel):
    role: EntryRole | None = None
    sequence: int | None = Field(default=None, ge=1)
    label: str | None = None
    raw: str | None = None
```

Aggiornare:

```python
class ModifyEntryEntities(IntentEntities):
    mode: ModifyEntryMode = "UNKNOWN"
    entry\_selector: EntrySelector | None = None
    entries: list\[EntryLeg] = Field(default\_factory=list)
    entry\_structure: EntryStructure | None = None
    raw\_mode\_marker: str | None = None
    raw\_selector\_marker: str | None = None
```

\---

### 16.3 `src/parser\_v2/contracts/canonical\_message.py`

Aggiornare:

```python
class ModifyEntriesOperation(CanonicalModel):
    kind: ModifyEntriesOperationKind
    entries: list\[EntryLeg] = Field(default\_factory=list)
    entry\_structure: EntryStructure | None = None
    entry\_selector: EntrySelector | None = None
```

Ricordarsi di importare `EntrySelector`.

\---

### 16.4 `src/parser\_v2/profiles/trader\_a/semantic\_markers.json`

Aggiornare solo:

```text
intent\_markers.MODIFY\_ENTRY
modify\_entry\_mode\_markers
```

Non fondere i marker di:

```text
ADD\_ENTRY
REENTER
```

dentro `MODIFY\_ENTRY`.

\---

### 16.5 `src/parser\_v2/profiles/trader\_a/intent\_entity\_extractor.py`

Rifattorizzare:

```python
\_modify\_entry\_entities()
```

Nuova struttura consigliata:

```python
def \_modify\_entry\_entities(ev: MarkerEvidence, normalized: NormalizedText) -> ModifyEntryEntities:
    text = normalized.normalized\_text
    window = \_modify\_entry\_context\_window(text, ev.start, ev.end)

    mode = \_detect\_modify\_entry\_mode(ev.marker, window)
    selector = \_detect\_entry\_selector(window)
    entries, entry\_structure = \_extract\_modify\_entry\_prices(text, ev.end, mode)

    if mode == "MARKET\_NOW":
        entries = \[EntryLeg(sequence=1, entry\_type="MARKET", role=selector.role if selector else "PRIMARY")]
        entry\_structure = "ONE\_SHOT"

    return ModifyEntryEntities(
        mode=mode,
        entry\_selector=selector,
        entries=entries,
        entry\_structure=entry\_structure,
        raw\_mode\_marker=ev.marker,
        raw\_selector\_marker=selector.raw if selector else None,
    )
```

Funzioni da aggiungere:

```python
\_detect\_modify\_entry\_mode()
\_detect\_entry\_selector()
\_extract\_modify\_entry\_prices()
\_detect\_entry\_structure()
\_modify\_entry\_context\_window()
```

\---

### 16.6 `src/parser\_v2/translation/canonical\_translator.py`

Aggiornare solo il ramo:

```python
if intent.type == "MODIFY\_ENTRY"
```

Da:

```python
ModifyEntriesOperation(
    kind=entities.mode,
    entries=entities.entries,
)
```

A:

```python
ModifyEntriesOperation(
    kind=entities.mode,
    entries=entities.entries,
    entry\_structure=entities.entry\_structure,
    entry\_selector=entities.entry\_selector,
)
```

Non cambiare la traduzione di:

```text
ADD\_ENTRY
REENTER
```

salvo adeguamenti tecnici necessari per i nuovi campi opzionali.

\---

## 17\. Output canonico atteso

### 17.1 UPDATE\_PRICE

Input:

```text
основной вход переносим на 2114
```

Output concettuale:

```json
{
  "primary\_class": "UPDATE",
  "intents": \["MODIFY\_ENTRY"],
  "update": {
    "operations": \[
      {
        "op\_type": "MODIFY\_ENTRIES",
        "source\_intent": "MODIFY\_ENTRY",
        "modify\_entries": {
          "kind": "UPDATE\_PRICE",
          "entry\_selector": {
            "role": "PRIMARY",
            "sequence": 1,
            "raw": "основной вход"
          },
          "entry\_structure": "ONE\_SHOT",
          "entries": \[
            {
              "sequence": 1,
              "entry\_type": "LIMIT",
              "price": {
                "raw": "2114",
                "value": 2114.0
              },
              "role": "PRIMARY"
            }
          ]
        }
      }
    ]
  }
}
```

\---

### 17.2 UPDATE\_RANGE

Input:

```text
вход теперь 2114-2120
```

Output concettuale:

```json
{
  "primary\_class": "UPDATE",
  "intents": \["MODIFY\_ENTRY"],
  "update": {
    "operations": \[
      {
        "op\_type": "MODIFY\_ENTRIES",
        "source\_intent": "MODIFY\_ENTRY",
        "modify\_entries": {
          "kind": "UPDATE\_RANGE",
          "entry\_structure": "RANGE",
          "entries": \[
            {
              "sequence": 1,
              "entry\_type": "LIMIT",
              "price": {
                "raw": "2114",
                "value": 2114.0
              }
            },
            {
              "sequence": 2,
              "entry\_type": "LIMIT",
              "price": {
                "raw": "2120",
                "value": 2120.0
              }
            }
          ]
        }
      }
    ]
  }
}
```

\---

### 17.3 MARKET\_NOW

Input:

```text
входим по рынку
```

Output concettuale:

```json
{
  "primary\_class": "UPDATE",
  "intents": \["MODIFY\_ENTRY"],
  "update": {
    "operations": \[
      {
        "op\_type": "MODIFY\_ENTRIES",
        "source\_intent": "MODIFY\_ENTRY",
        "modify\_entries": {
          "kind": "MARKET\_NOW",
          "entry\_structure": "ONE\_SHOT",
          "entries": \[
            {
              "sequence": 1,
              "entry\_type": "MARKET",
              "role": "PRIMARY"
            }
          ]
        }
      }
    ]
  }
}
```

\---

## 18\. Test di accettazione

### 18.1 `MODIFY\_ENTRY / UPDATE\_PRICE`

Input:

```text
новый вход 2114
```

Atteso:

```text
intent = MODIFY\_ENTRY
mode = UPDATE\_PRICE
entry\_structure = ONE\_SHOT
entries\[0].price = 2114
```

\---

### 18.2 Variante naturale

Input:

```text
вход теперь 2114
```

Atteso:

```text
intent = MODIFY\_ENTRY
mode = UPDATE\_PRICE
entries\[0].price = 2114
```

\---

### 18.3 Range

Input:

```text
вход теперь 2114-2120
```

Atteso:

```text
intent = MODIFY\_ENTRY
mode = UPDATE\_RANGE
entry\_structure = RANGE
entries = \[2114, 2120]
```

\---

### 18.4 Ladder

Input:

```text
вход теперь 2114 2100 2080
```

Atteso:

```text
intent = MODIFY\_ENTRY
mode = UPDATE\_PRICE
entry\_structure = LADDER
entries = \[2114, 2100, 2080]
```

\---

### 18.5 Selector primary

Input:

```text
основной вход переносим на 2114
```

Atteso:

```text
intent = MODIFY\_ENTRY
entry\_selector.role = PRIMARY
entry\_selector.sequence = 1
mode = UPDATE\_PRICE
entries\[0].price = 2114
```

\---

### 18.6 Selector averaging

Input:

```text
усреднение переносим на 2114
```

Atteso:

```text
intent = MODIFY\_ENTRY
entry\_selector.role = AVERAGING
mode = UPDATE\_PRICE
entries\[0].price = 2114
```

\---

### 18.7 Market now

Input:

```text
входим по рынку
```

Atteso:

```text
intent = MODIFY\_ENTRY
mode = MARKET\_NOW
entries\[0].entry\_type = MARKET
entries\[0].price = None
```

\---

### 18.8 Legacy remove non rotto

Input:

```text
убираем вход
```

Atteso:

```text
intent = MODIFY\_ENTRY
mode = REMOVE
nessun errore Pydantic
comportamento legacy mantenuto
```

Non aggiungere nuove aspettative operative su questo caso.

\---

### 18.9 `ADD\_ENTRY` resta separato

Input:

```text
добавляю вход 2114
```

Atteso:

```text
intent = ADD\_ENTRY
not MODIFY\_ENTRY
```

\---

### 18.10 `REENTER` resta separato

Input:

```text
перезаходим 2114
```

Atteso:

```text
intent = REENTER
not MODIFY\_ENTRY
```

\---

### 18.11 Signal non diventa update

Input:

```text
ETHUSDT LONG
вход с текущих
SL 2100
TP 2200
```

Atteso:

```text
primary\_class = SIGNAL
not UPDATE
```

\---

## 19\. Checklist implementativa

### Contratti

* \[ ] `ModifyEntryMode` aggiornato mantenendo `REMOVE`
* \[ ] `ModifyEntriesOperationKind` aggiornato mantenendo `REMOVE`
* \[ ] `EntrySelector` aggiunto
* \[ ] `ModifyEntryEntities` esteso
* \[ ] `ModifyEntriesOperation` esteso

### Marker

* \[ ] Marker `MODIFY\_ENTRY` ampliati
* \[ ] Marker `ADD\_ENTRY` lasciati separati
* \[ ] Marker `REENTER` lasciati separati
* \[ ] Marker troppo generici evitati
* \[ ] `modify\_entry\_mode\_markers` resi utili all’estrazione

### Extractor

* \[ ] `\_modify\_entry\_entities()` rifattorizzata
* \[ ] Supporto prezzo singolo
* \[ ] Supporto range
* \[ ] Supporto ladder
* \[ ] Supporto selector `PRIMARY`
* \[ ] Supporto selector `AVERAGING`
* \[ ] Supporto `MARKET\_NOW`
* \[ ] Legacy `REMOVE` non rotto

### Translator

* \[ ] `entry\_selector` propagato in `ModifyEntriesOperation`
* \[ ] `entry\_structure` propagato in `ModifyEntriesOperation`
* \[ ] `ADD\_ENTRY` non modificato semanticamente
* \[ ] `REENTER` non modificato semanticamente

### Test

* \[ ] Test `новый вход 2114`
* \[ ] Test `вход теперь 2114`
* \[ ] Test `вход теперь 2114-2120`
* \[ ] Test `вход теперь 2114 2100 2080`
* \[ ] Test `основной вход переносим на 2114`
* \[ ] Test `усреднение переносим на 2114`
* \[ ] Test `входим по рынку`
* \[ ] Test `убираем вход` legacy
* \[ ] Test `добавляю вход 2114` resta `ADD\_ENTRY`
* \[ ] Test `перезаходим 2114` resta `REENTER`
* \[ ] Test nuovo signal con `вход с текущих` resta `SIGNAL`

\---

## 20\. Criterio di successo

L’intervento è riuscito se:

```text
MODIFY\_ENTRY riconosce più varianti realistiche
MODIFY\_ENTRY produce mode più precisi
MODIFY\_ENTRY distingue almeno PRIMARY / AVERAGING quando il testo lo indica
MODIFY\_ENTRY supporta prezzo singolo, range e ladder
ADD\_ENTRY resta semanticamente separato
REENTER resta semanticamente separato
REMOVE legacy resta compatibile ma non viene ampliato
CanonicalTranslator propaga selector e structure
nessun path esistente viene rotto
```

Il parser deve migliorare la qualità semantica dell’output senza assumere responsabilità di esecuzione.

