# Orchestrazione Multi-Istanza - Design Spec
**Data:** 2026-06-30
**Stato:** In revisione

---

## Obiettivo operativo

TeleSignalBot oggi gira come istanza singola su un server. L'obiettivo e' introdurre un workflow standard per aprire e gestire piu' istanze indipendenti, dove ogni istanza rappresenta una unita' operativa autonoma con config, dati, credenziali e ciclo di trading propri.

Un'istanza non e' necessariamente dedicata a una sola fonte. Una singola istanza puo' gestire:

1. una o piu' fonti Telegram;
2. uno o piu' trader/profile;
3. uno o piu' account exchange;
4. un proprio gruppo Telegram di controllo e notifica.

Il workflow deve supportare sia:

1. creazione di una nuova istanza autonoma;
2. aggiunta di nuove fonti, trader o account exchange a una istanza gia' esistente.

Il sistema deve supportare:
- N istanze del bot, ciascuna con config, dati e credenziali proprie
- scelta esplicita del tipo istanza in creazione: `DEMO` oppure `LIVE`
- associazione tra istanza, fonti Telegram, trader/profile, account exchange e destinazioni Telegram
- generazione automatica dei file di configurazione runtime
- controllo centralizzato dello stato operativo delle istanze

---

## Workflow tipico

Il workflow approvato e' semi-guidato: il sistema automatizza la preparazione e la validazione, mentre i passaggi sensibili restano confermati o completati dall'operatore.

### Scenario base

1. L'operatore decide se usare una istanza esistente oppure crearne una nuova.
2. Se serve una nuova istanza, la crea scegliendo `DEMO` o `LIVE`.
3. L'istanza nasce in stato `draft`.
4. Associa all'istanza:
   - server target
   - gruppo Telegram dell'istanza
   - bot Telegram dell'istanza
   - eventuali credenziali Telethon dell'istanza
5. Per ogni nuova fonte da gestire:
   - costruisce e valida il parser specifico del trader/fonte
   - aggiunge la fonte all'istanza
   - associa uno o piu' trader/profile alla fonte
   - associa ogni trader a un account exchange condiviso o dedicato
6. Il sistema prepara in automatico:
   - record centrali in `management.db`
   - struttura filesystem dell'istanza
   - file YAML runtime
   - `.env` dell'istanza
   - mapping di fonti, trader e destinazioni
7. I passaggi sensibili vengono completati o confermati dall'operatore:
   - provisioning Bybit
   - provisioning Telegram
8. L'operatore esegue la validazione finale dell'istanza.
9. Se la validazione passa, l'istanza va in `ready`.
10. L'operatore esegue il deploy.
11. Il deploy installa file e servizio sul server target e porta l'istanza in `deployed`.
12. L'avvio finale e' esplicito: `start` porta l'istanza in `active`.

### Quando creare una nuova istanza

La creazione di una nuova istanza e' giustificata quando serve isolamento operativo su almeno uno di questi assi:

- database e stato runtime separati
- file di configurazione separati
- credenziali exchange separate
- gruppo Telegram separato
- bot Telegram separato
- ciclo di deploy/start/stop separato

Se invece una nuova fonte deve convivere nello stesso dominio operativo, la scelta preferita e' aggiungerla a una istanza esistente.

### Modifica di una istanza esistente

Una istanza gia' `active` deve poter essere evoluta senza essere ricreata da zero. Il caso operativo tipico e':

1. esiste una istanza attiva;
2. l'operatore vuole aggiungere una nuova fonte;
3. la nuova fonte porta con se' uno o piu' trader;
4. ogni trader viene collegato a un account exchange esistente o nuovo;
5. vengono aggiunti o aggiornati i topic Telegram necessari;
6. l'istanza viene rivalidata e ridistribuita.

Il principio operativo e' che l'operatore modifica lo **stato desiderato** dell'istanza nel control plane, poi applica la differenza al runtime.

### Workflow di edit raccomandato

1. aprire l'istanza in modalita' `edit`
2. aggiungere o modificare fonti, trader, account e destinazioni Telegram
3. visualizzare un riepilogo o diff delle modifiche
4. eseguire la validazione
5. applicare il deploy della nuova configurazione
6. riavviare l'istanza solo se richiesto dal tipo di modifica

### Obiettivo del workflow

Questo flusso evita due errori opposti:
- provisioning troppo manuale e frammentato;
- automazione one-shot troppo opaca per credenziali, Telegram e go-live.

L'interfaccia operativa raccomandata e' a due livelli:

- **wizard guidati** per creazione e modifica ordinaria
- **comandi tecnici granulari** per manutenzione, repair e automazione

---

## Stati istanza

Per il primo design bastano pochi stati operativi, leggibili e verificabili:

| Stato | Significato |
|---|---|
| `draft` | istanza creata ma incompleta |
| `ready` | configurazione completa e validata, pronta per deploy |
| `deployed` | file e servizio installati sul server target |
| `active` | istanza avviata |
| `error` | provisioning, validazione o deploy falliti |

### Regole di transizione

- `instance create` crea sempre una nuova istanza in `draft`
- `validate` puo' portare da `draft` a `ready`
- `deploy` puo' portare solo da `ready` a `deployed`
- `start` puo' portare solo da `deployed` a `active`
- errori in qualunque fase portano a `error` con motivazione tracciabile

Gli stati devono riflettere il workflow operativo, non tutti i dettagli interni dei singoli task.

---

## Comandi `tsbctl`

La CLI `tsbctl` e' l'orchestratore del workflow. Il principio e' evitare un unico comando "magico" di onboarding e preferire step espliciti, ripetibili e osservabili.

### Comandi principali

```bash
# Creazione guidata istanza
tsbctl instance init

# Modifica guidata istanza esistente
tsbctl instance edit alpha_demo

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
tsbctl instance create --name alpha_demo --type DEMO --server vps1
tsbctl telegram bind-group alpha_demo --chat-id -1001234567890 --bot-token-env CONTROL_BOT_ALPHA
tsbctl source add alpha_demo --channel 12345 --label fonte_a
tsbctl trader add alpha_demo --trader trader_a --source fonte_a
tsbctl account add alpha_demo --account acc_alpha --provider BYBIT
tsbctl trader bind-account alpha_demo --trader trader_a --account acc_alpha
tsbctl telegram bind-topic alpha_demo --scope trader_a --topic 201 --role NOTIFY
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
tsbctl source add alpha_demo --channel 55555 --label fonte_b
tsbctl trader add alpha_demo --source fonte_b --trader trader_x
tsbctl trader add alpha_demo --source fonte_b --trader trader_y
tsbctl trader bind-account alpha_demo --trader trader_x --account acc_main
tsbctl trader bind-account alpha_demo --trader trader_y --account acc_y
tsbctl telegram bind-topic alpha_demo --scope trader_x --topic 220 --role NOTIFY
tsbctl telegram bind-topic alpha_demo --scope trader_y --topic 221 --role NOTIFY
tsbctl instance summary alpha_demo
tsbctl validate alpha_demo
tsbctl deploy alpha_demo
```

### Ruolo dei comandi

- `instance init`
  - avvia un wizard testuale che raccoglie i dati minimi per creare una nuova istanza coerente
- `instance edit`
  - avvia un wizard testuale per modificare una istanza esistente senza ricrearla
- `instance summary`
  - mostra lo stato desiderato completo dell'istanza in modo leggibile
- `diff`
  - mostra la differenza tra stato desiderato e stato attualmente deployato
- `instance create`
  - crea il record base dell'istanza e lo stato iniziale `draft`
- `source add`
  - aggiunge una nuova fonte Telegram a una istanza esistente
- `trader add`
  - collega uno o piu' trader/profile a una fonte gia' registrata nell'istanza
- `account add`
  - registra un account exchange utilizzabile nell'istanza
- `trader bind-account`
  - collega ogni trader a un account exchange condiviso o dedicato
- `telegram bind-group`
  - collega all'istanza il gruppo Telegram e il bot di control plane da usare per notifiche e comandi
- `telegram bind-topic`
  - collega topic Telegram a scope di istanza, fonte, trader o account
- `provision prepare`
  - genera struttura, YAML, `.env` placeholder e check preliminari
- `provision bybit`
  - crea o collega account/subaccount e credenziali exchange
- `provision telegram`
  - crea o collega bot, gruppo e topic Telegram dell'istanza
- `validate`
  - controlla coerenza e completezza; se tutto e' corretto passa a `ready`
- `deploy`
  - installa sul server target e porta a `deployed`
- `instance start`
  - esegue l'avvio esplicito e porta a `active`

---

## Fonte di verita' e artefatti generati

### Fonte di verita'

`management.db` e' il registro centrale di verita' per:
- istanze
- server target
- mapping fonte/trader/account exchange
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

### Implicazione architetturale

Il runtime del bot resta quasi invariato:
- continua a leggere file di config e DB locali della singola istanza;
- non conosce la logica di orchestrazione;
- non dipende direttamente dalla semantica di onboarding.

I dati di trading di dettaglio restano nei database `ops.sqlite3` delle singole istanze. Il control plane mantiene solo metadati, stato operativo e riferimenti sufficienti per una futura dashboard fleet-level con drill-down verso il dettaglio locale.

### Modello concettuale

Il modello dati e operativo di riferimento e' il seguente:

- **Istanza** = unita' autonoma di esecuzione del bot
- **Fonte** = input Telegram gestito da una istanza
- **Trader** = parser/profile/identita' logica risolta dentro l'istanza
- **Account exchange** = risorsa assegnabile a uno o piu' trader della stessa istanza
- **Gruppo Telegram istanza** = destinazione di controllo e notifica dell'istanza

Relazioni attese:

- una istanza puo' avere piu' fonti
- una fonte puo' avere uno o piu' trader
- un trader e' sempre definito nel contesto di una istanza
- piu' trader della stessa istanza possono condividere lo stesso account exchange
- un trader puo' anche avere un account exchange dedicato
- ogni istanza ha il proprio gruppo Telegram, con eventuali topic separati per trader, account o funzione

---

## Principi architetturali

- **Un solo repo clone** - il codice e' condiviso; le istanze differiscono per config, dati e credenziali
- **Control plane centrale** - `management.db` governa inventory, stato e provisioning
- **Configurazione generata** - i file YAML sono artefatti derivati dal DB centrale
- **Bot quasi invariato** - il runtime deve restare focalizzato sull'esecuzione
- **Cifratura a riposo** - le credenziali in `management.db` devono essere cifrate
- **Workflow semi-guidato** - automazione alta sui passaggi meccanici, controllo umano sui passaggi sensibili

---

## Struttura filesystem proposta

```text
/opt/telesignalbot/
  repo/                        <- unico clone del codice
  instances/
    {name}/
      config/
        telegram_control.yaml
        channels.yaml
        execution.yaml
        traders/
      data/
        parser.sqlite3
        ops.sqlite3
      .env
  management.db
/etc/telesignalbot/
  secrets.env                  <- TSB_MASTER_KEY, permessi stretti
```

### Note

- ogni istanza ha isolamento operativo a livello di config e dati
- il codice viene aggiornato una volta sola sul clone condiviso
- `management.db` resta separato dai DB runtime delle istanze

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
| type | TEXT | `LIVE` \| `DEMO` |
| status | TEXT | `draft` \| `ready` \| `deployed` \| `active` \| `error` |
| instance_dir | TEXT | `/opt/telesignalbot/instances/{name}/` |
| systemd_unit | TEXT | `telesignalbot@{name}.service` |
| tg_bot_token | TEXT | cifrato |
| tg_group_id | TEXT | gruppo Telegram principale dell'istanza |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### `exchange_accounts`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| provider | TEXT | es. `BYBIT` |
| uid | TEXT | UID account o subaccount exchange |
| name | TEXT | es. `BOT_LIVE_X` |
| parent_account | TEXT | account master exchange |
| api_key_demo | TEXT | cifrato |
| api_secret_demo | TEXT | cifrato |
| api_key_live | TEXT | cifrato |
| api_secret_live | TEXT | cifrato |
| ip_whitelist | TEXT | JSON array |
| created_at | DATETIME | |
| status | TEXT | `active` \| `suspended` |

### `telegram_credentials`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| phone | TEXT | cifrato |
| session_string | TEXT | cifrato |

### `source_mappings`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| channel_id | TEXT | ID canale Telegram sorgente |
| topic_id | INTEGER | topic sorgente opzionale |
| channel_name | TEXT | label descrittiva |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `instance_traders`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| source_mapping_id | INTEGER FK | -> `source_mappings` |
| trader_id | TEXT | es. `trader_a`, `trader_3` |
| parser_profile | TEXT | profilo parser effettivo |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `trader_account_bindings`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| instance_trader_id | INTEGER FK | -> `instance_traders` |
| exchange_account_id | INTEGER FK | -> `exchange_accounts` |
| binding_mode | TEXT | `DEDICATED` \| `SHARED` |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `telegram_destinations`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| chat_id | TEXT | ID gruppo Telegram |
| thread_id | INTEGER | ID topic nel supergroup |
| role | TEXT | `NOTIFY` \| `CONTROL` \| `BOTH` |
| scope_type | TEXT | `INSTANCE` \| `SOURCE` \| `TRADER` \| `ACCOUNT` |
| scope_ref_id | INTEGER | FK logica verso la tabella rilevante per lo scope |
| label | TEXT | es. `trader_a - segnali` |
| enabled | BOOLEAN | |

---

## Prerequisiti minimi per dashboard futura

Questa spec non progetta la dashboard, ma deve lasciare i contratti minimi necessari per costruirla in seguito.

### Dati centrali richiesti

Il livello fleet dovra' poter leggere da `management.db` almeno:
- inventory istanze
- tipo `DEMO` o `LIVE`
- server associato
- source mapping, trader associati e binding account exchange
- stato operativo
- revisione deployata
- ultimo heartbeat
- ultimo deploy o rollout
- ultimo errore critico

### Confine dei dati

- `management.db` non replica il dettaglio trading
- `ops.sqlite3` resta la fonte di verita' per ordini, posizioni, fill e trade chain
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
  - genera struttura, YAML e `.env` partendo da istanza, fonti, trader e binding account
- `bybit_provisioner.py`
  - crea o collega account/subaccount e API key
- `telegram_provisioner.py`
  - crea o collega gruppo e topic Telegram dell'istanza
- `systemd_manager.py`
  - deploy, installazione e gestione del servizio
- `cli.py`
  - espone il workflow via `tsbctl`

---

## Aggiornamento repo e rollout versioni

Il workflow di onboarding di una nuova istanza non coincide con il workflow di aggiornamento del codice condiviso.

### Distinzione operativa

- `deploy istanza`
  - prepara o aggiorna config, `.env` e binding di servizio di una singola istanza
- `upgrade repo`
  - aggiorna il codice condiviso usato da tutte le istanze

### Modello iniziale raccomandato

Per la prima versione il modello piu' semplice e' un solo clone condiviso:

```text
/opt/telesignalbot/
  repo/            <- codice condiviso
  instances/       <- config e dati separati per istanza
```

In questo modello:
- il codice viene aggiornato una volta sola in `repo/`
- ogni istanza mantiene solo `config/`, `data/` e `.env`
- i servizi delle istanze puntano allo stesso codice ma con `BOT_INSTANCE_NAME` diverso

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

Il rollout standard non dovrebbe partire subito su tutte le istanze. La strategia raccomandata e':

1. aggiornare il clone condiviso
2. generare un piano di rollout
3. riavviare o aggiornare una sola istanza canary, preferibilmente `DEMO`
4. verificare health check, log e comportamento base
5. solo dopo eseguire il rollout sulle altre istanze

Questo produce un flusso operativo del tipo:

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

Per ogni istanza il piano dovrebbe mostrare almeno:

- nome istanza
- stato operativo
- revisione corrente
- revisione target
- presenza di config drift
- azione consigliata: `none`, `restart`, `apply`, `blocked`
- eventuali warning o blocchi

Esempio:

```text
INSTANCE     STATUS    CURRENT   TARGET    CONFIG_DRIFT   ACTION
alpha_demo   active    a1b2c3    d4e5f6    no             restart
alpha_live   active    a1b2c3    d4e5f6    yes            apply
beta_live    active    d4e5f6    d4e5f6    no             none
```

### Implicazioni

- un update del codice puo' impattare tutte le istanze
- onboarding di una nuova istanza e rollout di una nuova versione devono restare workflow distinti
- lo stato operativo deve rendere visibile quale revisione e' effettivamente in uso
- il control plane deve distinguere tra **revisione disponibile** e **revisione effettivamente in uso** per ogni istanza

### Estensioni consigliate

Il control plane dovrebbe tracciare almeno:
- `deployed_revision` per istanza
- `target_revision` per istanza o per rollout
- stato dell'ultimo deploy di configurazione
- stato dell'ultimo rollout codice
- esito dell'ultimo canary
- storico rollback

Comandi attesi in evoluzione:

```bash
tsbctl repo status
tsbctl repo upgrade
tsbctl rollout plan
tsbctl rollout restart alpha_demo
tsbctl rollout apply --group demo
tsbctl rollout apply --all
tsbctl rollout history
tsbctl rollback alpha_demo --to <revision>
tsbctl instance status alpha_demo
```

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

Questo mantiene la compatibilita' con il comportamento attuale e sposta l'intelligenza nel control plane.

---

## Cifratura e master key

### Meccanismo

La cifratura dei segreti a riposo usa `cryptography.fernet`.

### Campi da cifrare

- `servers.ssh_key`
- `instances.tg_bot_token`
- `bybit_subaccounts.api_key_demo`
- `bybit_subaccounts.api_secret_demo`
- `bybit_subaccounts.api_key_live`
- `bybit_subaccounts.api_secret_live`
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
| Backup di `management.db` | Alta | e' il punto centrale di verita' |
| Rotazione master key | Media | da progettare e testare prima del live |
| Storage della chiave SSH | Alta | meglio valutare path locale vs contenuto nel DB |
| Drift tra DB centrale e server target | Alta | `validate` e `deploy` devono rilevare inconsistenze |

---

## Piano implementativo ad alto livello

1. Introdurre `management.db` e il suo schema iniziale.
2. Implementare cifratura e gestione della master key.
3. Implementare `tsbctl instance create` e binding del gruppo Telegram istanza.
4. Implementare `source add`, `trader add` e `account bind`.
5. Implementare `provision prepare`.
6. Implementare `provision bybit` e `provision telegram`.
7. Implementare `validate`.
8. Implementare `deploy` e gestione del servizio.
9. Applicare la modifica minima a `main.py` per `BOT_INSTANCE_NAME`.

---

## Decisioni fissate da questa revisione

- l'istanza e' una **unita' operativa autonoma**
- una istanza puo' essere **multi-fonte**
- una fonte puo' servire **uno o piu' trader**
- i trader possono usare account exchange **dedicati o condivisi**
- ogni istanza ha un **proprio gruppo Telegram di controllo e notifica**
- il provisioning e' **semi-guidato**
- `DEMO` e `LIVE` sono **scelte esplicite in creazione**
- `management.db` e' la **fonte di verita'**
- `management.db` e' **control plane**, non replica il dettaglio trading
- YAML e `.env` sono **artefatti generati**
- il bot runtime resta **quasi invariato**
- onboarding istanza e upgrade repo sono **workflow distinti**

Questa revisione definisce il workflow operativo tipico. L'implementazione dovra' poi dettagliare contratti, validazioni e comportamento dei singoli comandi.
