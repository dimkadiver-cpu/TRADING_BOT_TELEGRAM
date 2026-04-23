# PRD — Semplificazione architetturale del parser e del flusso fino a Operation Rules

## 1. Scopo

Questo PRD definisce la semplificazione dell’architettura parser del progetto `TRADING_BOT_TELEGRAM`, con focus su:

- Layer 4 parser
- contratto dati parser
- passaggio dal parser ai layer successivi
- integrazione fino a `operation_rules`

L’obiettivo è ridurre ambiguità, ridondanza e complessità senza rifare da zero l’intero sistema e senza interrompere il flusso operativo esistente.

---

## 2. Problema attuale

L’architettura generale del progetto è valida, ma il cuore semantico del parser è diventato troppo ibrido.

### Problemi principali

1. **Più contratti impliciti contemporaneamente**
   - modelli parser documentati
   - `TraderParseResult` live
   - JSON normalizzato del router
   - semantica usata da `operation_rules`

2. **Troppa logica nei parser trader-specifici**
   I profili trader-specifici oggi non si limitano a:
   - classificare
   - estrarre dati dal linguaggio

   ma costruiscono già anche:
   - semantica quasi finale
   - targeting locale
   - linking locale
   - action-like structures
   - envelope v2 quasi definitivo

3. **Router ancora troppo coinvolto nel significato**
   Il router oggi non fa solo orchestration, ma lavora ancora troppo vicino alla semantica parser.

4. **Separazione incompleta tra UPDATE e REPORT**
   Eventi tipo TP hit, stop hit, fill e final result non sono separati in modo sufficientemente netto dagli update operativi.

5. **Difficoltà di estensione**
   Aggiungere o mantenere nuovi parser trader-specifici richiede troppo lavoro duplicato e troppe decisioni locali.

---

## 3. Obiettivo

Semplificare il progetto spostando la costruzione del significato finale in un punto unico e centrale.

### Obiettivo principale

Passare da un modello in cui:

`trader profile -> output quasi finale -> router si adatta`

a un modello in cui:

`trader parser -> estrazione grezza standard -> canonical normalizer -> CanonicalMessage -> operation_rules`

### Obiettivi concreti

- definire un solo contratto parser ufficiale
- alleggerire il ruolo dei parser trader-specifici
- centralizzare la normalizzazione semantica
- semplificare il router
- far leggere `operation_rules` solo il modello canonico
- mantenere riusabili i vocabolari e gran parte della logica di estrazione esistente

---

## 4. Non obiettivi

Questo PRD **non** prevede, come primo step:

- riscrittura completa di tutti i parser trader-specifici
- eliminazione immediata del flusso legacy
- redesign completo del listener
- redesign completo del DB
- sostituzione del sistema di execution
- rework del backtesting

---

## 5. Principio architetturale

### Regola chiave

Il parser trader-specifico **non deve più costruire l’output finale**.

Deve fare solo:

1. classificazione
2. estrazione grezza dal linguaggio del trader

Poi un componente centrale deve trasformare tutto nel formato canonico unico.

### Nuovo principio di flusso

```text
Telegram message
    ↓
Raw persistence
    ↓
Trader-specific parser
(classificazione + estrazione grezza)
    ↓
TraderRawExtraction
    ↓
CanonicalNormalizer
    ↓
CanonicalMessage
    ↓
Validator
    ↓
Router
    ↓
OperationRules
```

---

## 6. Decisione di semplificazione

## 6.1 Un solo contratto canonico

Il progetto deve convergere su un solo output parser ufficiale:

- `CanonicalMessage v1`

Questo diventa il formato che:
- il router riceve
- il router persiste
- `operation_rules` legge
- i parser futuri devono emettere direttamente o tramite normalizer

---

## 6.2 Nuovo ruolo dei parser trader-specifici

I parser trader-specifici restano necessari, perché conoscono:
- il linguaggio del trader
- i marker specifici
- le eccezioni linguistiche
- il vocabolario personale

### Però cambia il loro ruolo

### Prima
Costruivano già:
- intent
- entities
- target refs
- target scope
- linking
- semantica quasi finale

### Dopo
Devono produrre solo una **estrazione grezza standardizzata**.

### Output desiderato

Un oggetto intermedio del tipo:

- `message_class_hint`
- `intents_detected`
- `raw_entities`
- `raw_targets`
- `raw_report_events`
- `raw_fragments`
- `warnings`
- `confidence`

Il formato finale viene prodotto altrove.

---

## 6.3 Riuso della struttura attuale

La semplificazione **non** richiede di buttare via la struttura attuale dei parser e dei vocabolari.

### Si possono riusare

- `parsing_rules.json`
- vocabolari shared
- marker personali dei trader
- `RulesEngine`
- gran parte della logica regex / extractors attuale
- gran parte delle funzioni di estrazione per symbol, entry, stop, TP, target

### Cosa cambia
Non devono più essere la sede in cui si decide la forma finale del messaggio.

---

## 6.4 Canonical Normalizer centrale

Viene introdotto un modulo centrale di normalizzazione, responsabile di:

- convertire la raw extraction trader-specifica nel contratto unico
- trasformare vecchie semantiche in campi canonici
- separare `SIGNAL`, `UPDATE`, `REPORT`, `INFO`
- separare update operativi da eventi di report
- costruire il targeting canonico
- preparare il payload per i layer successivi

### Principio

Tutta la semantica finale deve stare qui, non sparsa nei profili.

---

## 6.5 Router semplificato

Il router deve diventare più vicino a un orchestratore.

### Deve fare
- gestire lifecycle del messaggio
- risolvere trader / eligibility
- chiamare parser
- chiamare normalizer
- validare
- persistere
- inoltrare ai layer successivi

### Non deve più fare
- adattamento semantico specifico del trader
- ricostruzione di meaning da output profile-specifici
- bridge impliciti tra più contratti parser

---

## 6.6 Operation Rules solo sul modello canonico

`operation_rules` non deve più leggere direttamente il vecchio output parser quasi finale.

Deve leggere solo:

- `CanonicalMessage`

In questo modo:
- non conosce più i dettagli dei trader
- non dipende da naming locali
- lavora solo sui concetti canonici

---

## 7. Modello canonico semplificato

La semplificazione architetturale assume come base il nuovo modello parser v1 già definito.

### Classi top-level
- `SIGNAL`
- `UPDATE`
- `REPORT`
- `INFO`

### Update canonici
- `SET_STOP`
- `CLOSE`
- `CANCEL_PENDING`
- `MODIFY_ENTRIES`
- `MODIFY_TARGETS`

### Report separato
Il report contiene solo:
- `events`
- `reported_result`
- `notes`

### Entry model
- `entry_structure` globale
- `entry_type` solo per singola leg

### Targeting
Un solo blocco unificato.

### Intents
Restano presenti nel modello, ma come supporto semantico, non come verità business finale.

---

## 8. Architettura target

## 8.1 Componenti

### A. Trader-specific parser
Responsabilità:
- classificazione
- estrazione grezza
- uso del vocabolario trader

### B. CanonicalNormalizer
Responsabilità:
- mapping centralizzato
- costruzione `CanonicalMessage`
- standardizzazione semantica

### C. CanonicalValidator
Responsabilità:
- validazione struttura
- validazione coerenza business minima

### D. Router
Responsabilità:
- orchestration
- persistenza
- review / failure / done
- inoltro ai layer successivi

### E. OperationRules
Responsabilità:
- leggere solo il contratto canonico
- costruire il piano operativo

---

## 8.2 Namespace suggerito

Per evitare ulteriore confusione, la nuova architettura dovrebbe vivere in uno spazio separato, per esempio:

- `src/parser_v1/`
oppure
- `src/parser/canonical_v1/`

con file tipo:
- `models.py`
- `raw_extraction.py`
- `normalizer.py`
- `validator.py`
- `examples/`
- `mapping_legacy.py`

---

## 9. Strategia di implementazione

## Fase 1 — congelare il contratto
- confermare il `CanonicalMessage v1`
- confermare lo schema Pydantic
- confermare esempi/fixture

## Fase 2 — introdurre TraderRawExtraction
- definire la struttura intermedia standard
- adattare i parser attuali a produrla, o creare adapter locali

## Fase 3 — introdurre CanonicalNormalizer
- mappare l’estrazione grezza nel nuovo contratto
- validare casi reali

## Fase 4 — introdurre modalità shadow
- mantenere il flusso attuale
- produrre in parallelo il nuovo `CanonicalMessage`
- confrontare risultati

## Fase 5 — adattare router
- fare in modo che il router lavori col nuovo payload

## Fase 6 — adattare operation_rules
- passare a lettura diretta del nuovo modello

## Fase 7 — migrare i parser trader-specifici uno per volta
- alleggerire i parser
- togliere costruzione locale di semantica finale

## Fase 8 — deprecare il legacy
- rimuovere output parser non più necessari
- eliminare contratti duplicati

---

## 10. Benefici attesi

### 10.1 Semplificazione tecnica
- meno duplicazione semantica
- meno logica dispersa
- meno dipendenza dal singolo profilo

### 10.2 Semplificazione manutentiva
- nuovi trader più facili da aggiungere
- refactor locali meno rischiosi
- meno regressioni trasversali

### 10.3 Semplificazione concettuale
- un solo formato finale
- update e report separati
- router più chiaro
- operation rules più chiaro

### 10.4 Semplificazione del debugging
- source of truth chiaro
- confronti più facili
- fixture canoniche più leggibili

---

## 11. Rischi

### Rischio 1
Il normalizer centrale diventa troppo complesso.

**Mitigazione**
- mantenerlo focalizzato solo sulla conversione canonica
- non inserirci logica runtime o execution

### Rischio 2
I parser trader-specifici continuano a costruire semantica finale anche dopo il refactor.

**Mitigazione**
- introdurre una struttura intermedia standard obbligatoria
- vietare nuovi campi quasi-finali locali

### Rischio 3
Router e operation_rules continuano a dipendere dal vecchio output.

**Mitigazione**
- modalità shadow
- audit differenziale
- passaggio progressivo con metriche

### Rischio 4
Si prova a riscrivere tutto in una volta.

**Mitigazione**
- migrazione progressiva
- riuso del vocabolario e degli extractor attuali
- un trader alla volta

---

## 12. Criteri di successo

La semplificazione sarà considerata riuscita quando:

1. esiste un solo contratto parser ufficiale
2. i parser trader-specifici non costruiscono più il significato finale completo
3. il normalizer centrale è il punto unico di standardizzazione
4. il router lavora sul nuovo payload
5. `operation_rules` legge solo `CanonicalMessage`
6. update e report sono separati correttamente
7. i vocabolari attuali sono stati riusati, non buttati
8. il Layer 4 non è più ibrido

---

## 13. Sintesi finale

La semplificazione del progetto non consiste nel cancellare il lavoro fatto.

Consiste nel:

- tenere la parte linguistica che già funziona
- togliere ai parser trader-specifici il compito di costruire l’output finale
- introdurre una normalizzazione canonica centralizzata
- far convergere router e operation rules su un solo modello unico

### Formula finale

Da:

`trader profile decides too much`

a:

`trader profile extracts, canonical normalizer decides`
