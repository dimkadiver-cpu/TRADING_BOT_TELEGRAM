# Orchestrazione Multi-Istanza - Design Spec
**Data:** 2026-06-30 (rev. 2026-07-01)
**Stato:** In revisione - architettura B approvata

> Revisione 2026-07-01: adottato il **Modello B** (ingestione per-fonte + esecutori
> per-istanza), isolamento rigido `DEMO`/`LIVE`, catalogo trader globale, modello
> account allineato al runtime reale, vocabolario detection allineato al codice.
> Le decisioni sono elencate nella sezione finale.

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

### Piano di ingestione (per fonte)

- Ogni **fonte** Telegram e' ascoltata da **un solo** listener/parser, indipendentemente
  da quante istanze la consumano.
- L'ingestione produce il **segnale capito** (canonico/enriched, con trader risolto)
  una volta sola.
- Motivazione: un canale Telegram e' una risorsa esterna condivisa. Leggerlo N volte
  significa (a) rischio di limitazioni/ban Telegram proporzionale al numero di istanze
  gemelle, e soprattutto (b) rischio che due istanze **interpretino lo stesso messaggio
  in modo diverso** (versioni parser o config pattern divergenti), eseguendo lo stesso
  segnale in modi diversi su conti diversi. Questo e' un difetto di **consistenza**,
  non una semplice inefficienza.

### Piano di esecuzione (per istanza)

- Ogni **istanza** e' un esecutore + notificatore, legato ai propri **account** e al
  proprio (eventuale) gruppo Telegram.
- Un'istanza si **iscrive** alle fonti che le interessano e riceve in **fan-out** il
  segnale gia' capito.
- Un'istanza "muta" (senza gruppo Telegram) e' in pratica **solo un esecutore** agganciato
  all'ingestione di una fonte gia' ascoltata da un'altra istanza.

### Regola di scaling

> **I listener contano le fonti, gli esecutori contano le istanze.**

Esempio: 5 istanze sulle fonti `{S1, S2}` e altre 5 sulle fonti `{S3, S4}` richiedono
**4 listener** (uno per fonte), non 20, e **10 esecutori** (uno per istanza). Aggiungere
una 6ª istanza a un gruppo esistente costa **zero listener** (le fonti sono gia' ascoltate)
e **un esecutore**.

### Invariante da preservare

> **L'account/esecutore deve essere indirizzabile indipendentemente da chi ascolta.**
> Il listener non deve mai "possedere" l'account.

Finche' questo invariante regge, il modello dati resta relazionale e account-centrico,
e l'evoluzione dell'infrastruttura di distribuzione (da DB condiviso a bus dedicato)
resta un cambio di runtime, non una ri-modellazione.

### Split dei dati

- **`parser.sqlite3` (segnale capito)** -> **condiviso a livello di fonte**. Raw, canonical
  ed enriched vivono qui, prodotti una volta.
- **`ops.sqlite3` (ordini/posizioni/fill/PnL)** -> **locale per istanza/account**.

Questa separazione e' gia' anticipata dal runtime attuale (`parser.sqlite3` e `ops.sqlite3`
sono file distinti e la pipeline `runtime_v2` gia' separa il messaggio canonico
dall'execution gateway).

---

## Workflow tipico

Il workflow approvato e' semi-guidato: il sistema automatizza la preparazione e la validazione, mentre i passaggi sensibili restano confermati o completati dall'operatore.

### Scenario base

1. L'operatore decide se usare una istanza esistente oppure crearne una nuova.
2. Se serve una nuova istanza, la crea scegliendo `DEMO` o `LIVE` (isolamento rigido:
   tutti gli account dell'istanza saranno dello stesso tipo).
3. L'istanza nasce in stato `draft`.
4. Associa all'istanza:
   - server target
   - gruppo Telegram dell'istanza **oppure** flag "istanza muta"
   - bot Telegram dell'istanza (se non muta)
   - eventuali credenziali Telethon dell'istanza (solo se introduce fonti nuove da ascoltare)
5. Per ogni fonte da gestire:
   - se la fonte **esiste gia'** (ascoltata da un'altra istanza): l'istanza vi si **iscrive**;
   - se la fonte e' **nuova**: si registra la fonte, si costruisce/valida il parser,
     si assegna il listener di ingestione, poi l'istanza vi si iscrive;
   - seleziona uno o piu' trader/profile dal **catalogo globale**
   - applica una policy account di default
   - applica una policy Telegram di default (ignorata se l'istanza e' muta)
   - definisce solo gli override locali necessari
6. Il sistema prepara in automatico:
   - record centrali in `management.db`
   - struttura filesystem dell'istanza
   - file YAML runtime
   - `.env` dell'istanza
   - mapping di fonti, trader, iscrizioni e destinazioni
7. I passaggi sensibili vengono completati o confermati dall'operatore:
   - claim degli account dal **pool** (il provisioning Bybit in bulk e' avvenuto prima, a parte)
   - provisioning Telegram automatico (gruppo + topic), confermato dall'operatore
8. L'operatore esegue la validazione finale dell'istanza.
9. Se la validazione passa, l'istanza va in `ready`.
10. L'operatore esegue il deploy.
11. Il deploy installa file e servizi sul server target e porta l'istanza in `deployed`.
    Per le fonti nuove installa anche il relativo servizio di ingestione.
12. L'avvio finale e' esplicito: `start` porta l'istanza in `active`.

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
5. applicare il deploy della nuova configurazione
6. riavviare l'istanza solo se richiesto dal tipo di modifica

### Workflow scalabile per fonti con molti trader

Per fonti che contengono molti trader il workflow non deve essere costruito come una configurazione piatta e manuale di:

- alias per trader
- pattern per trader
- binding trader -> account
- binding trader -> topic Telegram

Il modello raccomandato e' invece:

1. definire i trader una volta sola nel **catalogo trader globale**
2. registrare la fonte come contenitore leggero con il proprio listener
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

- **Istanza** = unita' autonoma di **esecuzione** del bot (esecutore + notificatore)
- **Fonte** = input Telegram **globale**, ascoltato da un solo listener e condivisibile
  tra piu' istanze
- **Trader** = parser/profile/identita' logica definita nel catalogo globale
- **Catalogo trader** = definizione **globale e unica** dei trader disponibili, con
  detection, alias e pattern; riusabile da piu' istanze
- **Account exchange** = risorsa assegnabile a uno o piu' trader della stessa istanza,
  con wiring account logico -> adapter -> credenziali
- **Iscrizione (subscription)** = legame fonte -> istanza che abilita il fan-out del
  segnale capito verso quell'istanza
- **Gruppo Telegram istanza** = destinazione di controllo e notifica dell'istanza,
  **opzionale** (istanza muta)

Relazioni attese:

- una istanza puo' avere piu' fonti (via iscrizione)
- una fonte puo' essere consumata da piu' istanze
- una fonte puo' avere uno o piu' trader presi dal catalogo globale (membership)
- il trader viene definito una volta sola nel catalogo globale e poi associato alle fonti
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
- `deploy` puo' portare solo da `ready` a `deployed`
- `start` puo' portare solo da `deployed` a `active`
- errori in qualunque fase portano a `error` con motivazione tracciabile

Gli stati riflettono il ciclo di vita dell'**esecutore/istanza**. I servizi di ingestione
per-fonte hanno un ciclo di vita proprio (vedi Provisioning tecnico) e non sono modellati
come stati dell'istanza.

---

## Comandi `tsbctl`

La CLI `tsbctl` e' l'orchestratore del workflow. Il principio e' evitare un unico comando "magico" di onboarding e preferire step espliciti, ripetibili e osservabili.

### Comandi principali

```bash
# Creazione guidata istanza
tsbctl instance init

# Modifica guidata istanza esistente
tsbctl instance edit alpha_demo

# Gestione catalogo trader globale
tsbctl trader catalog add
tsbctl trader catalog edit trader_a
tsbctl trader catalog list

# Gestione fonti globali e iscrizioni
tsbctl source register --channel 12345 --label fonte_a
tsbctl source subscribe alpha_demo --source fonte_a

# Pool account: creazione in bulk (staccata dalla creazione istanza)
tsbctl account provision --count 20 --type DEMO --provider BYBIT
tsbctl account pool list --type DEMO --status available
tsbctl account claim alpha_demo --from-pool --count 3 --as demo_1,demo_2,demo_3

# Riepilogo / diff / verifica
tsbctl instance summary alpha_demo
tsbctl diff alpha_demo
tsbctl validate alpha_demo

# Deploy e ciclo operativo
tsbctl deploy alpha_demo
tsbctl instance start alpha_demo
tsbctl instance stop alpha_demo
tsbctl instance status alpha_demo
```

### Comandi tecnici di basso livello

I comandi granulari restano disponibili per repair, automazione e casi speciali:

```bash
tsbctl instance create --name alpha_demo --type DEMO --server vps1 [--muted]
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

- `instance init`
  - avvia un wizard testuale che raccoglie i dati minimi per creare una nuova istanza-esecutore coerente
- `instance edit`
  - avvia un wizard testuale per modificare una istanza esistente senza ricrearla
- `trader catalog add/edit/list`
  - gestisce il catalogo **globale** dei trader disponibili, con detection, alias e pattern
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
  - crea in **bulk** subaccount + API key via API Bybit e popola il **pool** globale in stato `available` (operazione staccata dalla creazione istanza)
- `account pool list`
  - elenca gli account del pool per tipo e stato
- `account claim`
  - **rivendica** N account dal pool per un'istanza (set atomico di `instance_id` + nome logico), stato `assigned`
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
  - installa sul server target (esecutore + eventuali listener di fonti nuove) e porta a `deployed`
- `instance start`
  - esegue l'avvio esplicito e porta a `active`

---

## Fonte di verita' e artefatti generati

### Fonte di verita'

`management.db` e' il registro centrale di verita' per:
- istanze (esecutori)
- server target
- catalogo trader globale
- fonti globali e loro listener
- membership fonte/trader
- iscrizioni fonte/istanza
- policy account e Telegram
- override fonte/trader/account exchange
- stato operativo
- riferimenti alle credenziali
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

- **`parser.sqlite3`** -> segnale capito (raw, canonical, enriched). **Condiviso a livello
  di fonte** tra le istanze iscritte.
- **`ops.sqlite3`** -> dettaglio trading (ordini, posizioni, fill, trade chain).
  **Locale per istanza/account.**

Il control plane mantiene solo metadati, stato operativo e riferimenti sufficienti per
una futura dashboard fleet-level con drill-down verso il dettaglio locale.

### Implicazione architetturale

Il runtime del bot resta quasi invariato:
- continua a leggere file di config e DB locali;
- non conosce la logica di orchestrazione;
- non dipende direttamente dalla semantica di onboarding.

Sotto il Modello B, l'unica evoluzione runtime rilevante e' il **punto di consegna** del
segnale capito: da consegna interna (esecutore locale) a pubblicazione condivisa
consumata da piu' esecutori. La cucitura esiste gia' nella pipeline `runtime_v2`
(messaggio canonico/enriched separato dall'execution gateway).

---

## Principi architetturali

- **Un solo repo clone** - il codice e' condiviso; le istanze differiscono per config, dati e credenziali
- **Due piani** - ingestione per-fonte e esecuzione per-istanza restano separati
- **Fonte e trader globali** - definiti una volta, riusati da piu' istanze
- **Account-centrico** - l'esecutore e' indirizzabile indipendentemente dal listener
- **Isolamento rigido DEMO/LIVE** - un'istanza non mescola account demo e live
- **Control plane centrale** - `management.db` governa inventory, stato e provisioning
- **Configurazione generata** - i file YAML sono artefatti derivati dal DB centrale;
  il tuning comportamentale vive in template, non in colonne DB
- **Bot quasi invariato** - il runtime deve restare focalizzato sull'esecuzione
- **Cifratura a riposo** - le credenziali in `management.db` devono essere cifrate
- **Workflow semi-guidato** - automazione alta sui passaggi meccanici, controllo umano sui passaggi sensibili

---

## Struttura filesystem proposta

```text
/opt/telesignalbot/
  repo/                        <- unico clone del codice
  ingestion/                   <- piano di ingestione condiviso (per fonte)
    {source}/
      parser.sqlite3           <- segnale capito, condiviso tra le istanze iscritte
      .env                     <- credenziali Telethon del listener
  instances/
    {name}/
      config/
        telegram_control.yaml
        channels.yaml          <- fonti a cui l'istanza e' iscritta
        execution.yaml
        traders/
      data/
        ops.sqlite3            <- dettaglio trading, locale
      .env
  management.db
/etc/telesignalbot/
  secrets.env                  <- TSB_MASTER_KEY, permessi stretti
```

### Note

- l'ingestione di una fonte e' condivisa: un solo listener e un solo `parser.sqlite3` per fonte
- ogni istanza-esecutore ha isolamento operativo su `ops.sqlite3`, config e credenziali
- il codice viene aggiornato una volta sola sul clone condiviso
- `management.db` resta separato dai DB runtime
- se una fonte e' usata da una sola istanza, i due piani possono coabitare sullo stesso
  server senza costi aggiuntivi

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
| ssh_key | TEXT | cifrato - path o contenuto chiave privata |
| status | TEXT | `active` \| `offline` \| `maintenance` |
| notes | TEXT | |

### `instances`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT UNIQUE | es. `main_live`, `scalping_demo` |
| server_id | INTEGER FK | -> `servers` |
| type | TEXT | `LIVE` \| `DEMO` - isolamento rigido |
| status | TEXT | `draft` \| `ready` \| `deployed` \| `active` \| `error` |
| instance_dir | TEXT | `/opt/telesignalbot/instances/{name}/` |
| systemd_unit | TEXT | `telesignalbot@{name}.service` |
| muted | BOOLEAN | true = istanza senza gruppo Telegram |
| tg_bot_token | TEXT | cifrato - null se muted |
| tg_group_id | TEXT | gruppo Telegram principale dell'istanza - null se muted |
| deployed_revision | TEXT | revisione codice effettivamente in uso |
| target_revision | TEXT | revisione target per rollout |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### `sources`

Fonte **globale**, con il proprio listener di ingestione. Non ha `instance_id`.

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
| ingestion_server_id | INTEGER FK | -> `servers`, dove gira il listener |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> Nota vocabolario: `trader_binding`/`resolution_mode` sostituiscono il precedente
> `alias|pattern|hybrid`, che non esiste nel runtime. `fixed` corrisponde a
> `trader_id: <id>` in `channels.yaml`; `dynamic` corrisponde a `trader_id: null` con
> `resolution.mode`.

### `source_instance_subscriptions`

Fan-out: quali istanze consumano quale fonte.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| source_id | INTEGER FK | -> `sources` |
| instance_id | INTEGER FK | -> `instances` |
| cursor | TEXT | posizione consumata dall'esecutore sul segnale della fonte |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> `cursor` e' il **cursore per esecutore**: garantisce che un esecutore lento o
> riavviato non salti ne' esegua due volte lo stesso segnale. Unico pezzo genuinamente
> nuovo introdotto dal Modello B.

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
| parent_account | TEXT | account master exchange |
| api_key | TEXT | cifrato |
| api_secret | TEXT | cifrato |
| ip_whitelist | TEXT | JSON array |
| status | TEXT | `available` \| `assigned` \| `suspended` |
| created_at | DATETIME | |

> **Identita' vs routing.** L'identita' exchange (`execution_account_id`, chiavi,
> `connector`) e' intrinseca e nasce col pool. Il **nome logico di routing**
> (`logical_account_id`) e l'`adapter_name` sono relativi all'istanza e vengono
> assegnati **al momento del claim**, non alla creazione.
>
> **Claim atomico.** Il passaggio `available -> assigned` (set `instance_id` +
> `logical_account_id`) deve essere atomico per evitare che due istanze rivendichino lo
> stesso account.
>
> **Isolamento rigido gratis.** Poiche' `environment` e' intrinseco, un'istanza `DEMO`
> puo' rivendicare solo account del pool `DEMO`. L'isolamento non e' una regola imposta:
> emerge dai dati.
>
> Il **tuning comportamentale** dell'adapter (`strategy`, `websocket`, `retry`,
> `live_safety`, `trigger_by`, ...) non e' modellato in colonne: vive in
> `adapter_template` + eventuali override, per non trasformare `management.db` in un
> dump di config e non richiedere una migrazione DB per ogni tweak di strategia.

### `telegram_credentials`

Credenziali Telethon dei **listener di ingestione** (per fonte), non delle istanze.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| source_id | INTEGER FK | -> `sources` |
| phone | TEXT | cifrato |
| session_string | TEXT | cifrato |

### `trader_catalog`

Catalogo **globale**: nessun `instance_id`. Un trader si definisce una volta e si riusa.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| trader_id | TEXT UNIQUE | es. `trader_a`, `trader_3` |
| display_name | TEXT | label leggibile |
| parser_profile | TEXT | profilo parser di default |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `trader_aliases`

Alias di default del trader **globale**. Override per-fonte in `source_trader_memberships`.

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| trader_catalog_id | INTEGER FK | -> `trader_catalog` |
| alias_text | TEXT | tag o alias normalizzato |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `source_trader_memberships`

Quali trader del catalogo globale sono ammessi da una fonte. La risoluzione dentro la
fonte considera **solo** gli alias/pattern dei trader di questo insieme (scoping per
membership).

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| source_id | INTEGER FK | -> `sources` |
| trader_catalog_id | INTEGER FK | -> `trader_catalog` |
| alias_override_json | TEXT | override alias locale opzionale (vince sul globale) |
| pattern_override | TEXT | override pattern locale opzionale |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

> **Regola di risoluzione alias:**
> 1. alias di default sul trader globale;
> 2. `alias_override_json` per-fonte vince sul default;
> 3. la risoluzione considera solo i trader ammessi dalla fonte (membership);
> 4. `validate` deve **rilevare collisioni** di alias dentro l'insieme ammesso da una
>    fonte e bloccare: oggi un alias ambiguo passa silenzioso e puo' instradare il
>    segnale sul trader sbagliato.

### `source_account_policies`

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

---

## Migrazione dalla config esistente

La config attuale e' **mista** (demo e live nella stessa installazione) e va scomposta
in istanze a isolamento rigido. Mappatura reale ricostruita dal codice
(`execution.yaml`, `telegram_control.yaml`, `channels.yaml`):

| Account logico | Adapter | Trader | Mode reale |
|---|---|---|---|
| `demo_1` | `bybit_demo_1` | trader_a, trader_b, trader_c, trader_d, trader_3, trader_prova | demo |
| `demo_2` | `bybit_demo_2` | trader_devos_crypto | demo |
| `demo_3` | `bybit_demo_3` | trader_crypto_ninjias | demo |
| `live_1` | `bybit_live_1` | trader_gg_shot | live (`allow_live_trading: false`) |

**Finestra favorevole:** `live_1` non e' realmente operativo (`allow_live_trading: false`),
quindi la scomposizione avviene **prima** del go-live, senza book live aperti da spostare.
Farlo ora e' economico; farlo dopo, con posizioni live, e' rischioso.

**Percorso di migrazione:**

1. Registrare le **fonti globali** e i **trader nel catalogo globale** (una volta).
2. Definire almeno **due istanze**: `main_demo` (`DEMO`: demo_1/2/3 + relativi trader) e
   `gg_live` (`LIVE`: live_1 + trader_gg_shot).
3. Iscrivere ciascuna istanza alle fonti che consuma.
4. `tsbctl` genera per ciascuna il sottoinsieme di `channels.yaml` / `execution.yaml` /
   `telegram_control.yaml`.
5. **Nodo Telegram:** oggi tutti i `per_account` condividono lo **stesso supergroup**
   (`-1004240829081`). Con "ogni istanza ha il proprio gruppo", l'istanza `LIVE` dovrebbe
   avere gruppo/bot propri (oppure restare inizialmente `muted`). Decisione operativa da
   prendere al momento della migrazione.
6. Dati: l'istanza `DEMO` puo' ereditare l'`ops.sqlite3` esistente (storia quasi tutta
   demo); l'istanza `LIVE` parte pulita.

---

## Prerequisiti minimi per dashboard futura

Questa spec non progetta la dashboard, ma deve lasciare i contratti minimi necessari per costruirla in seguito. Rimane coerente con
`docs/Raggionamento/DASHBOARD_CENTRALE/2026-06-30-multi-instance-dashboard-monitoring-design.md`.

### Dati centrali richiesti

Il livello fleet dovra' poter leggere da `management.db` almeno:
- inventory istanze
- tipo `DEMO` o `LIVE`
- server associato (esecutore e listener)
- fonti, iscrizioni, trader associati, policy applicate e binding account exchange effettivi
- stato operativo
- revisione deployata
- ultimo heartbeat
- ultimo deploy o rollout
- ultimo errore critico

### Confine dei dati

- `management.db` non replica il dettaglio trading
- `ops.sqlite3` resta la fonte di verita' per ordini, posizioni, fill e trade chain
- `parser.sqlite3` resta la fonte di verita' per il segnale capito (per fonte)
- la dashboard globale dovra' usare `management.db` per la navigazione e il controllo fleet-level
- il drill-down di dettaglio dovra' interrogare la singola istanza o i suoi dati locali

### Estensioni future ammesse

Se in futuro servira' una vista aggregata piu' ricca, si potra' aggiungere:
- una summary cache centrale
- KPI aggregati per istanza
- snapshot sintetici non autoritativi

Queste estensioni non devono introdurre una seconda fonte di verita' del dominio trading.

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
    crypto.py
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
- `crypto.py`
  - encrypt/decrypt con `TSB_MASTER_KEY`
- `instance_provisioner.py`
  - genera struttura, YAML e `.env` dell'**esecutore** partendo da istanza, iscrizioni,
    catalogo trader, policy e override effettivi
- `ingestion_provisioner.py`
  - genera struttura e servizio del **listener di ingestione** per fonte
- `bybit_provisioner.py`
  - crea in bulk subaccount + API key via API e popola il pool; percorsi distinti demo/live
- `telegram_provisioner.py`
  - crea in automatico gruppo + topic Telegram via **sessione utente Telethon dedicata**
    e cattura i `thread_id`
- `systemd_manager.py`
  - deploy, installazione e gestione dei servizi
- `cli.py`
  - espone il workflow via `tsbctl`

### Due famiglie di servizi

- `tsb-ingest@{source}` - listener/parser per fonte (piano di ingestione)
- `telesignalbot@{instance}` - esecutore/notificatore per istanza (piano di esecuzione)

Se un listener di fonte cade, restano al buio **solo** le istanze iscritte a quella
fonte; l'esecuzione delle posizioni gia' aperte continua, perche' `ops.sqlite3` e' locale.
Blast radius equivalente al modello monolitico.

### Pool account (auto-provisioning Bybit)

La creazione degli account e' **staccata** dalla creazione istanza e avviene in bulk.

Flusso:

1. `account provision --count N --type DEMO|LIVE` chiama l'API Bybit, crea subaccount +
   API key, cifra le chiavi, salva in `exchange_accounts` con `status = available` e
   `instance_id = NULL`.
2. Alla creazione istanza, `account claim` rivendica N account **del tipo giusto** dal
   pool (claim atomico), assegnando `logical_account_id` e `adapter_name`.

Vincoli e accorgimenti:

- **Percorsi distinti demo/live.** Il flusso live tocca conti reali e va dietro gate
  esplicito (mai auto-creare account live senza conferma).
- **Limiti Bybit.** Esiste un tetto al numero di subaccount e rate limit per tier
  (da verificare); la creazione va **sequenziale con backoff**, non parallela.
- **Registrazione manuale** (`account register`) resta come percorso alternativo senza API
  per account creati a mano.

### Provisioning Telegram automatico

Vincolo di piattaforma: **un bot non puo' creare gruppi**. Il provisioning completo usa
quindi una **sessione utente Telethon dedicata** (separata da quella che ascolta le fonti,
per non mescolare i ruoli).

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
  - aggiorna il codice condiviso usato da tutte le istanze e da tutti i listener

### Modello iniziale raccomandato

Un solo clone condiviso:

```text
/opt/telesignalbot/
  repo/            <- codice condiviso
  ingestion/       <- listener e parser.sqlite3 per fonte
  instances/       <- config e ops.sqlite3 per istanza
```

In questo modello:
- il codice viene aggiornato una volta sola in `repo/`
- ogni istanza mantiene solo `config/`, `data/` e `.env`
- ogni fonte mantiene solo il proprio `parser.sqlite3` e `.env`
- i servizi puntano allo stesso codice ma con identificativo diverso
  (`BOT_INSTANCE_NAME` per gli esecutori, identificativo fonte per i listener)

### Workflow tipico di upgrade

1. verificare lo stato della repo condivisa
2. aggiornare il clone condiviso in `repo/`
3. aggiornare eventuali dipendenze richieste dalla nuova revisione
4. applicare eventuali migrazioni compatibili
5. pianificare il rollout verso le istanze interessate
6. riavviare o riapplicare in modo esplicito le istanze selezionate

### Regola fondamentale

Aggiornare il codice condiviso **non** deve riavviare automaticamente tutte le istanze.

La separazione corretta e':

- `repo upgrade`
  - aggiorna il codice condiviso disponibile sul server
- `rollout`
  - decide quali istanze passano alla nuova revisione e con quale ordine

### Comandi raccomandati per la repo condivisa

```bash
tsbctl repo status
tsbctl repo upgrade
tsbctl repo upgrade --ref main
tsbctl repo upgrade --ref <tag-or-commit>
```

#### `repo status`

Deve mostrare almeno:

- branch corrente
- commit attuale della repo condivisa
- ultimo commit disponibile da remoto
- working tree pulita o dirty
- timestamp ultimo upgrade

#### `repo upgrade`

Deve:

- fare fetch/pull o checkout della revisione richiesta
- mostrare chiaramente `from revision -> to revision`
- fallire se la working tree sul server non e' pulita
- registrare la nuova revisione target nel control plane
- non riavviare automaticamente nessuna istanza

### Strategia raccomandata di rollout

Il rollout standard non dovrebbe partire subito su tutte le istanze:

1. aggiornare il clone condiviso
2. generare un piano di rollout
3. riavviare o aggiornare una sola istanza canary, preferibilmente `DEMO`
4. verificare health check, log e comportamento base
5. solo dopo eseguire il rollout sulle altre istanze

```bash
tsbctl repo status
tsbctl repo upgrade
tsbctl rollout plan
tsbctl rollout restart alpha_demo
tsbctl instance status alpha_demo
tsbctl rollout apply --all
```

L'obiettivo non e' impedire il rollout globale, ma evitare che un aggiornamento difettoso impatti tutte le istanze in un solo passaggio.

### Comandi raccomandati per il rollout

```bash
tsbctl rollout plan
tsbctl rollout status
tsbctl rollout restart alpha_demo
tsbctl rollout apply alpha_demo
tsbctl rollout apply --group demo
tsbctl rollout apply --all
tsbctl rollout history
tsbctl rollout diff alpha_demo
tsbctl rollback alpha_demo --to <revision>
```

#### Significato operativo

- `rollout plan`
  - mostra quali istanze sono indietro rispetto alla revisione corrente della repo condivisa
- `rollout restart`
  - riavvia una istanza gia' compatibile con la config desiderata
- `rollout apply`
  - applica config aggiornata e riavvia se necessario
- `rollout status`
  - mostra lo stato del rollout corrente o dell'ultima revisione applicata
- `rollout history`
  - mostra gli eventi di rollout gia' eseguiti
- `rollout diff`
  - mostra differenza tra revisione/config attuale e target per una singola istanza
- `rollback`
  - riporta una istanza a una revisione precedente tracciata

### Output atteso di `rollout plan`

```text
INSTANCE     STATUS    CURRENT   TARGET    CONFIG_DRIFT   ACTION
alpha_demo   active    a1b2c3    d4e5f6    no             restart
alpha_live   active    a1b2c3    d4e5f6    yes            apply
beta_live    active    d4e5f6    d4e5f6    no             none
```

### Implicazioni

- un update del codice puo' impattare tutte le istanze e tutti i listener
- onboarding di una nuova istanza e rollout di una nuova versione devono restare workflow distinti
- lo stato operativo deve rendere visibile quale revisione e' effettivamente in uso
- il control plane deve distinguere tra **revisione disponibile** e **revisione effettivamente in uso** per ogni istanza (`deployed_revision` vs `target_revision`)

### Estensioni consigliate

Il control plane dovrebbe tracciare almeno:
- `deployed_revision` per istanza
- `target_revision` per istanza o per rollout
- stato dell'ultimo deploy di configurazione
- stato dell'ultimo rollout codice
- esito dell'ultimo canary
- storico rollback

Questi comandi non fanno parte del primo onboarding minimo, ma il design deve lasciargli spazio.

---

## Impatto minimo su `main.py`

Il runtime non deve diventare il luogo dove vive la logica multi-istanza. La modifica minima prevista e':

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
con `ops_db_path` e `parser_db_path` derivati da `root_dir`. La modifica va agganciata a
questo punto, mantenendo la compatibilita' con il comportamento a istanza singola.

Sotto il Modello B, `parser_db_path` (segnale capito) puo' puntare al `parser.sqlite3`
**condiviso della fonte**, mentre `ops_db_path` resta locale all'istanza. La risoluzione
di questi path e' compito del control plane, non del runtime.

---

## Cifratura e master key

### Meccanismo

La cifratura dei segreti a riposo usa `cryptography.fernet`.

### Campi da cifrare

- `servers.ssh_key`
- `instances.tg_bot_token`
- `exchange_accounts.api_key`
- `exchange_accounts.api_secret`
- `telegram_credentials.phone`
- `telegram_credentials.session_string`

### Vincoli operativi

- `TSB_MASTER_KEY` deve stare fuori dal repo
- permessi stretti sul file che la contiene
- rotazione chiave da prevedere come comando esplicito

---

## Rischi e vincoli

| Rischio | Severita' | Note |
|---|---|---|
| Rate limit Bybit per creazione subaccount | Media | da verificare prima di provisioning in bulk |
| Limiti Telegram su creazione gruppi/topic | Media | rischio limitazioni o ban se il volume e' alto |
| Interpretazione divergente dello stesso segnale | Alta | risolto dal Modello B: ingestione unica per fonte |
| Cursore per esecutore (fan-out) | Alta | senza cursore corretto un esecutore salta o duplica segnali |
| Contesa su `parser.sqlite3` condiviso | Media | WAL gia' attivo; a scala molto alta valutare un bus dedicato |
| Backup di `management.db` | Alta | e' il punto centrale di verita' |
| Rotazione master key | Media | da progettare e testare prima del live |
| Storage della chiave SSH | Alta | meglio valutare path locale vs contenuto nel DB |
| Drift tra DB centrale e server target | Alta | `validate` e `deploy` devono rilevare inconsistenze |
| Collisione alias dentro una fonte | Media | `validate` deve bloccare; oggi passa silenzioso |

---

## Piano implementativo ad alto livello

1. Introdurre `management.db` e il suo schema iniziale (istanze, sources, iscrizioni, catalogo globale).
2. Implementare cifratura e gestione della master key.
3. Implementare `tsbctl instance create` (con `--type` e `--muted`) e binding del gruppo Telegram istanza.
4. Implementare `source register`, `source subscribe`, `source attach-traders` e `trader catalog`.
5. Implementare il **pool account**: `account provision` (bulk via API Bybit), `account claim` (atomico) e `trader bind-account`.
6. Implementare `provision prepare` (esecutore + ingestione).
7. Implementare `provision bybit` e `provision telegram`.
8. Implementare `validate`, incluso il controllo collisioni alias per fonte.
9. Implementare `deploy` e gestione delle due famiglie di servizi.
10. Applicare la modifica minima a `main.py` per `BOT_INSTANCE_NAME` e path del `parser.sqlite3` condiviso.
11. Eseguire la migrazione della config mista esistente in istanze a isolamento rigido.

---

## Decisioni fissate da questa revisione

- l'istanza e' una **unita' operativa autonoma** di **esecuzione**
- architettura **Modello B**: ingestione **per-fonte**, esecuzione **per-istanza**
- una fonte e' **globale** e ascoltata da **un solo** listener, condivisibile tra istanze
- il segnale viene **capito una volta** e distribuito in **fan-out** (consistenza garantita)
- l'account/esecutore e' **indirizzabile indipendentemente dal listener** (invariante)
- split dati: **`parser.sqlite3` condiviso per fonte** / **`ops.sqlite3` locale per istanza**
- una istanza puo' essere **multi-fonte** (via iscrizione)
- una fonte puo' servire **uno o piu' trader** dal catalogo globale
- il **catalogo trader e' globale** (nessun `instance_id`)
- i trader possono usare account exchange **dedicati o condivisi**
- **isolamento rigido `DEMO`/`LIVE`**: nessuna istanza mescola account demo e live;
  `exchange_accounts` ha **una sola coppia** di chiavi, `mode` derivato dal `type`
- modello account: `management.db` modella il **wiring** (account logico -> adapter ->
  credenziali); il **tuning comportamentale** vive nei template
- account a **pool globale**: auto-provisioning Bybit in **bulk** (`account provision`),
  account in stato `available`, poi **claim** atomico all'istanza; `environment` demo/live
  intrinseco partiziona il pool e rende l'isolamento rigido una proprieta' dei dati
- provisioning Telegram **completamente automatico** via **sessione utente Telethon
  dedicata** (il bot non puo' creare gruppi): crea gruppo + topic e cattura i `thread_id`,
  con throttling anti-flood; saltato per istanze `muted`
- ogni istanza ha un **proprio gruppo Telegram** di controllo e notifica, **oppure nessuno**
  (istanza **muted**)
- vocabolario detection **allineato al codice**: `trader_binding fixed|dynamic` +
  `resolution_mode default|patterns_only` (niente `alias|pattern|hybrid`)
- alias: default sul **trader globale**, override **per-fonte**, risoluzione **scopata alla
  membership**, collisioni **bloccate da `validate`**
- il provisioning e' **semi-guidato**
- `management.db` e' la **fonte di verita'** e **control plane**, non replica il dettaglio trading
- YAML e `.env` sono **artefatti generati**
- il bot runtime resta **quasi invariato**
- onboarding istanza e upgrade repo sono **workflow distinti**

Questa revisione definisce il workflow operativo tipico e l'architettura B. L'implementazione dovra' poi dettagliare contratti, validazioni e comportamento dei singoli comandi.
