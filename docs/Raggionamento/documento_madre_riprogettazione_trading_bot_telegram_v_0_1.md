# Documento madre di riprogettazione — TRADING\_BOT\_TELEGRAM

**Stato:** Bozza architetturale v0.1\
**Scopo:** definire il ridisegno del progetto prima di avviare lo sviluppo del nuovo runtime.\
**Ambito:** nuovo sistema per acquisizione messaggi Telegram, parsing con `parser_v2`, applicazione di policy/regole operative, gestione del ciclo di vita dei segnali e predisposizione all’esecuzione tramite adapter neutro.

---

# 1. Obiettivo del ridisegno

Il progetto deve essere riprogettato a partire dall’esperienza accumulata, **senza trascinare nel nuovo runtime l’architettura ibrida attuale**.

Il nuovo sistema deve:

1. mantenere la logica valida di ascolto/acquisizione Telegram;
2. usare **solo **`` come parser del runtime nuovo;
3. mantenere e consolidare `` come harness ufficiale di replay, sviluppo e regressione del parser;
4. rifare il layer delle **operation rules** affinché lavori direttamente sul contratto canonico di `parser_v2`;
5. introdurre un layer esplicito di **lifecycle / signal state management**;
6. predisporre un **execution adapter neutro**, senza accoppiare il dominio interno a Hummingbot, OctoBot o altri motori;
7. semplificare la persistenza, distinguendo chiaramente:
   - messaggi acquisiti;
   - output parser;
   - decisioni operative;
   - stato dei segnali/istruzioni;
   - eventi di esecuzione e audit.

---

# 2. Principio guida

## 2.1 Il progetto attuale non è la base architetturale del nuovo

Il repository attuale va trattato come:

- **prototipo avanzato**;
- **fonte di logiche validate**;
- **fonte di test e casi reali**;
- **fonte di moduli riusabili selettivamente**.

Non va trattato come uno scheletro da continuare a patchare.

## 2.2 Strategia adottata

La strategia consigliata è:

> **Clean-core redesign in parallelo, nello stesso repository, con sostituzione progressiva dei moduli utili.**

Il vecchio runtime resta disponibile come baseline e riferimento.\
Il nuovo runtime viene sviluppato con confini autonomi, contratti nuovi e responsabilità esplicite.

---

# 3. Decisioni già assunte

## 3.1 Moduli da preservare

### 3.1.1 Telegram listening / ingestion

Da preservare e adattare:

- ascolto live dei messaggi Telegram;
- recupero in catchup/recovery;
- supporto canali e topic;
- deduplicazione;
- persistenza dei raw messages;
- configurazione canali;
- hot reload della configurazione, se già stabile.

Il listener deve restare **sottile**: acquisisce, normalizza il contesto minimo e passa il messaggio alla pipeline; non deve contenere parsing pesante, operation rules o logiche di esecuzione.

### 3.1.2 `parser_v2`

`parser_v2` è la base del nuovo parser live.\
Non va più tenuto come componente solo da test/replay: nel nuovo sistema deve diventare **il parser unico e ufficiale**.

Vanno preservati:

- `UniversalParserRuntime`;
- profili trader dedicati;
- semantic markers;
- disambiguazione locale;
- target hints / target binding;
- translator verso output canonico;
- contratti tipizzati di `CanonicalMessage`.

### 3.1.3 `parser_test`

`parser_test` va mantenuto e consolidato come tool ufficiale per:

- importare/scaricare messaggi Telegram in DB di test;
- assegnare trader di default quando richiesto;
- risolvere `effective_trader_id` con logica compatibile con il live;
- selezionare i messaggi da parsare tramite filtro trader;
- selezionare il parser/profile in modo separato dal filtro dei messaggi;
- eseguire `parser_v2` su dati reali;
- salvare run e risultati;
- produrre CSV/report di qualità.

### 3.1.4 Concetto di operation rules

Va preservata l’idea di un layer che:

- decide se un messaggio/segnale è operabile;
- applica policy di rischio e sizing;
- applica configurazioni per trader;
- blocca o manda in review ciò che non soddisfa i criteri;
- produce uno snapshot delle regole usate.

Va invece **ripensata l’implementazione**, perché deve ricevere direttamente il `CanonicalMessage` di `parser_v2`, non adattarsi a formati legacy o intermedi.

---

# 4. Cosa viene abbandonato nel nuovo runtime

## 4.1 Parser legacy `src/parser`

Nel nuovo runtime non deve più esistere un dual-stack tra:

- parser vecchio;
- ParsedMessage intermedio;
- canonical v1;
- parser\_v2.

Il nuovo sistema deve avere un solo percorso ufficiale:

```text
Raw message → parser_v2 → CanonicalMessageV2
```

## 4.2 `MessageRouter` monolitico attuale

L’attuale router concentra responsabilità eterogenee:

- trader resolution;
- eligibility;
- parsing;
- persistenza parser;
- normalizzazione/canonicalizzazione;
- operation rules;
- target resolving;
- update runtime;
- persistenza segnali operativi.

Nel nuovo progetto questa centralizzazione va eliminata.

## 4.3 Bridge e compatibilità temporanee

Da non portare nel nuovo sistema:

- shadow normalizer;
- dual-stack parser;
- conversioni verso canonical v1 se non strettamente necessarie per migrazione dati;
- adattatori temporanei costruiti solo per tenere vivo il vecchio runtime.

## 4.4 Accoppiamento precoce con execution engine

Il nuovo core non va costruito “per Hummingbot” o “per OctoBot”.\
L’esecuzione deve entrare tramite **porta/adattatore**.

---

# 5. Architettura target — vista d’insieme

```text
Telegram Listener
        ↓
Raw Message Ingestion
        ↓
Processing Queue / Dispatcher
        ↓
Trader Resolution
        ↓
Parser V2 Runtime
        ↓
CanonicalMessageV2 Store
        ↓
Operation Rules Engine V2
        ↓
OperationalDecision Store
        ↓
Lifecycle Engine
        ↓
Signal / Instruction State Store
        ↓
Execution Gateway / Adapter
        ↓
External Executor / Exchange Layer
```

Tooling parallelo:

```text
parser_test
        ↓
Raw messages DB di test
        ↓
Stesso parser_v2 del live
        ↓
parser_results_v2
        ↓
CSV / report / regressioni
```

---

# 6. Responsabilità dei layer

## 6.1 Telegram Listener

**Responsabilità:** ricevere messaggi Telegram live o recuperarli in recovery.

**Fa:**

- legge evento Telegram;
- rileva chat, topic, message id, reply id;
- inoltra a ingestion;
- accoda il messaggio per il processing.

**Non fa:**

- parsing;
- operation rules;
- lifecycle;
- esecuzione;
- accesso a modelli di trading.

---

## 6.2 Raw Message Ingestion

**Responsabilità:** rendere persistente il dato grezzo e tracciabile.

**Output:** `RawMessageEnvelope` persistito in DB.

**Fa:**

- deduplicazione;
- salvataggio raw;
- stato acquisizione;
- eventuale metadata di media, topic, chat, trader hint.

**Non fa:**

- scegliere il parser;
- trasformare in segnale;
- applicare regole operative.

---

## 6.3 Trader Resolution

**Responsabilità:** determinare il trader effettivo a cui associare il messaggio.

**Input:** `RawMessageEnvelope` + config/source mapping + contesto reply/ref.\
**Output:** `ResolvedTraderContext`.

**Deve essere condiviso concettualmente tra live e parser\_test**, così da evitare dataset contaminati o comportamenti divergenti.

---

## 6.4 Parser V2 Runtime

**Responsabilità:** interpretare linguisticamente il messaggio.

**Input:** testo + contesto + profilo trader.\
**Output:** `CanonicalMessageV2`.

**Fa:**

- normalizzazione testo;
- marker matching;
- evidenze semantiche;
- estrazione signal/update/report/info;
- disambiguazione;
- target binding;
- traduzione in contratto canonico tipizzato.

**Non fa:**

- decidere se il segnale è operabile;
- calcolare position sizing;
- modificare lo stato operativo del trade;
- inviare ordini.

---

## 6.5 Operation Rules Engine V2

**Responsabilità:** trasformare un messaggio canonico in una decisione operativa.

**Input:** `CanonicalMessageV2` + regole globali/trader + eventuale stato operativo necessario ai gate.\
**Output:** `OperationalDecision`.

**Fa:**

- bloccare segnali invalidi o non operabili;
- accettare segnali validi;
- marcare casi da review;
- calcolare risk/sizing quando possibile;
- gestire differenza tra entry MARKET e LIMIT;
- applicare policy per update ammessi o ignorati;
- produrre snapshot immutabile delle regole applicate.

**Non fa:**

- interpretazione linguistica;
- aggiornamento diretto delle posizioni;
- invio comandi a exchange.

---

## 6.6 Lifecycle Engine

**Responsabilità:** applicare decisioni e messaggi canonici allo stato di segnali, setup, trade e istruzioni.

**Input:**

- `CanonicalMessageV2`;
- `OperationalDecision`;
- stato attuale del dominio.

**Output:**

- `LifecycleCommand` o `DomainEvent`;
- nuove snapshot di stato.

**Fa:**

- creare un setup/segnale operativo;
- applicare UPDATE targetizzati;
- registrare REPORT;
- gestire invalidazioni/cancellazioni;
- mantenere la storia degli eventi;
- preparare comandi neutri verso l’execution layer.

**Non fa:**

- parsing;
- accesso diretto alla rete exchange;
- calcolo semantico dei marker.

---

## 6.7 Execution Gateway / Adapter

**Responsabilità:** tradurre comandi interni neutri in comandi dell’esecutore scelto.

**Input:** `ExecutionIntent`.\
**Output:** richieste verso Hummingbot / altro engine + eventi di risposta.

**Deve essere neutro rispetto al dominio interno.**

Esempi di intent:

- submit new signal;
- place pending entry;
- cancel pending entry;
- move stop;
- close partial;
- close full;
- sync order/position status.

---

# 7. Contratti centrali da definire

## 7.1 `RawMessageEnvelope`

```python
class RawMessageEnvelope:
    raw_message_id: int
    source_chat_id: str
    source_topic_id: int | None
    telegram_message_id: int
    reply_to_message_id: int | None
    raw_text: str
    acquired_at: datetime
    message_ts: datetime
    acquisition_mode: Literal["live", "catchup", "import"]
    source_trader_id: str | None
    resolved_trader_id: str | None
    resolution_method: str | None
```

## 7.2 `CanonicalMessageV2`

Il contratto di riferimento è quello di `src/parser_v2/contracts/canonical_message.py`.

Classi principali:

- `SIGNAL`;
- `UPDATE`;
- `REPORT`;
- `INFO`.

Stati parser:

- `PARSED`;
- `PARTIAL`;
- `UNCLASSIFIED` o equivalenti già previsti dal contratto.

### Regola di sistema

Il parser deve produrre output **schema-valid** anche quando il messaggio non è eseguibile.\
La non eseguibilità va espressa con `parse_status`, warning, diagnostics o successiva decisione delle operation rules, non rompendo il contratto.

---

## 7.3 `OperationalDecision`

Bozza di contratto:

```python
class OperationalDecision:
    decision_id: int
    canonical_message_id: int
    trader_id: str
    decision_type: Literal["ACCEPT", "BLOCK", "REVIEW", "IGNORE"]
    reason_code: str | None
    warnings: list[str]
    applied_rules: list[str]
    policy_snapshot: dict
    risk_decision: RiskDecision | None
    created_at: datetime
```

### Esempi

#### Segnale accettato

```text
ACCEPT
risk computed
lifecycle may create setup
```

#### Segnale bloccato

```text
BLOCK
reason = missing_stop_loss | unsupported_structure | capital_cap_exceeded
```

#### Update da review

```text
REVIEW
reason = unresolved_target | ambiguous_scope
```

---

## 7.4 `RiskDecision`

```python
class RiskDecision:
    sizing_mode: Literal["IMMEDIATE", "DEFERRED"]
    risk_pct_of_capital: float | None
    risk_budget_usdt: float | None
    position_size_usdt: float | None
    leverage: float | None
    entry_split: dict[str, float] | None
```

### Nota MARKET

Per entry MARKET senza prezzo affidabile, il sistema deve poter produrre:

```text
sizing_mode = DEFERRED
```

Il sizing finale verrà completato quando l’entry price diventa disponibile secondo la politica definita.

---

## 7.5 `LifecycleCommand`

```python
class LifecycleCommand:
    command_type: Literal[
        "CREATE_SETUP",
        "APPLY_UPDATE",
        "REGISTER_REPORT",
        "INVALIDATE_SETUP",
        "SEND_TO_REVIEW",
        "IGNORE"
    ]
    target_ref: dict | None
    payload: dict
```

---

## 7.6 `ExecutionIntent`

```python
class ExecutionIntent:
    intent_type: Literal[
        "SUBMIT_NEW_SIGNAL",
        "PLACE_ENTRY",
        "PLACE_STOP",
        "PLACE_TAKE_PROFITS",
        "CANCEL_PENDING_ENTRY",
        "MOVE_STOP",
        "CLOSE_PARTIAL",
        "CLOSE_FULL",
        "SYNC_STATE"
    ]
    attempt_key: str
    payload: dict
```

---

# 8. Data stores e persistenza — proposta concettuale

## 8.1 Principio

Separare le famiglie di dati:

1. **Acquisition DB / Raw layer**;
2. **Parser DB / Canonical layer**;
3. **Operational DB / Decision + lifecycle layer**;
4. **Execution state / sync layer**.

Nel primo ciclo di riprogettazione si può restare su un unico DB fisico, ma con **tabelle logicamente separate**.\
La separazione fisica in più DB va decisa solo quando il dominio e i flussi sono maturi.

---

## 8.2 Tabelle minime suggerite — nuova architettura

### Raw / intake

- `raw_messages`
- `processing_jobs` o `message_processing_status`

### Parser

- `parser_runs` — per replay/test
- `parser_results_v2`
- `canonical_messages_live` oppure una tabella comune parametrizzata per `source=live|replay`

### Operation rules

- `operational_decisions`
- `operation_rule_snapshots` se non inglobate direttamente nella decisione

### Lifecycle

- `signal_roots`
- `signal_events`
- `signal_state_snapshots`
- `pending_instructions`

### Execution

- `execution_requests`
- `execution_events`
- `exchange_state_snapshots`

### Review/Audit

- `review_queue`
- `audit_events`

---

# 9. Flussi principali

## 9.1 Flusso live — nuovo messaggio

```text
1. TelegramListener riceve messaggio
2. RawMessageIngestion salva raw_messages
3. Dispatcher apre job di processing
4. TraderResolver risolve il trader effettivo
5. ParserV2 produce CanonicalMessageV2
6. Persist canonical output
7. OperationRulesEngineV2 produce OperationalDecision
8. Persist operational decision
9. LifecycleEngine applica la decisione
10. Se necessario produce ExecutionIntent
11. ExecutionAdapter invia al motore esterno
12. Audit trail aggiornato
```

---

## 9.2 Flusso parser\_test

```text
1. Import/scarico messaggi Telegram in raw_messages del DB test
2. Risoluzione effective_trader_id
3. Selezione messaggi tramite trader_filter
4. Selezione parser/profile tramite parser_system / parser_profile
5. ParserV2 produce CanonicalMessageV2
6. Salvataggio in parser_results_v2
7. Export CSV / report qualità
```

Regola: **filtro del trader dei messaggi** e **profilo parser da applicare** devono restare concetti separati.

---

## 9.3 Flusso update operativo

```text
1. ParserV2 classifica UPDATE
2. Target hints / targeted actions vengono prodotti nel CanonicalMessageV2
3. OperationRules decide se l’update è ammissibile / bloccato / review
4. LifecycleEngine risolve target nello stato operativo
5. Applica modifica: SL, cancel pending, close partial, close full, modify entry, modify TP...
6. Genera eventuale ExecutionIntent
```

---

# 10. Boundary decisivi tra i layer

## 10.1 Parser vs Operation Rules

| Tema                               | Parser | Operation Rules |
| ---------------------------------- | ------ | --------------- |
| Riconosce MOVE\_STOP               | Sì     | No              |
| Decide se MOVE\_STOP è applicabile | No     | Sì              |
| Estrae entry/SL/TP                 | Sì     | No              |
| Calcola risk/sizing                | No     | Sì              |
| Produce schema-valid output        | Sì     | No              |
| Decide block/review                | No     | Sì              |

---

## 10.2 Operation Rules vs Lifecycle

| Tema                                              | Operation Rules | Lifecycle |
| ------------------------------------------------- | --------------- | --------- |
| Decide se segnale è operabile                     | Sì              | No        |
| Calcola size / risk                               | Sì              | No        |
| Crea lo stato del segnale                         | No              | Sì        |
| Applica update a un setup già noto                | No              | Sì        |
| Verifica target del messaggio rispetto allo stato | Solo pre-check  | Sì        |

---

## 10.3 Lifecycle vs Execution

| Tema                               | Lifecycle | Execution                    |
| ---------------------------------- | --------- | ---------------------------- |
| Decide cosa deve succedere         | Sì        | No                           |
| Traduce in ordine API specifico    | No        | Sì                           |
| Tiene stato dominio                | Sì        | No, salvo sync/cache tecnica |
| Gestisce ritardi/ack dell’executor | Coordina  | Comunica eventi              |

---

# 11. Operation Rules V2 — direzione di redesign

## 11.1 Input corretto

L’engine deve ricevere:

```python
CanonicalMessage from src.parser_v2.contracts.canonical_message
```

Non:

- `TraderParseResult` legacy;
- `CanonicalMessage v1` del parser precedente;
- payload dict non tipizzati.

## 11.2 Classificazione operativa

L’engine deve gestire almeno:

### SIGNAL

- gate completi;
- validità minima per l’operatività;
- policy rischio;
- sizing immediato o deferred;
- regole su entry/TP/SL.

### UPDATE

- stabilire se l’update è ammesso dalla policy;
- bloccare update non supportati;
- mandare in review quelli ambigui;
- non applicare direttamente stato trade.

### REPORT

- decidere se loggare soltanto o produrre un evento informativo verso lifecycle/audit.

### INFO

- normalmente `IGNORE` o `LOG_ONLY`.

---

# 12. Lifecycle Engine — direzione di progettazione

Il lifecycle è il blocco che manca davvero nell’attuale ridisegno.

Deve occuparsi di:

- setup iniziale;
- distinzione tra setup, ordine pending, posizione aperta, posizione parzialmente chiusa, chiusa, invalidata;
- applicazione degli update Telegram;
- reazione a eventi di mercato/esecuzione;
- emissione di comandi neutri verso executor.

## 12.1 Esempi di stati dominio

```text
SETUP_RECEIVED
RULES_ACCEPTED
PENDING_ENTRY
PARTIALLY_FILLED
OPEN
PARTIALLY_CLOSED
CLOSED
CANCELLED
INVALIDATED
REVIEW_REQUIRED
```

Questi stati sono preliminari e vanno validati in una specifica dedicata.

---

# 13. Execution Layer — principio di neutralità

Il core deve esporre un contratto neutro.\
Hummingbot è una possibile implementazione futura, ma non deve entrare nei modelli di dominio.

## 13.1 Vincolo

L’autorità su:

- segnali;
- policy;
- risk sizing;
- decisioni operative;
- lifecycle;

resta nel progetto.\
L’executor esegue istruzioni e restituisce eventi/stato.

---

# 14. Metodo di sviluppo consigliato

## 14.1 Governo del progetto

Usare un approccio a due livelli:

### Livello progetto

- roadmap complessiva;
- decision log;
- stato delle specifiche;
- milestones.

### Livello fase

- brainstorming delimitato;
- spec/PRD mirata;
- piano di implementazione;
- TDD;
- review tecnica;
- chiusura fase e aggiornamento documentazione.

## 14.2 Regola operativa

Non iniziare lo sviluppo di un blocco finché non sono definiti:

1. responsabilità;
2. input/output;
3. contratti;
4. casi principali;
5. test minimi di accettazione.

---

# 15. Roadmap di realizzazione proposta

## Fase A — Documento madre e decisioni architetturali

**Output:** questo documento consolidato.

Obiettivi:

- fissare cosa si salva e cosa si abbandona;
- fissare architettura target;
- fissare contratti principali;
- fissare la strategia di sviluppo.

---

## Fase B — Specifica e pulizia della pipeline intake + trader resolution

**Output:** PRD dedicato.

Obiettivi:

- definire `RawMessageEnvelope`;
- formalizzare risoluzione trader;
- chiarire allineamento live/parser\_test;
- decidere schema raw persistente nuovo.

---

## Fase C — Consolidamento parser\_v2 come parser ufficiale

**Output:** parser\_v2 stabilizzato e contratto blindato.

Obiettivi:

- chiudere gap aperti del parser\_v2;
- fissare comportamento multi-ref e priorità dei riferimenti;
- garantire parser output schema-valid;
- aggiornare parser\_test come harness ufficiale.

---

## Fase D — Operation Rules Engine V2

**Output:** nuovo engine basato su `CanonicalMessageV2`.

Obiettivi:

- definire `OperationalDecision`;
- rifare risk/sizing/policy gate;
- supportare MARKET deferred sizing;
- gestire UPDATE/REPORT/INFO a livello decisionale.

---

## Fase E — Lifecycle Engine

**Output:** stato operativo coerente e command model.

Obiettivi:

- definire state machine;
- definire applicazione update;
- gestire setup/order/position state;
- produrre `LifecycleCommand` ed `ExecutionIntent`.

---

## Fase F — Execution Adapter

**Output:** prima integrazione esterna controllata.

Obiettivi:

- definire adapter neutro;
- implementare una strategia MVP verso il motore scelto;
- ricevere eventi/stati di ritorno;
- testare sync e gestione degli errori.

---

## Fase G — Observability, audit, review queue

**Output:** sistema leggibile e diagnosticabile.

Obiettivi:

- audit eventi;
- review queue;
- report parser / operation / lifecycle;
- strumenti per debugging e comparazione replay/live.

---

# 16. Prima vertical slice da sviluppare

La prima fetta end-to-end deve essere:

```text
Telegram/raw import
→ trader resolution
→ parser_v2
→ persistence CanonicalMessageV2
→ operation rules v2 minimale
→ OperationalDecision persistita
```

Questa slice serve a validare:

- i confini dei layer;
- il contratto parser → rules;
- lo schema DB;
- la qualità del flusso live/replay.

Non deve ancora includere:

- esecuzione exchange;
- lifecycle completo;
- gestione posizione avanzata;
- dashboard.

---

# 17. Criteri di qualità del nuovo design

Il ridisegno è corretto se:

1. il runtime live usa solo `parser_v2`;
2. `parser_test` e live usano contratti coerenti;
3. il parser non contiene logiche operative;
4. operation rules non interpreta testo grezzo;
5. lifecycle non ricalcola semantica del messaggio;
6. execution adapter non decide policy;
7. ogni layer ha input/output testabili;
8. ogni decisione importante è tracciabile in DB/audit;
9. si può cambiare executor senza riscrivere parser/rules/lifecycle;
10. i casi non eseguibili non rompono la pipeline, ma vengono classificati e tracciati.

---

# 18. Decisioni ancora aperte

## 18.1 Nuovo runtime nello stesso repository o repository separato?

**Proposta:** stesso repository durante il refactoring.\
Eventuale separazione in repo nuovo solo dopo stabilizzazione del clean core.

## 18.2 Un DB fisico o più DB?

**Proposta:** un DB fisico iniziale con tabelle ben separate per bounded context.\
Separazione fisica successiva solo se emerge un motivo tecnico concreto.

## 18.3 Nome dei nuovi package

Possibili direzioni:

```text
src/runtime_v2/
src/domain/
src/application/
src/infrastructure/
```

Va scelto dopo aver definito il livello di Domain-Driven Design che si vuole realmente adottare.

## 18.4 Lifecycle state machine definitiva

Da definire in una specifica dedicata dopo il consolidamento del contratto parser → rules.

## 18.5 Executor target MVP

Da non decidere dentro questo documento.\
Il documento deve solo imporre il contratto neutro `ExecutionIntent`.

---

# 19. Deliverable successivi a questo documento

Dopo la chiusura del documento madre, produrre in ordine:

1. **PRD 01 — Intake, Raw Messages e Trader Resolution**
2. **PRD 02 — Parser V2 come parser ufficiale live + parser\_test consolidato**
3. **PRD 03 — Operation Rules Engine V2 su CanonicalMessageV2**
4. **PRD 04 — Lifecycle Engine e Signal State Model**
5. **PRD 05 — Execution Contract neutro e Adapter MVP**
6. **PRD 06 — Audit, Review Queue, Observability**

---

# 20. Decisione raccomandata di partenza

Prima di scrivere codice nuovo, chiudere questi tre punti:

1. confermare l’architettura target di questo documento;
2. fissare definitivamente il contratto `OperationalDecision`;
3. decidere la struttura dei package del nuovo runtime.

Solo dopo conviene aprire la prima fase implementativa.

