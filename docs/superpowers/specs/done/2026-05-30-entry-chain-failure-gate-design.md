# Entry Chain Failure Gate - Design Spec
**Date:** 2026-05-30  
**Status:** Draft - pending user review

---

## Problema

Nel runtime attuale una chain multi-leg di ingresso, in particolare `TWO_STEP` con:

- `leg1 = MARKET`
- `leg2 = LIMIT`

viene creata con entrambe le leg subito attive come comandi entry `PENDING`.

Questo comporta il seguente bug operativo:

1. `leg1` viene inviata all'exchange
2. `leg1` fallisce, viene rifiutata, oppure finisce in `REVIEW_REQUIRED`
3. `leg2` puo comunque essere inviata all'exchange
4. la chain puo restare viva oppure aprirsi tramite una leg successiva, anche se la leg primaria non e mai stata risolta correttamente

Per un piano `MARKET + LIMIT` questo comportamento e pericoloso: la leg successiva non e un ingresso indipendente, ma un'estensione di un piano la cui entry primaria non e andata a buon fine.

---

## Goal

Introdurre una regola esplicita di dipendenza tra le entry leg quando il piano richiede che la leg primaria sia risolta prima delle successive.

Per il caso `TWO_STEP (MARKET + LIMIT)` il comportamento target e:

- se `leg1` fallisce o va in `REVIEW_REQUIRED`, `leg2` e ogni entry successiva non devono essere inviate;
- la chain deve terminare in uno stato coerente, senza ordini entry residui non governati;
- l'operatore deve vedere chiaramente il motivo del blocco tramite lifecycle state ed evento esplicito.

---

## Stato attuale

### Costruzione comandi

`LifecycleEntryGate` crea una entry command per ogni leg del segnale e la mette subito in `PENDING`.

Effetto pratico:

- le leg di entry sono parallelamente schedulabili;
- solo i comandi `WAITING_POSITION` sono davvero differiti;
- le entry successive non aspettano l'esito della leg primaria.

### Failure handling attuale

Quando un `PLACE_ENTRY` fallisce:

- il singolo comando viene marcato `FAILED`, oppure `REVIEW_REQUIRED` nei casi di gating/capability/config;
- la chain viene cancellata solo se **tutte** le entry risultano ormai non attive;
- se esiste ancora una entry attiva, la chain resta viva.

Questo e corretto per alcune semantiche di ladder indipendente, ma non per il caso `primary-first`.

---

## Decisioni di design

### 1. Nuova policy per il piano entry

Introdurre una policy esplicita a livello di piano o chain:

- `entry_dependency_policy = "independent"`
- `entry_dependency_policy = "primary_first"`

Default raccomandato:

- `ONE_SHOT` -> `independent`
- `TWO_STEP` con `sequence=1 MARKET` e `sequence>1 LIMIT` -> `primary_first`
- `LADDER` e altri casi da valutare esplicitamente, non impliciti

La spec copre solo il comportamento `primary_first`.

### 2. Semantica di `primary_first`

Quando la policy e `primary_first`:

- solo `leg1` puo essere inviata immediatamente;
- `leg2..N` nascono in stato differito, non `PENDING`;
- le leg successive vengono rilasciate solo dopo una conferma positiva della leg primaria.

Conferma positiva significa:

- `ENTRY_FILLED` per la leg primaria

Non sono conferme positive:

- `FAILED`
- `REVIEW_REQUIRED`
- `PENDING_ENTRY_CANCELLED_CONFIRMED`
- timeout / reject / capability failure / gating failure

### 3. Failure della leg primaria

Se la leg primaria non viene confermata positivamente, il sistema deve:

1. bloccare il rilascio delle leg successive
2. cancellare o marcare `CANCELLED` le leg successive non ancora inviate
3. spostare la chain in stato terminale o quasi-terminale coerente

Scelta raccomandata:

- se il problema e operativo/configurativo/exchange-side -> `REVIEW_REQUIRED`
- se il problema e un fallimento secco di submit senza recovery utile -> `CANCELLED`

Per il bug segnalato, la scelta piu sicura e:

- `chain.lifecycle_state = REVIEW_REQUIRED`
- reason: `primary_entry_failed_blocked_subsequent_entries`

perche richiede intervento umano invece di chiudere silenziosamente una chain con intenzione operativa non soddisfatta.

---

## Modello target

### Nuovo stato comando entry differita

Le leg `sequence > 1` in policy `primary_first` non devono usare `PENDING`.

Opzioni:

1. Riutilizzare `WAITING_POSITION`
2. Introdurre `WAITING_PRIMARY_ENTRY`

Decisione raccomandata: **introdurre `WAITING_PRIMARY_ENTRY`**

Motivo:

- `WAITING_POSITION` significa "aspetta che esista posizione aperta"
- qui invece serve "aspetta esito positivo della leg primaria"
- fondere i due concetti renderebbe il worker ambiguo e meno leggibile

### Nuova semantica worker

`ExecutionCommandWorker` deve avere un ulteriore path:

- query `WAITING_PRIMARY_ENTRY`
- rilascio a `PENDING` solo quando la chain ha confermato `primary_leg_status = FILLED`

Il rilascio non deve basarsi genericamente su `lifecycle_state='OPEN'`, ma sullo stato specifico della leg primaria nel `plan_state_json`.

---

## Modifiche proposte

### 1. `src/runtime_v2/lifecycle/models.py`

Aggiungere il nuovo stato comando:

```python
CommandStatus = Literal[
    "PENDING",
    "SENT",
    "ACK",
    "WAITING_POSITION",
    "WAITING_PRIMARY_ENTRY",
    "DONE",
    "FAILED",
    "REVIEW_REQUIRED",
    "CANCELLED",
    "SUPERSEDED",
]
```

### 2. `src/runtime_v2/lifecycle/execution_plan.py`

Persistire nel piano anche la policy:

```json
{
  "entry_dependency_policy": "primary_first",
  "legs": [...]
}
```

Inoltre la `leg1` deve restare distinguibile come primaria non solo tramite `sequence=1`, ma come ruolo logico.

### 3. `src/runtime_v2/lifecycle/entry_gate.py`

Nel path di creazione comandi:

- se `entry_dependency_policy == "primary_first"`:
  - `leg1` -> `PENDING`
  - `leg2..N` -> `WAITING_PRIMARY_ENTRY`

Non basta cambiare i test: questa e la modifica che definisce il comportamento target.

### 4. `src/runtime_v2/lifecycle/event_processor.py`

Su `ENTRY_FILLED` della leg primaria:

- aggiornare il piano: `leg1.status = FILLED`
- emettere un segnale di rilascio logico per le leg successive

Su failure/cancel/review della leg primaria:

- aggiornare il piano: `leg1.status = FAILED | CANCELLED`
- emettere evento lifecycle:
  - `PRIMARY_ENTRY_FAILED`
  - oppure `PRIMARY_ENTRY_REVIEW_REQUIRED`
- far convergere la chain in `REVIEW_REQUIRED`

### 5. `src/runtime_v2/execution_gateway/command_worker.py`

Aggiungere una query specifica:

- `WAITING_PRIMARY_ENTRY` su chain la cui leg primaria nel piano e `FILLED`

Solo quei comandi possono essere trasformati in `PENDING`.

### 6. `src/runtime_v2/execution_gateway/gateway.py`

Quando un `PLACE_ENTRY` di `sequence=1` fallisce oppure va in review:

- invocare una nuova routine chain-level, non il solo `cancel_chain_if_all_entries_failed`

Nuova routine proposta:

```python
block_subsequent_entries_on_primary_failure(
    trade_chain_id,
    reason,
)
```

Responsabilita:

- trovare entry commands `sequence > 1` in stato `PENDING`, `WAITING_PRIMARY_ENTRY`, `SENT`, `ACK`
- se non ancora inviate: marcare `CANCELLED`
- se gia inviate: non nascondere il problema; chain -> `REVIEW_REQUIRED`
- scrivere evento lifecycle esplicito

### 7. `src/runtime_v2/execution_gateway/repositories.py`

Non basta la regola attuale "cancella la chain solo se tutte le entry sono fallite".

Serve una seconda regola distinta:

- `cancel_chain_if_all_entries_failed(...)`
- `review_chain_if_primary_entry_failed(...)`

La seconda e specifica per policy `primary_first`.

---

## Stato finale desiderato

### Caso A - leg1 fallisce prima di qualsiasi fill

Input:

- chain `TWO_STEP`
- `leg1 MARKET`
- `leg2 LIMIT`
- policy `primary_first`

Esito:

- `leg1` -> `FAILED` o `REVIEW_REQUIRED`
- `leg2` -> `CANCELLED` se non ancora inviata
- chain -> `REVIEW_REQUIRED`
- evento -> `PRIMARY_ENTRY_FAILED_BLOCKED_SUBSEQUENT`

### Caso B - leg1 va in review per capability/config gate

Esito:

- `leg1` -> `REVIEW_REQUIRED`
- `leg2` -> `CANCELLED`
- chain -> `REVIEW_REQUIRED`
- reason -> `primary_entry_review_required_blocked_subsequent`

### Caso C - leg2 era gia stata inviata per race

Esito:

- chain -> `REVIEW_REQUIRED`
- nessun tentativo di "nascondere" l'incoerenza
- evento esplicito di anomalia operativa:
  - `PRIMARY_ENTRY_FAILED_AFTER_SUBSEQUENT_SENT`

Questo caso deve restare visibile per audit.

### Caso D - leg1 filla correttamente

Esito:

- `leg2..N` possono passare da `WAITING_PRIMARY_ENTRY` a `PENDING`
- il comportamento successivo torna quello standard del piano

---

## Non-goals

Fuori da questa spec:

- ridefinire tutta la semantica di `LADDER`
- cambiare il parser
- cambiare il modello di TP/SL
- introdurre nuovo schema DB se non necessario
- risolvere i race exchange-side gia avvenuti oltre il necessario per marcarli `REVIEW_REQUIRED`

---

## Compatibilita

La modifica non deve cambiare il comportamento dei casi non `primary_first`.

Quindi:

- piani `independent` continuano a funzionare come oggi
- la logica nuova non deve essere applicata implicitamente a tutti i multi-entry

La policy deve essere esplicita e persistita.

---

## Acceptance criteria

1. Un piano `TWO_STEP` con `leg1 MARKET` e `leg2 LIMIT` crea `leg1` in `PENDING` e `leg2` in `WAITING_PRIMARY_ENTRY`.
2. Se `leg1` riceve `ENTRY_FILLED`, `leg2` viene rilasciata e puo passare a `PENDING`.
3. Se `leg1` fallisce al submit, `leg2` non viene inviata.
4. Se `leg1` va in `REVIEW_REQUIRED`, `leg2` non viene inviata.
5. In entrambi i casi precedenti, la chain finisce in `REVIEW_REQUIRED` con reason esplicita.
6. La logica attuale `cancel_chain_if_all_entries_failed` non viene usata come unico meccanismo per il caso `primary_first`.
7. I casi `independent` restano invariati.

---

## Test da scrivere

1. `entry_gate`:
   `TWO_STEP MARKET+LIMIT` con policy `primary_first` -> `leg1=PENDING`, `leg2=WAITING_PRIMARY_ENTRY`.

2. `command_worker`:
   un comando `WAITING_PRIMARY_ENTRY` non viene promosso se la leg primaria non e `FILLED`.

3. `command_worker`:
   un comando `WAITING_PRIMARY_ENTRY` viene promosso a `PENDING` dopo `ENTRY_FILLED` della leg primaria.

4. `gateway/repository`:
   `PLACE_ENTRY sequence=1` fallito -> `leg2` non inviata e chain `REVIEW_REQUIRED`.

5. `gateway/repository`:
   `PLACE_ENTRY sequence=1` in `REVIEW_REQUIRED` -> `leg2` `CANCELLED`, chain `REVIEW_REQUIRED`.

6. `integration`:
   worker su due entry con fake adapter che fallisce la `sequence=1` -> nessuna `place_order` per la `sequence=2`.

7. `regression`:
   un piano `independent` multi-entry continua a inviare le leg come oggi.

---

## Rischi e note

### Race intra-worker

Se il worker legge due `PENDING` nello stesso batch prima che la failure della `leg1` sia persistita, puo ancora provare a inviare la `leg2`.

Questa spec quindi richiede anche una guardia al momento del `process(cmd)`:

- prima di inviare una `sequence > 1`, il gateway o il repository deve ricontrollare la policy del piano e lo stato della leg primaria
- se la primaria non e `FILLED`, il comando non parte

Questa guardia runtime e obbligatoria. Il solo status iniziale non basta.

### Chain gia incoerente

Se in produzione esistono gia chain create col vecchio modello:

- la nuova logica deve degradare in modo sicuro
- in caso di ambiguita, meglio `REVIEW_REQUIRED` che submit automatico

---

## Open questions

1. La policy `primary_first` deve essere dedotta automaticamente dalla struttura `TWO_STEP MARKET+LIMIT`, oppure configurata esplicitamente dal planner?
2. Se `leg2` era gia `SENT` quando fallisce `leg1`, vogliamo emettere automaticamente un `CANCEL_PENDING_ENTRY` oppure limitarci a `REVIEW_REQUIRED`?
3. La stessa regola va estesa anche a `LADDER` con `leg1 MARKET`, oppure il requisito resta limitato a `TWO_STEP`?

---

## Raccomandazione

Implementare la regola in due livelli:

1. modellazione corretta dei comandi:
   `leg2..N` non devono nascere `PENDING` nei piani `primary_first`

2. guardia difensiva al submit:
   una `sequence > 1` non puo partire se il piano non attesta `leg1=FILLED`

La doppia barriera riduce sia il bug logico sia i race del worker.
