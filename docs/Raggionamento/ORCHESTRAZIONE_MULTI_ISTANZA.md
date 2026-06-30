# Orchestrazione Multi-Istanza - Design Spec
**Data:** 2026-06-30
**Stato:** In revisione

---

## Obiettivo operativo

TeleSignalBot oggi gira come istanza singola su un server. L'obiettivo e' introdurre un workflow standard per aprire e gestire piu' istanze indipendenti partendo da un caso operativo preciso:

1. viene trovata una nuova fonte Telegram;
2. viene costruito e validato il parser specifico;
3. viene creata una nuova istanza dedicata a quella fonte;
4. l'istanza viene preparata, validata, deployata e avviata con un processo semi-guidato.

Il sistema deve supportare:
- N istanze del bot, ciascuna con config, dati e credenziali proprie
- scelta esplicita del tipo istanza in creazione: `DEMO` oppure `LIVE`
- associazione tra istanza, fonte Telegram, trader/profile, account exchange e destinazioni Telegram
- generazione automatica dei file di configurazione runtime
- controllo centralizzato dello stato operativo delle istanze

---

## Workflow tipico

Il workflow approvato e' semi-guidato: il sistema automatizza la preparazione e la validazione, mentre i passaggi sensibili restano confermati o completati dall'operatore.

### Scenario base

1. L'operatore trova una nuova fonte Telegram.
2. Costruisce il parser specifico del trader/fonte.
3. Valida il parser con replay e smoke test.
4. Crea una nuova istanza scegliendo `DEMO` o `LIVE`.
5. L'istanza nasce in stato `draft`.
6. Associa all'istanza:
   - trader/profile
   - canale sorgente
   - server target
7. Il sistema prepara in automatico:
   - record centrali in `management.db`
   - struttura filesystem dell'istanza
   - file YAML runtime
   - `.env` dell'istanza
   - mapping iniziali delle sorgenti
8. I passaggi sensibili vengono completati o confermati dall'operatore:
   - provisioning Bybit
   - provisioning Telegram
   - eventuali credenziali Telethon
9. L'operatore esegue la validazione finale dell'istanza.
10. Se la validazione passa, l'istanza va in `ready`.
11. L'operatore esegue il deploy.
12. Il deploy installa file e servizio sul server target e porta l'istanza in `deployed`.
13. L'avvio finale e' esplicito: `start` porta l'istanza in `active`.

### Obiettivo del workflow

Questo flusso evita due errori opposti:
- provisioning troppo manuale e frammentato;
- automazione one-shot troppo opaca per credenziali, Telegram e go-live.

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
# Creazione istanza
tsbctl instance create --name alpha_demo --type DEMO --server vps1

# Associazione fonte / trader
tsbctl source add alpha_demo --trader trader_a --channel 12345

# Preparazione struttura e config
tsbctl provision prepare alpha_demo

# Provisioning sensibile
tsbctl provision bybit alpha_demo
tsbctl provision telegram alpha_demo

# Validazione finale
tsbctl validate alpha_demo

# Deploy e avvio
tsbctl deploy alpha_demo
tsbctl instance start alpha_demo
tsbctl instance stop alpha_demo
tsbctl instance status alpha_demo
```

### Ruolo dei comandi

- `instance create`
  - crea il record dell'istanza e lo stato iniziale `draft`
- `source add`
  - collega la nuova istanza alla fonte/trader da servire
- `provision prepare`
  - genera struttura, YAML, `.env` placeholder e check preliminari
- `provision bybit`
  - crea o collega subaccount e credenziali exchange
- `provision telegram`
  - crea o collega bot, gruppo e topic Telegram
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
- mapping fonte/trader
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

### `bybit_subaccounts`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| uid | TEXT | UID subaccount Bybit |
| name | TEXT | es. `BOT_LIVE_X` |
| parent_account | TEXT | account master Bybit |
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
| trader_id | TEXT | es. `trader_a`, `trader_3` |
| channel_id | TEXT | ID canale Telegram sorgente |
| channel_name | TEXT | label descrittiva |
| enabled | BOOLEAN | |
| added_at | DATETIME | |

### `telegram_destinations`

| Campo | Tipo | Note |
|---|---|---|
| id | INTEGER PK | |
| instance_id | INTEGER FK | -> `instances` |
| source_mapping_id | INTEGER FK | -> `source_mappings` |
| chat_id | TEXT | ID gruppo Telegram |
| thread_id | INTEGER | ID topic nel supergroup |
| role | TEXT | `NOTIFY` \| `CONTROL` \| `BOTH` |
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
- source mapping e trader associati
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
  - genera struttura, YAML e `.env`
- `bybit_provisioner.py`
  - crea o collega subaccount e API key
- `telegram_provisioner.py`
  - crea o collega gruppo e topic Telegram
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

1. aggiornare il clone condiviso in `repo/`
2. aggiornare eventuali dipendenze richieste dalla nuova revisione
3. applicare eventuali migrazioni compatibili
4. riavviare in modo esplicito le istanze interessate

### Strategia raccomandata di rollout

Il rollout standard non dovrebbe partire subito su tutte le istanze. La strategia raccomandata e':

1. aggiornare il clone condiviso
2. riavviare una sola istanza canary, preferibilmente `DEMO`
3. verificare health check, log e comportamento base
4. solo dopo eseguire il rollout sulle altre istanze

Questo produce un flusso operativo del tipo:

```bash
tsbctl repo upgrade
tsbctl rollout restart alpha_demo
tsbctl instance status alpha_demo
tsbctl rollout restart --all
```

L'obiettivo non e' impedire il rollout globale, ma evitare che un aggiornamento difettoso impatti tutte le istanze in un solo passaggio.

### Implicazioni

- un update del codice puo' impattare tutte le istanze
- onboarding di una nuova istanza e rollout di una nuova versione devono restare workflow distinti
- lo stato operativo deve rendere visibile quale revisione e' effettivamente in uso

### Estensioni consigliate

Il control plane dovrebbe tracciare almeno:
- `deployed_revision` per istanza
- stato dell'ultimo deploy di configurazione
- stato dell'ultimo rollout codice

Comandi attesi in evoluzione:

```bash
tsbctl repo upgrade
tsbctl rollout restart alpha_demo
tsbctl rollout restart --all
tsbctl rollout apply --all
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
3. Implementare `tsbctl instance create` e `source add`.
4. Implementare `provision prepare`.
5. Implementare `provision bybit` e `provision telegram`.
6. Implementare `validate`.
7. Implementare `deploy` e gestione del servizio.
8. Applicare la modifica minima a `main.py` per `BOT_INSTANCE_NAME`.

---

## Decisioni fissate da questa revisione

- il workflow e' **centrato sull'istanza**
- il provisioning e' **semi-guidato**
- `DEMO` e `LIVE` sono **scelte esplicite in creazione**
- `management.db` e' la **fonte di verita'**
- `management.db` e' **control plane**, non replica il dettaglio trading
- YAML e `.env` sono **artefatti generati**
- il bot runtime resta **quasi invariato**
- onboarding istanza e upgrade repo sono **workflow distinti**

Questa revisione definisce il workflow operativo tipico. L'implementazione dovra' poi dettagliare contratti, validazioni e comportamento dei singoli comandi.
