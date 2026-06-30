# Multi-Instance Dashboard And Monitoring Design
**Data:** 2026-06-30
**Stato:** Draft approvato a livello concettuale

---

## Obiettivo

Definire l'architettura osservabile della futura control plane dashboard per TeleSignalBot multi-istanza.

La spec copre:
- dashboard fleet-level
- monitoring operativo
- alerting di base
- navigazione dal livello globale fino al dettaglio locale

La spec non progetta ancora il layout visuale della UI. Fissa i contratti minimi, le relazioni e il modello dei dati necessari per rendere possibile una dashboard futura senza dover riprogettare il control plane.

---

## Perimetro

### Incluso

- vista globale delle istanze
- drill-down `Fleet -> Istanza -> Trader -> Trade`
- health operativo centrale
- mini summary trading visibili nella fleet view
- alerting su Telegram e inbox dashboard
- modello ibrido tra dati centrali e dettaglio locale

### Escluso

- design visuale dettagliato della dashboard
- query SQL definitive
- API finali
- RBAC completo
- incident management avanzato
- replica completa del dominio trading nel DB centrale

---

## Principi architetturali

- la navigazione principale e' **centrata sull'istanza**
- `management.db` resta **control plane**, non database di trading
- il dettaglio trading autorevole resta negli `ops.sqlite3` delle singole istanze
- la dashboard globale deve essere utile anche senza aprire il dettaglio
- i dati centrali possono includere summary sintetici, ma non una seconda fonte di verita'
- l'alerting deve esistere sia come push operativo sia come stato consultabile

---

## Gerarchia di navigazione

La gerarchia principale della dashboard e':

1. `Fleet`
2. `Istanza`
3. `Trader`
4. `Trade`

### Significato operativo

- `Fleet`
  - vista globale di inventory, stato, salute e attivita' recente
- `Istanza`
  - vista operativa di una singola istanza
- `Trader`
  - vista del flusso associato a un trader/profile o fonte
- `Trade`
  - vista del dettaglio reale del dominio trading gia' presente nell'istanza

Questa scelta mantiene coerenza con la spec di orchestrazione multi-istanza, che e' anch'essa istanza-centrica.

---

## Modello dati osservabile

### Modello ibrido

La dashboard futura deve usare un modello ibrido:

- dati centrali sempre disponibili da `management.db`
- dati di dettaglio interrogati on-demand dalla singola istanza
- summary centrali ammessi solo se sintetici e non autoritativi

### Dati centrali in `management.db`

`management.db` deve contenere almeno:

- inventory istanze
- tipo istanza `DEMO` o `LIVE`
- server associato
- source mapping e trader associati
- stato operativo istanza
- revisione deployata
- stato ultimo deploy
- stato ultimo rollout
- ultimo heartbeat
- ultimo errore critico
- alert aperti o recenti
- summary minimi per fleet view

### Dati locali per istanza

Ogni `ops.sqlite3` resta la fonte di verita' per:

- trade chain
- ordini
- posizioni
- fill
- lifecycle events
- history di dettaglio del trading

### Regola di confine

`management.db` non deve diventare un secondo database di trading. Serve come:

- indice
- control plane
- punto di coordinamento
- vista fleet-level
- base per alerting e health centrale

---

## Fleet View

La fleet view e' il pannello operativo principale. Deve permettere di capire rapidamente:

- quali istanze esistono
- quali sono sane
- quali hanno problemi
- quali stanno lavorando
- dove serve drill-down immediato

### Dati minimi per istanza

Per ogni istanza la vista globale dovrebbe mostrare almeno:

- nome istanza
- tipo `DEMO` o `LIVE`
- stato operativo `draft`, `ready`, `deployed`, `active`, `error`
- server associato
- trader o fonti associate in forma sintetica
- revisione deployata
- ultimo heartbeat
- ultimo evento utile
  - ultimo messaggio Telegram
  - ultimo evento exchange
  - ultimo fill
- mini summary trading
  - posizioni aperte
  - ordini attivi
  - PnL sintetico, se disponibile
- severita' alert corrente
- esito ultimo deploy o rollout

### Intento della vista

La fleet view non deve sostituire il drill-down di dettaglio. Deve solo dare abbastanza contesto per decidere dove entrare.

---

## Drill-Down

### Livello Istanza

Aprendo una singola istanza, la dashboard dovrebbe mostrare:

- stato operativo completo
- server e revisione deployata
- configurazione logica associata
  - trader
  - fonti
  - account
  - destinazioni Telegram
- health dei sottosistemi
  - listener Telegram
  - parser pipeline
  - lifecycle
  - execution gateway
- attivita' recente
  - ultimi messaggi
  - ultimi eventi exchange
  - ultimi fill
- mini summary trading
  - posizioni aperte
  - ordini attivi
  - PnL sintetico
- alert aperti relativi all'istanza

### Livello Trader

Per ogni trader/profile associato all'istanza:

- trader/profile
- canale o fonte Telegram
- stato mapping
- ultimi segnali ricevuti
- ultimi segnali parse-ati
- errori recenti del flusso parser/source
- trade chain recenti generate dal trader

### Livello Trade

Il livello `Trade` deve riusare il dominio esistente nell'istanza, senza ricrearlo centralmente:

- trade chain
- ordini
- fill
- lifecycle events
- stato corrente del trade
- errori o eccezioni operative

---

## Health Model

### Health minima centrale

Il control plane deve poter rappresentare almeno:

- processo istanza attivo o non attivo
- ultimo heartbeat valido
- ultimo errore critico
- stato dei sottosistemi principali
  - listener Telegram
  - parser pipeline
  - lifecycle
  - execution gateway
- timestamp ultima attivita' utile

### Summary centrali ammessi

Per la fleet view sono ammessi summary sintetici centralizzati, purche':

- non siano la fonte di verita'
- siano chiaramente snapshot
- abbiano timestamp associato

Summary ammessi:

- posizioni aperte
- ordini attivi
- ultimo fill
- ultimo evento exchange
- PnL sintetico

Questi summary servono a rendere utile la fleet view, non a sostituire il dettaglio locale.

---

## Alerting

### Canali

Gli alert devono essere visibili su due canali:

- `Telegram`
  - notifiche push operative
- `Dashboard inbox`
  - elenco e stato consultabile nella futura UI

### Tipi minimi di alert

- istanza down
- heartbeat scaduto
- errore ripetuto di un sottosistema
- parser/source flow degradato
- exchange sync degradato
- deploy fallito
- rollout fallito
- mismatch tra revisione attesa e revisione attiva
- summary trading non aggiornato oltre soglia

### Confine della spec

Questa spec non introduce ancora:

- ack formale
- silenziamento
- escalation multi-step
- gestione incident completa

Serve solo fissare che l'alert e' un oggetto centrale osservabile e che compare sia in push sia in inbox.

---

## Refresh Model

Il refresh non deve essere uniforme su tutti i livelli.

### Fleet View

- refresh periodico tramite snapshot centrali
- costo basso e visione coerente dell'intero sistema

### Dettaglio Istanza

- refresh piu' frequente oppure on-demand
- accesso a dati piu' ricchi della singola istanza

### Dettaglio Trade

- lettura del dato locale quando l'utente entra nel drill-down
- nessuna pretesa di centralizzare l'intera history nel control plane

---

## Dipendenze Dalla Spec Multi-Istanza

Questa spec assume come gia' fissato:

- onboarding multi-istanza semi-guidato
- `management.db` come fonte di verita' control plane
- distinzione tra onboarding istanza e upgrade repo
- modello `repo shared`
- rollout raccomandato `canary -> all`

La dashboard futura deve leggere e rappresentare questi concetti, non ridefinirli.

---

## Rischi

| Rischio | Severita' | Note |
|---|---|---|
| Troppe informazioni centralizzate | Alta | rischio di duplicare il dominio trading e introdurre drift |
| Fleet view troppo povera | Media | costringe a drill-down continuo e perde utilita' operativa |
| Fleet view troppo ricca | Media | diventa costosa, rumorosa e difficile da mantenere |
| Summary centrali non timestampati | Alta | impossibile capire se il dato e' fresco o stantio |
| Alert solo su Telegram | Media | manca visibilita' storica e consultabile |
| Alert solo in dashboard | Media | si perde reattivita' operativa |

---

## Decisioni Aperte

Da rinviare a spec successive o al piano implementativo:

- schema preciso delle tabelle health e alert
- modalita' tecnica di raccolta snapshot per istanza
- API o query layer per drill-down
- policy di retention degli alert
- dettaglio UX della dashboard
- eventuali filtri globali, grouping e ordinamenti di default

---

## Decisioni Fissate

- la spec e' **architetturale**, non di UI dettagliata
- la dashboard e il monitoring stanno nella **stessa spec**
- il modello dati e' **ibrido**
- la navigazione principale e' **Fleet -> Istanza -> Trader -> Trade**
- la fleet view include **health, attivita' e mini trading summary**
- `management.db` resta **control plane**, non replica il dettaglio trading
- il dettaglio trading autorevole resta negli `ops.sqlite3` locali
- gli alert esistono sia su **Telegram** sia in **dashboard inbox**

