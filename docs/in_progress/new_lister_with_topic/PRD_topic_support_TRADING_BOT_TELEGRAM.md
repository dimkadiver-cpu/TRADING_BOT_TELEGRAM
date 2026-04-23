# PRD — Estensione di `TRADING_BOT_TELEGRAM` per supportare anche i Telegram Forum Topics

## 1. Scopo del documento

Questo documento definisce il piano completo per portare il repository `TRADING_BOT_TELEGRAM` dallo stato attuale, in cui il listener e la pipeline operano a livello di `chat_id`, a uno stato in cui il sistema supporta anche i **Telegram forum topics** in modo esplicito, persistente e coerente lungo tutta la pipeline.

L’obiettivo non è introdurre una soluzione parziale o locale, ma una soluzione end-to-end che tocchi tutti i punti interessati e dipendenti:

- configurazione canali/topic;
- listener Telegram live;
- ingestione raw;
- persistenza;
- recovery/catchup;
- routing e risoluzione trader;
- blacklist;
- linking reply/thread;
- test;
- rollout graduale e compatibilità con il comportamento attuale.

---

## 2. Contesto e stato attuale

### 2.1 Stato attuale del repository

Nel repository attuale:

- la configurazione `channels.yaml` gestisce solo `chat_id`, non `topic_id`;
- `ChannelEntry` contiene `chat_id`, `label`, `active`, `trader_id`, `blacklist`;
- il listener filtra per `event.chat_id`;
- la raw ingestion salva `source_chat_id`, `telegram_message_id`, `reply_to_message_id`, ma non salva il topic;
- il recovery/catchup usa un checkpoint per `chat_id`;
- il router lavora semanticamente su `source_chat_id` e non su una coppia `(chat_id, topic_id)`.

Con questa struttura il bot può lavorare bene a livello di canale/gruppo/forum intero, ma **non può gestire correttamente topic multipli dello stesso forum**.

### 2.2 Problema da risolvere

Nel modello Telegram, i forum topics non vanno modellati come stringhe del tipo `chat_id/topic_id`, ma come:

- `chat_id` del forum/supergroup;
- `topic_id` separato, cioè il thread/topic ID.

Quindi il supporto topic richiede:

- configurazione esplicita del topic;
- estrazione del topic dai messaggi in ingresso;
- persistenza del topic;
- recovery e query topic-aware;
- regole di matching chiare tra modalità forum-wide e topic-specific.

---

## 3. Obiettivo prodotto

Consentire al bot di operare in **due modi supportati ufficialmente**:

### Modalità A — Forum-wide

L’entry di configurazione identifica un intero forum/chat.

Semantica:
- tutti i messaggi di quel `chat_id` sono ammessi;
- il comportamento è sostanzialmente compatibile con il sistema attuale.

### Modalità B — Topic-specific

L’entry di configurazione identifica uno specifico topic di un forum.

Semantica:
- sono ammessi solo i messaggi appartenenti a quel `chat_id` **e** a quel `topic_id`;
- la pipeline deve mantenere questa informazione fino a persistenza, recovery e routing.

---

## 4. Obiettivi funzionali

### 4.1 Obiettivi principali

1. Il sistema deve poter configurare sia forum interi sia topic specifici.
2. Il listener deve estrarre il topic dai messaggi Telegram quando presente.
3. Il topic deve essere persistito nella raw ingestion.
4. Il recovery deve poter lavorare in modo coerente con il supporto topic.
5. Blacklist, mapping trader e routing devono poter distinguere forum-wide vs topic-specific.
6. Il sistema deve restare retrocompatibile con l’uso attuale senza topic.
7. Il rollout deve poter essere graduale.

### 4.2 Obiettivi secondari

1. Migliorare osservabilità e log per debug topic.
2. Ridurre ambiguità future su provenance e replay.
3. Preparare la base per eventuali sviluppi futuri di invio outbound verso topic.

---

## 5. Non-obiettivi

Questa iniziativa **non** include, salvo estensioni esplicite successive:

1. supporto completo ai comment threads dei post di canale;
2. redesign del parser semantico dei messaggi;
3. refactor del motore operativo oltre quanto necessario a propagare il topic;
4. scrittura outbound nei topic come requisito principale;
5. supporto speciale a casi API avanzati come `monoforum`.

Nota: il sistema va progettato in modo da **non impedire** questi sviluppi futuri, ma non devono entrare nello scope di questa implementazione.

---

## 6. Principi di design

### 6.1 Modello dati corretto

Il topic non deve essere rappresentato come stringa unica `chat/topic`.

Il modello corretto deve essere:

- `chat_id: int`
- `topic_id: int | null`

### 6.2 Distinzione tra `topic_id: null` e `topic_id: 1`

Queste due condizioni **non sono equivalenti**.

- `topic_id: null` = forum-wide, cioè tutta la chat/forum
- `topic_id: 1` = topic “General”

Questa distinzione è critica e deve essere mantenuta in config, matching, persistenza e recovery.

### 6.3 Retrocompatibilità

Una configurazione priva di `topic_id` deve continuare a funzionare con semantica forum-wide.

### 6.4 Estensione minima ma completa

La modifica deve essere la più piccola possibile sul piano del codice, ma completa sul piano semantico.

Non va introdotto un supporto topic “finto” che filtra live ma poi perde il topic in DB o lo ignora in recovery.

---

## 7. Regole funzionali target

## 7.1 Schema config target

La config target deve supportare un unico blocco `channels:` con una lista di scope configurati.

Esempio completo con:

- più topic dello stesso forum;
- un gruppo/forum ascoltato forum-wide;
- un gruppo normale senza topic;
- blacklist globale e blacklist locali.

```yaml
recovery:
  max_hours: 7

blacklist_global:
  - "#admin"
  - "#info"
  - "#pinned"

channels:
  - chat_id: -3722628653
    topic_id: 3
    label: "PifSignal_topic_3"
    active: true
    trader_id: trader_a
    blacklist:
      - "#noise"

  - chat_id: -3722628653
    topic_id: 4
    label: "PifSignal_topic_4"
    active: true
    trader_id: trader_b
    blacklist: []

  - chat_id: -1001111111111
    topic_id: null
    label: "Forum_intero"
    active: true
    trader_id: null
    blacklist:
      - "#weekly"

  - chat_id: -1002222222222
    topic_id: null
    label: "Gruppo_normale"
    active: true
    trader_id: trader_c
    blacklist:
      - "#promo"
```

Nota importante:

- `topic_id: null` = scope forum/chat intero;
- `topic_id: 1` = topic General;
- un gruppo normale senza topic usa comunque `topic_id: null`;
- non devono esistere due chiavi YAML `channels:` duplicate nello stesso file.

## 7.2 Regola di matching

Per un messaggio in ingresso:

- se esistono entry topic-specific che matchano `(chat_id, topic_id)`, hanno priorità;
- altrimenti può matchare l’entry forum-wide `(chat_id, topic_id=null)`;
- se non esiste alcuna entry compatibile, il messaggio è fuori scope.

## 7.3 Regola di precedenza

Precedenza obbligatoria:

1. match topic-specific;
2. match forum-wide;
3. nessun match.

Questa regola evita conflitti quando un forum ha sia una entry globale sia una o più entry topic-specific.

## 7.4 Configurazioni vietate

Devono essere considerate invalide:

1. due entry identiche con stesso `chat_id` e stesso `topic_id`;
2. entry con `topic_id` non intero positivo;
3. `topic_id` espresso come stringa `"3"` se si decide di validare in modo stretto;
4. formati del tipo `chat_id: -100123/3`;
5. più chiavi YAML `channels:` nello stesso file;
6. entry topic-specific duplicate con label/trader diversi ma stesso scope `(chat_id, topic_id)`.

## 7.5 Regole di blacklist

Il sistema deve supportare contemporaneamente:

1. `blacklist_global` valida per tutti i messaggi;
2. `blacklist` locale su entry forum-wide `(chat_id, topic_id=None)`;
3. `blacklist` locale su entry topic-specific `(chat_id, topic_id=N)`.

### Risoluzione blacklist effettiva

Per ogni messaggio, la blacklist effettiva deve essere costruita con questa precedenza:

1. applicare sempre `blacklist_global`;
2. se il messaggio matcha una entry topic-specific, applicare la `blacklist` di quella entry;
3. altrimenti, se il messaggio matcha una entry forum-wide, applicare la `blacklist` di quella entry;
4. non sommare automaticamente anche la blacklist forum-wide quando esiste già un match topic-specific, salvo decisione futura esplicita.

Formula operativa:

- `blacklist_effettiva = blacklist_global + blacklist_scope_matchato`

### Motivazione

Questa regola evita ambiguità quando nello stesso forum convivono:

- una entry forum-wide;
- una o più entry topic-specific.

Senza questa regola, un messaggio di topic potrebbe ereditare blacklist non desiderate dal forum-wide.

## 7.6 Regole di coesistenza degli scope

Il sistema deve supportare contemporaneamente:

1. più topic dello stesso forum;
2. un forum intero senza topic-specific;
3. un gruppo normale senza topic;
4. una combinazione mista dei casi sopra.

Esempi validi:

- topic 3 e topic 4 dello stesso forum;
- topic 3 dello stesso forum + un altro gruppo normale con `topic_id=None`;
- forum-wide per un forum + gruppi normali senza topic.

Regola obbligatoria:

- la presenza di più topic nello stesso `chat_id` non deve interferire con l’ascolto di altri `chat_id` senza topic.

---

## 8. Impatti architetturali — panoramica

Le aree da modificare sono:

1. **Config loader**
2. **Listener**
3. **Ingestion DTO**
4. **Persistenza raw_messages**
5. **Processing status / recovery**
6. **Router**
7. **Blacklist e trader mapping**
8. **Reply/linking**
9. **Signals / operational provenance**
10. **Test**
11. **Migrazioni DB**
12. **Rollout e compatibilità**

---

## 9. Requisiti dettagliati per area

## 9.1 Config loader

### Obiettivo
Estendere `ChannelEntry` e `ChannelsConfig` per supportare `topic_id`.

### Requisiti

1. `ChannelEntry` deve includere `topic_id: int | None`.
2. `load_channels_config()` deve leggere `topic_id` se presente.
3. In assenza di `topic_id`, il valore deve diventare `None`.
4. Devono essere introdotte funzioni di lookup topic-aware, non solo `channel_for(chat_id)`.

### API suggerita

Nuove utility suggerite:

- `entries_for_chat(chat_id: int) -> list[ChannelEntry]`
- `match_entry(chat_id: int, topic_id: int | None) -> ChannelEntry | None`
- `active_scopes()` per costruire i filtri attivi in modo strutturato.

### Validazione config

Aggiungere validazione esplicita di:

- duplicati `(chat_id, topic_id)`;
- tipi non validi;
- `topic_id=0` non ammesso;
- mismatch strutturali.

---

## 9.2 Listener Telegram

### Obiettivo
Estrarre il topic dai messaggi in ingresso e usarlo nel matching.

### Requisiti

1. Il listener deve estrarre `chat_id` come oggi.
2. Il listener deve estrarre anche `topic_id` quando disponibile.
3. Il matching dei messaggi ammessi non deve più essere solo `_is_allowed_chat(chat_id)` ma uno scope-aware.
4. I log live devono mostrare anche `topic_id` quando noto.

### Requisito semantico critico

La logica non deve basarsi unicamente su `forum_topic=True`, perché il topic General è speciale.

### Strategia consigliata

Introdurre una funzione dedicata, per esempio:

- `extract_message_topic_id(message) -> int | None`

che implementa le regole in un solo punto.

### Output del listener

Ogni messaggio da passare a ingestione deve contenere:

- `source_chat_id`
- `source_topic_id`
- `telegram_message_id`
- `reply_to_message_id`
- eventualmente metadati utili al debug topic.

---

## 9.3 Ingestion DTO

### Obiettivo
Propagare `topic_id` dai layer Telegram fino alla persistenza.

### Requisiti

1. `TelegramIncomingMessage` deve includere `source_topic_id: int | None`.
2. `_build_incoming()` deve valorizzare questo campo.
3. L’oggetto di coda (`QueueItem`) deve includere `source_topic_id: int | None`.
4. `StaleMessage` deve includere `source_topic_id: int | None`.

---

## 9.4 Persistenza raw_messages

### Obiettivo
Salvare il topic nel record raw in modo stabile.

### Requisiti minimi

La tabella `raw_messages` deve acquisire:

- `source_topic_id INTEGER NULL`

### Motivazione

Senza questo campo il sistema può continuare a salvare messaggi, ma **non** può supportare topic in modo affidabile per:

- recovery;
- query;
- replay;
- debug;
- audit.

### Dedup

La dedup attuale basata su:

- `source_chat_id`
- `telegram_message_id`

può restare invariata come chiave tecnica primaria, perché in un forum/channel i message ID sono già coerenti a livello chat.

### Indici consigliati

Aggiungere un indice dedicato per query topic-aware, ad esempio:

- `(source_chat_id, source_topic_id, telegram_message_id)`

Non è obbligatorio usarlo come unique key, ma è utile per recovery e lookup.

---

## 9.5 Processing status e recovery

### Obiettivo
Rendere il recovery coerente con il supporto topic.

### Problema attuale

Il recovery attuale usa `get_last_telegram_message_id(chat_id)`.

Questo modello è insufficiente se si vogliono distinguere topic diversi nello stesso forum.

### Requisiti target

1. Il checkpoint di recovery deve poter essere calcolato per `(chat_id, topic_id)`.
2. `get_stale_messages()` deve restituire anche il `source_topic_id`.
3. `_reenqueue_stale()` deve preservare `source_topic_id` negli item rimessi in coda.
4. `_catchup_from_telegram()` deve essere topic-aware almeno a livello di filtro logico.

### Strategia di implementazione

#### Fase 1 — minima, sicura

- recovery per `chat_id` come oggi;
- filtri locali per `topic_id` sui messaggi recuperati;
- checkpoint per `(chat_id, topic_id)` in DB.

#### Fase 2 — ottimizzazione

Quando utile, introdurre recovery mirato per topic usando API/thread search lato Telegram.

### Nota importante

Per forum-wide (`topic_id=None`) il recovery continua a operare sul forum intero.

Per topic-specific il recovery deve usare solo messaggi di quel topic.

---

## 9.6 Router

### Obiettivo
Fare in modo che il router non perda il topic.

### Requisiti

1. `QueueItem` deve trasportare `source_topic_id`.
2. `route()` e `_route_inner()` devono includere topic nei log.
3. Le funzioni di supporto devono poter ricevere sia `source_chat_id` sia `source_topic_id`.

### Blacklist topic-aware

La blacklist deve usare l’entry configurata effettivamente matchata, non solo il `chat_id` generico.

Regola richiesta:

- `blacklist_global` sempre applicata;
- se esiste match topic-specific, usare la blacklist di quell’entry;
- altrimenti usare la blacklist dell’entry forum-wide;
- evitare merge impliciti forum-wide + topic-specific nella prima iterazione.

### Trader mapping topic-aware

Se uno stesso forum contiene topic di trader diversi, la risoluzione trader da config deve poter distinguere il topic.

Quindi il fallback “channels_yaml” deve appoggiarsi all’entry matchata per `(chat_id, topic_id)`.

---

## 9.7 Reply, linking e risoluzione parent

### Obiettivo
Mantenere corretto il linking tra messaggi quando si introducono i topic.

### Stato attuale

Il sistema usa `reply_to_message_id` e lookup su `source_chat_id` + `reply_to_message_id`.

### Valutazione

Per molti casi questa base continua a funzionare, ma il topic deve comunque essere preservato per evitare ambiguità future e per query più accurate.

### Requisiti

1. I parent lookup devono restare compatibili con il modello attuale.
2. Le API interne devono poter usare opzionalmente anche `source_topic_id`.
3. Non va introdotta una dipendenza errata dal solo `reply_to_message_id` per identificare il topic.

### Nota

Il topic va trattato come contesto del messaggio, non come semplice derivazione del parent reply.

---

## 9.8 Signals, operational signals ed eventi

### Obiettivo
Decidere il grado di estensione della provenance oltre `raw_messages`.

### Livello minimo obbligatorio

- salvare `source_topic_id` in `raw_messages`
- propagare topic nella coda e nel routing

### Livello consigliato

Valutare estensione provenance anche in:

- `signals`
- `events`
- `operational_signals`

aggiungendo `topic_id` o un concetto analogo di provenance.

### Motivazione

Non è strettamente necessario per la prima versione se la raw provenance è già affidabile, ma è consigliato per:

- audit;
- debugging di attempt key;
- filtri futuri per report e replay.

### Raccomandazione

Versione iniziale:
- **required** in `raw_messages`;
- **recommended** nelle tabelle operative, se il costo di modifica è contenuto.

---

## 9.9 Logging e osservabilità

### Obiettivo
Rendere evidente il topic nei log.

### Requisiti

Tutti i log principali devono includere `topic_id` quando disponibile:

- `raw acquired`
- `blacklisted`
- `duplicate skipped`
- `trader_unresolved`
- `parse result persisted`
- `phase4 complete`
- `recovery`

### Beneficio

Questo riduce molto il costo di debug quando nello stesso forum convivono più topic/trader.

---

## 10. Migrazioni DB

## 10.1 Requisito

Creare una nuova migration incrementale che aggiunga almeno:

- `source_topic_id` a `raw_messages`

## 10.2 Compatibilità dati esistenti

I record storici resteranno con `source_topic_id = NULL`.

Questo implica:

- i dati vecchi non sono sempre ricostruibili topic-aware;
- i dati nuovi saranno corretti;
- eventuale backfill storico è opzionale e va trattato come best-effort, non come prerequisito.

## 10.3 Strategia consigliata

1. migration additive;
2. nessuna riscrittura distruttiva;
3. valori storici lasciati a `NULL`;
4. eventuali indici creati nella stessa migration o in una successiva.

---

## 11. Strategia di matching dettagliata

## 11.1 Matching richiesto

Data una lista di `ChannelEntry` attive e un messaggio con:

- `msg_chat_id`
- `msg_topic_id`

il sistema deve:

1. cercare entry con stesso `chat_id` e `topic_id == msg_topic_id`;
2. se trovate, usare quella entry;
3. altrimenti cercare entry con stesso `chat_id` e `topic_id is None`;
4. se trovata, usare quella entry;
5. altrimenti scartare il messaggio.

## 11.2 Caso General topic

`topic_id=1` deve essere trattato come topic-specific valido.

Non deve mai essere equiparato a `None`.

## 11.3 Messaggi senza topic rilevabile

Se un messaggio non ha `topic_id` rilevabile:

- può matchare solo entry forum-wide;
- non può matchare entry topic-specific.

---

## 12. Strategia tecnica di estrazione topic

## 12.1 Requisito

La logica di estrazione topic deve stare in una funzione dedicata, centralizzata e testata.

## 12.2 Comportamento atteso

La funzione deve:

1. leggere i campi disponibili del reply/thread header Telegram;
2. derivare il `topic_id` in modo coerente con forum topics;
3. non dipendere solo dal flag `forum_topic`;
4. supportare correttamente il topic General.

## 12.3 Regola di robustezza

L’estrazione topic deve essere **best-effort ma deterministica**:

- se il topic è identificabile con confidenza sufficiente, restituire `int`
- altrimenti restituire `None`

Questa funzione deve essere l’unico punto di verità per la semantica topic nel listener.

---

## 13. Impatti sui test

## 13.1 Test da aggiornare

### Channel config

Aggiungere test per:

- load con `topic_id` assente;
- load con `topic_id` presente;
- duplicati `(chat_id, topic_id)`;
- doppia chiave YAML `channels:` invalida;
- matching topic-specific vs forum-wide;
- coesistenza di più topic nello stesso forum;
- coesistenza di topic in un forum e gruppo normale senza topic;
- `topic_id=1`.

### Listener

Aggiungere test per:

- messaggio forum-wide;
- messaggio topic-specific;
- topic General;
- messaggio non ammesso perché topic diverso;
- preservation di `source_topic_id` nell’enqueue.

### Recovery

Aggiungere test per:

- stale messages con topic;
- catchup filtrato correttamente per topic;
- coesistenza di due topic nello stesso forum;
- fallback forum-wide.

### Router

Aggiungere test per:

- blacklist topic-aware;
- precedenza blacklist_global + blacklist scope matchato;
- nessun merge implicito forum-wide + topic-specific nella prima iterazione;
- trader fallback topic-aware;
- logs con topic;
- propagation topic lungo route/process.

### Persistence

Aggiungere test per:

- save con `source_topic_id` null;
- save con `source_topic_id` intero;
- query/index path topic-aware.

---

## 14. Piano di implementazione per work package

## WP1 — Design e config

### Deliverable

- estensione `ChannelEntry`
- schema config target
- funzioni di matching topic-aware
- validazione config

### Dipendenze

Nessuna.

### Esito atteso

Il progetto sa esprimere formalmente forum-wide e topic-specific.

---

## WP2 — Migrazione DB e raw store

### Deliverable

- migration DB per `raw_messages.source_topic_id`
- update di `RawMessageRecord`, `StoredRawMessage`, `RawMessageSaveResult` dove serve
- indici topic-aware

### Dipendenze

WP1.

### Esito atteso

La persistenza raw può memorizzare il topic.

---

## WP3 — Listener e ingestion

### Deliverable

- funzione `extract_message_topic_id`
- modifica listener per usare match scope-aware
- aggiornamento `_build_incoming`
- aggiornamento `QueueItem`

### Dipendenze

WP1, WP2.

### Esito atteso

I messaggi live entrano in pipeline con `source_topic_id` corretto.

---

## WP4 — Recovery e processing status

### Deliverable

- checkpoint topic-aware
- `StaleMessage` topic-aware
- catchup topic-aware
- test recovery topic-aware

### Dipendenze

WP2, WP3.

### Esito atteso

Il restart recovery è coerente con la nuova semantica.

---

## WP5 — Router, blacklist, trader mapping

### Deliverable

- router con propagation topic
- blacklist basata su entry matchata
- fallback trader da entry topic-aware

### Dipendenze

WP1, WP3.

### Esito atteso

La parte decisionale non mescola più topic diversi dello stesso forum.

---

## WP6 — Provenance operativa opzionale

### Deliverable

- valutazione e, se approvato, aggiunta di `topic_id` anche a signals/events/operational_signals

### Dipendenze

WP2, WP5.

### Esito atteso

Audit e debug futuri più robusti.

---

## WP7 — Test suite finale

### Deliverable

- copertura regressione completa
- casi topic-specific e forum-wide
- General topic
- compatibilità config legacy

### Dipendenze

WP1–WP5 almeno.

### Esito atteso

L’implementazione è stabile e verificabile.

---

## 15. Piano di rollout

## Fase 0 — Preparazione

- aggiungere migration e codice compatibile con `topic_id=None`
- nessuna config topic-specific ancora attiva

## Fase 1 — Compatibilità silente

- deploy con supporto codice già presente
- config ancora prevalentemente forum-wide
- logging topic attivo per osservazione

## Fase 2 — Primo topic specifico pilota

- attivare un solo topic-specific in un forum reale
- verificare listener, DB, recovery, routing

## Fase 3 — Estensione controllata

- aggiungere altri topic-specific
- introdurre eventuale provenance estesa nelle tabelle operative

## Fase 4 — Consolidamento

- chiudere gap residui
- aggiornare documentazione operativa
- eventuale deprecazione di assunzioni forum-wide in alcuni casi specifici

---

## 16. Compatibilità retroattiva

## 16.1 Config legacy

Config senza `topic_id` devono continuare a funzionare.

## 16.2 Dati storici

I dati storici raw senza topic restano validi ma non topic-aware.

## 16.3 Recovery storico

Per i record vecchi, il recovery topic-specific potrebbe non essere perfettamente ricostruibile a ritroso. Questo è accettabile se ben documentato.

---

## 17. Rischi principali

## 17.1 Topic General gestito male

Rischio:
- confondere `topic_id=1` con forum-wide.

Mitigazione:
- test dedicati;
- regola esplicita in codice e config.

## 17.2 Matching ambiguo tra forum-wide e topic-specific

Rischio:
- un messaggio matcha due entry.

Mitigazione:
- priorità obbligatoria topic-specific > forum-wide.

## 17.3 Blacklist ambigua tra forum-wide e topic-specific

Rischio:
- un messaggio topic-specific eredita blacklist non desiderate dal forum-wide;
- comportamenti diversi tra listener e router.

Mitigazione:
- regola unica di risoluzione `blacklist_global + blacklist_scope_matchato`;
- niente merge implicito tra blacklist forum-wide e topic-specific nella prima iterazione;
- test dedicati.

## 17.4 Topic perso in persistenza

Rischio:
- listener corretto ma DB incompleto.

Mitigazione:
- `source_topic_id` obbligatorio nella raw ingestion.

## 17.5 Recovery incoerente

Rischio:
- catchup di un topic che in realtà recupera tutto il forum.

Mitigazione:
- checkpoint topic-aware;
- filtro esplicito lato recovery.

## 17.6 Mapping trader forum-wide ancora troppo largo

Rischio:
- topic diversi dello stesso forum vengono assegnati allo stesso trader per errore.

Mitigazione:
- fallback trader basato sull’entry realmente matchata.

---

## 18. Decisioni obbligatorie da fissare prima dell’implementazione

1. Confermare che il supporto richiesto è per **forum topics**, non per comment threads di canale.
2. Confermare la regola di precedenza: **topic-specific vince su forum-wide**.
3. Confermare che `topic_id: null` significa forum-wide.
4. Confermare che `topic_id: 1` identifica il General topic.
5. Confermare la regola blacklist: **`blacklist_global + blacklist_scope_matchato`**, senza merge implicito forum-wide + topic-specific nella prima iterazione.
6. Confermare se estendere `topic_id` anche alle tabelle operative nella prima iterazione o in seconda fase.

Per questo PRD si assume che tutte queste decisioni siano **SI**, tranne il punto 6, che viene lasciato come **recommended / optional in first iteration**.

---

## 19. Acceptance criteria

L’implementazione sarà considerata completa quando:

1. `channels.yaml` accetta entry con o senza `topic_id`.
2. Il listener filtra correttamente forum-wide e topic-specific.
3. `source_topic_id` viene salvato in `raw_messages`.
4. `source_topic_id` viene preservato in enqueue, recovery e routing.
5. La precedenza topic-specific > forum-wide è rispettata.
6. Il topic General (`topic_id=1`) è supportato e non confuso con `null`.
7. Blacklist e fallback trader usano l’entry matchata corretta.
8. La risoluzione blacklist segue la regola `blacklist_global + blacklist_scope_matchato`.
9. La config legacy senza `topic_id` continua a funzionare.
10. La test suite copre i casi principali e le regressioni, inclusi casi misti topic + gruppi normali.
11. I log mostrano `topic_id` nei punti chiave.

---

## 20. Piano operativo raccomandato

Ordine consigliato di esecuzione:

1. estendere config e matching;
2. aggiungere migration DB;
3. propagare `topic_id` in listener/DTO/queue;
4. rendere topic-aware recovery e processing status;
5. aggiornare router, blacklist e trader mapping;
6. aggiungere test dedicati;
7. valutare provenance topic nelle tabelle operative.

Questo ordine minimizza regressioni e consente di introdurre i topic senza rompere il comportamento attuale forum-wide.

---

## 21. Allegato — schema dati target minimo

### ChannelEntry

```python
@dataclass(slots=True)
class ChannelEntry:
    chat_id: int
    topic_id: int | None
    label: str
    active: bool
    trader_id: str | None
    blacklist: list[str] = field(default_factory=list)
```

### TelegramIncomingMessage

```python
@dataclass(slots=True)
class TelegramIncomingMessage:
    source_chat_id: str
    source_topic_id: int | None
    telegram_message_id: int
    message_ts: datetime
    ...
```

### QueueItem

```python
@dataclass(slots=True)
class QueueItem:
    raw_message_id: int
    source_chat_id: str
    source_topic_id: int | None
    telegram_message_id: int
    raw_text: str
    source_trader_id: str | None
    reply_to_message_id: int | None
    acquisition_mode: str
```

### raw_messages

Colonna nuova minima:

```sql
ALTER TABLE raw_messages ADD COLUMN source_topic_id INTEGER NULL;
```

Indice consigliato:

```sql
CREATE INDEX IF NOT EXISTS idx_raw_messages_chat_topic_msg
ON raw_messages(source_chat_id, source_topic_id, telegram_message_id);
```

---

## 22. Allegato — note di implementazione

### Nota 1

Non introdurre scorciatoie del tipo:

- trasformare `chat_id` in stringa `"-100.../3"`
- usare `topic_id` solo nei log ma non nel DB
- filtrare live per topic ma continuare recovery per forum intero senza distinzione

Queste soluzioni producono supporto topic apparente ma non corretto.

### Nota 2

La funzione di matching non deve cercare solo “canale attivo”, ma il **scope attivo** corretto.

### Nota 3

Per i dati storici già acquisiti senza `source_topic_id`, l’assenza di topic deve essere considerata una limitazione accettata del passato, non un errore della nuova pipeline.

---

## 23. Conclusione

La gestione dei Telegram forum topics non è un semplice filtro aggiuntivo, ma un’estensione di scope del sistema. Per essere fatta bene richiede una modifica coordinata di:

- config;
- listener;
- persistenza;
- recovery;
- routing;
- test.

La soluzione raccomandata è supportare **entrambi i modi**:

- forum-wide (`topic_id=None`)
- topic-specific (`topic_id=int`)

con precedenza obbligatoria del topic-specific sul forum-wide.

Questa soluzione è coerente con l’architettura attuale del bot, minimizza il rischio di regressioni e fornisce una base solida per sviluppi futuri.
