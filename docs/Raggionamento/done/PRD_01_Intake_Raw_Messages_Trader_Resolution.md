# PRD 01 - Intake, Raw Messages e Trader Resolution

**Stato:** bozza operativa per il clean core `runtime_v2`.
**Deriva da:** `docs/Raggionamento/documento_madre_riprogettazione_trading_bot_telegram_v_0_1.md` v0.2.
**Ambito:** acquisizione messaggi Telegram, persistenza raw, job di processing, risoluzione trader effettivo, allineamento live / `parser_test`.
**Fuori ambito:** parsing linguistico, operation rules, lifecycle operativo, execution adapter, migrazione completa dal `MessageRouter`.

---

## 1. Scopo

Questo PRD definisce il primo blocco del nuovo runtime:

```text
Telegram event / imported message
        ->
RawMessageEnvelope persistito
        ->
Processing job idempotente
        ->
ResolvedTraderContext
        ->
input pronto per parser_v2
```

L'obiettivo e' separare in modo esplicito cio' che oggi e' disperso tra `TelegramListener`, `RawMessageIngestionService`, `RawMessageStore`, `EffectiveTraderResolver`, `MessageEligibilityEvaluator` e `MessageRouter`.

Il risultato della fase non deve ancora parsare con `parser_v2`: deve consegnare un raw message persistito e un trader effettivo tracciabile, con gli stessi criteri usabili dal live e da `parser_test`.

---

## 2. Contesto verificato nel repository

### 2.1 Componenti attuali da preservare

- `src/telegram/listener.py` riceve messaggi live, fa recovery, supporta topic e accoda item al worker.
- `src/telegram/ingestion.py` contiene `TelegramIncomingMessage` e `RawMessageIngestionService`.
- `src/storage/raw_messages.py` salva raw messages con dedup su `(source_chat_id, telegram_message_id)`.
- `src/storage/processing_status.py` gestisce lo stato di processing dei raw messages.
- `src/telegram/effective_trader.py` risolve il trader da alias nel testo, reply-chain e fallback sorgente.
- `src/telegram/trader_mapping.py` risolve mapping da chat id, username, title e alias.
- `src/telegram/eligibility.py` distingue short update senza strong link e strong link via reply, link Telegram o ref esplicito.
- `parser_test/scripts/trader_resolution.py` riusa `EffectiveTraderResolver`, `TelegramSourceTraderMapper` e i profili `parser_v2`.
- `parser_test/db/schema.py` ha gia' `raw_messages.resolved_trader_id` e `resolution_method`.

### 2.2 Limiti attuali da correggere

- Il live passa rapidamente dal listener al `MessageRouter`, che oggi concentra anche parsing, persistence parser, operation rules, target resolver ed execution.
- Nel DB live `raw_messages` non ha ancora il contratto completo gia' consolidato in `parser_test`, in particolare `resolved_trader_id` e `resolution_method`.
- La nozione di `acquisition_mode` vive negli item di queue e nei contesti parser legacy, ma non e' persistita nel raw store live.
- `source_trader_id`, `resolved_trader_id`, `trader_filter` e `parser_profile` sono concetti gia' separati in `parser_test`; il live deve adottare la stessa distinzione.
- L'idempotenza e' forte sull'acquisizione raw, ma il job di processing non e' ancora un bounded context autonomo.

---

## 3. Obiettivi

1. Definire `RawMessageEnvelope` come contratto unico di intake.
2. Rendere persistenti i metadati minimi necessari a replay, audit e parser context.
3. Separare acquisizione raw, eligibility iniziale, job processing e trader resolution.
4. Riutilizzare una sola logica di trader resolution tra live e `parser_test`.
5. Rendere idempotente la processing pipeline senza importare `src/telegram/router.py`.
6. Preparare l'input per `parser_v2` senza dipendere dal parser legacy o da canonical v1.

---

## 4. Non obiettivi

- Non definire il contratto `CanonicalMessage`: appartiene al PRD 02.
- Non decidere policy operative, sizing o review di trading: appartengono al PRD 03.
- Non creare lifecycle state machine: appartiene al PRD 04.
- Non inviare ordini o generare execution intent.
- Non riscrivere il listener Telethon se il comportamento attuale resta sufficiente.
- Non eliminare il vecchio runtime in questa fase.

---

## 5. Decisioni di design

### 5.1 Il listener resta sottile

Il listener deve:

- ricevere evento Telegram live;
- eseguire recovery/catchup;
- estrarre chat, topic, message id, reply id, timestamp, testo e media metadata;
- applicare solo filtri di acquisizione evidenti, come canale/topic non attivo e media-only senza testo;
- chiamare intake;
- creare o riattivare un job di processing.

Il listener non deve:

- scegliere parser;
- interpretare semanticamente il testo;
- applicare operation rules;
- aggiornare stato operativo di segnali;
- chiamare execution.

### 5.2 Blacklist ed eligibility appartengono al bordo intake/job

La blacklist di rumore configurato puo' essere applicata prima del job parser, ma il messaggio resta persistito in `raw_messages` con stato tracciabile.

L'eligibility iniziale non decide se un segnale e' tradable. Decide solo se un messaggio puo' passare alla fase parser o deve andare in review/log per contesto insufficiente, ad esempio short update senza reply/link/ref forte.

### 5.3 Trader resolution: config-driven first, then algorithm

Il resolver deve essere lo stesso concetto per live e `parser_test`.

**Strategia raccomandata per live:**

```
1. Lookup channels.yaml per chat_id (e topic_id se presente)
   → Se entry ha trader_id valorizzato
      → Usa direttamente, metodo="source_chat_id" o "source_topic_config"
      → FINE (risolto)
   → Se entry NON ha trader_id (multi-trader)
      → Prosegui a step 2

2. Chiama EffectiveTraderResolver con questa priorità:
   a) alias/tag esplicito nel testo del messaggio
   b) trader già risolto o source_trader_id nella reply-chain
   c) alias/tag nel testo degli antenati della reply-chain
   → Se risolto
      → Usa il risultato
      → FINE
   → Se non risolto
      → Vai a step 3

3. Unresolved
   → processing_status=review
   → reason="unresolved_trader"
```

Un risultato ambiguo e' diverso da un risultato assente: entrambi non passano al parser live, ma devono avere `resolution_method` e reason distinguibili.

**Strategia per parser_test:**

```
1. Se --default-source-trader fornito
   → Assegna source_trader_id = --default-source-trader
   → Assegna resolved_trader_id = normalized(--default-source-trader)
   → Metodo = "source_trader_id"
   → FINE

2. Altrimenti, per ogni raw_message:
   a) Se source_trader_id già valorizzato (da import precedente)
      → Usa direttamente, metodo="source_trader_id"
      → FINE
   
   b) Se NO, chiama EffectiveTraderResolver (stessa priorità del live):
      - alias nel testo
      - reply-chain
      - alias negli antenati
      → Se risolto → usa quello
      → Se non risolto → prosegui a step c
   
   c) Se --assume-trader fornito (fallback SOLO parser_test)
      → Usa --assume-trader, metodo="assume_trader"
      → FINE
   
   d) Altrimenti
      → resolved_trader_id=NULL, metodo="unresolved"
```

### 5.4 Filtro messaggi e profilo parser restano separati

Il PRD recepisce la regola di `parser_test`:

| Concetto | Responsabilita' |
|---|---|
| `source_trader_id` | Trader suggerito dalla sorgente/import/config |
| `resolved_trader_id` | Trader effettivo deciso dal resolver |
| `trader_filter` | Quali messaggi includere in replay/report |
| `parser_profile` | Quale profilo `parser_v2` applicare |

**Regola di derivazione `parser_profile`:**

Il `parser_profile` e' uguale a `resolved_trader_id` per default.
Puo' essere sovrascritto esplicitamente per una sorgente specifica in `channels.yaml` tramite il campo `parser_profile`.

Se il profilo derivato non e' registrato in `parser_v2`, il messaggio va in `processing_status=review` con reason `no_parser_profile`.

Nel live il `parser_profile` iniziale sara' normalmente uguale a `resolved_trader_id`, ma il modello non deve fondere i due concetti.

### 5.5 Idempotenza per livelli

L'idempotenza minima e':

- acquisizione raw: `(source_chat_id, telegram_message_id)`;
- topic-aware query/recovery: `(source_chat_id, source_topic_id, telegram_message_id)`;
- processing job: un job attivo o completato per `raw_message_id` e pipeline version;
- trader resolution: ultima risoluzione persistita con metodo e timestamp, ricalcolabile in modo esplicito.

### 5.6 Scope di `processing_status`

`processing_status` traccia la fase di intake del `runtime_v2`, non la pipeline completa.

`done` significa: trader risolto, `ParserDispatchCandidate` prodotto, intake completato.

Lo stato del parsing (`parser_v2`) e' tracciato separatamente in `parser_results_v2`. Il PRD 02 definisce il proprio ciclo di vita.

---

## 6. Contratti

### 6.1 `RawMessageEnvelope`

Contratto logico del dato acquisito:

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
    acquisition_status: Literal[
        "ACQUIRED",
        "BLACKLISTED",
        "MEDIA_ONLY_SKIPPED",
    ]
    processing_status: Literal[
        "pending",
        "processing",
        "done",
        "failed",
        "blacklisted",
        "review",
        "skipped",
    ]
    source_trader_id: str | None
    resolved_trader_id: str | None
    resolution_method: str | None
    resolution_detail: str | None
    has_media: bool
    media_kind: str | None
    media_mime_type: str | None
    media_filename: str | None
```

**Regola:** `acquisition_status` e' immutabile — viene impostato una sola volta al momento dell'ingest e non cambia mai. `processing_status` e' l'unico campo mutabile che traccia l'avanzamento della pipeline di intake.

Se il salvataggio raw fallisce con eccezione, nessun record viene creato e l'errore viene loggato esternamente senza persistere `acquisition_status`.

Nota: `media_blob` puo' restare storage-level e non deve entrare per forza nel contratto che passa ai layer successivi.

### 6.2 `ProcessingJob`

Il job separa lo stato di acquisizione dallo stato della pipeline:

```python
class ProcessingJob:
    job_id: int
    raw_message_id: int
    pipeline_name: Literal["runtime_v2_intake"]
    pipeline_version: str
    status: Literal["queued", "processing", "done", "failed", "review", "skipped"]
    attempt_count: int
    locked_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
```

MVP ammesso: continuare a usare `raw_messages.processing_status`, purche' il codice sia disegnato come se il job fosse estraibile in tabella dedicata.

### 6.3 `ResolvedTraderContext`

```python
class ResolvedTraderContext:
    raw_message_id: int
    trader_id: str | None
    method: Literal[
        "content_alias",
        "content_alias_ambiguous",
        "reply_chain",
        "reply_chain_alias",
        "source_chat_id",
        "source_chat_username",
        "source_chat_title",
        "source_topic_config",
        "assume_trader",
        "unresolved",
    ]
    detail: str | None
    is_ambiguous: bool
    resolved_at: datetime
```

Regola: `assume_trader` e' ammesso nei tool `parser_test`, ma nel live deve essere configurato esplicitamente e visibile come fallback, non implicito.

### 6.4 `ParserDispatchCandidate`

Output di questa fase verso il PRD 02:

```python
class ParserDispatchCandidate:
    raw_message: RawMessageEnvelope
    resolved_trader: ResolvedTraderContext
    parser_profile: str
    parser_context: ParserContext  # da src.parser_v2.contracts.parser_context
```

Questo oggetto non contiene `CanonicalMessage` e non chiama `parser_v2`.

---

## 7. Persistenza

### 7.1 Schema raw live target

La tabella live `raw_messages` deve convergere verso il superset utile gia' presente in `parser_test`:

```sql
raw_message_id INTEGER PRIMARY KEY
source_chat_id TEXT NOT NULL
source_chat_title TEXT
source_type TEXT
source_trader_id TEXT
source_topic_id INTEGER
telegram_message_id INTEGER NOT NULL
reply_to_message_id INTEGER
raw_text TEXT
message_ts TEXT NOT NULL
acquired_at TEXT NOT NULL
acquisition_mode TEXT NOT NULL DEFAULT 'live'
acquisition_status TEXT NOT NULL
processing_status TEXT NOT NULL DEFAULT 'pending'
resolved_trader_id TEXT
resolution_method TEXT
resolution_detail TEXT
has_media INTEGER NOT NULL DEFAULT 0
media_kind TEXT
media_mime_type TEXT
media_filename TEXT
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
```

Indici minimi:

```sql
UNIQUE(source_chat_id, telegram_message_id)
INDEX(source_chat_id, source_topic_id, telegram_message_id)
INDEX(processing_status)
INDEX(resolved_trader_id)
INDEX(message_ts)
```

### 7.2 Separazione parser DB / ops DB

Per questa fase la separazione fisica non e' obbligatoria.

Vincolo: il codice deve poter usare lo stesso contratto su:

- DB live locale;
- DB `parser_test`;
- futura separazione fisica `parser_db`.

### 7.3 Migrazione dati

La futura implementazione deve aggiungere colonne mancanti con migrazioni additive. Non deve riscrivere o cancellare dati raw esistenti.

Colonne candidate da aggiungere al live:

- `acquisition_mode`;
- `resolved_trader_id`;
- `resolution_method`;
- `resolution_detail`.

---

## 8. Flussi

### 8.1 Live - nuovo messaggio

```text
1.  TelegramListener riceve evento.
2.  Estrae chat/topic/message/reply/timestamp/testo/media metadata.
3.  Scarta senza persistere solo eventi fuori config o media-only senza caption, con log.
4.  Raw intake salva o ritrova raw_message_id via dedup.
5.  Se blacklistato:
    → acquisition_status = BLACKLISTED
    → processing_status = blacklisted
    → FINE
6.  Se media-only senza testo:
    → acquisition_status = MEDIA_ONLY_SKIPPED
    → processing_status = skipped
    → FINE
7.  acquisition_status = ACQUIRED
    processing_status = pending
    Crea job.
8.  Worker prende il job.
9.  Eligibility iniziale valuta strong link per short update.
    → Se short update senza strong link:
       processing_status = review
       reason = "short_update_without_strong_link"
       → FINE intake
10. TraderResolver:
    10a. Lookup channels.yaml per (source_chat_id, source_topic_id)
         → Se entry.trader_id valorizzato
            → resolved_trader_id = entry.trader_id
            → resolution_method = "source_chat_id" o "source_topic_config"
            → GOTO step 11
    10b. Altrimenti chiama EffectiveTraderResolver con priorità:
         1) alias/tag nel testo
         2) reply-chain entro depth_limit (default 5, configurabile)
         3) alias negli antenati reply-chain
         → Se risolto
            → resolved_trader_id = result.trader_id
            → resolution_method = result.method
            → GOTO step 11
         → Se ambiguo/unresolved
            → processing_status = review
            → reason = "unresolved_trader" o "ambiguous_trader_alias"
            → FINE intake
11. Persist resolved_trader_id / resolution_method / resolution_detail.
12. Verifica parser_profile:
    → parser_profile = channels.yaml[source].parser_profile ?? resolved_trader_id
    → Se profilo non registrato in parser_v2:
       processing_status = review
       reason = "no_parser_profile"
       → FINE intake
13. processing_status = done
    Produce ParserDispatchCandidate per la fase parser (PRD 02).
```

### 8.2 Recovery / catchup

```text
1. Re-enqueue dei messaggi stale in processing.
2. Per ogni chat/topic attivo, recupero da checkpoint topic-aware.
3. Persistenza raw con acquisition_mode=catchup.
4. Dedup evita doppia acquisizione.
5. Processing segue lo stesso flusso del live (sezione 8.1).
```

Regola: il fatto che un messaggio sia `catchup` non lo rende non parsabile. Eventuali policy operative su MARKET catchup appartengono alle operation rules, non all'intake.

### 8.3 Import / parser_test

```text
1. import_history.py salva raw_messages con acquisition_mode=import.

2. Se --default-source-trader è presente:
   → source_trader_id = --default-source-trader
   → resolved_trader_id = normalize(--default-source-trader)
   → resolution_method = "source_trader_id"
   → FINE (skip resolve_traders per questi messaggi)

3. resolve_traders.py (per messaggi senza resolved_trader_id già valorizzato):
   a) Se source_trader_id già presente (da import precedente)
      → resolved_trader_id = normalize(source_trader_id)
      → resolution_method = "source_trader_id"
      → FINE
   
   b) Altrimenti chiama EffectiveTraderResolver (stessa priorità del live):
      1) alias nel testo
      2) reply-chain entro depth_limit (default 5, configurabile)
      3) alias negli antenati
      → Se risolto
         → resolved_trader_id = result.trader_id
         → resolution_method = result.method
         → FINE
      → Se non risolto, prosegui a step c
   
   c) Se --assume-trader fornito (fallback SOLO parser_test)
      → resolved_trader_id = normalize(--assume-trader)
      → resolution_method = "assume_trader"
      → FINE
   
   d) Altrimenti
      → resolved_trader_id = NULL
      → resolution_method = "unresolved"

4. replay_parser_v2.py filtra per trader_filter e sceglie parser_profile.
```

Il live deve poter produrre dati confrontabili con questo flusso almeno su:

- raw ids;
- source/topic;
- reply;
- `source_trader_id`;
- `resolved_trader_id`;
- `resolution_method`;
- testo e timestamp.

---

## 9. Errori, review e audit

### 9.1 Stati e reason minime

| Caso | Stato | Reason |
|---|---|---|
| Duplicato gia' acquisito | nessun nuovo job | `duplicate_raw_message` |
| Blacklist | `blacklisted` | `blacklist_match` |
| Media-only senza testo | `skipped` | `media_only_no_text` |
| Short update senza strong link | `review` | `short_update_without_strong_link` |
| Trader non risolto | `review` | `unresolved_trader` |
| Trader ambiguo da alias multipli | `review` | `ambiguous_trader_alias` |
| Profilo trader non registrato in parser_v2 | `review` | `no_parser_profile` |
| Profilo trader noto ma inattivo | `skipped` o `done` | `trader_inactive` |
| Eccezione resolver/job | `failed` | `intake_processing_error` |

### 9.2 Review queue

La review queue resta il meccanismo minimo per casi che non devono sparire:

```text
raw_message_id
reason
created_at
resolved_at
resolution
```

Per `runtime_v2` e' consigliato aggiungere, in una fase successiva, un audit event piu' ricco. In PRD 01 basta non perdere il motivo della review.

### 9.3 Logging

Ogni cambio stato deve essere loggato con:

```text
raw_message_id | source_chat_id | source_topic_id | telegram_message_id | status | reason | method
```

Non loggare segreti, sessioni Telegram o `.env`.

---

## 10. Interfacce proposte per `src/runtime_v2/`

Package raccomandato:

```text
src/runtime_v2/
    intake/
        models.py
        ingestion.py
        eligibility.py
        jobs.py
    trader_resolution/
        resolver.py
        models.py
        channel_config_resolver.py
    persistence/
        raw_messages.py
        processing_jobs.py
    audit/
        events.py
```

Uso dei moduli esistenti:

- riusare o adattare `src.storage.raw_messages.RawMessageStore`;
- riusare `src.telegram.effective_trader.EffectiveTraderResolver` come base della logica;
- riusare `src.telegram.trader_mapping.TelegramSourceTraderMapper`;
- caricare `config/channels.yaml` per lookup rapido;
- non importare `src.telegram.router.MessageRouter`;
- non importare parser legacy `src/parser`;
- non importare operation rules o execution.

Per una prima slice e' accettabile creare adapter sottili in `runtime_v2/persistence` che chiamano gli store esistenti, evitando duplicazione prematura.

---

## 11. Acceptance contract

### 11.1 Done significa

La fase PRD 01 e' implementata quando un messaggio live, catchup o importato puo' essere salvato come raw, processato in modo idempotente, risolto a trader effettivo o mandato in review con reason tracciabile, e produrre un `ParserDispatchCandidate` tipizzato pronto per il PRD 02 — senza importare `src.telegram.router`.

### 11.2 Criteri pass/fail

1. Un messaggio nuovo produce un solo `raw_message_id` anche se ricevuto due volte.
2. Un messaggio con topic conserva `source_topic_id` e partecipa a checkpoint/recovery topic-aware.
3. Un messaggio da canale mono-trader (con trader_id in channels.yaml) risolve trader da config subito.
4. Un messaggio da canale multi-trader con alias nel testo risolve `resolved_trader_id` e `resolution_method=content_alias`.
5. Un messaggio da canale multi-trader senza alias va in review e prova reply-chain.
6. Un update breve senza reply/link/ref forte va in review con `short_update_without_strong_link`.
7. Un reply-to-reply eredita il trader dalla chain entro il depth limit configurato (default 5) e senza loop.
8. Un alias ambiguo non viene forzato a un trader: va in review con `ambiguous_trader_alias`.
9. `parser_test` e live usano la stessa semantica di trader resolution: config → text → reply-chain.
10. Nessun modulo del nuovo intake importa `src.telegram.router`.
11. Il risultato finale della fase e' un `ParserDispatchCandidate` con `parser_context: ParserContext`, non un parse result.
12. Un messaggio con `resolved_trader_id` valido ma profilo non registrato in `parser_v2` va in review con `no_parser_profile`.
13. `acquisition_status` e' immutabile: dopo l'ingest non cambia mai.

### 11.3 Segnale primario

Il segnale primario e' una suite mirata che dimostri:

```text
raw ingest → trader resolution (config-driven first) → ParserDispatchCandidate oppure review
```

su casi live-like e parser_test-like.

### 11.4 Segnali secondari

- Test storage raw messages e dedup.
- Test topic/recovery sui checkpoint.
- Test effective trader resolver con priorità config → text → reply-chain.
- Test review queue per unresolved/ambiguous/no_parser_profile.
- Test import/replay compatibility su DB `parser_test`.

---

## 12. Test minimi raccomandati

### 12.1 Unit

- `RawMessageEnvelope` rifiuta campi incoerenti o mancanti.
- `acquisition_status` immutabile: non viene modificato dopo l'ingest.
- Dedup raw su `(source_chat_id, telegram_message_id)`.
- Salvataggio metadata topic/media senza perdere compatibilita' con schemi vecchi.
- Lookup channels.yaml per (chat_id, topic_id) -> trader_id risolve direttamente.
- Canale multi-trader: alias nel testo con un solo trader.
- Canale multi-trader: alias nel testo con piu' trader -> ambiguous.
- Reply-chain transitiva, entro depth limit, con loop protection.
- Short update senza strong link -> review-only.
- `parser_profile` derivato da `resolved_trader_id` se non sovrascritto in channels.yaml.
- `parser_profile` non registrato in `parser_v2` -> review con `no_parser_profile`.

### 12.2 Integration

- Ingest live-like (canale mono-trader) → risolve da config.
- Ingest live-like (canale multi-trader + alias) → risolve da text.
- Ingest live-like (reply-chain) → eredita trader.
- Catchup duplicate -> non crea doppio raw ne' doppio job attivo.
- Import con --default-source-trader → assegna subito resolved_trader_id.
- Parser test DB resolve_traders.py → stessi method/reason del live.
- Canale/topic configurato inattivo -> skip tracciato.
- Nuovo package `runtime_v2` non importa `src.telegram.router`.

### 12.3 Regression

Usare come base i test esistenti:

- `src/storage/tests/test_raw_messages_topic.py`;
- `src/storage/tests/test_raw_messages_media.py`;
- `src/storage/tests/test_processing_status_topic.py`;
- `src/telegram/tests/test_reply_chain.py`;
- `parser_test/scripts/tests/test_trader_resolution.py`;
- `parser_test/scripts/tests/test_resolve_traders.py`;
- `parser_test/db/tests/test_schema.py`.

---

## 13. Piano di implementazione suggerito

1. Aggiungere migrazioni additive per colonne mancanti in `raw_messages`.
2. Introdurre modelli `runtime_v2.intake` e `runtime_v2.trader_resolution`.
3. Creare modulo `runtime_v2.trader_resolution.channel_config_resolver` che:
   - Carica e cache `config/channels.yaml` (singleton, hot-reload via watchdog)
   - Lookup per `(source_chat_id, source_topic_id)` → restituisce `trader_id` e `parser_profile` se presenti
4. Creare adapter persistence su `RawMessageStore` e processing status esistenti.
5. Estrarre un servizio `RuntimeV2IntakeProcessor` che:
   - Prova channel_config_resolver FIRST
   - Se no result, chiama EffectiveTraderResolver (priorità: text → reply-chain entro depth_limit)
   - Gestisce unresolved/ambiguous → review
   - Verifica parser_profile nel registry parser_v2
   - Gestisce no_parser_profile → review
6. Allineare `parser_test/scripts/resolve_traders.py` a stessa logica (config → text → reply-chain).
7. Aggiungere test mirati su live-like (mono-trader + multi-trader) e parser_test-like.
8. Solo dopo, passare al PRD 02 per chiamare `parser_v2`.

---

## 14. Rischi e decisioni aperte

### 14.1 Tabella job dedicata

Decisione raccomandata: progettare l'interfaccia come job dedicato, ma permettere MVP su `raw_messages.processing_status`.

Rischio se rimandata troppo: retry, lock e audit dei tentativi restano deboli.

### 14.2 Topic come parte della dedup key

La dedup attuale e' `(source_chat_id, telegram_message_id)`, che di norma e' sufficiente per Telegram. Il topic resta indice/query dimension, non parte della unique key primaria.

Decisione: mantenere unique attuale e usare topic per checkpoint, filtri e contesto.

### 14.3 Fallback trader nel live

`assume_trader` e' utile nei tool e per dataset mono-trader. Nel live puo' nascondere errori di configurazione.

Decisione: fallback live solo se configurato esplicitamente e auditato. parser_test ha --assume-trader come fallback opzionale.

### 14.4 Media

L'MVP del parser lavora sul testo. I media con caption possono essere acquisiti; i media-only senza testo vengono loggati con `acquisition_status=MEDIA_ONLY_SKIPPED`. L'analisi del contenuto media e' fuori ambito.

### 14.5 Cache channels.yaml

La config `channels.yaml` deve essere caricata al bootstrap e hot-reloadata su modifica (via watchdog).

Decisione: singleton cache in `runtime_v2.trader_resolution.channel_config_resolver` per evitare lookup ripetuti. Errore di caricamento loggato con fallback all'ultima versione valida.

### 14.6 Depth limit reply-chain

Il depth limit e' un parametro globale configurabile (default: 5). Va dichiarato esplicitamente nella configurazione di `runtime_v2` e documentato come contratto. Modificare il valore richiede un config change, non un code change.

---

## 15. Documentazione da allineare dopo implementazione

- `README.md`: stato del nuovo runtime e comandi di test.
- `parser_test/README.md`: aggiornare logica resolve_traders con nuova priorità.
- `docs/PRD_listener.md` e `docs/PRD_router.md`: indicare che descrivono il runtime legacy/baseline, non il clean core.
- Documento madre: aggiornare lo stato della fase B quando la slice sara' implementata.

---

## 16. Output atteso per il PRD 02

Il PRD 02 puo' partire quando PRD 01 consegna questo oggetto logico:

```text
ParserDispatchCandidate(
    raw_message=RawMessageEnvelope(raw_message_id=123, source_chat_id="-1003722628653"),
    resolved_trader=ResolvedTraderContext(trader_id="trader_a", method="source_chat_id"),
    parser_profile="trader_a",
    parser_context=ParserContext(telegram_message_id=456, source_topic_id=3, ...)
)
```

Da quel punto il prossimo blocco puo' concentrarsi solo su:

```text
ParserDispatchCandidate -> UniversalParserRuntime -> CanonicalMessage v2 -> canonical persistence
```
