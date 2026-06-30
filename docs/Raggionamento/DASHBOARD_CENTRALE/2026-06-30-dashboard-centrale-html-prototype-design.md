# Dashboard Centrale HTML Prototype Design
**Data:** 2026-06-30
**Stato:** Draft approvato a livello concettuale

---

## Obiettivo

Definire un prototype statico, navigabile e apribile localmente che mostri la futura dashboard centrale multi-istanza in forma concreta.

Il prototype serve a validare:
- struttura delle viste
- gerarchia di navigazione
- densita' informativa
- confine tra dati centrali e dettaglio locale

Non serve ancora a validare:
- backend
- dati live
- autenticazione
- azioni operative reali

---

## Principi del prototype

- multi-page semplice
- stile operativo sobrio
- dati fake ma realistici
- navigazione reale tra pagine
- nessuna dipendenza da server o build
- file apribili direttamente da filesystem

---

## Struttura file proposta

Il prototype vivra' dentro `docs/Raggionamento/DASHBOARD_CENTRALE/` con struttura frammentata:

```text
docs/Raggionamento/DASHBOARD_CENTRALE/
  index.html
  instance.html
  trader.html
  trade.html
  assets/
    styles.css
    app.js
    data.js
```

### Ruolo dei file

- `index.html`
  - fleet view globale
- `instance.html`
  - dettaglio singola istanza
- `trader.html`
  - dettaglio trader/profile
- `trade.html`
  - dettaglio trade chain
- `assets/styles.css`
  - stile condiviso
- `assets/app.js`
  - rendering, helper, badge, query string, navigazione
- `assets/data.js`
  - dataset demo centralizzato e coerente tra pagine

---

## Gerarchia di navigazione

Il prototype deve rappresentare esplicitamente la gerarchia:

1. `Fleet`
2. `Istanza`
3. `Trader`
4. `Trade`

### Routing locale

Il routing avverra' con query string semplici:

- `index.html`
- `instance.html?id=alpha_live`
- `trader.html?id=trader_alpha`
- `trade.html?id=chain_2031`

Ogni pagina deve avere:
- breadcrumb chiaro
- link di ritorno
- contesto corrente sempre visibile

---

## Contenuto delle pagine

### `index.html`

La fleet view deve mostrare:

- overview globale
  - numero istanze
  - numero `LIVE`
  - numero `DEMO`
  - alert aperti
  - revisione corrente
- elenco istanze
  - nome
  - tipo
  - stato
  - server
  - trader associati
  - ultimo heartbeat
  - ultimo evento utile
  - posizioni aperte
  - ordini attivi
  - pnl sintetico
  - severita' alert
- sezione compatta laterale o inferiore
  - rollout recenti
  - alert recenti
  - note operative

### `instance.html`

Il dettaglio istanza deve mostrare:

- identita' istanza
  - nome
  - tipo
  - stato
  - server
  - revisione
- sottosistemi
  - telegram listener
  - parser
  - lifecycle
  - execution gateway
- summary operativo
  - heartbeat
  - ultimi eventi
  - ultimo deploy
  - ultimo rollout
- summary trading
  - open positions
  - active orders
  - last fill
  - pnl
- elenco trader associati
- alert aperti istanza
- lista trade recenti

### `trader.html`

Il dettaglio trader deve mostrare:

- identita' trader/profile
- canale sorgente
- stato mapping
- ultimi segnali ricevuti
- ultimi parse riusciti/falliti
- anomalie parser/source recenti
- trade chain recenti collegate

### `trade.html`

Il dettaglio trade deve mostrare:

- trade id o chain id
- simbolo
- lato
- stato corrente
- account, istanza e trader collegati
- timeline sintetica lifecycle
- ordini principali
- fill recenti
- pnl, qty ed esposizione sintetica
- errori o warning eventuali

---

## Dataset demo

Il dataset deve essere centralizzato in `assets/data.js`.

### Copertura minima

- 4-6 istanze
- mix `LIVE` e `DEMO`
- stati diversi
- livelli di health diversi
- trader associati per istanza
- trade chain recenti per alcuni trader
- alert recenti
- rollout recenti
- eventi come heartbeat, fill e parser anomaly

### Requisiti del dataset

- coerenza narrativa tra le pagine
- riferimenti stabili via `id`
- dati plausibili, non placeholder generici
- separazione chiara tra summary centrale e dettaglio locale simulato

---

## Interattivita'

Il prototype deve restare semplice e robusto in locale.

### Inclusa

- navigazione via link
- rendering dinamico basato su query string
- badge stato e severita'
- filtri semplici in fleet view
  - `all`
  - `live`
  - `demo`
  - `alerting`

### Esclusa

- persistenza browser
- fetch asincroni
- websocket
- grafici complessi
- azioni reali di deploy, restart o ack

---

## Direzione visuale

Lo stile deve essere operativo sobrio:

- leggibilita' prima di tutto
- gerarchia visiva chiara
- densita' informativa controllata
- look tecnico e stabile, non marketing
- uso disciplinato di colori per stato, severita' e salute

Il prototype non deve sembrare un wireframe grezzo, ma nemmeno una UI premium orientata alla presentazione.

---

## Confine architetturale rappresentato nel prototype

Il prototype deve rendere leggibile un punto importante della spec architetturale:

- la fleet view e i summary arrivano dal control plane centrale
- il dettaglio profondo di `Trader` e `Trade` rappresenta il dominio locale della singola istanza

Questo confine va suggerito attraverso i contenuti delle pagine, senza introdurre complessita' tecnica reale.

---

## Incluso nel primo rilascio

- 4 pagine HTML
- 1 CSS condiviso
- 1 JS condiviso
- 1 dataset demo condiviso
- navigazione completa `Fleet -> Istanza -> Trader -> Trade`
- stati visuali `draft`, `ready`, `deployed`, `active`, `error`
- severita' alert `info`, `warning`, `critical`

---

## Escluso dal primo rilascio

- autenticazione
- backend
- persistenza
- live updates
- responsive avanzato
- inbox alert interattiva completa
- operazioni reali sulla fleet

---

## Criteri di successo

Il prototype e' riuscito se:

1. aprendo `index.html` si capisce subito la fleet
2. il drill-down fino a `trade.html` e' naturale
3. i dati sembrano coerenti tra le pagine
4. e' chiaro cosa appartiene al control plane e cosa al dettaglio locale
5. tutto resta apribile e leggibile come insieme di file statici

---

## Decisioni fissate

- il prototype e' **multi-page semplice**
- lo stile e' **operativo sobrio**
- la navigazione e' **Fleet -> Istanza -> Trader -> Trade**
- il contenuto e' **statico ma realistico**
- la struttura e' **frammentata in HTML, CSS e JS separati**
- il primo rilascio e' **dimostrativo**, non base produttiva
