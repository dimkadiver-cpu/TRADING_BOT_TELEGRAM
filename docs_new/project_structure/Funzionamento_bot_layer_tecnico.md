# Funzionamento del bot — versione più tecnica a layer  
`TRADING_BOT_TELEGRAM`

## 1. Obiettivo del sistema

Il bot acquisisce messaggi Telegram da sorgenti configurate, li persiste come eventi raw, li classifica con parser per-trader, applica regole operative e aggiorna uno stato interno utilizzabile da moduli successivi.

Non è quindi solo un listener, ma una pipeline composta da più layer con responsabilità separate.

---

## 2. Vista architetturale a layer

Schema logico sintetico:

```text
Telegram
  ↓
[Layer 0] Bootstrap / Config / Migrations
  ↓
[Layer 1] Listener Telegram + Intake live/catchup
  ↓
[Layer 2] Raw persistence + processing status + queue
  ↓
[Layer 3] Routing / trader resolution / eligibility
  ↓
[Layer 4] Parsing / normalization / parse_results
  ↓
[Layer 5] Operation rules / target resolution / operational signals
  ↓
[Layer 6] Runtime state updates / signals / execution-facing data
```

Layer trasversali:
- logging;
- hot reload config canali;
- recovery al restart;
- review queue;
- dynamic pairlist.

---

## 3. Layer 0 — Bootstrap e infrastruttura

## Responsabilità
Questo layer prepara l’ambiente prima che inizi il flusso dei messaggi.

## Componenti principali
- `main.py`
- loader `.env`
- `apply_migrations(...)`
- `load_channels_config(...)`
- `load_config(...)`
- setup logger
- costruzione dei service/store principali

## Cosa fa
All’avvio il bot:

1. carica variabili ambiente;
2. applica le migration DB;
3. legge la configurazione generale;
4. legge `config/channels.yaml`;
5. costruisce i componenti principali:
   - listener
   - router
   - store DB
   - resolver
   - motore di operation rules
6. avvia il watcher della config;
7. crea il client Telethon;
8. lancia:
   - listener live
   - recovery iniziale
   - worker queue

## Output del layer
Un processo pronto a:
- ricevere nuovi messaggi;
- fare catchup dei messaggi persi;
- elaborare la coda interna.

---

## 4. Layer 1 — Acquisizione Telegram

## Responsabilità
Ricevere i messaggi da Telegram e trasformarli in eventi interni minimi, senza fare ancora parsing profondo.

## Componenti principali
- `TelegramListener`
- handler `events.NewMessage`
- `_handle_new_message(...)`
- `_ingest_and_enqueue(...)`
- `_catchup_from_telegram(...)`

## Flussi gestiti
### 4.1 Live
Messaggi ricevuti in tempo reale tramite Telethon.

### 4.2 Catchup / recovery
Messaggi recuperati al riavvio interrogando Telegram entro una finestra temporale configurata.

## Cosa fa
Per ogni messaggio:
- legge `chat_id`;
- verifica se la chat è ammessa;
- scarta alcuni messaggi non utili, ad esempio media-only;
- raccoglie dati base:
  - chat
  - message id
  - testo
  - reply_to
  - timestamp
- passa il messaggio al layer di ingestione.

## Principio chiave
Questo layer è volutamente leggero: deve acquisire rapidamente, non prendere decisioni complesse.

---

## 5. Layer 2 — Persistenza raw, stato di lavorazione e coda

## Responsabilità
Persistire subito il messaggio grezzo e gestire il ciclo di vita di lavorazione.

## Componenti principali
- `RawMessageIngestionService`
- `RawMessageStore`
- `ProcessingStatusStore`
- coda `asyncio.Queue`
- dataclass di trasporto:
  - `TelegramIncomingMessage`
  - `QueueItem`
  - `StaleMessage`

## Cosa viene salvato
A livello concettuale il record raw contiene:
- sorgente (`source_chat_id`);
- id messaggio Telegram;
- testo originale;
- reply parent;
- timestamp del messaggio;
- stato acquisizione;
- eventuali informazioni media.

## Stato di lavorazione
Il bot tiene anche uno stato di processing dei raw messages, per esempio:
- pending
- processing
- done
- failed
- blacklisted
- review

## Perché serve
Questo layer garantisce:
- durabilità del messaggio prima del parsing;
- possibilità di recovery dopo crash o restart;
- disaccoppiamento tra intake rapido e analisi lenta;
- audit del flusso.

## Coda interna
Dopo il salvataggio raw, il messaggio viene inserito in una queue interna.  
Un worker separato lo leggerà e lo inoltrerà al router.

---

## 6. Layer 3 — Routing, risoluzione trader, eleggibilità

## Responsabilità
Decidere come il messaggio deve essere trattato prima del parsing vero e proprio.

## Componenti principali
- `MessageRouter`
- `EffectiveTraderResolver`
- `MessageEligibilityEvaluator`
- `TelegramSourceTraderMapper`
- blacklist globale / per source
- `ReviewQueueStore`

## Sotto-passaggi
### 6.1 Blacklist
Il router verifica se il testo rientra in pattern o tag da ignorare.

### 6.2 Trader resolution
Il sistema cerca di capire a quale trader appartiene il messaggio, usando:
- mapping sorgente → trader;
- config del canale;
- contesto reply;
- dati già presenti nel DB.

### 6.3 Eligibility
Il sistema decide se il messaggio è trattabile nel flusso:
- messaggio valido e utile;
- messaggio da review;
- messaggio da ignorare;
- messaggio non processabile.

## Esiti possibili
- il messaggio procede al parsing;
- il messaggio viene marcato come `review`;
- il messaggio viene chiuso come `blacklisted` o equivalente;
- il messaggio viene lasciato come raw ma non entra nel flusso operativo.

## Ruolo del router
Il router è il punto in cui la pipeline decide:  
“questo raw message va trasformato in informazione strutturata oppure no”.

---

## 7. Layer 4 — Parsing e normalizzazione

## Responsabilità
Interpretare il testo Telegram e produrre una struttura coerente e normalizzata.

## Componenti principali
- registry parser per trader
- `ParserContext`
- parser profile-specific
- validazione strutturale
- `ParseResultStore`

## Classi logiche principali dei messaggi
Il parser tende a classificare i messaggi in categorie come:
- `NEW_SIGNAL`
- `UPDATE`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `UNCLASSIFIED`

## Dati estratti
A seconda del testo, il parser prova a estrarre:
- simbolo;
- side;
- entry;
- stop;
- target;
- leverage/risk hints;
- riferimenti ad altri messaggi;
- intenti operativi.

## Normalizzazione
Il parser non si limita a “leggere testo”, ma trasforma il contenuto in una forma comune tra trader diversi.

Questo layer produce tipicamente:
- `message_type`
- `entities`
- `intents`
- `target_refs`
- `warnings`
- `confidence`
- struttura normalizzata finale

## Persistenza del risultato
L’output del parsing viene salvato in `parse_results`, separando chiaramente:
- raw message originale;
- interpretazione del parser.

## Valore del layer
Questo è il confine tra:
- messaggio umano non strutturato;
- dato macchina normalizzato.

---

## 8. Layer 5 — Operation rules e target resolution

## Responsabilità
Convertire il parse result in un segnale operativo coerente con le regole del sistema.

## Componenti principali
- `OperationRulesEngine`
- `TargetResolver`
- `OperationalSignalsStore`
- `SignalsStore`

## Sotto-passaggi
### 8.1 Operation rules
Il motore applica regole di business, ad esempio:
- blocco di segnali non accettabili;
- derivazione del rischio;
- calcolo size;
- warning operativi;
- impostazioni di management.

### 8.2 Costruzione del segnale operativo
Dal parse result viene prodotto un oggetto più operativo, con dati già pronti per uso runtime.

### 8.3 Target resolution
Per i messaggi di tipo `UPDATE`, il sistema prova a capire a quale posizione/segnale esistente si riferiscono.

Questo è essenziale perché molti update Telegram non sono auto-consistenti: hanno senso solo se collegati al segnale di origine.

## Esiti possibili
- target risolto correttamente;
- target ambiguo/non risolto;
- messaggio operativo salvato ma non applicabile;
- invio in review in caso di incertezza.

---

## 9. Layer 6 — Aggiornamento dello stato runtime

## Responsabilità
Applicare gli effetti degli update allo stato interno dei segnali/posizioni.

## Componenti principali
- planner/update applier
- `apply_update_plan(...)`
- `build_update_plan(...)`
- tabelle operative
- dynamic pairlist manager

## Cosa succede
Se il messaggio è un `UPDATE` valido e il target è stato risolto:
- gli intenti vengono convertiti in azioni;
- viene costruito un piano di update;
- il piano viene applicato allo stato interno.

Esempi concettuali:
- spostamento stop;
- chiusura parziale;
- chiusura completa;
- aggiornamento stato di fill;
- aggiornamento risultati.

## Segnali iniziali
Per i `NEW_SIGNAL`, il sistema può inserire un record iniziale in `signals` e aggiornare anche altre strutture collegate, come la dynamic pairlist.

## Risultato del layer
Il sistema passa da:
- “ho letto un messaggio”
a
- “ho aggiornato uno stato operativo interno coerente”.

---

## 10. Layer trasversale A — Recovery e resilienza

## Responsabilità
Evitare perdita di messaggi in caso di crash, stop o riavvio.

## Come funziona
Al restart il bot:

1. rimette in coda messaggi rimasti in `pending` o `processing`;
2. chiede a Telegram messaggi recenti sopra l’ultimo id noto;
3. reingesta e reaccoda i messaggi mancanti.

## Perché è importante
Telegram listener live da solo non basta.  
Serve anche una logica di catchup per coprire:
- downtime;
- restart volontari;
- eccezioni;
- problemi di rete.

---

## 11. Layer trasversale B — Configurazione dinamica

## Responsabilità
Permettere al bot di reagire ai cambi di configurazione senza restart completo.

## Componenti principali
- `ChannelConfigWatcher`
- reload di `channels.yaml`

## Cosa aggiorna
Principalmente:
- canali/chat attive;
- mapping trader fallback per source;
- blacklist specifiche per source.

## Effetto
Il bot può cambiare comportamento su nuove sorgenti o nuove regole senza dover essere fermato e riavviato.

---

## 12. Layer trasversale C — Review queue

## Responsabilità
Separare i messaggi che non possono essere processati automaticamente in modo affidabile.

## Quando viene usata
Esempi:
- trader non risolto;
- target update non risolto;
- ambiguità forte;
- casi in cui la pipeline non vuole inventare.

## Effetto
Il sistema non perde il messaggio, ma evita di applicare automaticamente qualcosa di incerto.

---

## 13. Layer trasversale D — Logging e audit tecnico

## Responsabilità
Rendere osservabile il comportamento del sistema.

## Tipi di eventi logici
- acquisizione raw;
- duplicate skip;
- blacklist;
- parse persistito;
- target unresolved;
- update applied;
- recovery catchup;
- failure in worker/router.

## Perché è importante
Questo progetto ha una pipeline lunga; senza log chiari è difficile capire:
- dove si ferma un messaggio;
- se un update è stato applicato;
- se il problema è nel parser, nel targeting o nel runtime.

---

## 14. Sequenza completa di un messaggio

Esempio tecnico semplificato:

1. Telegram invia un nuovo messaggio.
2. Il listener lo riceve.
3. Il messaggio viene filtrato a livello base.
4. Il raw viene salvato nel DB.
5. Lo stato entra in `pending`.
6. Il messaggio entra in queue.
7. Il worker lo preleva.
8. Il router risolve trader ed eleggibilità.
9. Il parser produce il risultato normalizzato.
10. Il parse result viene salvato.
11. Se valido, entrano in gioco operation rules.
12. Se è un update, si prova a risolvere il target.
13. Se il target è risolto, l’update viene applicato allo stato interno.
14. Il messaggio viene marcato come `done`.

---

## 15. Mappa dei principali artefatti dati

## Dati in ingresso
- messaggi Telegram
- config canali/chat
- mapping trader
- regole operative

## Dati intermedi
- raw messages
- processing status
- queue items
- parse results
- review queue items

## Dati operativi
- signals
- operational signals
- stato runtime aggiornato
- eventuali pairlist dinamiche

---

## 16. Stato attuale della separazione delle responsabilità

La pipeline è già abbastanza ben separata:

- intake Telegram separato dal parsing;
- raw persistence separata dal routing;
- routing separato dai parser;
- parser separato dalle operation rules;
- operation rules separate dall’update runtime.

Questo è utile perché permette di modificare un layer senza dover rifare tutto il resto.

---

## 17. Punto chiave da ricordare

La logica del bot non è lineare in senso banale.  
Non è:

> arriva messaggio → apro trade

È invece:

> arriva messaggio → salvo raw → verifico contesto → risolvo trader → classifico → normalizzo → applico regole → collego a stato esistente → aggiorno stato

Questo è il motivo per cui il progetto ha più layer e più store.

---

## 18. Riassunto tecnico breve

Versione compatta:

- **Layer 0** prepara ambiente, config e servizi.
- **Layer 1** acquisisce messaggi Telegram live e catchup.
- **Layer 2** persiste raw, gestisce processing status e queue.
- **Layer 3** risolve trader, blacklist ed eleggibilità.
- **Layer 4** esegue parsing e normalizzazione.
- **Layer 5** applica operation rules e risolve target.
- **Layer 6** aggiorna segnali e stato runtime.

Il risultato finale è una pipeline che trasforma messaggi Telegram non strutturati in stato operativo coerente e persistito.
