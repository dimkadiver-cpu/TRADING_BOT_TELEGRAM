# Documento madre di riprogettazione — TRADING_BOT_TELEGRAM

**Stato:** Revisione architetturale v0.2 dopo lettura di `docs/Raggionamento` e codebase.
**Scopo:** fissare una direzione di redesign verificata sul repository, prima di aprire il nuovo runtime.
**Ambito:** acquisizione messaggi Telegram, parser unico `parser_v2`, regole operative, lifecycle dei segnali/istruzioni, persistenza, audit e predisposizione a un execution adapter neutro.

---

# 0. Sintesi della revisione

La direzione della v0.1 è corretta: il nuovo runtime deve nascere come **clean core in parallelo**, non come ulteriore patch del router attuale.

La revisione aggiunge però quattro correzioni importanti:

1. `parser_v2` esiste già come pipeline autonoma e produce `CanonicalMessage` tipizzato con `schema_version = "canonical_message_v2"`; quindi il documento deve chiamarlo con il nome reale del contratto, non con un nuovo modello astratto scollegato dal codice.
2. `parser_test` esiste già come harness v2 con `parser_runs`, `parser_results_v2`, replay, report e risoluzione trader; va elevato a strumento ufficiale di qualità del nuovo runtime.
3. Il runtime live attuale è ancora centrato su `MessageRouter`, parser legacy `src/parser`, canonical v1, operation rules e execution Freqtrade nello stesso flusso; quindi va tenuto come baseline, ma non deve diventare il modello del clean core.
4. I promemoria in `docs/Raggionamento` spingono verso una separazione forte tra parser/core operativo/execution. La decisione aggiornata è: **iniziare con un unico DB fisico solo se accelera la migrazione locale, ma modellare da subito bounded context e tabelle come se parser DB e ops DB fossero separabili**.

---

# 1. Fonti lette e fatti verificati

## 1.1 Documenti letti

- `docs/Raggionamento/Hummingbot_execution_lifecycle_summary.md`
- `docs/Raggionamento/OPS_DB_Lifecycle_Workers_Summary.md`
- `docs/Raggionamento/PROMEMORIA_DB_parser_vs_operativo.md`
- `docs/Raggionamento/PROMEMORIA_new_operation_template_review.md`
- `docs/Raggionamento/PROMEMORIA_regole_policy_lifecycle_execution.md`
- `docs/Raggionamento/documento_madre_riprogettazione_trading_bot_telegram_v_0_1.md`
- `README.md`
- `src/parser_v2/README.md`

## 1.2 Codebase verificata

Aree ispezionate:

- `src/telegram/` — listener, ingestion, router, topic, effective trader, eligibility;
- `src/storage/` — raw messages, processing status, parse results, parser results v2, operational signals;
- `src/parser_v2/` — contratti, runtime, core, translation, profili trader;
- `parser_test/` — schema DB, replay parser v2, report, trader resolution;
- `src/operation_rules/` — engine corrente, loader, risk calculator;
- `src/execution/` — Freqtrade/exchange bridge, planner/applier, reconciliation;
- `src/target_resolver/` e `src/validation/` — layer attuali accoppiati al router.

## 1.3 Fatti di repository da rispettare

- Il parser v2 reale produce `src.parser_v2.contracts.canonical_message.CanonicalMessage`.
- Le classi messaggio reali sono `SIGNAL`, `UPDATE`, `REPORT`, `INFO`.
- Gli stati parse reali sono `PARSED`, `PARTIAL`, `UNCLASSIFIED`, `ERROR`.
- Il runtime reale è `UniversalParserRuntime` in `src/parser_v2/core/runtime.py`.
- Il contratto canonical v2 è strict Pydantic con `extra="forbid"`.
- `parser_test/db/schema.py` definisce già `raw_messages`, `parser_runs`, `parser_results_v2` e colonne di `resolved_trader_id`/`resolution_method`.
- `parser_test/scripts/trader_resolution.py` riusa `EffectiveTraderResolver`, `TelegramSourceTraderMapper`, alias e profili `parser_v2`.
- `src/telegram/router.py` importa ancora parser legacy, canonical v1, operation rules, target resolver, execution planner/applier e store operativi: è il punto principale da decomporre.
- `src/operation_rules/engine.py` oggi contiene logiche utili di policy/risk/sizing, ma accetta formati adattati/legacy e non deve essere copiato tale e quale nel nuovo core.

---

# 2. Obiettivo del ridisegno

Il progetto deve essere riprogettato a partire dall’esperienza accumulata, **senza trascinare nel nuovo runtime l’architettura ibrida attuale**.

Il nuovo sistema deve:

1. mantenere la logica valida di ascolto/acquisizione Telegram;
2. usare **solo `parser_v2`** come parser del runtime nuovo;
3. mantenere e consolidare **`parser_test`** come harness ufficiale di replay, sviluppo e regressione del parser;
4. rifare il layer delle **operation rules** affinché lavori direttamente sul contratto canonico di `parser_v2`;
5. introdurre un layer esplicito di **lifecycle / signal state management**;
6. predisporre un **execution adapter neutro**, senza accoppiare il dominio interno a Hummingbot, Freqtrade, OctoBot o altri motori;
7. semplificare la persistenza, distinguendo chiaramente:
   - messaggi acquisiti;
   - output parser;
   - decisioni operative;
   - stato dei segnali/istruzioni;
   - richieste/eventi di esecuzione;
   - audit/review.

---

# 3. Principio guida

## 3.1 Il runtime attuale non è lo scheletro del nuovo sistema

Il repository attuale va trattato come:

- **prototipo avanzato**;
- **fonte di logiche validate**;
- **fonte di test e casi reali**;
- **fonte di contratti/parsers riusabili selettivamente**;
- **baseline funzionante** per confrontare regressioni.

Non va trattato come uno scheletro da continuare a patchare.

## 3.2 Strategia adottata

La strategia raccomandata è:

> **Clean-core redesign in parallelo, nello stesso repository, con sostituzione progressiva dei moduli utili.**

Il vecchio runtime resta disponibile come baseline e riferimento.
Il nuovo runtime viene sviluppato con confini autonomi, contratti nuovi e responsabilità esplicite.

## 3.3 Vincolo anti-big-bang

Non sostituire tutto in un unico taglio. Procedere per vertical slice, mantenendo:

- confronto live/replay;
- audit delle decisioni;
- test automatici su contratti;
- possibilità di spegnere il nuovo runtime senza rompere la baseline.

---

# 4. Decisioni già assunte

## 4.1 Moduli da preservare e adattare

### 4.1.1 Telegram listening / ingestion

Da preservare:

- ascolto live dei messaggi Telegram;
- recupero in catchup/recovery;
- supporto canali e topic;
- deduplicazione;
- persistenza dei raw messages;
- configurazione canali/source mapping;
- hot reload della configurazione, se già stabile.

Il listener deve restare **sottile**: acquisisce, normalizza il contesto minimo e passa il messaggio alla pipeline. Non deve contenere parsing pesante, operation rules, lifecycle o logiche di esecuzione.

### 4.1.2 `parser_v2`

`parser_v2` è la base del nuovo parser live.
Non va più tenuto come componente solo da test/replay: nel nuovo sistema deve diventare **il parser unico e ufficiale**.

Vanno preservati:

- `UniversalParserRuntime`;
- `CanonicalMessage` da `src.parser_v2.contracts.canonical_message`;
- profili trader dedicati;
- semantic markers;
- disambiguazione locale;
- target hints / target binding;
- translator verso output canonico;
- contratti Pydantic strict.

### 4.1.3 `parser_test`

`parser_test` va mantenuto e consolidato come tool ufficiale per:

- importare/scaricare messaggi Telegram in DB di test;
- assegnare trader di default quando richiesto;
- risolvere `effective_trader_id` con logica compatibile con il live;
- selezionare i messaggi da parsare tramite filtro trader;
- selezionare il parser/profile in modo separato dal filtro dei messaggi;
- eseguire `parser_v2` su dati reali;
- salvare run e risultati in `parser_results_v2`;
- produrre CSV/report di qualità;
- confrontare replay e futuro live v2.

### 4.1.4 Concetto di operation rules

Va preservata l’idea di un layer che:

- decide se un messaggio/segnale è operabile;
- applica policy di rischio e sizing;
- applica configurazioni per trader;
- blocca o manda in review ciò che non soddisfa i criteri;
- produce uno snapshot delle regole usate.

Va invece **ripensata l’implementazione**, perché deve ricevere direttamente il `CanonicalMessage` di `parser_v2`, non adattarsi a formati legacy o intermedi.

### 4.1.5 Logiche execution già validate

I moduli in `src/execution/` contengono conoscenza utile su:

- Freqtrade bridge;
- planner/applier di update;
- market entry dispatch;
- order reconciliation;
- protective orders;
- dynamic pairlist.

Queste logiche sono patrimonio tecnico, ma nel nuovo runtime vanno rientrate dietro una **porta di execution neutra**. Il core non deve dipendere da Freqtrade né da Hummingbot.

---

# 5. Cosa viene abbandonato nel nuovo runtime

## 5.1 Parser legacy `src/parser`

Nel nuovo runtime non deve più esistere un dual-stack tra:

- parser vecchio;
- `ParsedMessage` intermedio legacy;
- canonical v1;
- `parser_v2`.

Il nuovo percorso ufficiale deve essere:

```text
Raw message → parser_v2 → CanonicalMessage
```

Dove `CanonicalMessage.schema_version == "canonical_message_v2"`.

## 5.2 `MessageRouter` monolitico attuale

L’attuale router concentra responsabilità eterogenee:

- trader resolution;
- eligibility;
- parsing;
- persistenza parser;
- normalizzazione/canonicalizzazione;
- operation rules;
- target resolving;
- update runtime;
- persistenza segnali operativi;
- integrazione verso execution.

Nel nuovo progetto questa centralizzazione va eliminata. Il router attuale resta una baseline da cui estrarre casi e test, non un pattern da replicare.

## 5.3 Bridge e compatibilità temporanee

Da non portare nel nuovo sistema, salvo strumenti di migrazione isolati:

- shadow normalizer;
- dual-stack parser;
- conversioni verso canonical v1;
- adattatori temporanei costruiti solo per tenere vivo il vecchio runtime;
- payload operativi basati su dict non tipizzati quando esiste un contratto Pydantic.

## 5.4 Accoppiamento precoce con execution engine

Il nuovo core non va costruito “per Hummingbot”, “per Freqtrade” o “per OctoBot”.
L’esecuzione deve entrare tramite **porta/adattatore**.

---

# 6. Architettura target — vista d’insieme

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
Canonical Message Store
        ↓
Operation Rules Engine V2
        ↓
Operational Decision Store
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

# 7. Responsabilità dei layer

## 7.1 Telegram Listener

**Responsabilità:** ricevere messaggi Telegram live o recuperarli in recovery.

**Fa:**

- legge evento Telegram;
- rileva chat, topic, message id, reply id, media metadata;
- inoltra a ingestion;
- accoda il messaggio per il processing.

**Non fa:**

- parsing;
- operation rules;
- lifecycle;
- esecuzione;
- accesso a modelli di trading.

## 7.2 Raw Message Ingestion

**Responsabilità:** rendere persistente il dato grezzo e tracciabile.

**Output:** `RawMessageEnvelope` persistito in DB.

**Fa:**

- deduplicazione;
- salvataggio raw;
- stato acquisizione/processing;
- metadata di media, topic, chat, trader hint;
- idempotenza su `(source_chat_id, telegram_message_id)`.

**Non fa:**

- scegliere il parser;
- trasformare in segnale;
- applicare regole operative.

## 7.3 Trader Resolution

**Responsabilità:** determinare il trader effettivo a cui associare il messaggio.

**Input:** `RawMessageEnvelope` + source mapping + alias + contesto reply/ref.
**Output:** `ResolvedTraderContext`.

Deve restare concettualmente condiviso tra live e `parser_test`, perché il repository ha già dimostrato il rischio di divergenza tra:

- trader del messaggio da filtrare;
- profilo parser da applicare.

## 7.4 Parser V2 Runtime

**Responsabilità:** interpretare linguisticamente il messaggio.

**Input:** testo + `ParserContext`/raw context + profilo trader.
**Output:** `CanonicalMessage` (`schema_version = "canonical_message_v2"`).

**Fa:**

- normalizzazione testo;
- marker matching;
- evidenze semantiche;
- estrazione signal/update/report/info;
- disambiguazione;
- target hints;
- target binding;
- traduzione in contratto canonico tipizzato.

**Non fa:**

- decidere se il segnale è operabile;
- calcolare position sizing;
- modificare lo stato operativo del trade;
- inviare ordini.

## 7.5 Operation Rules Engine V2

**Responsabilità:** trasformare un messaggio canonico in una decisione operativa.

**Input:** `CanonicalMessage` + regole globali/trader + eventuale stato operativo letto solo per gate/policy.
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
- invio comandi a exchange;
- risoluzione definitiva dello stato lifecycle.

## 7.6 Lifecycle Engine

**Responsabilità:** applicare decisioni e messaggi canonici allo stato di segnali, setup, ordini logici, posizioni e istruzioni.

**Input:**

- `CanonicalMessage`;
- `OperationalDecision`;
- stato attuale del dominio;
- eventuali eventi executor/exchange normalizzati.

**Output:**

- `LifecycleCommand`;
- `DomainEvent`;
- nuove snapshot di stato;
- eventuale `ExecutionIntent`.

**Fa:**

- creare un setup/segnale operativo;
- applicare update targetizzati;
- registrare report;
- gestire invalidazioni/cancellazioni;
- mantenere la storia degli eventi;
- preparare comandi neutri verso l’execution layer.

**Non fa:**

- parsing;
- accesso diretto alla rete exchange;
- calcolo semantico dei marker;
- policy risk iniziale già decisa dalle operation rules.

## 7.7 Execution Gateway / Adapter

**Responsabilità:** tradurre comandi interni neutri in comandi dell’esecutore scelto.

**Input:** `ExecutionIntent`.
**Output:** richieste verso Hummingbot / Freqtrade / exchange API + eventi di risposta normalizzati.

Deve essere neutro rispetto al dominio interno.

Esempi di intent:

- submit new signal;
- place pending entry;
- cancel pending entry;
- move stop;
- close partial;
- close full;
- sync order/position status.

---

# 8. Contratti centrali da definire

## 8.1 `RawMessageEnvelope`

Bozza coerente con gli store attuali e con `parser_test`:

```python
class RawMessageEnvelope:
    raw_message_id: int
    source_chat_id: str
    source_chat_title: str | None
    source_type: str | None
    source_topic_id: int | None
    telegram_message_id: int
    reply_to_message_id: int | None
    raw_text: str | None
    message_ts: datetime
    acquired_at: datetime
    acquisition_mode: Literal["live", "catchup", "import"]
    acquisition_status: str
    processing_status: str | None
    source_trader_id: str | None
    resolved_trader_id: str | None
    resolution_method: str | None
    has_media: bool
    media_kind: str | None
    media_mime_type: str | None
    media_filename: str | None
```

## 8.2 `CanonicalMessage`

Il contratto di riferimento è:

```python
src.parser_v2.contracts.canonical_message.CanonicalMessage
```

Classi principali:

- `SIGNAL`
- `UPDATE`
- `REPORT`
- `INFO`

Stati parser:

- `PARSED`
- `PARTIAL`
- `UNCLASSIFIED`
- `ERROR`

### Regola di sistema

Il parser deve produrre output **schema-valid** anche quando il messaggio non è eseguibile.
La non eseguibilità va espressa con `parse_status`, warning, diagnostics o successiva decisione delle operation rules, non rompendo il contratto.

## 8.3 `OperationalDecision`

Bozza di contratto:

```python
class OperationalDecision:
    decision_id: int
    canonical_message_id: int
    raw_message_id: int
    trader_id: str
    decision_type: Literal["ACCEPT", "BLOCK", "REVIEW", "IGNORE", "LOG_ONLY"]
    decision_scope: Literal["SIGNAL", "UPDATE", "REPORT", "INFO"]
    reason_code: str | None
    warnings: list[str]
    applied_rules: list[str]
    policy_snapshot: dict
    risk_decision: RiskDecision | None
    idempotency_key: str
    created_at: datetime
```

### Esempi

#### Segnale accettato

```text
ACCEPT
risk computed or deferred
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

#### Info/report solo log

```text
LOG_ONLY
reason = informational_message | report_without_lifecycle_effect
```

## 8.4 `RiskDecision`

```python
class RiskDecision:
    sizing_mode: Literal["IMMEDIATE", "DEFERRED", "NOT_APPLICABLE"]
    risk_pct_of_capital: float | None
    risk_budget_usdt: float | None
    position_size_usdt: float | None
    leverage: float | None
    entry_split: dict[str, float] | None
    deferred_reason: str | None
```

### Nota MARKET

Per entry MARKET senza prezzo affidabile, il sistema deve poter produrre:

```text
sizing_mode = DEFERRED
```

Il sizing finale verrà completato quando l’entry price diventa disponibile secondo la policy definita.

## 8.5 `LifecycleCommand`

```python
class LifecycleCommand:
    command_id: int
    command_type: Literal[
        "CREATE_SETUP",
        "APPLY_UPDATE",
        "REGISTER_REPORT",
        "INVALIDATE_SETUP",
        "SEND_TO_REVIEW",
        "IGNORE",
    ]
    signal_root_id: int | None
    target_ref: dict | None
    payload: dict
    idempotency_key: str
    created_at: datetime
```

## 8.6 `ExecutionIntent`

```python
class ExecutionIntent:
    intent_id: int
    intent_type: Literal[
        "SUBMIT_NEW_SIGNAL",
        "PLACE_ENTRY",
        "PLACE_STOP",
        "PLACE_TAKE_PROFITS",
        "CANCEL_PENDING_ENTRY",
        "MOVE_STOP",
        "CLOSE_PARTIAL",
        "CLOSE_FULL",
        "SYNC_STATE",
    ]
    signal_root_id: int
    attempt_key: str
    payload: dict
    created_at: datetime
```

---

# 9. Data stores e persistenza — decisione aggiornata

## 9.1 Principio

Separare le famiglie di dati:

1. **Acquisition / Raw layer**;
2. **Parser / Canonical layer**;
3. **Operational decision layer**;
4. **Lifecycle state layer**;
5. **Execution state / sync layer**;
6. **Audit / Review layer**.

## 9.2 Decisione pratica

I documenti di ragionamento propongono anche la separazione fisica `parser_db` / `ops_db`. Questa è architetturalmente corretta perché parser e operativo hanno rischi diversi.

Decisione per la prima implementazione:

- modellare da subito tabelle e repository come bounded context separati;
- evitare foreign key/coupling non necessari tra parser e operativo;
- consentire un unico SQLite locale per sviluppo e test;
- non bloccare la futura separazione fisica in `parser_db` e `ops_db`.

Quindi: **separazione logica obbligatoria, separazione fisica rimandabile ma non ostacolata dal design**.

## 9.3 Tabelle minime suggerite

### Raw / intake

- `raw_messages`
- `message_processing_jobs` o `message_processing_status`

### Parser

- `parser_runs` — replay/test
- `parser_results_v2` — harness parser_test
- `canonical_messages` — output canonical live e/o replay normalizzato

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

# 10. Flussi principali

## 10.1 Flusso live — nuovo messaggio

```text
1. TelegramListener riceve messaggio
2. RawMessageIngestion salva raw_messages
3. Dispatcher apre job di processing
4. TraderResolver risolve il trader effettivo
5. ParserV2 produce CanonicalMessage
6. Persist canonical output
7. OperationRulesEngineV2 produce OperationalDecision
8. Persist operational decision
9. LifecycleEngine applica la decisione
10. Se necessario produce ExecutionIntent
11. ExecutionAdapter invia al motore esterno
12. Audit trail aggiornato
```

## 10.2 Flusso `parser_test`

```text
1. Import/scarico messaggi Telegram in raw_messages del DB test
2. Risoluzione effective_trader_id
3. Selezione messaggi tramite trader_filter
4. Selezione parser/profile tramite parser_system / parser_profile
5. ParserV2 produce CanonicalMessage
6. Salvataggio in parser_results_v2
7. Export CSV / report qualità
```

Regola: **filtro del trader dei messaggi** e **profilo parser da applicare** devono restare concetti separati.

## 10.3 Flusso update operativo

```text
1. ParserV2 classifica UPDATE
2. Target hints / targeted actions vengono prodotti nel CanonicalMessage
3. OperationRules decide se l’update è ammissibile / bloccato / review
4. LifecycleEngine risolve target nello stato operativo
5. Applica modifica: SL, cancel pending, close partial, close full, modify entry, modify TP...
6. Genera eventuale ExecutionIntent
```

## 10.4 Flusso eventi executor/exchange

```text
1. ExecutionAdapter riceve ack/fill/reject/sync state
2. Normalizza in ExecutionEvent
3. Persist execution_events
4. LifecycleEngine aggiorna signal/order/position state
5. Produce eventuali nuovi LifecycleCommand o review
6. Audit trail aggiornato
```

---

# 11. Boundary decisivi tra i layer

## 11.1 Parser vs Operation Rules

| Tema                               | Parser | Operation Rules |
| ---------------------------------- | ------ | --------------- |
| Riconosce MOVE_STOP                | Sì     | No              |
| Decide se MOVE_STOP è applicabile  | No     | Sì              |
| Estrae entry/SL/TP                 | Sì     | No              |
| Calcola risk/sizing                | No     | Sì              |
| Produce schema-valid output        | Sì     | No              |
| Decide block/review                | No     | Sì              |
| Legge testo grezzo per policy      | No     | No              |

## 11.2 Operation Rules vs Lifecycle

| Tema                                              | Operation Rules | Lifecycle |
| ------------------------------------------------- | --------------- | --------- |
| Decide se segnale è operabile                     | Sì              | No        |
| Calcola size / risk                               | Sì              | No        |
| Crea lo stato del segnale                         | No              | Sì        |
| Applica update a un setup già noto                | No              | Sì        |
| Verifica target del messaggio rispetto allo stato | Solo pre-check  | Sì        |
| Decide idempotenza degli eventi dominio           | No              | Sì        |

## 11.3 Lifecycle vs Execution

| Tema                               | Lifecycle | Execution                    |
| ---------------------------------- | --------- | ---------------------------- |
| Decide cosa deve succedere         | Sì        | No                           |
| Traduce in ordine API specifico    | No        | Sì                           |
| Tiene stato dominio                | Sì        | No, salvo sync/cache tecnica |
| Gestisce ritardi/ack dell’executor | Coordina  | Comunica eventi              |
| Decide policy/risk                 | No        | No                           |

---

# 12. Operation Rules V2 — direzione di redesign

## 12.1 Input corretto

L’engine deve ricevere:

```python
CanonicalMessage from src.parser_v2.contracts.canonical_message
```

Non:

- `TraderParseResult` legacy;
- `CanonicalMessage v1` del parser precedente;
- payload dict non tipizzati;
- testo grezzo da interpretare.

## 12.2 Classificazione operativa

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

## 12.3 Riuso ammesso dal codice attuale

Da `src/operation_rules/` si possono recuperare:

- calcoli risk/sizing già testati;
- schema mentale delle policy per trader;
- configurazione YAML e snapshot;
- casi limite già coperti dai test.

Non si deve recuperare:

- dipendenza da modelli legacy;
- conversioni ad hoc;
- accoppiamento al router attuale.

---

# 13. Lifecycle Engine — direzione di progettazione

Il lifecycle è il blocco che manca come responsabilità autonoma.

Deve occuparsi di:

- setup iniziale;
- distinzione tra setup, ordine pending, posizione aperta, posizione parzialmente chiusa, chiusa, invalidata;
- applicazione degli update Telegram;
- reazione a eventi di mercato/esecuzione;
- idempotenza di update e command;
- emissione di comandi neutri verso executor.

## 13.1 Esempi di stati dominio

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
ERROR
```

Questi stati sono preliminari e vanno validati in una specifica dedicata.

## 13.2 Regola chiave su target/update

Il parser può indicare target hints e targeted actions.
La risoluzione definitiva contro lo stato operativo spetta al lifecycle, perché solo il lifecycle sa quali chain sono aperte, pending, chiuse, parzialmente fillate o già invalidate.

---

# 14. Execution Layer — principio di neutralità

Il core deve esporre un contratto neutro.
Hummingbot, Freqtrade o un adapter exchange diretto sono implementazioni possibili, ma non devono entrare nei modelli di dominio.

## 14.1 Vincolo

L’autorità su:

- segnali;
- policy;
- risk sizing;
- decisioni operative;
- lifecycle;

resta nel progetto.
L’executor esegue istruzioni e restituisce eventi/stato.

## 14.2 Nota Hummingbot/Freqtrade

I documenti di ragionamento su Hummingbot sono coerenti con questa direzione: Hummingbot deve essere motore di esecuzione/monitoraggio, non cervello decisionale. Lo stesso vale per Freqtrade nel codice attuale.

---

# 15. Package target raccomandati

Decisione proposta per evitare un Domain-Driven Design eccessivo ma separare bene le responsabilità:

```text
src/runtime_v2/
    intake/
    trader_resolution/
    parser_pipeline/
    operation_rules/
    lifecycle/
    execution_gateway/
    audit/
    persistence/
```

Regole:

- `src/parser_v2/` resta dove si trova: è un modulo già autonomo e testato;
- `parser_test/` resta tool separato, ma deve usare lo stesso parser runtime del live;
- `src/runtime_v2/` orchestra il nuovo flusso senza importare `src/telegram/router.py`;
- eventuali adapter verso vecchi store devono stare in `runtime_v2/persistence` o in moduli di migrazione, non nel dominio.

---

# 16. Metodo di sviluppo consigliato

## 16.1 Governo del progetto

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

## 16.2 Regola operativa

Non iniziare lo sviluppo di un blocco finché non sono definiti:

1. responsabilità;
2. input/output;
3. contratti;
4. casi principali;
5. test minimi di accettazione;
6. criterio di migrazione dal runtime attuale.

---

# 17. Roadmap di realizzazione proposta

## Fase A — Documento madre e decisioni architetturali

**Output:** questo documento consolidato.

Obiettivi:

- fissare cosa si salva e cosa si abbandona;
- fissare architettura target;
- fissare contratti principali;
- fissare la strategia di sviluppo;
- allineare la terminologia al codice reale.

## Fase B — Specifica intake + trader resolution

**Output:** PRD dedicato.

Obiettivi:

- definire `RawMessageEnvelope`;
- formalizzare risoluzione trader;
- chiarire allineamento live/`parser_test`;
- decidere schema raw persistente nuovo;
- definire idempotenza job/processing.

## Fase C — Consolidamento `parser_v2` come parser ufficiale

**Output:** `parser_v2` stabilizzato e contratto blindato.

Obiettivi:

- chiudere gap aperti del parser v2;
- fissare comportamento multi-ref e priorità dei riferimenti;
- garantire parser output schema-valid;
- aggiornare `parser_test` come harness ufficiale;
- aggiungere test di compatibilità live/replay sui contratti.

## Fase D — Operation Rules Engine V2

**Output:** nuovo engine basato su `CanonicalMessage` v2.

Obiettivi:

- definire `OperationalDecision`;
- rifare risk/sizing/policy gate;
- supportare MARKET deferred sizing;
- gestire UPDATE/REPORT/INFO a livello decisionale;
- separare completamente policy da lifecycle.

## Fase E — Lifecycle Engine

**Output:** stato operativo coerente e command model.

Obiettivi:

- definire state machine;
- definire applicazione update;
- gestire setup/order/position state;
- produrre `LifecycleCommand` ed `ExecutionIntent`;
- gestire eventi executor/exchange normalizzati.

## Fase F — Execution Adapter

**Output:** prima integrazione esterna controllata.

Obiettivi:

- definire adapter neutro;
- implementare una strategia MVP verso il motore scelto;
- ricevere eventi/stati di ritorno;
- testare sync, retry, idempotenza e gestione degli errori.

## Fase G — Observability, audit, review queue

**Output:** sistema leggibile e diagnosticabile.

Obiettivi:

- audit eventi;
- review queue;
- report parser / operation / lifecycle;
- strumenti per debugging e comparazione replay/live.

---

# 18. Prima vertical slice da sviluppare

La prima fetta end-to-end deve essere:

```text
Raw message già presente o importato
→ trader resolution
→ parser_v2
→ persistence CanonicalMessage
→ operation rules v2 minimale
→ OperationalDecision persistita
→ audit minimo
```

Questa slice serve a validare:

- i confini dei layer;
- il contratto parser → rules;
- lo schema DB;
- la qualità del flusso live/replay;
- l’assenza di dipendenza dal `MessageRouter` monolitico.

Non deve ancora includere:

- esecuzione exchange;
- lifecycle completo;
- gestione posizione avanzata;
- dashboard.

## 18.1 Criteri di accettazione della prima slice

1. Un raw message con trader risolto produce un `CanonicalMessage` v2 persistito.
2. Un messaggio non eseguibile resta schema-valid e produce `OperationalDecision` `BLOCK`, `REVIEW`, `IGNORE` o `LOG_ONLY`.
3. `parser_test` e runtime v2 usano lo stesso `UniversalParserRuntime` e lo stesso contratto canonical.
4. Nessun codice della slice importa `src/telegram/router.py`.
5. La decisione operativa contiene policy snapshot, warnings e reason code tracciabili.

---

# 19. Criteri di qualità del nuovo design

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
10. i casi non eseguibili non rompono la pipeline, ma vengono classificati e tracciati;
11. replay e live sono comparabili almeno su raw input, trader resolution, canonical output e decisione operativa;
12. la futura separazione fisica parser DB / ops DB resta possibile.

---

# 20. Decisioni ancora aperte

## 20.1 Un DB fisico o più DB?

**Decisione provvisoria:** separazione logica obbligatoria; separazione fisica rimandata.
Il design deve poter evolvere verso `parser_db` e `ops_db` senza riscrivere il dominio.

## 20.2 Nome definitivo dei nuovi package

**Proposta:** `src/runtime_v2/` con sottopackage per bounded context leggeri.

Da confermare prima della prima implementazione.

## 20.3 Lifecycle state machine definitiva

Da definire in una specifica dedicata dopo il consolidamento del contratto parser → rules.

## 20.4 Executor target MVP

Da non decidere dentro questo documento.
Il documento impone solo il contratto neutro `ExecutionIntent`.

## 20.5 Strategia di migrazione dati

Da definire dopo la prima vertical slice:

- quali tabelle legacy leggere;
- quali dati convertire;
- quali store lasciare storici;
- quali report usare per validare la migrazione.

---

# 21. Deliverable successivi a questo documento

Dopo la chiusura del documento madre, produrre in ordine:

1. **PRD 01 — Intake, Raw Messages e Trader Resolution**
2. **PRD 02 — Parser V2 come parser ufficiale live + parser_test consolidato**
3. **PRD 03 — Operation Rules Engine V2 su CanonicalMessage v2**
4. **PRD 04 — Lifecycle Engine e Signal State Model**
5. **PRD 05 — Execution Contract neutro e Adapter MVP**
6. **PRD 06 — Audit, Review Queue, Observability**
7. **Piano migrazione — dal MessageRouter monolitico a runtime_v2**

---

# 22. Decisione raccomandata di partenza

Prima di scrivere codice nuovo, chiudere questi tre punti:

1. confermare `src/runtime_v2/` come package del clean core;
2. fissare definitivamente il contratto `OperationalDecision`;
3. scrivere il PRD 01 su intake/trader resolution con test di accettazione.

Solo dopo conviene aprire la prima fase implementativa.
