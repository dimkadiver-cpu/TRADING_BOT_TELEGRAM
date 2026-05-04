# Pipeline completa del parser nuovo

## Flusso generale

```text
Input Telegram message
↓
1. Normalize text
↓
2. Detect marker matches
↓
3. Resolve local evidence
↓
4. Extract signal draft
↓
5. Extract intent entities
↓
6. Classify message
↓
7. Build ParsedMessage
↓
8. Translate to CanonicalMessage
```

\---

# 1\. Normalize text

## Output

```python
NormalizedText(
    raw\_text="Стоп в БУ",
    normalized\_text="стоп в бу",
    lines=\["стоп в бу"]
)
```

## Regole

```text
- lowercase
- ё -> е
- normalize dash: – — − -> -
- trim
- collassa spazi multipli
- conserva sempre raw\_text
```

La normalizzazione serve per matching. L’estrazione numerica può usare anche il testo raw per non perdere formati.

\---

# 2\. Detect marker matches

## Input

```text
normalized\_text
semantic\_markers.json
```

## Output

```python
MarkerMatch(
    kind="intent",
    name="MOVE\_STOP\_TO\_BE",
    strength="strong",
    marker="стоп в бу",
    start=0,
    end=9
)
```

## Perché servono gli span

Senza `start/end` non puoi sapere se:

```text
бу
```

è un marker autonomo oppure è contenuto in:

```text
стоп в бу
```

Questa è una fonte reale di falsi positivi.

\---

# 3\. Resolve local evidence

Questa fase pulisce le evidenze prima di creare gli intenti.

## Regole minime

```text
1. strong batte weak se il weak è contenuto nello span dello strong
2. same-intent weak dentro same-intent strong viene soppresso
3. cross-intent weak dentro strong viene soppresso solo se rules.json lo prevede
4. intent specifico batte intent generico se dichiarato in rules.json
5. i messaggi compositi restano compositi se non c'è conflitto reale
```

## Esempio

Input:

```text
стоп в бу
```

Match grezzi:

```text
MOVE\_STOP\_TO\_BE strong "стоп в бу"
MOVE\_STOP\_TO\_BE weak   "бу"
EXIT\_BE weak           "бу"
```

Output risolto:

```text
MOVE\_STOP\_TO\_BE strong
```

\---

# 4\. Extract signal draft

## Responsabilità

Estrarre un eventuale segnale nuovo.

## Output

```python
SignalDraft(
    symbol="ETHUSDT",
    side="LONG",
    entries=\[...],
    entry\_structure="ONE\_SHOT",
    stop\_loss=...,
    take\_profits=\[...],
    risk\_hint=...,
    leverage\_hint=...,
    missing\_fields=\[],
    completeness="COMPLETE"
)
```

## Regola di completezza

Un segnale è completo solo se ha:

```text
symbol
side
entries
stop\_loss
take\_profits
```

Se manca almeno uno:

```text
primary\_class = SIGNAL
parse\_status = PARTIAL
```

\---

# 5\. Extract intent entities

Per ogni intento risolto, estrai solo le entità necessarie.

Esempi:

```text
MOVE\_STOP\_TO\_BE -> nessuna entità obbligatoria
MOVE\_STOP       -> new\_stop\_price oppure stop\_to\_tp\_level
CLOSE\_FULL      -> close\_price opzionale
CLOSE\_PARTIAL   -> fraction opzionale, close\_price opzionale
CANCEL\_PENDING  -> cancel\_scope opzionale
REPORT\_RESULT   -> tipo report minimale
```

Non estrarre dati che il sistema non usa.

\---

# 6\. Classify message

La classificazione deve essere deterministica.

```python
if signal\_draft is not None:
    primary\_class = "SIGNAL"
elif has\_update\_intents:
    primary\_class = "UPDATE"
elif has\_report\_intents:
    primary\_class = "REPORT"
elif has\_info\_marker:
    primary\_class = "INFO"
else:
    primary\_class = "INFO"
    parse\_status = "UNCLASSIFIED"
```

Non servono due campi paralleli tipo:

```text
message\_type
primary\_class
```

Tenere solo:

```text
primary\_class
parse\_status
primary\_intent
```

\---

# 7\. Build ParsedMessage

Il `ParsedMessage` è il contratto interno del parser.

Deve contenere:

```text
- classe messaggio
- status parse
- signal draft
- intents
- primary intent
- raw context
- warnings
- diagnostics
```

Non deve contenere validazione DB.

\---

# 8\. Translate to CanonicalMessage

Il translator crea il contratto finale.

Esempio:

```text
MOVE\_STOP\_TO\_BE
↓
UpdateOperation SET\_STOP -> ENTRY
```

```text
CLOSE\_FULL
↓
UpdateOperation CLOSE -> FULL
```

```text
REPORT\_RESULT
↓
ReportPayload con event\_type minimale
```

\---

# Output finale

```text
CanonicalMessage
```

A questo punto il parser ha finito.

Tutto ciò che riguarda target reale, DB, lifecycle ed esecuzione avviene dopo.

