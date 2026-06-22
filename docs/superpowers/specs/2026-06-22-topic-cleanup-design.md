# Topic Cleanup Design

Data: 2026-06-22
Stato: draft approvato a livello conversazionale, in attesa di review utente sul file

## Obiettivo

Implementare due comandi distruttivi del control plane Telegram:

- `/clear_topic`
- `/clear_all_topic`

Entrambi devono funzionare nel supergruppo configurato del control plane, con conferma esplicita prima dell'esecuzione.

`/clear_topic` deve svuotare solo il topic corrente senza eliminarlo.

`/clear_all_topic` deve svuotare tutti i topic del supergruppo, compreso il topic da cui parte il comando, lasciando intatti i topic come contenitori riutilizzabili.

Dove Telegram non consente di rimuovere il messaggio tecnico root del topic, quel messaggio può restare.

## Contesto Attuale

Il control plane corrente:

- riceve i comandi in `TelegramControlBot`;
- applica autorizzazione tramite `AuthValidator`;
- instrada i comandi in `CommandRouter`;
- usa già un flusso di conferma inline per i comandi distruttivi `/close`, `/close_all`, `/cancel_all`.

Il runtime dispone anche di un client Telethon già attivo, che è il layer corretto per:

- leggere messaggi storici non presenti nel DB;
- enumerare i messaggi di un topic Telegram;
- cancellare messaggi per ID a batch.

La Bot API da sola non è sufficiente per questa feature perché non è il layer giusto per ricostruire in modo affidabile tutto lo storico di un topic o di tutti i topic.

## Decisioni Chiuse

- I nomi dei comandi restano quelli proposti: `/clear_topic` e `/clear_all_topic`.
- I comandi devono funzionare da qualunque topic del supergruppo configurato, non solo dai topic `commands` o `clean_log`.
- I comandi restano comunque vincolati:
  - allo stesso `chat_id` del supergruppo configurato;
  - agli utenti autorizzati del control plane.
- Entrambi sono comandi distruttivi e richiedono conferma inline.
- `/clear_all_topic` deve fare pulizia completa di tutti i topic del supergruppo, incluso `General` quando Telegram lo espone come thread pulibile.
- A fine operazione non deve essere inviato alcun messaggio finale nel topic.

## Approccio Scelto

Approccio scelto: comando nel control plane, esecuzione della pulizia via Telethon condiviso.

Flusso ad alto livello:

1. L'utente autorizzato invia `/clear_topic` oppure `/clear_all_topic`.
2. Il control plane valida chat, topic e utente.
3. Il bot invia una preview distruttiva con bottoni inline `Conferma` e `Annulla`.
4. Alla conferma, `CommandRouter` delega a un nuovo servizio di topic cleanup.
5. Il servizio usa il client Telethon del runtime per enumerare i messaggi reali del topic o di tutti i topic.
6. Il servizio cancella i messaggi a batch, senza eliminare i topic.
7. Il bot non invia alcun messaggio finale di successo.

## Architettura

### Layer Coinvolti

- `src/runtime_v2/control_plane/auth.py`
- `src/runtime_v2/control_plane/telegram_bot.py`
- nuovo modulo dedicato, proposto: `src/runtime_v2/control_plane/topic_cleanup.py`
- wiring bootstrap/runtime per passare il client Telethon già connesso al servizio

### Ownership

`AuthValidator`
- continua a fare enforcement di `chat_id` e utente autorizzato;
- introduce un'eccezione mirata per consentire `/clear_topic` e `/clear_all_topic` da qualunque topic del supergruppo.

`CommandRouter`
- riconosce i nuovi comandi;
- crea la preview;
- gestisce token pending e callback inline;
- invoca il servizio di cleanup solo dopo la conferma.

`TopicCleanupService` nuovo
- possiede la logica di cleanup;
- usa Telethon per leggere i messaggi reali;
- applica lock in memoria;
- esegue delete a batch;
- gestisce flood-wait e best effort sugli errori parziali.

`TelegramControlBot`
- resta un adapter sottile;
- manda la preview nel topic corrente;
- per il callback di conferma richiama il router come già accade per gli altri comandi distruttivi.

## Comportamento Dettagliato

### `/clear_topic`

Precondizioni:

- il messaggio deve arrivare nel supergruppo configurato;
- il mittente deve essere autorizzato;
- il comando deve essere lanciato dentro un topic del forum.

Comportamento:

1. Il router accetta il comando anche se il topic non è uno dei topic canonici del control plane.
2. Se il messaggio non è in un topic forum, il comando non esegue la pulizia.
3. Il bot invia una preview minimale nel topic corrente con warning distruttivo e bottoni `Conferma` / `Annulla`.
4. Alla conferma:
   - ricava `chat_id`;
   - ricava `topic_id` dal thread corrente;
   - usa Telethon per leggere i messaggi appartenenti a quel thread;
   - esclude il messaggio root del topic;
   - include nella cancellazione:
     - il messaggio `/clear_topic`;
     - il messaggio preview con inline buttons;
     - eventuali altri messaggi presenti nel topic;
   - cancella gli ID a batch di massimo 100.
5. Nessun messaggio finale viene inviato nel topic.

Risultato atteso:

- il topic resta esistente;
- il topic risulta vuoto, salvo l'eventuale messaggio tecnico root non eliminabile.

### `/clear_all_topic`

Precondizioni:

- il messaggio deve arrivare nel supergruppo configurato;
- il mittente deve essere autorizzato;
- il comando può partire da qualunque topic del supergruppo.

Comportamento:

1. Il router accetta il comando da qualsiasi topic del forum del supergruppo autorizzato.
2. Il bot invia una preview con warning esplicito di pulizia completa del forum.
3. Alla conferma:
   - il servizio enumera tutti i topic del supergruppo;
   - include anche il topic da cui è partito il comando;
   - prova a includere anche `General` quando Telegram lo espone come topic pulibile;
   - per ogni topic:
     - legge tutti i messaggi del thread;
     - esclude il messaggio root del topic;
     - raccoglie gli ID cancellabili;
   - include nella cancellazione anche:
     - il messaggio `/clear_all_topic`;
     - il messaggio preview;
   - esegue la cancellazione per topic e per batch.
4. Nessun messaggio finale viene inviato.

Risultato atteso:

- tutti i topic vengono svuotati;
- i topic restano esistenti e riutilizzabili;
- l'ultimo topic processato non contiene messaggi residui del bot, salvo root tecnico non eliminabile.

## Identificazione di Chat e Topic

Il perimetro di ogni operazione è sempre guidato da ID reali Telegram.

Per `/clear_topic`:

- `chat_id`: supergruppo del control plane
- `topic_id`: thread corrente del messaggio comando

Per `/clear_all_topic`:

- `chat_id`: supergruppo del control plane
- enumerazione di tutti i `topic_id` disponibili nel forum

Mai usare:

- titolo del topic;
- testo del messaggio;
- mapping statici dei topic del control plane

per decidere il perimetro della cancellazione.

## Recupero Messaggi

Il recupero dei messaggi deve avvenire via Telethon.

Requisiti funzionali del layer Telethon:

- leggere l'intero thread di un topic a partire dal suo root/thread identifier;
- filtrare i messaggi che appartengono a quel topic;
- includere messaggi vecchi che il sistema non ha mai registrato nel database;
- tollerare topic con messaggi già eliminati o thread parzialmente inconsistenti.

Il servizio deve trattare come non eliminabile il messaggio root del topic quando coincide con il messaggio tecnico iniziale del forum thread.

## Cancellazione

La cancellazione avviene tramite ID di messaggio a blocchi di massimo 100.

Vincoli:

- non eliminare il topic;
- non inviare messaggi finali di successo;
- non allargare mai il perimetro oltre il topic target o oltre il supergruppo target.

Per `/clear_topic`:

- batch su tutti gli ID cancellabili del topic corrente.

Per `/clear_all_topic`:

- batch topic per topic, con lo stesso criterio di esclusione del root tecnico.

## Conferma Distruttiva

I nuovi comandi devono riusare il pattern già usato dai comandi distruttivi esistenti.

Pattern richiesto:

- preview di testo nel topic corrente;
- bottoni inline `Conferma` e `Annulla`;
- token pending con TTL;
- callback che esegue la pulizia solo dopo conferma esplicita.

Per coerenza con il comportamento richiesto:

- il messaggio di preview deve essere esso stesso eliminato durante la pulizia;
- il comando originale deve essere eliminato durante la pulizia;
- non deve restare un messaggio di esito finale.

## Autorizzazione

Regola generale:

- il comando deve essere ignorato se arriva da chat diverse dal supergruppo configurato;
- il comando deve essere rifiutato se l'utente non è autorizzato.

Eccezione voluta rispetto al comportamento attuale:

- `/clear_topic` e `/clear_all_topic` non devono essere soggetti al vincolo "solo topic commands";
- devono essere validi da qualunque topic del forum del supergruppo configurato.

Questo richiede una modifica specifica di `AuthValidator`, senza allargare il perimetro degli altri comandi.

## Lock e Concorrenza

Non serve persistenza a database per i lock.

Lock richiesti in memoria:

- `/clear_topic`: lock per `(chat_id, topic_id)`
- `/clear_all_topic`: lock per `chat_id`

Regole:

- se un topic è già in pulizia, un secondo `/clear_topic` sullo stesso topic viene ignorato;
- se è in corso `/clear_all_topic`, nuovi `/clear_topic` sullo stesso supergruppo vengono ignorati;
- se è in corso `/clear_all_topic`, un secondo `/clear_all_topic` sullo stesso supergruppo viene ignorato.

## Error Handling

### Comando fuori topic

Per `/clear_topic`:

- se il comando arriva fuori da un topic forum, la pulizia non parte.

### Utente non autorizzato

- si applica il comportamento standard del control plane.

### Messaggi già rimossi

- il servizio ignora l'errore sul singolo messaggio e continua.

### Rate limit o flood wait

- il servizio aspetta il tempo richiesto da Telegram e poi riprende.

### Errore parziale durante `/clear_all_topic`

- il servizio continua sugli altri topic;
- l'errore viene loggato lato runtime;
- non viene lasciato un messaggio finale nel forum.

## Osservabilità

Questa feature non deve introdurre:

- nuovi record applicativi di business;
- job persistiti;
- report finali nel topic.

Sono accettabili:

- log runtime tecnici per errori o retry;
- audit standard del comando già esistente nel control plane, se deriva dal router corrente.

Non è richiesto un nuovo audit store dedicato alla pulizia topic.

## Testing Strategy

### Test unitari auth/router

- `/clear_topic` accettato da topic arbitrario del supergruppo autorizzato
- `/clear_all_topic` accettato da topic arbitrario del supergruppo autorizzato
- stessi comandi ignorati da chat non autorizzata
- stessi comandi rifiutati per utente non autorizzato

### Test unitari callback/pending

- preview con keyboard inline
- conferma esegue il servizio
- annulla non esegue nulla
- token scaduto non esegue nulla

### Test unitari servizio cleanup

- filtra solo i messaggi del topic corrente
- esclude il root del topic
- include comando e preview tra gli ID da eliminare
- batch di massimo 100
- lock topic-level
- lock chat-level per `clear_all`
- gestione flood-wait con resume
- best effort su messaggi già cancellati

### Test specifici `/clear_all_topic`

- enumera più topic
- include il topic di origine
- prova a gestire `General`
- blocca clear concorrenti sullo stesso supergruppo

## Acceptance Contract

Done significa:

- i nuovi comandi sono disponibili nel control plane;
- richiedono conferma esplicita;
- usano Telethon per la pulizia reale;
- non eliminano i topic;
- non lasciano messaggi finali nel forum.

Criteri osservabili:

1. `/clear_topic` svuota solo il topic corrente.
2. `/clear_all_topic` svuota tutti i topic del supergruppo.
3. Entrambi funzionano da qualunque topic del supergruppo autorizzato.
4. Entrambi restano bloccati a chat e utenti autorizzati.
5. Al termine può restare solo il messaggio root tecnico non eliminabile.

Segnale primario:

- stato finale dei topic su Telegram.

Segnali secondari:

- test unitari del router/auth;
- test unitari del servizio cleanup;
- assenza di regressioni nel flusso di conferma distruttiva esistente.

## Rischi e Note di Implementazione

- L'enumerazione di tutti i topic del forum via Telethon va verificata contro le API realmente già usate nel progetto.
- Il wiring del client Telethon nel control plane deve evitare la creazione di un secondo client concorrente.
- Il comportamento di `General` dipende da come Telethon espone quel thread; il design richiede di pulirlo quando tecnicamente enumerabile e pulibile.
- I comandi esistenti non devono cambiare perimetro di autorizzazione.

## Non Obiettivi

- eliminare i topic;
- introdurre un workflow persistito;
- creare una UI diversa dai bottoni inline già in uso;
- usare il database come fonte di verità per i messaggi da cancellare.
