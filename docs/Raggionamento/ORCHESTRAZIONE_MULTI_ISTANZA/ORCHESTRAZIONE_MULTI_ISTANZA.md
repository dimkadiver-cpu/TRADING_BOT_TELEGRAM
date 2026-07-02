# Orchestrazione Multi-Istanza - Design Spec
**Data:** 2026-06-30 (rev. 2026-07-02)
**Stato:** Rivisto - architettura B + Sistema approvati; revisione punto-per-punto del 2026-07-02 applicata

> Revisione 2026-07-01: adottato il **Modello B** (ingestione per-Sistema + esecutori
> per-istanza), isolamento rigido `DEMO`/`LIVE`, catalogo trader globale, modello
> account allineato al runtime reale, vocabolario detection allineato al codice.
> Le decisioni sono elencate nella sezione finale.
>
> Revisione 2026-07-01 (2ª passata, sfida al design): introdotto il **Sistema** come
> unita' di deployment (`Server -> Sistema -> Istanza`). Ingestione **per-Sistema**
> (una sessione Telethon, un `parser.sqlite3` per Sistema), canali ascoltati **derivati**
> dalle sottoscrizioni, migrazioni dello shared DB applicate **solo dall'ingestione**,
> regole del consumo per-esecutore fissate (feed canonical condiviso, cursori locali,
> idempotenza, backlog sicuro), macchina a stati
> completata (`stop`, drift), canary **per-Sistema** (un solo server basta).
>
> Revisione 2026-07-02 (3ª passata, revisione punto-per-punto con l'utente): definita la
> semantica **edit/delete sotto fan-out** (decisione all'esecutore), chiarito il vincolo
> del flusso legacy sul parser DB, **fresh start** alla migrazione (nessuna eredita' di
> `ops.sqlite3`), **claim account al deploy**, separati inizializzazione cursore e policy
> di riavvio, completata la macchina a stati (`apply`, `error`), attribuite le blacklist
> (testo=fonte, simboli=istanza), sessioni Telethon su un solo numero, aggiornamenti a
> **fermo coordinato per-Sistema** (additive-only non piu' vincolante), alert su gap di
> retention, `parser_profile` agganciato al **registry parser_v2**, migrazione Telegram
> senza riuso (gruppi nuovi per tutte le istanze).

---

## Obiettivo operativo

TeleSignalBot oggi gira come istanza singola su un server. L'obiettivo e' introdurre un workflow standard per aprire e gestire piu' istanze indipendenti, dove ogni istanza rappresenta una unita' operativa autonoma con config, dati, credenziali e ciclo di trading propri.

Un'istanza non e' necessariamente dedicata a una sola fonte. Una singola istanza puo' gestire:

1. una o piu' fonti Telegram;
2. uno o piu' trader/profile;
3. uno o piu' account exchange;
4. un proprio gruppo Telegram di controllo e notifica, **oppure nessuno** (istanza muta).

Il workflow deve supportare sia:

1. creazione di una nuova istanza autonoma;
2. aggiunta di nuove fonti, trader o account exchange a una istanza gia' esistente.

Il sistema deve supportare:
- N istanze del bot, ciascuna con config, dati e credenziali proprie
- scelta esplicita del tipo istanza in creazione: `DEMO` oppure `LIVE`, con **isolamento rigido** (un'istanza non mescola account demo e live)
- associazione tra istanza, fonti Telegram, trader/profile, account exchange e destinazioni Telegram
- gestione scalabile di fonti con molti trader senza duplicazione massiva di alias, topic e binding
- **condivisione di fonti e trader tra piu' istanze** senza duplicare l'ascolto Telegram
- generazione automatica dei file di configurazione runtime
- controllo centralizzato dello stato operativo delle istanze

---

## Architettura a due piani (Modello B)

Questa e' la decisione architetturale portante della revisione. Il sistema separa
**chi ascolta e capisce il segnale** da **chi lo esegue e notifica**.

### Gerarchia Server -> Sistema -> Istanza

Il **Sistema** e' l'unita' di deployment: un clone del repo + un processo di ingestione +
N istanze-esecutori, **tutti sullo stesso host**. E' il confine di condivisione: dentro il
Sistema si condividono codice, `parser.sqlite3` e sessione Telethon; fuori, niente.
Un Sistema e' tipato `DEMO` o `LIVE` e contiene solo istanze del proprio tipo. Piu'
Sistemi possono coabitare sullo stesso server fisico, ognuno col proprio clone a
revisione propria.

### Piano di ingestione (per Sistema)

- Ogni Sistema ha **un solo** processo di ingestione: una sessione Telethon che ascolta
  **tutti** i canali derivati dalle sottoscrizioni delle sue istanze e scrive un unico
  `parser.sqlite3` per Sistema. E' il runtime di oggi, promosso a servizio dedicato.
- Dentro il Sistema, l'ingestione produce il **segnale capito condiviso** (raw,
  canonical, trader risolto) **una volta sola**: nessuna interpretazione divergente del
  testo Telegram tra le istanze dello stesso Sistema.
- L'**enrichment di esecuzione** (account, risk, management plan, policy snapshot) resta
  **per-istanza**, perche' dipende da `operation_config.yaml`, `config/traders/<id>.yaml`
  e dai binding account della singola istanza. Il feed condiviso non possiede account.
- I canali ascoltati sono **derivati**: l'unione delle fonti sottoscritte dalle istanze
  del Sistema. Nessuna assegnazione manuale fonte -> listener; la config di ingestione
  e' generata, come `registered_traders`. Non puo' esistere un'istanza iscritta a una
  fonte che il suo Sistema non ascolta.
- **Tra Sistemi diversi** la stessa fonte puo' essere ascoltata piu' volte (un'ingestione
  per Sistema): la doppia lettura e' accettata e tracciata — e' esattamente il meccanismo
  del canary (il Sistema DEMO legge la stessa fonte del LIVE con codice piu' nuovo). Il
  numero di Sistemi resta piccolo (tipicamente 2), quindi il rischio Telegram non scala
  con le istanze.

### Piano di esecuzione (per istanza)

- Ogni **istanza** e' un esecutore + notificatore, legato ai propri **account** e al
  proprio (eventuale) gruppo Telegram.
- Un'istanza si **iscrive** alle fonti che le interessano e riceve in **fan-out** il
  segnale gia' capito.
- Un'istanza "muta" (senza gruppo Telegram) e' in pratica **solo un esecutore** agganciato
  all'ingestione del proprio Sistema.

### Regola di scaling

> **L'ingestione conta i Sistemi, gli esecutori contano le istanze.**

Esempio: 10 istanze dentro lo stesso Sistema, distribuite su 4 fonti, richiedono
**1 processo di ingestione** e **10 esecutori**. Aggiungere l'11ª istanza costa **zero
ingestione** (al piu' un canale in piu' nello stesso processo) e **un esecutore**.
Aggiungere un Sistema (es. il canary DEMO) costa un'ingestione.

### Invariante da preservare

> **L'account/esecutore deve essere indirizzabile indipendentemente da chi ascolta.**
> L'ingestione non deve mai "possedere" l'account.

Finche' questo invariante regge, il modello dati resta relazionale e account-centrico,
e l'evoluzione dell'infrastruttura di distribuzione (da DB condiviso a bus dedicato)
resta un cambio di runtime, non una ri-modellazione.

### Split dei dati

- **`parser.sqlite3` (segnale capito condiviso)** -> **condiviso a livello di Sistema**.
  Raw, canonical e risultati parser vivono qui, prodotti una volta dall'ingestione del
  Sistema. Gli esecutori lo leggono come feed condiviso, ma non lo scrivono.
- **`ops.sqlite3` (enrichment per-istanza + ordini/posizioni/fill/PnL)** -> **locale per
  istanza/account**.
  **Nessuna potatura**: e' la storia contabile dell'istanza (audit, statistiche,
  ricostruzione); un'eventuale archiviazione dei trade chiusi e' fuori dal primo design.

Questa separazione e' gia' anticipata dal runtime attuale (`parser.sqlite3` e `ops.sqlite3`
sono file distinti e la pipeline `runtime_v2` gia' separa il messaggio canonico
dall'execution gateway).

### Fan-out, feed canonical e cursore per esecutore

**Decisione aggiornata.** Nel Modello B l'ingestione per-Sistema produce il feed
condiviso **canonical/trader-resolved**, non l'enrichment finale di esecuzione. Il feed
degli esecutori e' una vista logica sulle tabelle esistenti:

```text
canonical_messages JOIN raw_messages
```

Il canonical contiene classe, intent e payload; `raw_messages` contiene fonte Telegram e
trader risolto. L'esecutore legge solo candidati trading:

- `parse_status IN ('PARSED', 'PARTIAL')`
- `primary_class IN ('SIGNAL', 'UPDATE')`
- `resolved_trader_id IS NOT NULL`
- `source_key` sottoscritta dall'istanza
- `run_context = 'live' OR run_context LIKE 'edit:%'` (i contesti `delete:` e altri
  restano fuori dal feed di esecuzione)

**Edit e delete sotto fan-out (decisione della revisione 2026-07-02).** Oggi il listener,
su un messaggio editato, decide da solo se riprocessarlo consultando le trade chain
(`ops.sqlite3`) — stato di **esecuzione per-istanza** che l'ingestione condivisa non puo'
e non deve vedere: con N istanze la risposta "chain esistente?" e' diversa per ognuna.
La regola nel Modello B:

- l'**ingestione e' "stupida"**: su un edit registra sempre la revisione
  (`raw_message_revisions`), aggiorna il raw e ri-parsa producendo un nuovo canonical con
  `run_context = 'edit:<ts>'` — **senza mai consultare le chain** e senza decidere nulla;
- la **decisione migra all'esecutore**: quando il worker incontra un canonical `edit:`,
  controlla le **proprie** chain nel proprio `ops.sqlite3`: chain esistente -> skip +
  notifica sul proprio gruppo (stesso comportamento di oggi, ma per-istanza); nessuna
  chain -> applica l'edit come sostituzione del segnale originale. L'ordine per fonte del
  cursore garantisce che l'edit arrivi dopo l'originale: la logica e' locale;
- i canonical `delete:` non entrano nel feed di esecuzione (solo osservabilita', come oggi);
- **regola di lettura**: gli esecutori consumano il `canonical_json` (snapshot immutabile);
  **non devono mai rileggere `raw_text`** per decisioni di trading — il raw viene mutato
  in place dagli edit ed e' dato condiviso sotto i cursori.

`source_key` e' l'identita' runtime della fonte: `"{source_chat_id}:{source_topic_id_or_0}"`.
Le PK e le label di `management.db` restano control-plane; il runtime consuma questa chiave
derivata dai dati Telegram reali.

**Enrichment per-istanza.** L'esecutore non consuma `enriched_canonical_messages` globale.
Per ogni canonical candidato chiama un builder estratto dalla logica attuale:

```python
build_enriched_for_instance(canonical, raw_context, trader_id, source_key, config_loader, account_binding)
```

Il builder applica `operation_config.yaml`, `config/traders/<id>.yaml`, binding account,
blacklist, risk e management plan dell'istanza, producendo l'`EnrichedCanonicalMessage`
che il lifecycle sa gia' processare.

**Stato locale dell'esecutore.** Cursori ed enrichment per-istanza vivono nell'`ops.sqlite3`
locale, non in `management.db` e non nel `parser.sqlite3` condiviso. Il parser DB resta
single-writer: l'ingestione scrive raw/canonical/parser results; gli esecutori leggono.

Tabelle locali previste:

```sql
ops_instance_source_cursors (
  source_key TEXT PRIMARY KEY,
  last_canonical_message_id INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
)

ops_instance_enriched_events (
  id INTEGER PRIMARY KEY,
  canonical_message_id INTEGER NOT NULL,
  raw_message_id INTEGER NOT NULL,
  source_key TEXT NOT NULL,
  trader_id TEXT,
  account_id TEXT,
  primary_class TEXT NOT NULL,
  enrichment_decision TEXT NOT NULL,
  reason_code TEXT,
  enriched_payload_json TEXT,
  status TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  processed_at TEXT,
  UNIQUE(canonical_message_id)
)
```

`status` minimo: `pending`, `processing`, `done`, `skipped`, `error`, `dead_letter`.
Il cursore avanza solo dopo stato terminale (`done`, `skipped`, `dead_letter`). Uno stato
`processing` vecchio puo' essere reclamato; `pending`/`error` sono ritentabili.

**Backlog sicuro: due meccanismi distinti (chiariti nella revisione 2026-07-02).**
`recovery.max_hours` resta una policy dell'ingestione Telethon. Per l'esecutore vanno
tenuti separati due momenti che rispondono a domande diverse:

1. **Prima iscrizione** (cursore inesistente): il cursore si inizializza **sempre** a
   `MAX(canonical_message_id)` — live-only, niente replay della storia. Non e' una policy
   configurabile: eventuale recupero storico solo con flag esplicito su `source subscribe`.
2. **Riavvio** (cursore esistente): l'esecutore **recupera sempre il backlog** dal cursore
   in poi, protetto dalle regole anti-stale: un `SIGNAL` arretrato oltre
   `max_signal_age_minutes` non apre trade; un `UPDATE/CLOSE` arretrato si applica solo se
   trova una chain aperta coerente, altrimenti diventa no-op/review terminale e il cursore
   avanza.

Il campo `resume_policy` della prima bozza (default ambiguo `catch_up_available`) e'
rimosso: i due comportamenti sopra non sono configurazione, sono la semantica del cursore.

**Nota legacy.** Il testo sotto descrive il problema storico del cursore globale; nella
nuova implementazione multi-istanza `enriched_canonical_messages.lifecycle_processed` resta
solo compatibilita' single-instance/legacy finche' il nuovo worker non sostituisce il flusso.

Il pezzo runtime piu' delicato di B. Il problema: oggi la pipeline avanza uno **stato sul
messaggio** (`raw_messages.processing_status`), che vale per **un solo** consumatore. Sotto
fan-out lo stesso segnale enriched e' consumato da **N esecutori**: uno status sul messaggio
non basta (se α marca `done`, β non lo vede piu'). Serve una **posizione per-consumatore**.

**Regole di consumo per esecutore (high-water-mark).** *(In una prima versione il cursore
era previsto sull'enriched globale; superato perche' l'enrichment e' per-istanza. Le regole
seguenti sono **vigenti** e si applicano al feed canonical.)*

- Ogni esecutore tiene un **cursore** per ogni fonte a cui e' iscritto: "ultimo
  `canonical_message_id` processato". Legge i segnali con `id > cursore ORDER BY id`,
  processa, avanza. E' lo stesso idioma di `ops_trade_chains.last_projected_event_id`
  gia' presente nel runtime.
- **Ordine stretto per fonte** = feature, non limite: un ingresso e il suo update/cancel
  successivo devono applicarsi in ordine; l'high-water-mark lo impone.
- **Semantica di fallimento sicura**: se il segnale N fallisce, il cursore **non avanza**,
  l'esecutore ritenta N e non esegue nulla dopo N (mai eseguire l'update di una posizione
  non aperta).
- **Poison message**: se N e' permanentemente non processabile per un esecutore, dopo K
  retry viene marcato **dead-letter** in una piccola lista locale, il cursore avanza e
  parte un alert tech. High-water-mark per il caso normale, dead-letter per l'eccezione.
- **Cursore iniziale = `MAX(id)` al deploy (no replay)**: quando un'istanza si iscrive a
  una fonte con storia, parte dai soli segnali futuri. Mai rieseguire segnali storici:
  sarebbero ordini reali su trade morti.
- **Idempotenza + avanzamento atomico**: l'esecutore registra l'esito e avanza il cursore
  **nella stessa transazione** sul proprio `ops.sqlite3`; prima di eseguire controlla
  l'identita' del segnale (base runtime: `014_ops_signal_identity`). Un segnale gia'
  registrato non si riesegue, anche se il cursore e' rimasto indietro per un crash:
  niente doppio ordine.
- **Retention per eta' (solo parser DB)**: l'ingestione pota i segnali piu' vecchi di una
  finestra generosa (es. 60 giorni), molto piu' larga del massimo ritardo di recupero di
  un esecutore fermo. `min(cursori)` non e' praticabile: i cursori vivono negli
  `ops.sqlite3` locali che l'ingestione non vede.
- **Alert su gap di retention**: se un'istanza resta ferma oltre la finestra, al risveglio
  il suo cursore punta a segnali gia' potati. All'avvio il worker controlla per ogni fonte:
  se `cursore < MIN(canonical_message_id disponibile)` -> **alert tech esplicito** ("fonte
  X: gap di N segnali potati") e riparte dal primo disponibile. Il buco diventa visibile,
  mai attraversato in silenzio (il rischio trading resta basso: quei segnali sarebbero
  comunque scartati dalle protezioni anti-stale).

**Dove vive il cursore.** Il cursore e' **stato runtime ad alta frequenza** e vive
nell'**`ops.sqlite3` locale** dell'esecutore, **non** in `management.db`. La sottoscrizione
(quale istanza consuma quale fonte) e' control-plane; il *progresso di consumo* e' runtime.
Metterlo nel control plane creerebbe contesa in scrittura centrale a ogni segnale.

**Topologia un-writer / molti-reader (invariante mantenuto nel modello aggiornato).**

- L'**ingestione** del Sistema e' l'unica **writer** del `parser.sqlite3` condiviso (id
  monotono); e' anche l'unica ad applicare le **migrazioni** dello shared DB (gli
  esecutori aprono senza migrare).
- Ogni **esecutore** e' un **reader** del parser DB: nel modello aggiornato legge
  `canonical_message_id > cursore_locale`, processa nel proprio `ops.sqlite3`, avanza il
  cursore locale.
- **Wake**: poll dello shared DB a intervallo breve (`MAX(canonical_message_id) > cursore`
  e' banale); WAL
  (gia' attivo: `db/migrations/001_init.sql` sul parser DB; `017_ops_enable_wal.sql` copre
  l'ops DB) regge bene un writer + molti reader. Il vincolo WAL (stesso host, stesso
  filesystem locale) e' **garantito per costruzione**: ingestione ed esecutori dello
  stesso Sistema coabitano per definizione. Un canale di notifica e' un'ottimizzazione
  futura.

---

## Workflow tipico

Il workflow approvato e' semi-guidato: il sistema automatizza la preparazione e la validazione, mentre i passaggi sensibili restano confermati o completati dall'operatore.

### Due piani di lavoro

Il flusso si divide in due piani con **blast radius diverso**, coerenti con
l'architettura a due piani:

- **Piano condiviso (setup)** - definisce gli oggetti globali: pool account, catalogo
  trader, fonti. Operazioni **rare, lente, sensibili**; una modifica qui **impatta tutte
  le istanze** che usano quell'oggetto.
- **Piano istanza (assembly)** - costruisce una singola istanza **scegliendo** tra gli
  oggetti gia' esistenti: iscrizione a fonti, claim account, policy, provisioning, deploy.
  Operazioni **veloci e isolate** alla singola istanza.

**Regola del confine (wizard netto).** Se durante l'assembly serve un oggetto che non
esiste ancora (es. una fonte nuova), il wizard **si ferma**: prima lo crei nel piano
condiviso, poi torni all'assembly. Non si creano oggetti globali "di nascosto" dentro la
creazione di un'istanza, perche' sono operazioni che riguardano anche le altre istanze.

### Piano condiviso - setup (prerequisiti)

0. **Bootstrap (una tantum)** - prima di tutto il resto:
   - `server add`: registra il server (host, utente; la chiave SSH deve essere gia'
     autorizzata sul server - prerequisito manuale);
   - `sistema create`: crea il Sistema (nome, server, tipo) e ne fa il primo deploy
     (struttura directory, **clone del repo**, servizio di ingestione);
   - `provider add`: carica le credenziali **master** del provider per ambiente
     (`provider_credentials`), necessarie ad `account provision`;
   - `telethon login`: genera le sessioni Telethon in modo **interattivo** (telefono +
     codice di conferma): una per l'ingestione di ogni Sistema, una per il provisioning.
1. **Pool account** - `account provision` in bulk popola il pool (demo/live) in stato `available`.
2. **Catalogo trader** - `trader catalog add` definisce ogni trader una volta
   (`trader_id`, nome leggibile, profilo parser); alias e pattern restano nei frammenti
   `config/patterns/<gruppo>.yaml`.
3. **Fonti** - `source register`: canale + parser **della fonte** + `resolution` +
   **membership trader** (quali trader porta). Nessuna assegnazione di listener:
   l'ascolto e' **derivato** dalle sottoscrizioni (ingestione per Sistema).

> **Parser della fonte (Modello A, fedele al codice).** Il parser e' della **fonte**:
> per una fonte a trader singolo defaulta al parser di quel trader; per una fonte
> multi-trader e' **comune** a tutti i trader del topic. Per avere parser diversi per
> trader si usano **fonti/topic separati** (come oggi). Il parser per-trader dentro la
> stessa fonte multi-trader non e' supportato dal runtime attuale: eventuale estensione
> futura.
>
> **Blast radius.** `source edit` e `trader catalog edit` modificano oggetti globali:
> devono **avvisare "consumato da N istanze"** e richiedere conferma esplicita.

### Piano istanza - assembly (scenario base)

1. `instance create --type DEMO|LIVE --sistema ... [--muted]` -> stato `draft`
   (il tipo deve coincidere con quello del Sistema).
2. Associa gruppo/bot Telegram dell'istanza (oppure flag muta).
3. **Iscrizione** alle fonti che l'istanza deve consumare (`source subscribe`). Se serve
   una fonte nuova, il wizard si ferma (regola del confine): la crei nel piano condiviso,
   poi riprendi.
4. **Selezione** degli account dal **pool** (solo del tipo giusto). E' solo stato
   desiderato: il **claim** effettivo (`available -> assigned`) avviene al `deploy`
   (decisione 2026-07-02) — un draft abbandonato non blocca conti del pool. Se al deploy
   il conto selezionato non e' piu' disponibile, il deploy fallisce con motivo chiaro e
   si riseleziona.
5. **Policy account** per fonte + eventuale **binding trader -> account** (override).
   *Questo e' per-istanza:* la stessa fonte, su istanze diverse, esegue su conti diversi.
6. **Policy/destinazioni Telegram** (saltate se l'istanza e' muta).
7. `provision prepare` genera in automatico: record in `management.db`, struttura
   filesystem, YAML runtime, `.env`, mapping di iscrizioni e destinazioni.
8. `provision telegram` crea in automatico gruppo + topic e cattura i `thread_id` (se non muta).
9. `validate` controlla coerenza e completezza, incluse le **collisioni alias**; se passa -> `ready`.
10. `deploy` installa l'esecutore e aggiorna l'ingestione del Sistema (canali derivati) -> `deployed`.
11. `start` avvia esplicitamente -> `active`.

### Cosa e' condiviso e cosa e' per-istanza

Due regole che chiariscono la separazione:

- **Membership trader -> fonte = globale** (definita sulla fonte, una volta): descrive
  quali trader la fonte puo' produrre.
- **Binding trader -> account = per-istanza** (gli account sono claimati per-istanza):
  descrive quali trader sono davvero attivi nell'istanza e dove vengono eseguiti.

In breve: *chi puo' esserci nella fonte* e' condiviso; *quali trader uso e dove li
eseguo* e' dell'istanza. Questo e' il fan-out del Modello B espresso nel workflow.

### Quando creare una nuova istanza

La creazione di una nuova istanza e' giustificata quando serve isolamento operativo su almeno uno di questi assi:

- account exchange separati (in particolare passaggio da `DEMO` a `LIVE`)
- ciclo di deploy/start/stop separato
- gruppo Telegram separato
- stato di esecuzione separato (`ops.sqlite3` proprio)

Nota: sotto il Modello B, **non** serve creare una nuova istanza solo per ascoltare una
fonte gia' ascoltata. Se il caso e' "stesse fonti/trader, account diversi", si crea una
nuova istanza-esecutore che si **iscrive** alle fonti esistenti (eventualmente muta).

### Modifica di una istanza esistente

Una istanza gia' `active` deve poter essere evoluta senza essere ricreata da zero. Il caso operativo tipico e':

1. esiste una istanza attiva;
2. l'operatore vuole aggiungere una nuova fonte (nuova o gia' esistente);
3. la fonte porta con se' uno o piu' trader dal catalogo;
4. ogni trader viene collegato a un account exchange esistente o nuovo;
5. vengono aggiunti o aggiornati i topic Telegram necessari (se l'istanza non e' muta);
6. l'istanza viene rivalidata e ridistribuita.

Il principio operativo e' che l'operatore modifica lo **stato desiderato** dell'istanza nel control plane, poi applica la differenza al runtime.

### Workflow di edit raccomandato

1. aprire l'istanza in modalita' `edit`
2. aggiungere o modificare fonti (iscrizioni), trader, account e destinazioni Telegram
3. visualizzare un riepilogo o diff delle modifiche
4. eseguire la validazione
5. applicare la riconciliazione della nuova configurazione (`rollout apply`)
6. riavviare l'istanza solo se richiesto dal tipo di modifica

### Workflow scalabile per fonti con molti trader

Per fonti che contengono molti trader il workflow non deve essere costruito come una configurazione piatta e manuale di:

- alias per trader
- pattern per trader
- binding trader -> account
- binding trader -> topic Telegram

Il modello raccomandato e' invece:

1. definire i trader una volta sola nel **catalogo trader globale**
2. registrare la fonte come contenitore leggero (l'ascolto e' derivato dalle sottoscrizioni)
3. associare alla fonte i trader ammessi (membership)
4. applicare policy di default per account e Telegram
5. mantenere espliciti solo gli override locali

In questo modo l'operatore ragiona per intenzione:

1. registra/riusa la fonte
2. assegna il parser comune
3. seleziona trader dal catalogo globale
4. sceglie policy account
5. sceglie policy Telegram
6. rivede gli override
7. conferma

### Obiettivo del workflow

Questo flusso evita due errori opposti:
- provisioning troppo manuale e frammentato;
- automazione one-shot troppo opaca per credenziali, Telegram e go-live.

L'interfaccia operativa raccomandata e' a due livelli:

- **wizard guidati** per creazione e modifica ordinaria
- **comandi tecnici granulari** per manutenzione, repair e automazione

---

## Modello concettuale

Il modello dati e operativo di riferimento e' il seguente:

- **Sistema** = unita' di **deployment** su un host: clone del repo + ingestione +
  istanze; tipato `DEMO`/`LIVE`; confine di condivisione di codice e `parser.sqlite3`
- **Istanza** = unita' autonoma di **esecuzione** del bot (esecutore + notificatore),
  appartiene a un Sistema
- **Fonte** = input Telegram **globale** (catalogo), ascoltata dall'ingestione di ogni
  Sistema che la consuma e condivisibile tra piu' istanze
- **Trader** = identita' runtime eseguibile nel **registro magro** globale; detection
  (alias/pattern) e comportamento (risk/entry/management) vivono nei file, non nel registro
- **Catalogo trader** = registro **globale, unico e magro** dei trader disponibili
  (`trader_id`, `display_name`, riferimento `parser_profile`); alias e pattern vivono nei
  frammenti versionati `config/patterns/<gruppo>.yaml`
- **Account exchange** = risorsa assegnabile a uno o piu' trader della stessa istanza,
  con wiring account logico -> adapter -> credenziali
- **Iscrizione (subscription)** = legame fonte -> istanza che abilita il fan-out del
  segnale capito verso quell'istanza
- **Gruppo Telegram istanza** = destinazione di controllo e notifica dell'istanza,
  **opzionale** (istanza muta)

Relazioni attese:

- una istanza puo' avere piu' fonti (via iscrizione)
- una fonte puo' essere consumata da piu' istanze
- una fonte puo' avere uno o piu' trader presi dal catalogo globale (membership): e' il
  set **potenziale** di trader che la fonte puo' produrre
- ogni istanza sceglie il sottoinsieme **attivo** dei trader della fonte tramite binding
  `trader -> account`; nessun binding significa trader non eseguito in quella istanza
- il trader viene definito una volta sola nel catalogo globale e poi associato alle fonti;
  se la stessa persona/strategia deve avere account o comportamento separato per fonte, si
  crea un `trader_id` distinto
- piu' trader della stessa istanza possono condividere lo stesso account exchange
- un trader puo' anche avere un account exchange dedicato
- ogni istanza ha il proprio gruppo Telegram (o nessuno), con eventuali topic separati
  per trader, account o funzione
- account e Telegram devono supportare policy di default con override locali

---

## Stati istanza

Per il primo design bastano pochi stati operativi, leggibili e verificabili:

| Stato | Significato |
|---|---|
| `draft` | istanza creata ma incompleta |
| `ready` | configurazione completa e validata, pronta per deploy |
| `deployed` | file e servizi installati sul server target |
| `active` | istanza avviata |
| `error` | provisioning, validazione o deploy falliti |

### Regole di transizione

- `instance create` crea sempre una nuova istanza in `draft`
- `validate` puo' portare da `draft` a `ready`
- `deploy` porta da `ready` a `deployed` (**primo deploy**); su un'istanza gia'
  `deployed`/`active`, `rollout apply` **riconcilia senza cambiare stato** (l'istanza
  resta `active` se era attiva, `deployed` se era ferma)
- `start` puo' portare solo da `deployed` a `active`
- `stop` riporta da `active` a `deployed` (installata, non in esecuzione):
  start/stop e' una coppia simmetrica
- errori in qualunque fase portano a `error` con motivazione tracciabile

**Semantica di `error` (chiarita nella revisione 2026-07-02).** `error` non e' uno stato
di parcheggio: e' un'etichetta con memoria — *quale operazione* e' fallita (`validate`,
`provision`, `deploy`) e *perche'* (registrato in `control_events`). Le cause sono errori
del control plane (config incoerente, API Bybit/Telegram che rifiuta, SSH/server), mai
eventi di trading: non e' successo nulla di irreversibile — l'istanza non girava, o
continua a girare con la vecchia config. L'uscita e' sempre la stessa: correggere la causa
e **rilanciare l'operazione fallita**; un'operazione rilanciata con successo riporta
l'istanza allo stato che le compete. Caso deploy fallito a meta' su istanza attiva:
`diff`/`rollout plan` mostra lo stato incoerente del server; `apply` e' **idempotente**
(riconcilia allo stato desiderato), quindi ritentare e' sempre sicuro.

**L'edit non cambia stato.** Lo stato descrive il **runtime**, non la freschezza della
config: modificare lo stato desiderato di un'istanza `active` crea **drift** (visibile in
`diff` e `rollout plan`), non una regressione di stato; `deploy`/`apply` riallinea;
`validate` su un'istanza gia' attiva e' solo un controllo, non una transizione.

Gli stati riflettono il ciclo di vita dell'**esecutore/istanza**. L'ingestione per-Sistema
ha un ciclo di vita proprio (vedi Provisioning tecnico) e non e' modellata
come stato dell'istanza.

---

## Comandi `tsbctl`

La CLI `tsbctl` e' l'orchestratore del workflow. Il principio e' evitare un unico comando "magico" di onboarding e preferire step espliciti, ripetibili e osservabili.

### Comandi principali

```bash
# Bootstrap control plane (una tantum)
tsbctl server add --name vps1 --host 1.2.3.4 --ssh-user tsb --ssh-key ~/.ssh/tsb_vps1
tsbctl sistema create --name demo --server vps1 --type DEMO   # struttura + clone + ingestione
tsbctl provider add --provider BYBIT --env DEMO               # chiavi master via prompt
tsbctl telethon login --role provisioning                     # interattivo (codice sul telefono)
tsbctl telethon login --role ingestion --sistema demo         # interattivo

# Creazione guidata istanza
tsbctl instance init

# Modifica guidata istanza esistente
tsbctl instance edit alpha_demo

# Gestione catalogo trader globale
tsbctl trader catalog add
tsbctl trader catalog edit trader_a
tsbctl trader catalog list
tsbctl trader catalog import --file traders.yaml        # bulk: N trader in un colpo
tsbctl trader catalog import --from-existing-config     # seed dai file attuali (migrazione)

# Gestione fonti globali e iscrizioni
tsbctl source register --channel 12345 --label fonte_a
tsbctl source subscribe alpha_demo --source fonte_a

# Pool account: creazione in bulk (staccata dalla creazione istanza)
tsbctl account provision --count 20 --type DEMO --provider BYBIT --position-mode hedge
tsbctl account pool list --type DEMO --status available
tsbctl account claim alpha_demo --from-pool --count 3 --as demo_1,demo_2,demo_3

# Riepilogo / diff / verifica
tsbctl instance summary alpha_demo
tsbctl diff alpha_demo
tsbctl validate alpha_demo

# Deploy e ciclo operativo
tsbctl rollout apply alpha_demo
tsbctl instance start alpha_demo
tsbctl instance stop alpha_demo
tsbctl instance status alpha_demo
```

### Comandi tecnici di basso livello

I comandi granulari restano disponibili per repair, automazione e casi speciali:

```bash
tsbctl instance create --name alpha_demo --type DEMO --sistema demo [--muted]
tsbctl telegram bind-group alpha_demo --chat-id -1001234567890 --bot-token-env CONTROL_BOT_ALPHA
tsbctl source register --channel 12345 --label fonte_a --resolution dynamic --pattern-group multi_ru
tsbctl source subscribe alpha_demo --source fonte_a
tsbctl source attach-traders --source fonte_a --traders trader_a,trader_b
tsbctl account provision --count 20 --type DEMO --provider BYBIT   # bulk, popola il pool
tsbctl account claim alpha_demo --from-pool --count 1 --as demo_1  # rivendica dal pool
tsbctl account register --provider BYBIT --uid <uid> --env DEMO    # registrazione manuale (no API)
tsbctl source set-account-policy alpha_demo --source fonte_a --mode shared_account_per_source --account demo_1
tsbctl source set-telegram-policy alpha_demo --source fonte_a --notify-mode per_source --default-topic 201
tsbctl trader bind-account alpha_demo --trader trader_a --account demo_1 --override
tsbctl telegram bind-topic alpha_demo --scope trader_a --topic 211 --role NOTIFY
tsbctl provision prepare alpha_demo
tsbctl provision bybit alpha_demo
tsbctl provision telegram alpha_demo
```

### Esempio di modifica di una istanza attiva

Caso: aggiungere una nuova fonte con due trader a una istanza gia' attiva.

```bash
tsbctl instance edit alpha_demo
```

Oppure in forma esplicita:

```bash
tsbctl source register --channel 55555 --label fonte_b --resolution dynamic --pattern-group multi_ru
tsbctl source attach-traders --source fonte_b --traders trader_x,trader_y
tsbctl source subscribe alpha_demo --source fonte_b
tsbctl source set-account-policy alpha_demo --source fonte_b --mode shared_account_per_source --account demo_1
tsbctl source set-telegram-policy alpha_demo --source fonte_b --notify-mode per_source --default-topic 220
tsbctl trader bind-account alpha_demo --trader trader_y --account acc_y --override
tsbctl telegram bind-topic alpha_demo --scope trader_x --topic 220 --role NOTIFY
tsbctl telegram bind-topic alpha_demo --scope trader_y --topic 221 --role NOTIFY
tsbctl instance summary alpha_demo
tsbctl validate alpha_demo
tsbctl deploy alpha_demo
```

### Ruolo dei comandi

- `server add`
  - registra un server target (host, utente SSH, path chiave; la chiave va autorizzata a mano)
- `sistema create`
  - crea il Sistema su un server (nome, tipo `DEMO`/`LIVE`) e ne esegue il primo deploy:
    struttura directory, clone del repo, servizio di ingestione
- `provider add`
  - registra le credenziali master di un provider per ambiente (input segreti via prompt,
    mai in argv)
- `telethon login`
  - flusso interattivo (telefono + codice) che genera e salva una sessione Telethon;
    `--role ingestion --sistema X` per l'ingestione, `--role provisioning` per il
    provisioning Telegram
- `instance init`
  - avvia un wizard testuale che raccoglie i dati minimi per creare una nuova istanza-esecutore coerente
- `instance edit`
  - avvia un wizard testuale per modificare una istanza esistente senza ricrearla
- `trader catalog add/edit/list`
  - gestisce il catalogo **globale e magro** dei trader disponibili; alias e pattern restano
    nei frammenti `config/patterns/<gruppo>.yaml`
- `trader catalog import`
  - popolamento **in bulk**: da file YAML/CSV (`trader_id, display_name, alias, ...`) per
    fonti con molti trader, o `--from-existing-config` che semina il catalogo dai file
    attuali (`trader_aliases.json`, `text_patterns.yaml`, `registered_traders`) in
    migrazione. `display_name` defaulta da `trader_id` o dal primo alias. Mai inserimenti
    uno-per-uno per canali multi-trader
- `source register`
  - registra una fonte Telegram **globale** con il suo parser e la sua modalita' di risoluzione
- `source subscribe`
  - iscrive una istanza a una fonte esistente (abilita il fan-out del segnale capito)
- `source attach-traders`
  - associa alla fonte (membership) uno o piu' trader gia' presenti nel catalogo globale
- `instance summary`
  - mostra lo stato desiderato completo dell'istanza in modo leggibile
- `diff`
  - mostra la differenza tra stato desiderato e stato attualmente deployato
- `instance create`
  - crea il record base dell'istanza-esecutore e lo stato iniziale `draft`
- `source set-account-policy`
  - applica alla fonte una policy account di default per l'istanza
- `source set-telegram-policy`
  - applica alla fonte una policy Telegram di default per l'istanza
- `account provision`
  - crea in **bulk** subaccount + API key via API Bybit e popola il **pool** globale in stato `available` (operazione staccata dalla creazione istanza); con `--position-mode hedge` imposta subito la modalita' posizione via API e la registra in `exchange_accounts.position_mode`
- `account pool list`
  - elenca gli account del pool per tipo e stato
- `account claim`
  - **seleziona** N account dal pool per un'istanza (stato desiderato); il claim effettivo
    (set atomico di `instance_id` + nome logico, stato `assigned`) viene eseguito da `deploy`
- `account register`
  - registra manualmente un account creato a mano su Bybit (percorso senza API, per casi speciali)
- `trader bind-account`
  - definisce un override di binding trader -> account rispetto alla policy di default
- `telegram bind-group`
  - collega all'istanza il gruppo Telegram e il bot di control plane da usare per notifiche e comandi
- `telegram bind-topic`
  - definisce un override di routing Telegram a scope di istanza, fonte, trader o account
- `provision prepare`
  - genera struttura, YAML, `.env` placeholder e check preliminari
- `provision bybit`
  - crea o collega account/subaccount e credenziali exchange
- `provision telegram`
  - crea o collega bot, gruppo e topic Telegram dell'istanza
- `validate`
  - controlla coerenza e completezza; se tutto e' corretto passa a `ready`
- `deploy`
  - installa sul server target (esecutore + aggiornamento dell'ingestione del Sistema se
    servono canali nuovi) e porta a `deployed`
- `instance start`
  - esegue l'avvio esplicito e porta a `active`

---

## Fonte di verita' e artefatti generati

### Fonte di verita'

`management.db` e' il registro centrale di verita' per:
- istanze (esecutori)
- server target
- catalogo trader globale
- sistemi (unita' di deployment) e loro revisioni
- fonti globali
- membership fonte/trader
- iscrizioni fonte/istanza
- policy account e Telegram
- override fonte/trader/account exchange
- stato operativo e log delle operazioni (`control_events`)
- riferimenti alle credenziali (accesso server, master provider, sessioni Telethon)
- destinazioni Telegram

`management.db` e' un control-plane database, non un database di trading.

### Artefatti generati

I file runtime del bot non sono fonte di verita'. Sono artefatti derivati:
- `telegram_control.yaml`
- `channels.yaml`
- `execution.yaml`
- eventuali file per trader/profili
- `.env` dell'istanza

Questi file devono essere generati da `tsbctl` e non modificati a mano.

### Split dei database runtime

- **`parser.sqlite3`** -> segnale capito condiviso (raw, canonical, parser results).
  **Condiviso a livello di Sistema** tra le sue istanze; scritto dall'ingestione, letto
  dagli esecutori.
- **`ops.sqlite3`** -> enrichment per-istanza, cursori locali e dettaglio trading
  (ordini, posizioni, fill, trade chain). **Locale per istanza/account.**

Il control plane mantiene solo metadati, stato operativo e riferimenti sufficienti per
una futura dashboard fleet-level con drill-down verso il dettaglio locale.

### Implicazione architetturale

Il runtime del bot resta quasi invariato:
- continua a leggere file di config e DB locali;
- non conosce la logica di orchestrazione;
- non dipende direttamente dalla semantica di onboarding.

Sotto il Modello B, l'evoluzione runtime rilevante e' lo split tra **feed canonical
condiviso** e **enrichment di esecuzione per-istanza**. La cucitura esiste gia' nella
pipeline `runtime_v2` (messaggio canonico separato dall'execution gateway), ma va estratta
una funzione/servizio `build_enriched_for_instance` per riusare la logica oggi dentro il
Signal Enrichment senza far decidere account/risk all'ingestione condivisa.

---

## Sistema di configurazione

Oggi la config e' **piatta e scritta a mano** dentro la repo (una sola `config/`, con
detection, comportamento e wiring impastati). Nel modello scalabile diventa **generata e
stratificata**, ma il **runtime non cambia**: carica le stesse forme di file dalla propria
config dir e applica la **stessa logica di merge a 2 livelli** di oggi.

### Classificazione dei file config

| File | Categoria | Dove / come |
|---|---|---|
| `channels.yaml` | inventory/wiring | generato per-istanza da `management.db` |
| `execution.yaml` | inventory/wiring + tuning | wiring da `management.db` + template adapter |
| `telegram_control.yaml` | inventory/wiring | generato per-istanza da `management.db` |
| `trader_aliases.json` | detection | **generato per Sistema** dai frammenti di gruppo |
| `text_patterns.yaml` | detection | **generato per Sistema** dai frammenti di gruppo |
| `config/patterns/<gruppo>.yaml` | detection (sorgente) | **frammenti per pattern_group** (pattern + alias del gruppo), versionati nel repo |
| `operation_config.yaml` | comportamento | generato per-istanza da **scheletro + override** |
| `traders/<id>.yaml` | comportamento | generato per-istanza da **scheletro + override** |
| `setup_reshape_templates.yaml`, `templates/` | template comportamentali | condivisi, riusati per nome |

### Partizione per piano

La config si divide seguendo i due piani del Modello B:

- **Ingestione (capire il segnale), condivisa per Sistema**: detection del trader (alias,
  pattern) e `channels.yaml`. La detection **non e' mai stata** nel `traders/<id>.yaml`.
  A scala (decine di fonti, centinaia di pattern) il file unico non regge: la **sorgente**
  diventa un **frammento per pattern_group** (`config/patterns/<gruppo>.yaml`, con i
  pattern e gli alias di quel gruppo, versionato nel repo); `tsbctl` **genera** per ogni
  Sistema i `text_patterns.yaml`/`trader_aliases.json` consolidati con i **soli gruppi
  delle fonti sottoscritte**. Il runtime resta invariato (legge gli stessi file di oggi);
  l'operatore edita frammenti piccoli con diff puliti.
- **Esecuzione (fare trading), per istanza**: `operation_config.yaml`, `traders/<id>.yaml`
  (comportamento), `execution.yaml`. Il `traders/<id>.yaml` di oggi e' gia' **quasi solo
  comportamento** (risk, entry_split, management_plan, account): diventa per-istanza.

### Precedenza e propagazione

- **Due livelli, come oggi**: `operation_config` (default) -> override `traders/<id>.yaml`.
  Il runtime mantiene la stessa logica di merge.
- **N copie da uno scheletro**: `operation_config.yaml` era nato come **template generale
  di default applicabile a tutti**, sovrascrivibile dal trader, con alcuni concetti
  global-only (`global_safety`, `account_mode`, `symbol_blacklist.global`). Nel modello
  scalabile ogni istanza ne riceve una **copia generata da uno scheletro comune**. Lo
  scheletro **non e' un livello che si eredita**: e' un punto di partenza.
- **Propagazione esplicita**: cambiare lo scheletro **non** tocca le istanze esistenti;
  si applica **rigenerando + ridistribuendo** le istanze scelte, mostrando "N istanze
  impattate" (coerente con "stato desiderato -> diff -> apply").
- **`registered_traders` derivato**: non piu' elencato a mano, ma **dedotto** dai trader
  che l'istanza consuma (via sottoscrizioni + membership delle sue fonti).
  - Il runtime usa `registered_traders` come **gate**: solo i trader nel registro ricevono
    config effettiva (`config_loader.py`) e la validazione incrocia canali, pattern e
    blacklist con esso (`validator.py`). Il gate resta **identico**: `tsbctl` scrive la
    lista derivata nella `operation_config.yaml` generata.
  - Il **registro magro** in `management.db` (solo `trader_id` + `display_name`) e' la
    **lista canonica** da cui `registered_traders` viene derivato.
  - Poiche' `registered_traders`, `channels.yaml` e i pattern sono derivati dalla **stessa
    membership**, le incoerenze che `validator.py` rincorre (trader nel canale ma non
    registrato, pattern con trader non registrato, ...) diventano **impossibili per
    costruzione**.

### Il runtime non cambia

L'esecutore carica `operation_config.yaml` + `traders/<id>.yaml` dalla propria config dir
e applica il merge a 2 livelli **identico a oggi**. Cambia solo che la config e'
**generata** invece che scritta a mano, e ne esistono **N copie** invece di una.

### Scala: nessun file piatto gigante

A scala reale (decine di fonti, alcune con decine di trader e topic), il modello **rimuove**
il problema del file piatto, non lo aggrava:

- l'**inventory** vive in `management.db` (righe indicizzate), navigato via UI con
  ricerca/filtri/gruppi/bulk — non in un YAML da centinaia di voci;
- la config **generata** di ogni istanza contiene solo il **suo sottoinsieme** di
  fonti/trader: e' piccola; il file completo non esiste da nessuna parte;
- il comportamento non esplode in file: **pochi scheletri + piccoli override**, non un file
  pieno per trader.

Output generato: puo' essere organizzato **per fonte** (`sources/<label>/...`) per
leggibilita'/versionamento se si vuole ispezionarlo; il runtime puo' comunque leggere un
file consolidato generato, quindi resta invariato. Cambiare la *struttura letta dal runtime*
(es. `gruppo -> topics`) sarebbe una modifica al runtime, valutabile a parte.

### Schema config come singola fonte di verita'

Oggi la config e' parsata con `raw.get("campo", default)` sparsi (`config_loader.py`) +
i commenti nei YAML come documentazione umana: **non esiste uno schema machine-readable**.
Questo impedisce un editor UI auto-generato e lascia i default impliciti.

Decisione: introdurre uno **schema config tipizzato** (Pydantic) come **singola fonte di
verita'**. Il **formato file resta YAML** — "JSON Schema" e' il linguaggio di schema, non un
obbligo di formato; lo YAML parsa allo stesso dict.

```text
config.yaml  --parse-->  dict  --valida-->  modello Pydantic (SSOT)
                                                  |
                                                  |-- runtime carica e valida
                                                  |-- editor UI si genera da .model_json_schema()
                                                  \-- validate = validazione di schema
```

Una definizione, tre usi: **runtime + editor + validate**. Aggiungi un campo al modello una
volta -> il runtime lo accetta **e** l'editor lo mostra, senza toccare il codice dell'editor,
senza drift. I commenti ricchi degli YAML attuali diventano metadati dello schema
(`Field(description=..., enum=..., default=...)`). Lavoro reale: sostituire i `.get()` con i
modelli; **nessuna migrazione di formato**.

### Override per-trader

L'override comportamentale di un trader in un'istanza (es. `setup_mode: reshape`,
`template: ladder_4_3_Tprofit`, `risk: 2%`) e' scritto nella UI da un **editor generato dallo
schema** e salvato come **blob opaco** per la coppia `(istanza, trader)`. `management.db`
non *modella* i campi del comportamento: li **trasporta**. `tsbctl` fonde e genera
`traders/<id>.yaml`. Il merge resta a **2 livelli** (`operation_config` + override trader);
i `template` reshape restano riferimenti risolti dalla logica runtime esistente.

### Applicare i cambiamenti: hot-reload vs restart

Verificato nel codice:

| Cambio | Effetto |
|---|---|
| Detection (`channels.yaml`) | **hot-reload** (`ChannelConfigWatcher`) |
| Comportamento (`operation_config`, trader config) | **hot-reload** (`reload_if_changed()` chiamato in `signal_enrichment/processor.py`) |
| Wiring esecuzione (`execution.yaml`: conti/adapter) | **restart** (connessioni all'avvio, nessun watcher) |

Implicazione operativa: la maggior parte dei tweak per-trader (reshape, risk) si applica
**a caldo, senza restart** e senza toccare posizioni aperte. Solo i cambi di **wiring**
(aggiungi/togli conto, cambia adapter) richiedono restart. Il **diff** nella UI classifica
il cambiamento e indica quale dei due.

---

## Principi architetturali

- **Un clone per Sistema** - il codice e' condiviso dentro il Sistema; le istanze
  differiscono per config, dati e credenziali; Sistemi diversi possono stare a revisioni
  diverse (canary)
- **Due piani** - ingestione per-Sistema e esecuzione per-istanza restano separati
- **Fonte e trader globali** - definiti una volta, riusati da piu' istanze
- **Account-centrico** - l'esecutore e' indirizzabile indipendentemente dall'ingestione
- **Isolamento rigido DEMO/LIVE** - un'istanza non mescola account demo e live
- **Control plane centrale** - `management.db` governa inventory, stato e provisioning
- **Configurazione generata** - i file YAML sono artefatti derivati dal DB centrale;
  il tuning comportamentale vive in template, non in colonne DB
- **Bot quasi invariato** - il runtime deve restare focalizzato sull'esecuzione
- **Control plane locale** - `management.db` e i segreti stanno sulla macchina di
  controllo; i server ricevono solo il `.env` della propria istanza al deploy
- **Segreti protetti a livello file** - `management.db` come un `.env` (permessi stretti,
  disco cifrato), backup cifrati ed export redatti; cifratura a livello campo come
  hardening futuro
- **Workflow semi-guidato** - automazione alta sui passaggi meccanici, controllo umano sui passaggi sensibili

---

## Topologia: control plane locale, server solo runtime

Il control plane vive sulla **macchina di controllo** dell'operatore (come uno stato
Terraform); i server eseguono solo i workload.

```text
MACCHINA DI CONTROLLO (operatore)        SERVER TARGET (runtime)
  management.db  (stato + segreti)          Sistemi (ingestione + esecutori)
  tsbctl / UI di controllo          --SSH-> ops.sqlite3 / parser.sqlite3 / .env
```

Punto chiave: le **istanze in esecuzione non dipendono da `management.db`** per girare.
Continuano a fare trading con la loro config locale anche a macchina di controllo spenta.
`management.db` serve solo per **cambiare** le cose (creare/modificare/deployare).

Caveat: con control node locale, creare/modificare/deployare e la UI richiedono la
macchina accesa; adatto a **operatore singolo**. Per un team servira' un control node
sempre acceso (fuori scope ora).

## Struttura filesystem proposta

### Lato controllo (macchina dell'operatore)

```text
~/telesignalbot-control/
  management.db                <- stato desiderato + segreti (chmod 600, non committato)
  tsbctl                       <- CLI di orchestrazione
  backups/                     <- export cifrati di management.db
```

### Lato server target (runtime)

```text
/opt/telesignalbot/
  sistemi/
    {sistema}/                 <- unita' di deployment (es. demo, live)
      repo/                    <- clone del codice del Sistema (revisione propria)
      ingestion/
        parser.sqlite3         <- raw/canonical/parser results, condiviso dentro il Sistema
        channels.yaml          <- canali ascoltati (derivati dalle sottoscrizioni)
        text_patterns.yaml     <- generato: soli gruppi delle fonti sottoscritte
        trader_aliases.json    <- generato: soli alias dei gruppi usati
        .env                   <- credenziali Telethon dell'ingestione (scritto al deploy)
      instances/
        {name}/
          config/
            telegram_control.yaml
            channels.yaml      <- fonti a cui l'istanza e' iscritta
            execution.yaml
            traders/
          data/
            ops.sqlite3        <- cursori/enrichment per-istanza + dettaglio trading, locale
          .env                 <- segreti della sola istanza (scritto al deploy)
```

### Note

- `management.db` e i segreti stanno **sulla macchina di controllo**, non sui server
- l'ingestione e' per Sistema: una sessione Telethon e un solo `parser.sqlite3` per Sistema
- ingestione ed esecutori dello stesso Sistema coabitano **per costruzione** sullo stesso
  host: il vincolo WAL (filesystem locale) e' garantito dalla forma del modello
- ogni istanza-esecutore ha isolamento operativo su `ops.sqlite3`, config e `.env`
- il codice viene aggiornato **per Sistema** (un clone per Sistema, revisioni indipendenti)
- `tsbctl` decifra/legge i segreti in locale e scrive i `.env` sui server via SSH al deploy
- Sistema `DEMO` e Sistema `LIVE` possono coabitare sullo stesso server: **un solo server
  basta per partire**; separare i server resta la scelta raccomandata quando il LIVE va
  in produzione seria (isolamento di guasto, contesa risorse, superficie di sicurezza)

---

## Schema `management.db`

Lo schema deve supportare il workflow approvato, non solo la persistenza tecnica.

### `servers`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | es. `vps_main`, `vps_eu1` |
| host | TEXT | IP o hostname |
| ssh_user | TEXT | |
| ssh_port | INTEGER | default 22 |
| ssh_key | TEXT | **path** a file chiave con permessi stretti (non il contenuto) |
| status | TEXT | `active` \| `offline` \| `maintenance` |
| notes | TEXT | |

### `sistemi`

Unita' di deployment su un server: clone del repo + ingestione + istanze.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT UNIQUE | es. `demo`, `live` |
| server_id | INTEGER FK | -> `servers` (piu' Sistemi possono condividere il server) |
| type | TEXT | `DEMO` \| `LIVE` - le istanze del Sistema sono solo del suo tipo |
| sistema_dir | TEXT | `/opt/telesignalbot/sistemi/{name}/` |
| deployed_revision | TEXT | revisione del clone del Sistema effettivamente in uso |
| target_revision | TEXT | revisione target per rollout |
| status | TEXT | `active` \| `maintenance` |
| created_at | DATETIME | |

### `instances`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT UNIQUE | es. `main_live`, `scalping_demo` |
| sistema_id | INTEGER FK | -> `sistemi` (il server si deriva dal Sistema) |
| type | TEXT | `LIVE` \| `DEMO` - deve coincidere col tipo del Sistema |
| status | TEXT | `draft` \| `ready` \| `deployed` \| `active` \| `error` |
| instance_dir | TEXT | `/opt/telesignalbot/sistemi/{sistema}/instances/{name}/` |
| systemd_unit | TEXT | `telesignalbot@{name}.service` |
| muted | BOOLEAN | true = istanza senza gruppo Telegram |
| tg_bot_token | TEXT | segreto (file locale permissionato) - null se muted |
| tg_group_id | TEXT | gruppo Telegram principale dell'istanza - null se muted |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### `sources`

Fonte **globale** (catalogo). Non ha `instance_id` ne' assegnazione di listener:
l'ascolto e' derivato dalle sottoscrizioni delle istanze di ciascun Sistema.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| label | TEXT UNIQUE | label descrittiva della fonte |
| channel_id | TEXT | ID canale Telegram sorgente |
| topic_id | INTEGER | topic sorgente opzionale |
| parser_profile_common | TEXT | parser comune della fonte |
| trader_binding | TEXT | `fixed` \| `dynamic` |
| fixed_trader_catalog_id | INTEGER FK | -> `trader_catalog`, valorizzato solo se `fixed` |
| resolution_mode | TEXT | solo se `dynamic`: `default` \| `patterns_only` |
| pattern_group | TEXT | gruppo pattern per la risoluzione dinamica |
| max_depth | INTEGER | profondita' reply-chain (default 5) |
| signal_message_type | TEXT | `any` \| `inline_buttons` |
| alias_overrides | TEXT | JSON map `alias -> trader_id`: override di detection **scopati alla fonte** (il rimedio alle collisioni cross-gruppo); generati nel blocco `resolution.aliases` del `channels.yaml` |
| text_blacklist | TEXT | JSON array di tag testuali (spam/rumore) filtrati dall'ingestione |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> Nota vocabolario: `trader_binding`/`resolution_mode` sostituiscono il precedente
> `alias|pattern|hybrid`, che non esiste nel runtime. `fixed` corrisponde a
> `trader_id: <id>` in `channels.yaml`; `dynamic` corrisponde a `trader_id: null` con
> `resolution.mode`.
>
> **Forme di fonte.** Una fonte e' una coppia `(channel_id, topic_id)`. Ne derivano tre casi:
> canale **mono-trader** (1 fonte `fixed`); canale/topic **multi-trader** (1 fonte `dynamic`,
> N trader via alias/pattern); **gruppo con un topic per trader** = **N fonti** che
> condividono lo stesso `channel_id` (una per topic). La UI le **raggruppa** sotto il canale.
>
> **Blacklist: chi possiede cosa (decisione 2026-07-02).** Esistono due blacklist di natura
> diversa e la proprieta' va tenuta separata:
> - **blacklist di testo = della fonte** (globale): tag di spam/rumore del canale, filtrati
>   dall'ingestione *prima* del parse — "cos'e' rumore" e' un fatto del canale, uguale per
>   chiunque lo ascolti (come il parser della fonte). Vive in `sources.text_blacklist`,
>   finisce nel `channels.yaml` generato dell'ingestione e vale per tutto il Sistema;
>   modificarla segue la regola del blast radius ("consumata da N istanze");
> - **blacklist di simboli = per-istanza**: "cosa non tradare" lo decide l'istanza,
>   applicata dall'esecutore nell'enrichment, con i suoi due livelli interni
>   (`symbol_blacklist.global` dell'istanza + `symbol_blacklist.per_trader.<id>`, come
>   oggi in `operation_config`) piu' gli **override runtime** via comandi Telegram, che
>   restano nell'`ops.sqlite3` locale (comandi operativi, non stato desiderato).
>
> **Selezione per-istanza.** La membership dice quali trader la fonte *puo'* portare; **quali**
> un'istanza esegue e **su quale conto** e' per-istanza, espresso dai `trader_account_bindings`
> (nessun binding = trader non eseguito in quell'istanza). Il binding e' per
> `(istanza, trader)`: se la stessa persona/strategia deve essere separata per fonte,
> account o comportamento, si crea un `trader_id` globale distinto.

### `source_instance_subscriptions`

Fan-out: quali istanze consumano quale fonte.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| source_id | INTEGER FK | -> `sources` |
| instance_id | INTEGER FK | -> `instances` |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> La sottoscrizione e' control-plane (chi consuma cosa). Il **cursore di consumo** NON
> vive qui: e' stato runtime ad alta frequenza e sta nell'`ops.sqlite3` locale
> dell'esecutore (vedi "Fan-out e cursore per esecutore").

### `exchange_accounts`

Modello **pool**: gli account vengono creati in bulk (auto-provisioning Bybit) e vivono
in un pool globale finche' un'istanza non li **rivendica** (claim). Isolamento rigido:
**una sola coppia** di chiavi; il tipo demo/live e' **intrinseco** all'account
(`environment`) e determina da quale pool un'istanza puo' pescare.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK NULL | -> `instances`; **null** finche' l'account e' nel pool |
| environment | TEXT | `DEMO` \| `LIVE` - intrinseco, partiziona il pool |
| logical_account_id | TEXT | es. `demo_1` - chiave di routing, **assegnata al claim** |
| adapter_name | TEXT | es. `bybit_demo_1` - wiring verso l'adapter, assegnato al claim |
| adapter_template | TEXT | template di tuning comportamentale da applicare |
| connector | TEXT | es. `bybit` |
| provider | TEXT | es. `BYBIT` |
| execution_account_id | TEXT | UID account/subaccount lato exchange (identita' intrinseca) |
| position_mode | TEXT | `one_way` \| `hedge` - impostato **alla creazione** via API (`account provision --position-mode`); proprieta' intrinseca dell'account, registrata qui |
| parent_account | TEXT | account master exchange |
| api_key | TEXT | segreto (file locale permissionato) |
| api_secret | TEXT | segreto (file locale permissionato) |
| api_permissions | TEXT | JSON array, es. `["ContractTrade"]` - permessi minimi, **mai** withdraw/transfer |
| ip_whitelist | TEXT | JSON array - assegnata/aggiornata **al claim** (IP del server del Sistema) |
| status | TEXT | `available` \| `assigned` \| `suspended` |
| created_at | DATETIME | |

> **Identita' vs routing.** L'identita' exchange (`execution_account_id`, chiavi,
> `connector`) e' intrinseca e nasce col pool. Il **nome logico di routing**
> (`logical_account_id`) e l'`adapter_name` sono relativi all'istanza e vengono
> assegnati **al momento del claim**, non alla creazione.
>
> **Claim atomico, al deploy.** Il passaggio `available -> assigned` (set `instance_id` +
> `logical_account_id`) deve essere atomico per evitare che due istanze rivendichino lo
> stesso account. Avviene **dentro `deploy`** (decisione 2026-07-02): prima del deploy la
> scelta dei conti e' solo stato desiderato — coerente col principio "nulla tocca il mondo
> prima del deploy", stessa semantica per CLI e UI.
>
> **Isolamento rigido gratis.** Poiche' `environment` e' intrinseco, un'istanza `DEMO`
> puo' rivendicare solo account del pool `DEMO`. L'isolamento non e' una regola imposta:
> emerge dai dati.
>
> Il **tuning comportamentale** dell'adapter (`strategy`, `websocket`, `retry`,
> `live_safety`, `trigger_by`, ...) non e' modellato in colonne: vive in
> `adapter_template` + eventuali override, per non trasformare `management.db` in un
> dump di config e non richiedere una migrazione DB per ogni tweak di strategia.

### `provider_credentials`

Credenziali **master** del provider per il provisioning in bulk (`account provision`):
la creazione di subaccount + API key richiede la chiave del master account, distinta
per ambiente (endpoint demo e live diversi).

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| provider | TEXT | es. `BYBIT` |
| environment | TEXT | `DEMO` \| `LIVE` - master ed endpoint distinti |
| master_uid | TEXT | UID dell'account master lato exchange |
| api_key | TEXT | segreto - chiave master con permesso gestione subaccount |
| api_secret | TEXT | segreto |
| endpoint | TEXT | es. `api.bybit.com` / `api-demo.bybit.com` |
| enabled | BOOLEAN | |

> `account provision --type DEMO` usa la riga `DEMO`; la riga `LIVE` sta dietro il
> gate LIVE (mai auto-creare account live senza conferma esplicita).

### `telegram_credentials`

Credenziali Telethon: la sessione di **ingestione** (una per Sistema) e la sessione
utente di **provisioning** (crea gruppi/topic, separata per non mescolare i ruoli).

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| sistema_id | INTEGER FK NULL | -> `sistemi`; **NULL** per la sessione di provisioning |
| role | TEXT | `ingestion` \| `provisioning` |
| api_id | TEXT | segreto - app Telethon (serve per creare nuove sessioni) |
| api_hash | TEXT | segreto |
| phone | TEXT | segreto (file locale permissionato) |
| session_string | TEXT | segreto (file locale permissionato) |

> **Un solo numero per tutte le sessioni (decisione 2026-07-02).** Le sessioni di
> ingestione dei vari Sistemi e la sessione di provisioning sono tutte **dispositivi
> multi-device dello stesso account Telegram** (stesso `phone`). Vantaggi: zero SIM
> aggiuntive, i canali privati gia' joinati sono visibili da tutte le sessioni.
> Rischio accettato: destino condiviso — un ban/limitazione dell'account o un logout
> globale (cambio password, terminate sessions) abbatte tutte le sessioni insieme.
> Mitigazione: throttling anti-flood sul provisioning (l'unica attivita' "rumorosa").

### `control_events`

Log delle operazioni del control plane, scritto da `tsbctl`: alimenta le automazioni e
le viste "ultimo deploy / ultimo rollout / ultimo errore". **Non** contiene heartbeat
runtime: le istanze non scrivono su `management.db` (macchina di controllo anche spenta);
il monitoring vivo appartiene alla dashboard (doc separato).

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| event_type | TEXT | `deploy` \| `upgrade` \| `rollout` \| `rollback` \| `validate` \| `provision` \| `error` |
| target_type | TEXT | `instance` \| `sistema` \| `source` \| `account` |
| target_ref_id | INTEGER | FK logica verso la tabella del target |
| outcome | TEXT | `ok` \| `failed` |
| detail | TEXT | messaggio/motivazione (es. esito canary, errore deploy) |
| revision | TEXT | revisione coinvolta, se applicabile |
| created_at | DATETIME | |

### `trader_catalog` (registro magro)

Registro **globale** e **magro**: solo l'identita' canonica del trader. Nessun
`instance_id`. La **detection** (alias, pattern) e il **comportamento** (risk, entry,
management) **non** stanno qui: vivono nei file globali (`trader_aliases.json`,
`text_patterns.yaml`) e negli scheletri di comportamento. Il control plane governa
*chi esiste e come si relaziona*, non *come si riconosce o come si comporta*.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| trader_id | TEXT UNIQUE | es. `trader_a`, `trader_3` |
| display_name | TEXT | label leggibile |
| parser_profile | TEXT | **riferimento per nome** a un profilo parser nel codice del repo; default per le fonti `fixed` del trader |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> **Il parser e' codice, non dato.** Il profilo vive nel repo e si distribuisce via
> `repo upgrade` (canary DEMO incluso); `parser_profile` e' solo il nome che lo riferisce,
> come `adapter_template` per gli account. Ordine obbligato: prima il parser nel codice
> (deployato), poi il trader che lo riferisce; `validate` controlla che il nome esista
> nel codice del Sistema target.
>
> **Aggancio a parser_v2 (chiarito 2026-07-02).** **parser_v2 e' l'unico parser attivo del
> runtime** (verificato: la pipeline `src/runtime_v2/parser_pipeline/processor.py` importa
> esclusivamente `parser_v2.core.runtime` + registry; `src/parser_v2/profiles/Legacy/` e'
> solo archivio storico dei vecchi profili, non un runtime parallelo — nessuna ambiguita'
> su quale registry interrogare). Il contratto esiste gia' nel codice:
> `parser_profile` e' una **chiave canonica** del registry parser_v2
> (`src/parser_v2/profiles/registry.py`, mappa `_PROFILE_FACTORIES`, 11 profili
> registrati); `validate` chiama `list_parser_v2_profiles()` sul clone del Sistema target.
> Nel catalogo si scrivono **solo chiavi canoniche, mai gli alias** del registry
> (`_ALIASES` e' tolleranza interna del runtime, non contratto). Gli internals di
> parser_v2 (operation_rules, target_resolver, ...) restano fuori dal contratto:
> l'unico aggancio e' il nome nel registry.

> Alimenta il `registered_traders` per-istanza (gate del runtime) ed e' il bersaglio FK
> della membership. Niente tabella `trader_aliases`: gli alias vivono nei **frammenti di
> gruppo** (`config/patterns/<gruppo>.yaml`), da cui si generano i file consolidati.

### `source_trader_memberships`

**Relazione** pura: quali trader del registro sono ammessi da una fonte. E' l'ancora del
fan-out e lo **scope** della risoluzione: la detection dentro una fonte considera **solo**
gli alias/pattern dei trader di questo insieme.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| source_id | INTEGER FK | -> `sources` |
| trader_catalog_id | INTEGER FK | -> `trader_catalog` |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> **Regola di risoluzione alias/collisioni** (opera sui frammenti di gruppo, scopata alla membership):
> 1. detection di default nei frammenti di gruppo (`config/patterns/<gruppo>.yaml`), da cui
>    si generano i `trader_aliases.json`/`text_patterns.yaml` consolidati del Sistema;
> 2. eventuali override per-fonte nella detection della fonte (in `channels.yaml` generato);
> 3. la risoluzione considera solo i trader **ammessi dalla fonte** (membership);
> 4. `validate` legge i frammenti incrociati con la membership e **blocca le collisioni**
>    di alias dentro l'insieme ammesso (oggi un alias ambiguo passa silenzioso).
> 5. **Collisioni cross-gruppo alla generazione**: i pattern sono scopati per gruppo dal
>    runtime (mai in conflitto tra gruppi), ma `trader_aliases.json` consolidato e' una
>    **mappa piatta**: se due gruppi consumati dallo stesso Sistema danno lo **stesso alias
>    a trader diversi**, la generazione collide. `validate` blocca anche questo caso
>    (controllo sull'insieme consolidato del Sistema); la soluzione e' spostare l'alias
>    ambiguo nell'**override per-fonte** (punto 2), scopato al canale, invece che nella
>    mappa globale. Stesso `trader_id` in piu' gruppi invece e' normale (trader in piu' fonti).
>
> **Convenzione per fonti risolte a pattern (`patterns_only`).** I `trader_id` sono
> identita' runtime globali: la convenzione raccomandata e' **`<source_slug>__<code>`**
> (es. `vip_ru__a1`, `vip_ru__a2`, `scalping_room__b1`), non codici nudi tipo `A1`.
> La firma reale del trader ("genio", "#", il formato del messaggio) vive
> **dentro il pattern** del frammento, che punta al codice; `display_name` e' solo
> leggibilita' (es. `"genio (vip_ru__a1)"`). Con `patterns_only` il runtime **non consulta gli
> alias**: per queste fonti la mappa alias resta vuota e le collisioni cross-gruppo
> (punto 5) non si applicano.

### `source_account_policies`

Policy account per fonte/istanza. Non e' la sorgente finale di verita' dei trader
eseguiti: e' una scorciatoia/default del wizard per materializzare i binding
`trader -> account`. La sorgente effettiva dell'esecuzione e' `trader_account_bindings`.
Nessun binding per un trader significa: quel trader non e' attivo in quella istanza.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| source_id | INTEGER FK | -> `sources` |
| mode | TEXT | `shared_account_per_source` \| `dedicated_account_per_trader` \| `reuse_existing_bindings` |
| default_exchange_account_id | INTEGER FK | -> `exchange_accounts` |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `trader_account_bindings`

Binding effettivo dei trader attivi nell'istanza. Il binding e' **per istanza + trader**,
non per fonte: il `trader_id` e' l'identita' runtime eseguibile. Se la stessa persona o
strategia deve essere distinta per fonte/account/comportamento, si crea un `trader_id`
globale diverso (convenzione raccomandata: `<source_slug>__<code>`, es.
`vip_ru__a1`, non `A1` nudo).

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| trader_catalog_id | INTEGER FK | -> `trader_catalog` |
| exchange_account_id | INTEGER FK | -> `exchange_accounts` |
| binding_mode | TEXT | `DEDICATED` \| `SHARED` |
| is_override | BOOLEAN | true se supera la policy di default |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `telegram_policies`

Ignorata per le istanze `muted`.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| source_id | INTEGER FK | -> `sources` |
| notify_mode | TEXT | `per_source` \| `per_trader` \| `shared_instance` |
| default_notify_thread_id | INTEGER | thread di default per notify (clean_log) |
| control_thread_id | INTEGER | thread comandi (commands) |
| tech_thread_id | INTEGER | thread tecnico (tech_log) |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> Allineamento al runtime: i tre ruoli-topic reali del bot sono `clean_log` (notify),
> `tech_log` e `commands`. `default_notify_thread_id`/`tech_thread_id`/`control_thread_id`
> mappano rispettivamente questi tre ruoli, coerenti con `telegram_control.yaml`.

### `telegram_destinations`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| chat_id | TEXT | ID gruppo Telegram |
| thread_id | INTEGER | ID topic nel supergroup |
| role | TEXT | `NOTIFY` \| `CONTROL` \| `TECH` \| `BOTH` |
| scope_type | TEXT | `INSTANCE` \| `SOURCE` \| `TRADER` \| `ACCOUNT` |
| scope_ref_id | INTEGER | FK logica verso la tabella rilevante per lo scope |
| is_override | BOOLEAN | true se supera la policy Telegram di default |
| label | TEXT | es. `trader_a - segnali` |
| enabled | BOOLEAN | |

### Vincoli minimi

Vincoli DB o validazioni applicative obbligatorie:

- `sistemi.name` UNIQUE
- `instances.name` UNIQUE
- `instances.type` deve coincidere con `sistemi.type` (validazione `tsbctl`)
- `sources.label` UNIQUE
- `sources(channel_id, topic_id)` UNIQUE, normalizzando `topic_id NULL` come topic assente
- `source_instance_subscriptions(source_id, instance_id)` UNIQUE
- `source_trader_memberships(source_id, trader_catalog_id)` UNIQUE
- `trader_account_bindings(instance_id, trader_catalog_id)` UNIQUE
- `exchange_accounts(instance_id, logical_account_id)` UNIQUE per account assegnati
- `exchange_accounts.environment` deve coincidere con il tipo dell'istanza al claim
- `telegram_destinations(instance_id, role, scope_type, scope_ref_id)` UNIQUE

Validazioni operative collegate:

- una subscription deve avere almeno un trader attivo/bindato, salvo modalita' esplicita
  `listen_only`
- un trader attivo deve avere account claimato dall'istanza e ambiente coerente
- un `parser_profile` referenziato dal catalogo deve esistere nel codice del Sistema target
- collisioni alias dentro la membership di una fonte e collisioni cross-gruppo nel
  consolidato del Sistema devono bloccare `validate`

---

## Migrazione dalla config esistente

La config attuale e' **mista** (demo e live nella stessa installazione) e va scomposta
in istanze a isolamento rigido. Mappatura reale ricostruita dal codice
(`execution.yaml`, `telegram_control.yaml`, `channels.yaml`):

| Account logico | Adapter | Trader | Mode reale |
|---|---|---|---|
| `demo_1` | `bybit_demo_1` | trader_a, trader_b, trader_c, trader_d, trader_3, trader_prova* | demo |
| `demo_2` | `bybit_demo_2` | trader_devos_crypto, rsi_swing, rsi_intraday, sma_intraday | demo |
| `demo_3` | `bybit_demo_3` | trader_crypto_ninjias, trader_3_1 | demo |
| `live_1` | `bybit_live_1` | trader_gg_shot | live (`allow_live_trading: false`) |

\* `trader_prova` e' in `registered_traders` ma **non ha file** `traders/trader_prova.yaml`:
gira sui soli default (account `demo_1`).

**Decisione:** i profili di test (`trader_prova`, `rsi_swing`, `rsi_intraday`,
`sma_intraday`) vengono **registrati nel catalogo globale** e migrati in `main_demo`
come gli altri trader, con i binding account attuali (`demo_1` per `trader_prova`,
`demo_2` per `rsi_*`/`sma_*`). Per `trader_prova` va creato il file
`traders/trader_prova.yaml` mancante (oggi gira sui soli default).

**Finestra favorevole:** `live_1` non e' realmente operativo (`allow_live_trading: false`),
quindi la scomposizione avviene **prima** del go-live, senza book live aperti da spostare.
Farlo ora e' economico; farlo dopo, con posizioni live, e' rischioso.

**Percorso di migrazione:**

1. Registrare le **fonti globali** e i **trader nel catalogo globale** (una volta) —
   il catalogo si **semina automaticamente** dai file esistenti con
   `trader catalog import --from-existing-config`, niente reinserimento manuale.
2. Definire **due Sistemi** (`demo` e `live`, anche sullo stesso server) e almeno **due
   istanze**: `main_demo` nel Sistema `demo` (demo_1/2/3 + relativi trader) e `gg_live`
   nel Sistema `live` (live_1 + trader_gg_shot).
3. Iscrivere ciascuna istanza alle fonti che consuma.
4. `tsbctl` genera per ciascuna il sottoinsieme di `channels.yaml` / `execution.yaml` /
   `telegram_control.yaml`.
5. **Nodo Telegram (deciso 2026-07-02): nessun riuso.** Oggi tutti i `per_account`
   condividono lo **stesso supergroup** (`-1004240829081`). Alla migrazione ogni istanza
   riceve un **gruppo nuovo** creato da `provision telegram` (bot propri via BotFather);
   il supergroup attuale resta come **archivio storico**, fuori dal runtime. La migrazione
   diventa cosi' anche il primo collaudo completo del provisioning Telegram automatico.
   Nota operativa: un'istanza `muted` non ha canale comandi runtime (blacklist al volo,
   pausa, interventi manuali) — resta controllabile solo nel ciclo di vita via `tsbctl`.
   `muted` e' quindi un'**eccezione consapevole** per repliche usa-e-dimentica, non il
   default; mutare/smutare e' reversibile (bind del gruppo + restart, cambio di wiring).
6. **Dati (deciso 2026-07-02): fresh start.** Tutte le istanze partono con **DB nuovi**
   (`ops.sqlite3` vuoto, cursori inizializzati live-only a `MAX(id)` come da regola
   standard); il vecchio `ops.sqlite3` resta **archiviato come storia consultabile**,
   fuori dal runtime. Nessun cutover di cursori dal flusso legacy: il problema non esiste
   per costruzione.

---

## Prerequisiti minimi per dashboard futura

Questa spec non progetta la dashboard di monitoring, ma deve lasciare i contratti minimi
necessari per costruirla in seguito. Rimane coerente con
`docs/Raggionamento/DASHBOARD_CENTRALE/2026-06-30-multi-instance-dashboard-monitoring-design.md`.

### Dati centrali richiesti

Il livello fleet dovra' poter leggere da `management.db` almeno:
- inventory istanze
- tipo `DEMO` o `LIVE`
- Sistema e server associati
- fonti, iscrizioni, trader associati, policy applicate e binding account exchange effettivi
- stato operativo
- revisione deployata
- ultimo stato osservato dal control plane, se disponibile
- ultimo deploy o rollout
- ultimo errore critico

### Confine dei dati

- `management.db` non replica il dettaglio trading e non e' il monitoring runtime
- `ops.sqlite3` resta la fonte di verita' per ordini, posizioni, fill e trade chain
- `parser.sqlite3` resta la fonte di verita' per il segnale capito (per Sistema)
- la dashboard globale dovra' usare `management.db` per la navigazione e il controllo fleet-level
- il drill-down di dettaglio dovra' interrogare la singola istanza, i suoi dati locali o
  un collector/snapshot definito dalla spec dashboard

La dashboard di monitoring e' una spec separata. Questo documento definisce solo inventory,
riferimenti e stato di controllo necessari per sapere **dove** cercare i dati runtime.

### Estensioni future ammesse

Se in futuro servira' una vista aggregata piu' ricca, si potra' aggiungere:
- una summary cache centrale
- KPI aggregati per istanza
- snapshot sintetici non autoritativi

Queste estensioni non devono introdurre una seconda fonte di verita' del dominio trading.

---

## UI di controllo (fleet + editing)

Superficie **dedicata** di creazione/modifica delle istanze e delle associazioni.
E' **complementare** alla dashboard di monitoring
(`DASHBOARD_CENTRALE/2026-06-30-multi-instance-dashboard-monitoring-design.md`): quella
**legge** (heartbeat, posizioni, errori runtime), questa **scrive** (inventory e config).
Sono superfici distinte con auth diversa; possono condividere la shell ma non i permessi.

### Principio non negoziabile

La UI chiama **lo stesso core di provisioning della CLI** (`management/`). E' un
**chiamante**, non una reimplementazione: nessuna logica duplicata, nessun drift. Tutto
cio' che fa la UI e' esprimibile anche via `tsbctl`.

### Forma tecnica

Non e' una pagina statica: deve **scrivere** e orchestrare (SSH, Bybit, Telegram). E' una
**web app locale** servita da `tsbctl ui` su **localhost**, accanto a `management.db` sulla
macchina di controllo. Il backend e' il core `management/`; il front-end e' un chiamante.

### Le 4 tabelle umane

Viste leggibili sulle tabelle DB (che restano implementazione). L'operatore ragiona in
4 entita', non in 11.

| Tabella | Colonne principali | Azioni |
|---|---|---|
| **Istanze** (home / fleet) | nome, tipo, stato, sistema (server), #fonti, #account, Telegram/muta, revisione del Sistema + drift | crea, apri riga -> dettaglio/edit |
| **Account (pool)** | provider, `environment` DEMO/LIVE, `status` available/assigned, istanza, adapter, `execution_account_id` | provision (bulk), register (manuale), claim, suspend |
| **Fonti** | label, canale, fixed/dynamic, trader (membership), parser, **#sistemi che la ascoltano**, **#istanze consumatrici** | register, edit (avviso blast-radius), attach-traders |
| **Trader (catalogo)** | trader_id, display_name, parser, alias risolti dai frammenti, **#fonti / #istanze** | add, **import (bulk)**, edit (avviso blast-radius), apri frammenti pattern |

La tabella **Istanze** e' la vista d'insieme fleet. La tabella **Account** e' letteralmente
il pool da cui si pesca in fase di creazione istanza.

I **Sistemi** non sono una quinta tabella: sono pochi (tipicamente `demo` e `live`) e
vivono come **raggruppamento** della tabella Istanze (le istanze raggruppate sotto il
loro Sistema, con revisione e stato dell'ingestione a livello di gruppo). La gestione
dei Sistemi (creazione, upgrade, rollout) passa dai comandi `repo`/`rollout`.

### Regole di sicurezza della UI

1. **Core condiviso** UI <-> CLI: un solo percorso di provisioning.
2. **Editing dello stato desiderato + apply esplicito**: le modifiche scrivono il
   *desiderato* in `management.db`; **nulla tocca il runtime** finche' non si esegue
   `validate` -> `deploy`. Nessun "click = cambio live".
3. **Blast-radius visibile**: le tabelle Fonti/Trader mostrano `#istanze`; modificare un
   oggetto globale avvisa *"consumato da N istanze"* prima della conferma.
4. **Credenziali mai mostrate**: campi write-only, mai restituiti in lettura; nessun segreto visibile nella UI.
5. **Gate LIVE**: azioni su istanze/account `LIVE` (deploy, start, claim dal pool live)
   richiedono conferma esplicita aggiuntiva.

### Flusso di creazione visuale

Rispetta il **wizard netto** dei due piani. Ogni step scrive lo **stato desiderato**;
nulla tocca il runtime fino a `Deploy`.

1. **Identita'**: nome, **Sistema** (che fissa tipo `DEMO`/`LIVE`, server e revisione),
   `muted` si/no.
2. **Fonti (iscrizione)**: multi-select dalla tabella Fonti (solo iscrizione, non creazione).
   Se serve una fonte inesistente, la UI **rimanda alla tabella Fonti** per crearla nel
   piano condiviso: non la crea di nascosto.
3. **Selezione trader + conto** (per ogni fonte iscritta): la membership della fonte e'
   globale, ma **quali** trader questa istanza esegue e **su quale conto** e' per-istanza.
   Il pannello mostra i trader della fonte con spunta usa/non-usa e un menu conto per ognuno,
   piu' una scorciatoia "tutti -> conto X" (la policy di default), con override per-trader:

   ```text
   USA?   TRADER        CONTO
    [x]   trader_a   -> demo_1
    [x]   trader_b   -> demo_1
    [ ]   trader_c      (non usato in questa istanza)
    [x]   trader_3   -> demo_2
   ```

   "USA? = no" = nessun binding -> quel trader non gira qui. Due istanze sulla stessa fonte
   possono scegliere **sottoinsiemi diversi** su conti diversi (fan-out al livello del trader).
4. **Claim account**: i conti si pescano dalle righe `available` del pool del tipo giusto.
5. **Telegram**: gruppo/bot o muta.
6. **Riepilogo + diff**: `Salva bozza` (draft, zero effetti) -> `Valida` (ready) ->
   `Deploy` (prima volta che tocca il mondo: claim atomico, provisioning, push SSH) ->
   `Start`. Pulsanti espliciti sulla stessa macchina a stati della CLI; `Deploy` su `LIVE`
   chiede conferma.

### Funzioni di scala

A scala reale (es. ~15 fonti, alcune con decine di trader e topic), l'interfaccia **non**
puo' essere una lista piatta. Le tabelle devono avere:

- **Ricerca e filtri**: trova un trader, filtra per fonte, stato, conto, tipo.
- **Raggruppamento gerarchico**: la tabella Fonti raggruppa i topic sotto il loro canale
  (un gruppo Telegram con N topic-fonte si espande/collassa), con `#istanze` per gruppo.
- **Azioni bulk**: "tutti i trader di questa fonte -> conto X", "abilita/disabilita tutti",
  "applica scheletro Y a tutti" — per non ripetere N click.
- **Import con anteprima**: la tabella Trader ha un flusso di import bulk (upload file
  YAML/CSV o incolla tabella; in migrazione, seed dalla config esistente). L'import mostra
  un'**anteprima validata** prima di scrivere: righe nuove/aggiornate/duplicate e
  **collisioni alias** contro i frammenti/pattern consolidati; la conferma scrive solo lo stato
  desiderato (stesso core di `trader catalog import`). Analogamente, `attach-traders`
  sulla fonte accetta multi-selezione dal catalogo, non un trader per volta.
- **Paginazione**.

Nota di scala: il file piatto gigante **non esiste** in questo modello. L'inventory vive in
`management.db` (righe indicizzate), e la config **generata** di ogni istanza contiene solo
il **suo sottoinsieme** di fonti/trader. Nessuno edita ne' legge un file da centinaia di voci.

### Fasatura

- **Fase 1**: CLI (`tsbctl`) come interfaccia sorgente, scriptabile e testabile.
- **Fase 2**: UI a tabelle sottile sopra lo stesso core.
- Il **modello a 4 tabelle** si progetta da subito, perche' plasma anche la CLI
  (`instance list`, `account pool list`, `source list`, `trader catalog list`,
  `instance summary` sono gli equivalenti testuali delle 4 viste; `repo status`
  e' l'equivalente della vista raggruppata per Sistema).

---

## Provisioning tecnico

### Struttura codice proposta

```text
TeleSignalBot/
  management/
    __init__.py
    db/
      schema.py
      migrations/
    secrets.py
    bybit_provisioner.py
    instance_provisioner.py
    ingestion_provisioner.py
    systemd_manager.py
    telegram_provisioner.py
    cli.py
```

### Responsabilita' dei moduli

- `db/schema.py`
  - definizione e migrazioni di `management.db`
- `secrets.py`
  - lettura segreti dal `management.db` locale, scrittura `.env` per-istanza al deploy,
    backup cifrati ed export redatti
- `instance_provisioner.py`
  - genera struttura, YAML e `.env` dell'**esecutore** partendo da istanza, iscrizioni,
    catalogo trader, policy e override effettivi
- `ingestion_provisioner.py`
  - genera struttura, config derivata (canali dalle sottoscrizioni) e servizio
    dell'**ingestione** per Sistema
- `bybit_provisioner.py`
  - crea in bulk subaccount + API key via API (usa le `provider_credentials` master
    dell'ambiente giusto) e popola il pool; percorsi distinti demo/live
- `telegram_provisioner.py`
  - crea in automatico gruppo + topic Telegram via **sessione utente Telethon dedicata**
    e cattura i `thread_id`
- `systemd_manager.py`
  - deploy, installazione e gestione dei servizi
- `cli.py`
  - espone il workflow via `tsbctl`

### Due famiglie di servizi

- `tsb-ingest@{sistema}` - ingestione del Sistema (una sessione Telethon, canali derivati)
- `telesignalbot@{instance}` - esecutore/notificatore per istanza (piano di esecuzione)

Se l'ingestione di un Sistema cade, restano al buio tutte le istanze del Sistema (blast
radius equivalente al monolite di oggi); l'esecuzione delle posizioni gia' aperte continua,
perche' `ops.sqlite3` e' locale. Uno split per-canale dell'ingestione e' un'evoluzione
futura se il blast radius diventera' un problema.

### Pool account (auto-provisioning Bybit)

La creazione degli account e' **staccata** dalla creazione istanza e avviene in bulk.

Flusso:

1. `account provision --count N --type DEMO|LIVE [--position-mode one_way|hedge]` chiama
   l'API Bybit, crea subaccount + API key con **permessi minimi** (trade + lettura,
   **mai withdraw/transfer**: una chiave compromessa non deve poter spostare fondi) e,
   se richiesto, imposta subito la **modalita' posizione** (`hedge`) via API — cosi'
   l'account entra nel pool gia' configurato, senza passaggi manuali post-claim. Salva in
   `exchange_accounts` con `status = available`, `instance_id = NULL` e `position_mode`
   registrato. *(Nota Bybit da verificare all'implementazione: lo switch position mode e'
   per coin/simbolo, non un flag account-wide — il provisioner lo applica come default
   sui perpetual USDT.)*
2. In assembly l'operatore **seleziona** N account **del tipo giusto** dal pool (stato
   desiderato); al **deploy** avviene il claim atomico (`available -> assigned`,
   assegnazione di `logical_account_id` e `adapter_name`) e la **IP whitelist** della key
   viene impostata (via API modify) sull'IP del server del Sistema dell'istanza - al
   provision l'account e' nel pool e il server non e' ancora noto.

Vincoli e accorgimenti:

- **Percorsi distinti demo/live.** Il flusso live tocca conti reali e va dietro gate
  esplicito (mai auto-creare account live senza conferma).
- **Limiti Bybit.** Esiste un tetto al numero di subaccount e rate limit per tier
  (da verificare); la creazione va **sequenziale con backoff**, non parallela.
- **Scadenza key senza IP.** Policy Bybit (da riverificare all'implementazione): le API
  key **senza IP whitelist scadono dopo ~90 giorni**; quelle vincolate a IP non scadono.
  Gli account che restano a lungo nel pool senza claim vanno quindi o vincolati subito a
  un IP provvisorio, o monitorati per la scadenza (alert da `control_events`).
- **Registrazione manuale** (`account register`) resta come percorso alternativo senza API
  per account creati a mano.

### Provisioning Telegram automatico

Vincoli di piattaforma: **un bot non puo' creare gruppi**, e **un bot non si puo' creare
via API** (solo BotFather, a mano): il token del bot e' sempre un **input dell'operatore**.
Il provisioning completo usa quindi una **sessione utente Telethon dedicata** (separata
da quella che ascolta le fonti, per non mescolare i ruoli).

Flusso di `provision telegram` (saltato per istanze `muted`):

1. `CreateChannel(megagroup=True)` - crea il supergroup
2. `ToggleForum` - abilita i topic
3. aggiunge il bot dell'istanza e lo promuove admin (`EditAdmin`)
4. `CreateForumTopic` per ogni trader/ruolo - **cattura i `thread_id`**
5. salva `chat_id` + `thread_id` + bot token in `management.db`

Vincoli:

- **Flood limit Telegram.** La creazione di gruppi/topic va **throttlata**; a volume alto
  puo' scattare `FloodWait` o restrizioni sull'account utente.
- Il gruppo risulta di proprieta' dell'utente Telethon di provisioning, non del bot.

---

## Aggiornamento repo e rollout versioni

Il workflow di onboarding di una nuova istanza non coincide con il workflow di aggiornamento del codice condiviso.

### Distinzione operativa

- `deploy istanza`
  - prepara o aggiorna config, `.env` e binding di servizio di una singola istanza
- `upgrade repo`
  - aggiorna il clone di codice **di un Sistema** (uno per Sistema, non uno globale)

Il rollout e' guidato dalla **macchina di controllo locale** via SSH. Ogni Sistema ha il
proprio clone in `/opt/telesignalbot/sistemi/{sistema}/repo/`, condiviso dai suoi
esecutori e dalla sua ingestione.

### Canary a livello Sistema: DEMO prima, LIVE dopo

Il canary **non e' per-istanza**: e' il **Sistema DEMO**. Ogni Sistema ha **il proprio
clone** a revisione propria, quindi il canary funziona anche con **un solo server fisico**:

```text
Sistema DEMO:  repo @ rev N+1    <- in test
Sistema LIVE:  repo @ rev N      <- stabile, intatto
```

Aggiornare il clone del Sistema DEMO non tocca in alcun modo il LIVE. `management.db`
(locale) traccia la revisione di ciascun Sistema; quando DEMO valida, si **promuove**
il LIVE.

Un solo server basta per partire. Separare i server resta la scelta **raccomandata**
quando il LIVE entra in produzione seria: isolamento di guasto, contesa risorse,
superficie di sicurezza (le chiavi live non coabitano con codice sperimentale).

### Aggiornamenti a fermo coordinato per-Sistema (decisione 2026-07-02)

La procedura standard di aggiornamento codice e' il **fermo coordinato del Sistema
intero**: stop di ingestione + tutte le istanze del Sistema -> `repo upgrade` (codice +
migrazioni, a servizi fermi) -> restart di tutto. E' il modello operativo gia' in uso oggi
(stop, pull, restart), esteso al Sistema. Il canary resta per-Sistema: fermare e aggiornare
il Sistema `demo` non tocca in alcun modo il `live`, anche sullo stesso server.

- **Nessuna finestra mista** codice vecchio/nuovo: a Sistema fermo le migrazioni possono
  essere anche **distruttive** senza rischio. La disciplina additive-only **non e' piu'
  un vincolo** del primo design.
- **La finestra di fermo e' gia' coperta dal design**: al restart gli esecutori recuperano
  il backlog con le protezioni anti-stale (`SIGNAL` vecchio non apre, `UPDATE/CLOSE`
  arretrato si applica solo su chain coerente). Vale la stessa logica del riavvio normale.
- **Rischio accettato**: durante il fermo (tipicamente 1-2 minuti) le posizioni aperte
  restano senza gestione — un TP/SL gestito dal bot in quella finestra non scatta finche'
  il Sistema non riparte. E' lo stesso rischio del riavvio singolo di oggi, moltiplicato
  per le istanze del Sistema.
- **Proprieta' delle migrazioni (invariata)**: lo shared DB lo migra **solo l'ingestione**
  al proprio boot (o `repo upgrade`); gli esecutori aprono **senza migrare** (oggi
  `main.py` migra il parser DB a ogni avvio: in modalita' esecutore questo passo si
  disattiva). `ops.sqlite3` (per-istanza) migra al riavvio della singola istanza.
- **Rolling update = opzione futura, non MVP**: la meccanica `plan`/`apply` per singolo
  target resta nel design (serve comunque per applicare *config* a una sola istanza), ma
  non e' la procedura di aggiornamento codice. Se con molte istanze il fermo totale
  diventasse oneroso, il rolling si riabilita — e **solo allora** servira' la disciplina
  additive-only sulle migrazioni dello shared DB (con relativo check in `repo upgrade`).

### I quattro verbi (MVP)

```bash
tsbctl repo status <sistema>                   # revisione del clone vs remoto
tsbctl repo upgrade <sistema> [--ref R]        # git pull + deps + migrazioni; NON riavvia (si usa dentro il fermo coordinato)
tsbctl rollout plan                            # chi (istanze e ingestioni) e' indietro / ha config drift
tsbctl rollout apply <target> [--no-restart]   # riconcilia config + riavvia (target: istanza|ingestione|--all)
tsbctl rollback <target> --to <rev>            # riconcilia a una revisione precedente
```

- `repo upgrade` rende il codice **disponibile** nel clone del Sistema, senza riavviare
  nulla; mostra `from revision -> to revision`, fallisce se la working tree e' dirty,
  registra la revisione nel control plane locale.
- `rollout apply` = **riconcilia il target al suo stato desiderato**: rigenera la config da
  `management.db` locale se serve, la pusha via SSH, e riavvia il servizio sul codice corrente.

Il concetto mentale e' uno solo: **`apply` = fai combaciare questo servizio col suo stato
desiderato** (config + codice). `repo upgrade` mette il codice *a disposizione*; `apply`
decide *chi* ci passa e *quando*.

### Controllo del riavvio

Per l'**aggiornamento codice** vale la procedura standard a fermo coordinato (sezione
sopra). Il controllo fine del riavvio resta utile per i **cambi di config** su una
singola istanza:

- `rollout apply <target> --no-restart` prepara (config pronta) **senza riavviare**:
  riparti quando l'istanza e' **flat** o il mercato e' calmo
  (`systemctl restart telesignalbot@<nome>`). Rilevante solo per i cambi che richiedono
  restart (wiring `execution.yaml`); detection e comportamento sono hot-reload.
- Su **LIVE**, `apply` chiede **conferma esplicita** (gate LIVE).

### Flusso tipico

```bash
# 1. Testa su DEMO (canary): fermo coordinato del solo Sistema demo
tsbctl sistema stop demo       # ferma ingestione + tutte le istanze del Sistema
tsbctl repo upgrade demo       # git pull + deps + migrazioni (a Sistema fermo)
tsbctl sistema start demo      # riavvia tutto; gli esecutori recuperano il backlog

# ...si valida sul demo (stesse fonti del live, segnali reali)...

# 2. Promuovi su LIVE quando validato (gate LIVE: conferma esplicita)
tsbctl sistema stop live
tsbctl repo upgrade live
tsbctl sistema start live
```

`sistema stop <nome>` / `sistema start <nome>` sono i verbi del fermo coordinato:
fermano/avviano in ordine sicuro l'ingestione e tutte le istanze del Sistema.

### Cosa traccia il control plane

- `deployed_revision` vs `target_revision` per **Sistema** (revisione disponibile vs
  effettivamente in uso dai suoi servizi)
- esito dell'ultimo canary DEMO
- stato dell'ultimo deploy di config e dell'ultimo upgrade codice

Il supporto e' la tabella `control_events`: ogni verbo (`deploy`, `upgrade`, `rollout`,
`rollback`) registra esito, target e revisione.

`rollout plan` puo' **verificare** la revisione reale sul server via SSH contro quella
registrata, per rilevare drift.

### Fuori MVP (future)

`rollout history`, `rollout diff`, targeting `--group`: utili in evoluzione, non nel primo
design. Con `plan` + `apply` + `rollback` si copre il caso da operatore singolo.

---

## Impatto minimo su `main.py`

Il runtime non deve diventare il luogo dove vive la logica multi-istanza. `main.py` deve
solo selezionare modalita', config dir e path DB; la logica nuova sta in servizi/worker
dedicati. La modifica minima prevista e':

```python
instance_name = os.environ.get("BOT_INSTANCE_NAME")

if instance_name:
    instance_dir = resolve_instance_dir(instance_name)
    config_dir = instance_dir / "config"
    data_dir = instance_dir / "data"
else:
    config_dir = Path("config")
    data_dir = Path(".local")
```

Stato attuale del codice: `main.py` usa `config_dir = str(root_dir / "config")` hardcoded,
ma i path dei DB sono **gia' overridabili via env**: `PARSER_DB_PATH` e `OPS_DB_PATH`
(default `root_dir/db/parser.sqlite3` e `root_dir/db/ops.sqlite3`). La modifica minima
riguarda quindi **solo la config dir**; per i DB il control plane scrive le env giuste
nel `.env` generato, senza toccare il runtime.

Sotto il Modello B, `PARSER_DB_PATH` (segnale capito) punta al `parser.sqlite3`
**condiviso del Sistema**, mentre `OPS_DB_PATH` resta locale all'istanza. La risoluzione
di questi path e' compito del control plane, non del runtime.

Seconda modifica richiesta: in **modalita' esecutore** il runtime **non applica le
migrazioni** al parser DB (oggi `main.py` le applica a ogni avvio). Lo shared DB e'
migrato solo dall'ingestione, che ne e' l'unica writer.

Modifica runtime collegata ma non da mettere in `main.py`: estrarre la logica di
enrichment in `build_enriched_for_instance(...)` e introdurre un worker esecutore che legge
il feed canonical condiviso (`canonical_messages JOIN raw_messages`) e scrive cursori /
enrichment per-istanza nel proprio `ops.sqlite3`.

---

## Segreti e protezione

### Modello

I segreti (API key, token bot, session string Telethon) vivono **in chiaro dentro
`management.db`**, che sta sulla **macchina di controllo locale**, non sui server (vedi
Topologia). Non si usa cifratura a livello campo: `management.db` si tratta come si tratta
oggi un `.env` (`chmod 600`, mai committato, mai lasciato in giro).

Perche' e' accettabile - anzi migliore di oggi: la copia **principale** di tutti i segreti
si sposta **fuori dai server esposti a internet**, sulla macchina di controllo. I server
mantengono solo il `.env` della propria istanza, scritto da `tsbctl` al deploy (come oggi).

### Confine di protezione

Il rischio residuo e' uno: `management.db` e' **un unico file con tutti i segreti di tutte
le istanze**. Due regole lo neutralizzano:

1. **Backup cifrato.** Ogni export/backup di `management.db` cifra il **file intero** al
   momento del backup (tarball + passphrase), non i singoli campi. Un solo punto di controllo.
2. **Export redatto.** La dashboard e il debug non hanno bisogno dei valori dei segreti:
   le copie verso altre macchine si esportano **senza le colonne dei segreti**. Il file
   che viaggia non porta chiavi.

Con queste due regole, il DB grezzo non lascia mai la macchina di controllo in chiaro.

### Chiave SSH

`servers.ssh_key` e' preferibilmente un **path** a un file chiave con permessi stretti,
**non** il contenuto nel DB. Non si centralizza la chiave SSH dentro `management.db`.

### Estensione futura opzionale

La cifratura a livello campo (`cryptography.fernet` + master key + rotazione via
`MultiFernet`) resta come **hardening futuro**, non nel primo design. Se e quando servira'
(es. control node condiviso in team), si aggiunge senza cambiare il modello dati.

---

## Rischi e vincoli

| Rischio | Severita' | Note |
|---|---|---|
| Rate limit Bybit per creazione subaccount | Media | da verificare prima di provisioning in bulk; creazione sequenziale con backoff |
| Key API con permessi eccessivi o senza IP | Alta | permessi minimi alla creazione (mai withdraw/transfer); IP whitelist impostata al claim; key senza IP scadono ~90gg (pool da monitorare) |
| Doppia rivendicazione di un account dal pool | Alta | due istanze sullo stesso conto = doppia esecuzione; mitigato da **claim atomico** `available -> assigned` |
| Esecuzione su ambiente sbagliato (demo vs live) | Alta | mitigato per costruzione: `environment` intrinseco al pool + claim solo per tipo + gate `LIVE` |
| Limiti Telegram / ban dell'utente Telethon di provisioning | Media | mitigato da **throttling** anti-flood; sessione utente **dedicata** separata dall'ingestione |
| Interpretazione divergente dello stesso segnale | Alta | risolto dentro il Sistema (ingestione unica); tra Sistemi la doppia lettura e' voluta e tracciata (canary) |
| Cursore per esecutore (fan-out) | Alta | high-water-mark locale + dead-letter; ordine stretto per fonte; nuova sottoscrizione live-only; cursore in `ops.sqlite3`, non nel control plane |
| Doppia esecuzione dopo crash dell'esecutore | Alta | `ops_instance_enriched_events` locale con `UNIQUE(canonical_message_id)`, stati terminali non riprocessati, idempotenza su identita' segnale (`014_ops_signal_identity`) |
| Head-of-line blocking su poison message | Media | mitigato da dead-letter dopo K retry + alert tech |
| Contesa su `parser.sqlite3` condiviso | Media | l'ingestione resta unica writer e gli esecutori sono reader; WAL gia' attivo; stesso host garantito per costruzione (Sistema); a scala molto alta valutare un bus dedicato |
| Ingestione del Sistema giu' | Media | tutte le istanze del Sistema al buio (come il monolite); posizioni aperte continuano su `ops.sqlite3` locale; split per-canale come evoluzione futura |
| Crescita illimitata del `parser.sqlite3` | Bassa | retention per eta' lato ingestione (finestra >> massimo ritardo di recupero); `ops.sqlite3` mai potato (storia contabile) |
| Backup di `management.db` | Alta | punto centrale di verita' + tutti i segreti; **backup cifrato** + export redatti; macchina non esposta |
| Perdita del control node locale | Media | le istanze continuano a girare; mitigato da backup cifrati regolari |
| Storage della chiave SSH | Media | path a file con permessi stretti, non contenuto nel DB |
| Drift tra DB centrale e server target | Alta | `validate` e `deploy` devono rilevare inconsistenze; `rollout plan` verifica via SSH |
| Migrazione distruttiva sullo shared `parser.sqlite3` | Media | neutralizzata dalla procedura standard a **fermo coordinato per-Sistema** (nessun lettore attivo durante la migrazione); migra solo l'ingestione; additive-only torna necessario solo se si adotta il rolling update |
| Posizioni senza gestione durante il fermo coordinato | Media | rischio accettato (finestra 1-2 min, come il riavvio singolo di oggi); recupero backlog + protezioni anti-stale al restart |
| Edit di un segnale sotto fan-out | Media | decisione per-esecutore sulle proprie chain (skip+notifica se chain esiste, applica se no); ingestione sempre "stupida"; feed include `run_context edit:%` |
| Riavvio di un'istanza con posizioni aperte (cambio config) | Media | mitigato da `rollout apply --no-restart` + riavvio quando l'istanza e' flat |
| Segreti in chiaro nel `.env` per-istanza sui server | Media | residuo, come oggi; confine di protezione = control node non esposto; disco cifrato consigliato |
| Collisione alias dentro una fonte o cross-gruppo (consolidato Sistema) | Media | `validate` blocca entrambi i casi; alias ambigui -> override per-fonte in `channels.yaml`; oggi passa silenzioso |

---

## Piano implementativo ad alto livello

1. Introdurre `management.db` e il suo schema iniziale (sistemi, istanze, sources, iscrizioni, catalogo globale).
2. Implementare gestione segreti (file locale permissionato) e backup cifrati/export redatti.
3. Implementare il bootstrap: `server add`, `sistema create` (struttura + clone +
   servizio ingestione), `provider add`, `telethon login` (interattivo).
4. Implementare `tsbctl instance create` (con `--type`, `--sistema` e `--muted`) e binding del gruppo Telegram istanza.
5. Implementare `source register`, `source subscribe`, `source attach-traders` e `trader catalog`.
6. Implementare il **pool account**: `account provision` (bulk via API Bybit), `account claim` (atomico) e `trader bind-account`.
7. Estrarre `build_enriched_for_instance` dalla logica attuale di `SignalEnrichmentProcessor`
   e usarlo anche nel flusso single-instance legacy.
8. Introdurre le tabelle locali in `ops.sqlite3`: `ops_instance_source_cursors` e
   `ops_instance_enriched_events`.
9. Implementare il worker esecutore multi-istanza: legge `canonical_messages JOIN raw_messages`,
   filtra per `source_key`/trader attivi, chiama il builder, passa al lifecycle, avanza il
   cursore locale.
10. Implementare `provision prepare` (esecutore + ingestione).
11. Implementare `provision bybit` e `provision telegram`.
12. Implementare `validate`, incluso il controllo collisioni alias per fonte.
13. Implementare `deploy` e gestione delle due famiglie di servizi.
14. Applicare la modifica minima a `main.py`: `BOT_INSTANCE_NAME` per la config dir e
    disattivazione delle migrazioni parser in modalita' esecutore (i path DB sono gia'
    pilotabili via `PARSER_DB_PATH`/`OPS_DB_PATH`).
15. Mantenere `enriched_canonical_messages.lifecycle_processed` come compatibilita'
    single-instance/legacy finche' il nuovo worker non sostituisce il flusso.
    **Vincolo esplicito (2026-07-02):** il flusso legacy **scrive sul parser DB**
    (`enriched_canonical_messages` vive li': `main.py:536`, `entry_gate.py`), quindi e'
    compatibile **solo** con parser DB non condiviso (single-instance). Il worker
    esecutore (step 9) e' **prerequisito** del primo deployment multi-istanza: nessun
    Sistema puo' ospitare piu' di un'istanza prima. (Vincolo di fatto non stringente:
    non e' previsto alcun deploy prima del completamento della nuova logica.)
16. Eseguire la migrazione della config mista esistente in istanze a isolamento rigido.

---

## Decisioni fissate da questa revisione

- l'istanza e' una **unita' operativa autonoma** di **esecuzione**
- gerarchia **Server -> Sistema -> Istanza**: il Sistema e' l'unita' di deployment
  (clone del repo + ingestione + istanze, stesso host), tipato `DEMO`/`LIVE`; piu'
  Sistemi possono coabitare sullo stesso server, ognuno a revisione propria
- architettura **Modello B**: ingestione **per-Sistema**, esecuzione **per-istanza**
- una fonte e' **globale** (catalogo); dentro un Sistema e' ascoltata **una sola volta**;
  tra Sistemi la doppia lettura e' accettata e tracciata (e' il canary); i canali ascoltati
  da un Sistema sono **derivati** dalle sottoscrizioni delle sue istanze (config generata,
  nessuna assegnazione manuale fonte -> listener)
- il segnale viene **capito una volta per Sistema** e distribuito in **fan-out** alle
  istanze del Sistema (consistenza garantita per costruzione)
- l'account/esecutore e' **indirizzabile indipendentemente dall'ingestione** (invariante)
- split dati: **`parser.sqlite3` condiviso per Sistema** / **`ops.sqlite3` locale per
  istanza**; il parser DB contiene raw/canonical/parser results ed e' scritto solo
  dall'ingestione; l'ops DB locale contiene cursori, enrichment per-istanza e storia
  contabile/trading
- fan-out via feed canonical condiviso (`canonical_messages JOIN raw_messages`) letto dagli
  esecutori; l'enrichment finale e' **per-istanza** tramite `build_enriched_for_instance`
  (account, risk, management plan, policy snapshot)
- consumo via **cursore high-water-mark locale per fonte** (+ dead-letter per poison
  message), con **ordine stretto per fonte**; il cursore vive nell'**`ops.sqlite3` locale**,
  non nel control plane; topologia **un-writer / molti-reader** sul parser DB con poll +
  WAL (stesso host garantito dal Sistema); nuova sottoscrizione **live-only** di default;
  backlog sicuro (`SIGNAL` stale non apre, `UPDATE` senza target diventa no-op/review);
  migrazioni dello shared DB applicate **solo dall'ingestione** (gli esecutori aprono senza
  migrare il parser DB)
- una istanza puo' essere **multi-fonte** (via iscrizione)
- una fonte puo' servire **uno o piu' trader** dal catalogo globale
- il **registro trader e' globale e magro** (`trader_id` + `display_name` + riferimento
  `parser_profile` per nome; nessun `instance_id`): alimenta il gate `registered_traders`;
  detection e comportamento nei file; i parser sono **codice** distribuito via `repo upgrade`
- i trader possono usare account exchange **dedicati o condivisi**
- **isolamento rigido `DEMO`/`LIVE`**: nessuna istanza mescola account demo e live;
  `exchange_accounts` ha **una sola coppia** di chiavi e `environment` DEMO/LIVE
  **intrinseco** all'account (partiziona il pool)
- modello account: `management.db` modella il **wiring** (account logico -> adapter ->
  credenziali); il **tuning comportamentale** vive nei template
- account a **pool globale**: auto-provisioning Bybit in **bulk** (`account provision`),
  account in stato `available`, poi **claim** atomico all'istanza; `environment` demo/live
  intrinseco partiziona il pool e rende l'isolamento rigido una proprieta' dei dati
- provisioning Telegram **completamente automatico** via **sessione utente Telethon
  dedicata** (il bot non puo' creare gruppi): crea gruppo + topic e cattura i `thread_id`,
  con throttling anti-flood; saltato per istanze `muted`
- workflow a **due piani**: piano condiviso (pool, catalogo trader, fonti) e piano istanza
  (iscrizione, claim, policy, deploy), con **wizard netto** (i prerequisiti si creano nel
  piano condiviso, non "di nascosto" durante l'assembly)
- **membership trader -> fonte globale**, **binding trader -> account per-istanza**;
  parser **della fonte** (parser per-trader solo via fonti separate)
- `source edit` / `trader catalog edit` devono avvisare **"consumato da N istanze"**
  (blast radius delle modifiche globali)
- **UI di controllo dedicata** (Fase 2): **web app locale** servita da `tsbctl ui` su
  localhost, sopra lo **stesso core** della CLI (non statica: scrive e orchestra). **4
  tabelle umane** (Istanze, Account/pool, Fonti, Trader) con **funzioni di scala** (ricerca,
  filtri, raggruppamento per canale, azioni bulk); i Sistemi non sono una quinta tabella ma
  il **raggruppamento** della vista Istanze; editing dello **stato desiderato + apply
  esplicito**, blast-radius visibile, credenziali write-only, gate `LIVE`
- **forme di fonte**: mono-trader (`fixed`), multi-trader (`dynamic`), gruppo con topic per
  trader = **N fonti che condividono il canale**; la fonte definisce il set potenziale di
  trader, mentre ogni istanza seleziona il sottoinsieme attivo e il conto per trader via
  `trader_account_bindings`
- ogni istanza ha un **proprio gruppo Telegram** di controllo e notifica, **oppure nessuno**
  (istanza **muted**)
- vocabolario detection **allineato al codice**: `trader_binding fixed|dynamic` +
  `resolution_mode default|patterns_only` (niente `alias|pattern|hybrid`)
- alias/detection **nei frammenti per pattern_group** (`config/patterns/<gruppo>.yaml`,
  versionati nel repo), non nel registro; `tsbctl` **genera per Sistema** i file consolidati
  (`text_patterns.yaml`, `trader_aliases.json`) con i soli gruppi delle fonti sottoscritte
  (runtime invariato); risoluzione **scopata alla membership**, collisioni **bloccate da
  `validate`**
- macchina a stati completata: `stop: active -> deployed` (coppia simmetrica con `start`);
  **l'edit non cambia stato**: crea **drift** visibile in `diff`/`rollout plan`
- il provisioning e' **semi-guidato**
- `management.db` e' la **fonte di verita'** e **control plane**, non replica il dettaglio trading
- `management.db` contiene **tutti i dati per le automazioni**: accesso server (chiave SSH,
  niente password), credenziali **master provider** per il provisioning subaccount
  (`provider_credentials`, demo e live distinte), sessioni Telethon (ingestione per Sistema
  + provisioning, con `api_id`/`api_hash`), log operazioni (`control_events`); l'heartbeat
  runtime resta fuori (piano monitoring della dashboard)
- **topologia control plane locale**: `management.db` e i segreti stanno sulla macchina di
  controllo dell'operatore; i server eseguono solo runtime e ricevono il `.env` per-istanza
  al deploy; le istanze girano anche a control node spento
- **segreti in chiaro in `management.db` locale** (trattato come un `.env`), protetti da
  permessi OS + disco cifrato, **backup cifrati** ed **export redatti**; niente Fernet/master
  key nel primo design (hardening futuro opzionale)
- YAML e `.env` sono **artefatti generati**
- sistema config **stratificato ma runtime invariato**: detection globale condivisa
  (ingestione); comportamento (`operation_config`, `traders/<id>.yaml`) per-istanza,
  generato da **scheletro + override** con precedenza **a 2 livelli come oggi** (niente
  eredita' viva); `registered_traders` **derivato** dalle sottoscrizioni; propagazione
  dello scheletro **esplicita**
- **schema config come SSOT** (Pydantic): il **formato resta YAML**; una definizione ->
  runtime (con validazione) + **editor UI auto-generato** + `validate`. Override per-trader
  come **blob opaco** in `management.db` (trasportato, non modellato)
- **hot-reload vs restart** (verificato): detection e comportamento si applicano **a caldo**
  (`ChannelConfigWatcher`, `reload_if_changed()`); solo il **wiring** esecuzione (conti/adapter)
  richiede restart; il diff lo classifica
- il bot runtime resta **quasi invariato**
- onboarding istanza e upgrade repo sono **workflow distinti**
- rollout: **canary a livello Sistema DEMO** (un clone per Sistema, revisioni indipendenti;
  un solo server basta per partire, server separati raccomandati in produzione),
  **migrazioni shared additive-only** (niente coordinamento), **quattro verbi**
  (`repo upgrade`, `rollout plan`, `rollout apply --no-restart`, `rollback`), **conferma su
  LIVE**; `repo upgrade` non riavvia mai, `apply` riconcilia allo stato desiderato

### Decisioni della revisione punto-per-punto (2026-07-02)

Rassegna completa del documento contro il codebase, decisa punto per punto con l'utente:

1. **Edit/delete sotto fan-out**: decisione all'**esecutore** (sulle proprie chain);
   l'ingestione registra/ri-parsa sempre senza consultare stato di esecuzione; il feed
   include `run_context live + edit:%` (mai `delete:`); gli esecutori non rileggono mai
   `raw_text` per decisioni di trading.
2. **Flusso legacy sul parser DB**: `enriched_canonical_messages` vive nel parser DB ->
   legacy compatibile solo single-instance; il worker esecutore e' prerequisito del
   multi-istanza.
3. **Migrazione dati = fresh start**: DB nuovi per tutte le istanze, nessuna eredita' di
   `ops.sqlite3` (resta archivio), nessun cutover cursori.
4. **Claim account al deploy**: l'assembly registra solo la selezione (stato desiderato);
   `available -> assigned` avviene atomicamente dentro `deploy`, stessa semantica CLI e UI.
5. **Cursore: due meccanismi separati**: prima iscrizione = sempre live-only (`MAX(id)`);
   riavvio = sempre recupero backlog con protezioni anti-stale; rimosso il campo
   `resume_policy` col default ambiguo.
6. **Macchina a stati completata**: `rollout apply` su istanza gia' deployata/attiva non
   cambia stato (riconcilia); `error` = "operazione X fallita per motivo Y"
   (`control_events`), uscita = correggi e rilancia; `apply` idempotente.
7. **Blacklist attribuite**: testo = della fonte (globale, `sources.text_blacklist`,
   blast radius); simboli = per-istanza (livelli istanza + per-trader, override runtime
   nell'ops DB locale).
8. **Telethon: un solo numero** per tutte le sessioni (ingestioni + provisioning);
   rischio "destino condiviso" accettato; throttling sul provisioning.
9. **Sezione cursore ristrutturata**: le regole (high-water-mark, dead-letter, MAX(id),
   idempotenza, retention) sono vigenti sul feed canonical, non "legacy".
10. **Aggiornamenti = fermo coordinato per-Sistema** (`sistema stop` -> `repo upgrade` ->
    `sistema start`): niente rolling update nell'MVP, additive-only non piu' vincolante;
    canary DEMO->LIVE invariato; rischio finestra senza gestione accettato.
11. **Alert su gap di retention**: cursore piu' vecchio del primo segnale disponibile ->
    alert tech esplicito + ripartenza dal primo disponibile; mai silenzioso.
12. **`parser_profile` = chiave canonica del registry parser_v2**
    (`src/parser_v2/profiles/registry.py`); `validate` usa `list_parser_v2_profiles()`;
    mai alias nel catalogo. parser_v2 e' l'**unico parser attivo** del runtime
    (`profiles/Legacy/` = solo archivio storico): un solo registry, nessuna convivenza.
13. **Telegram alla migrazione: nessun riuso**: gruppi nuovi provisionati per ogni
    istanza; supergroup attuale = archivio; `muted` = eccezione consapevole (nessun
    canale comandi runtime), reversibile.

---

Questa revisione definisce il workflow operativo tipico e l'architettura B. L'implementazione dovra' poi dettagliare contratti, validazioni e comportamento dei singoli comandi.
