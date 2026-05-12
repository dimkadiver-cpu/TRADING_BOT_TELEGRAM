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

L'obiettivo e' separare in modo esplicito cio' che oggi e' disperso tra `TelegramListener`, `RawMessageIngestionService`, `RawMessageStore`, `EffectiveTraderResolver`, `MessageEligibilityEvaluator`, `parser_test` e `MessageRouter`.

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

L'eligibility iniziale non decide se un segnale e' tradabile. Decide solo se un messaggio puo' passare alla fase parser o deve andare in review/log per contesto insufficiente, ad esempio short update senza strong link.

### 5.3 Trader resolution e' un servizio condiviso

Il resolver deve essere lo stesso concetto per live e `parser_test`.

Priorita' raccomandata:

1. alias/tag esplicito nel testo del messaggio;
2. trader gia' risolto o `source_trader_id` nella reply-chain;
3. alias/tag nel testo degli antenati della reply-chain;
4. mapping sorgente da chat id, username, title, topic/config;
5. fallback esplicito solo nei tool di import/replay, mai silenzioso nel live.

Un risultato ambiguo e' diverso da un risultato assente: entrambi non passano al parser live, ma devono avere `resolution_method` e reason distinguibili.

### 5.4 Filtro messaggi e profilo parser restano separati

Il PRD recepisce la regola di `parser_test`:

| Concetto | Responsabilita' |
|---|---|
| `source_trader_id` | Trader suggerito dalla sorgente/import/config |
| `resolved_trader_id` | Trader effettivo deciso dal resolver |
| `trader_filter` | Quali messaggi includere in replay/report |
| `parser_profile` | Quale profilo `parser_v2` applicare |

Nel live il `parser_profile` iniziale sara' normalmente uguale a `resolved_trader_id`, ma il modello non deve fondere i due concetti.

### 5.5 Idempotenza per livelli

L'idempotenza minima e':

- acquisizione raw: `(source_chat_id, telegram_message_id)`;
- topic-aware query/recovery: `(source_chat_id, source_topic_id, telegram_message_id)`;
- processing job: un job attivo o completato per `raw_message_id` e pipeline version;
- trader resolution: ultima risoluzione persistita con metodo e timestamp, ricalcolabile in modo esplicito.

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
        "ACQUIRED_ELIGIBLE",
        "ACQUIRED_REVIEW_ONLY",
        "BLACKLISTED",
        "MEDIA_ONLY_SKIPPED",
        "INGEST_FAILED",
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
    is_fallback: bool
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
    parser_context_payload: dict
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
1. TelegramListener riceve evento.
2. Estrae chat/topic/message/reply/timestamp/testo/media metadata.
3. Scarta senza persistere solo eventi fuori config o media-only senza caption, con log.
4. Raw intake salva o ritrova raw_message_id via dedup.
5. Se blacklistato, imposta acquisition_status=BLACKLISTED e processing_status=blacklisted.
6. Altrimenti crea job/processing_status=pending.
7. Worker prende il job.
8. Eligibility iniziale valuta strong link per short update.
9. TraderResolver produce ResolvedTraderContext.
10. Se unresolved/ambiguous, processing_status=review e review_queue/audit riceve reason.
11. Se risolto, persist resolved_trader_id/resolution_method.
12. Produce ParserDispatchCandidate per la fase parser.
```

### 8.2 Recovery / catchup

```text
1. Re-enqueue dei messaggi stale in processing.
2. Per ogni chat/topic attivo, recupero da checkpoint topic-aware.
3. Persistenza raw con acquisition_mode=catchup.
4. Dedup evita doppia acquisizione.
5. Processing segue lo stesso flusso del live.
```

Regola: il fatto che un messaggio sia `catchup` non lo rende non parsabile. Eventuali policy operative su MARKET catchup appartengono alle operation rules, non all'intake.

### 8.3 Import / parser_test

```text
1. import_history.py salva raw_messages con acquisition_mode=import.
2. Se --default-source-trader e' presente, valorizza source_trader_id e resolved_trader_id con method source/import esplicito.
3. resolve_traders.py ricalcola/persiste resolved_trader_id quando serve.
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
| Short update senza strong link | `review` | `short_update_without_strong_link` |
| Trader non risolto | `review` | `unresolved_trader` |
| Trader ambiguo da alias multipli | `review` | `ambiguous_trader_alias` |
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
- non importare `src.telegram.router.MessageRouter`;
- non importare parser legacy `src/parser`;
- non importare operation rules o execution.

Per una prima slice e' accettabile creare adapter sottili in `runtime_v2/persistence` che chiamano gli store esistenti, evitando duplicazione prematura.

---

## 11. Acceptance contract

### 11.1 Done significa

La fase PRD 01 e' implementata quando un messaggio live, catchup o importato puo' essere salvato come raw, processato in modo idempotente, risolto a trader effettivo o mandato in review con reason tracciabile, senza passare dal `MessageRouter`.

### 11.2 Criteri pass/fail

1. Un messaggio nuovo produce un solo `raw_message_id` anche se ricevuto due volte.
2. Un messaggio con topic conserva `source_topic_id` e partecipa a checkpoint/recovery topic-aware.
3. Un messaggio mono-trader risolve il trader da source mapping o config.
4. Un messaggio multi-trader con alias nel testo risolve `resolved_trader_id` e `resolution_method=content_alias`.
5. Un update breve senza reply/link/ref forte va in review con `short_update_without_strong_link`.
6. Un reply-to-reply eredita il trader dalla chain entro depth limit e senza loop.
7. Un alias ambiguo non viene forzato a un trader: va in review.
8. `parser_test` e live usano la stessa semantica di `source_trader_id`, `resolved_trader_id`, `resolution_method`.
9. Nessun modulo del nuovo intake importa `src.telegram.router`.
10. Il risultato finale della fase e' un `ParserDispatchCandidate`, non un parse result.

### 11.3 Segnale primario

Il segnale primario e' una suite mirata che dimostri:

```text
raw ingest -> trader resolution -> ParserDispatchCandidate oppure review
```

su casi live-like e parser_test-like.

### 11.4 Segnali secondari

- Test storage raw messages e dedup.
- Test topic/recovery sui checkpoint.
- Test effective trader resolver.
- Test review queue per unresolved/ambiguous.
- Test import/replay compatibility su DB `parser_test`.

---

## 12. Test minimi raccomandati

### 12.1 Unit

- `RawMessageEnvelope` rifiuta campi incoerenti o mancanti.
- Dedup raw su `(source_chat_id, telegram_message_id)`.
- Salvataggio metadata topic/media senza perdere compatibilita' con schemi vecchi.
- Mapping source chat id / username / title / topic.
- Alias nel testo con un solo trader.
- Alias nel testo con piu' trader -> ambiguous.
- Reply-chain transitiva, max depth e loop protection.
- Short update senza strong link -> review-only.

### 12.2 Integration

- Ingest live-like -> raw -> job -> resolved trader.
- Catchup duplicate -> non crea doppio raw ne' doppio job attivo.
- Parser test DB -> resolve traders -> stessi method/reason del live.
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
3. Creare adapter persistence su `RawMessageStore` e processing status esistenti.
4. Estrarre un servizio `RuntimeV2IntakeProcessor` che fa eligibility + trader resolution.
5. Allineare `parser_test` a eventuali enum/method condivisi senza cambiare il suo flusso.
6. Aggiungere test mirati su live-like e parser_test-like.
7. Solo dopo, passare al PRD 02 per chiamare `parser_v2`.

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

Decisione: fallback live solo se configurato esplicitamente e auditato.

### 14.4 Media

L'MVP del parser lavora sul testo. I media con caption possono essere acquisiti; i media-only senza testo possono essere loggati/skippati. L'analisi del contenuto media e' fuori ambito.

---

## 15. Documentazione da allineare dopo implementazione

- `README.md`: stato del nuovo runtime e comandi di test.
- `parser_test/README.md`: se vengono introdotti enum condivisi per `resolution_method`.
- `docs/PRD_listener.md` e `docs/PRD_router.md`: indicare che descrivono il runtime legacy/baseline, non il clean core.
- Documento madre: aggiornare lo stato della fase B quando la slice sara' implementata.

---

## 16. Output atteso per il PRD 02

Il PRD 02 puo' partire quando PRD 01 consegna questo oggetto logico:

```text
ParserDispatchCandidate(
    raw_message=RawMessageEnvelope(raw_message_id=123, source_chat_id="-1003722628653"),
    resolved_trader=ResolvedTraderContext(trader_id="trader_a", method="content_alias"),
    parser_profile="trader_a",
    parser_context_payload={"telegram_message_id": 456, "source_topic_id": 3}
)
```

Da quel punto il prossimo blocco puo' concentrarsi solo su:

```text
ParserDispatchCandidate -> UniversalParserRuntime -> CanonicalMessage v2 -> canonical persistence
```
