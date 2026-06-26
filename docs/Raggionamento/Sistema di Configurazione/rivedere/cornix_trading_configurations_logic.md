# Cornix Signals Bot — logica completa delle Trading Configurations

**Scopo:** ricostruzione operativa della logica di configurazione dei *Signals Bots* Cornix, con distinzione tra ciò che la documentazione afferma esplicitamente, le dipendenze tra impostazioni e le ambiguità non risolte dalle fonti ufficiali.

**Lingua delle fonti:** inglese.  
**Verifica effettuata:** 25 giugno 2026.  
**Ambito:** raccolta ufficiale *Trading Configurations* e articoli ufficiali collegati necessari per completare la logica delle cinque aree: General, Entries, Take-Profits, Stop, Advanced.

> **Nota metodologica**
>
> Questo documento è una sintesi tecnica, non una copia della documentazione. Quando Cornix non specifica un dettaglio operativo, il testo lo indica esplicitamente invece di dedurlo.

---

## 1. Modello mentale: da segnale a trade eseguito

Cornix separa almeno quattro livelli:

```text
Segnale pubblicato
        │
        ├─ configurazione del canale / gruppo
        │
        ├─ configurazione personale del Signals Bot
        │
        └─ filtri e limiti avanzati del bot
                │
                ▼
       Trade Cornix (ordini gestiti)
                │
                ▼
       Ordini reali sull’exchange
```

Un **signal** è una specifica teorica pubblicata su un canale.  
Un **trade** è l’insieme reale di ordini che Cornix invia e gestisce sul conto exchange: entry, take-profit, stop e ordini condizionali.

Il risultato del trade può differire dal segnale per liquidità del book, slippage, variazioni di prezzo tra exchange, esecuzioni parziali e configurazione personale del bot.

---

# 2. Regola di precedenza delle configurazioni

## 2.1 I tre stati per ogni parametro

Per molti parametri Cornix offre tre modalità:

| Modalità | Effetto |
|---|---|
| `Channel` | Il valore è determinato dinamicamente da segnale e configurazione del canale. |
| `Personal` | Il valore personale del Signals Bot sostituisce quello del canale/segnale. |
| `Only use if not defined by group` | Il valore personale è un fallback: viene applicato solo se il parametro non è definito nella configurazione del gruppo o nel segnale. |

## 2.2 Precedenza operativa

La documentazione consente di ricostruire questa logica:

```text
Per ogni parametro p:

se bot[p] = Personal senza fallback:
    valore finale = bot[p]

se bot[p] = Personal con “Only use if not defined by group”:
    se il parametro esiste nel segnale o nella configurazione gruppo:
        valore finale = valore del gruppo/segnale
    altrimenti:
        valore finale = bot[p]

se bot[p] = Channel:
    valore finale = valore del gruppo/segnale
```

## 2.3 Ambiguità importante: gruppo contro segnale

Le fonti parlano alternativamente di “group configuration” e di “posted signal”, ma **non definiscono con precisione una tabella universale di priorità quando entrambi valorizzano lo stesso parametro in modo diverso**.

Quindi non è corretto affermare, sulla sola base della documentazione consultata, una regola assoluta del tipo:

```text
signal > group
```

oppure:

```text
group > signal
```

La cosa verificabile è solo questa:

- `Personal` prevale;
- `Channel` segue la configurazione pubblicata dal canale/segnale;
- `Only use if not defined by group` lascia il controllo al canale/segnale quando quel campo è valorizzato.

---

# 3. Risoluzione del trade e mutabilità nel tempo

## 3.1 Trade automatici

Quando un trade è aperto automaticamente da un Signals Bot:

- modifiche di prezzo;
- modifiche agli ordini;
- cancellazione del segnale;
- altri aggiornamenti del segnale;

vengono applicati automaticamente al trade Cornix.

## 3.2 Trade semi-automatici e modificati manualmente

Un trade creato con `One Click Follow` è considerato semi-automatico:

- non riceve automaticamente aggiornamenti o cancellazioni del segnale;
- deve essere aggiornato o chiuso manualmente.

Un trade automatico che viene modificato manualmente **diventa semi-automatico**. Da quel momento non riceve ulteriori aggiornamenti automatici del segnale.

## 3.3 Conseguenza progettuale

La configurazione non è soltanto un template statico. Deve essere trattata come:

```text
bot defaults
    → trade resolved configuration
        → order state on exchange
```

Dopo un edit manuale, la relazione `signal → trade` cambia: il trade non deve più essere trattato come mirror automatico del segnale.

---

# 4. General

## 4.1 Amount Per Trade

Cornix documenta quattro modalità.

| Modalità | Logica |
|---|---|
| `Percentage` | Percentuale fissa del portafoglio totale della valuta di quote, includendo fondi disponibili e già impegnati in trade aperti. |
| `Risk Percentage` | Dimensiona la posizione in modo che la perdita teorica allo stop corrisponda alla percentuale di rischio scelta. |
| `Fixed BTC Amount` | Usa un valore equivalente all’importo BTC configurato, convertito nella valuta necessaria al pair. |
| `Fixed USD Amount` | Usa un valore equivalente all’importo USD configurato, convertito nella valuta necessaria al pair. |

### Percentage

Se il portafoglio USDT totale è 1.000 USDT, di cui 500 disponibili e 500 già usati, un `Amount per Trade = 10%` produce un trade da 100 USDT.

**Caveat:** usare il 100% può fallire, soprattutto nei futures, perché le fee rendono insufficiente il saldo disponibile.

### Risk Percentage

La formula concettuale documentata è:

```text
dimensione posizione =
(rischio % × valore portafoglio rilevante)
/
perdita potenziale del trade %
```

Dove:

```text
perdita potenziale % =
|entry calcolata − stop|
/
entry calcolata
```

Con entry multiple, l’entry usata è la **media ponderata**, calcolata dopo l’applicazione della strategia entry del bot.

#### Regole e limiti

- senza Stop Loss, Cornix ricade su una percentuale piatta del portafoglio;
- Cross leverage senza stop non è supportato per `Risk Percentage`;
- il calcolo considera il **trigger dello stop**, non il prezzo di esecuzione effettivo: il rischio reale può quindi differire;
- se la size calcolata supera il saldo disponibile, Cornix apre fino all’importo disponibile;
- entry troppo piccole rispetto al minimo exchange possono essere saltate;
- la size effettiva può quindi essere inferiore alla size teorica.

### Relazione Amount × Leverage

Per gli exchange margin, la documentazione indica in generale:

```text
position size ≈ amount per trade × leverage
```

salvo casi specifici di trade indicati come leveraged margin.

---

## 4.2 Symbols

Il bot può filtrare i pair negoziabili:

- default: tutti i simboli consentiti;
- blacklist: si deselezionano specifici simboli;
- whitelist: si deselezionano tutti e si selezionano solo i pair ammessi.

Un segnale su un simbolo escluso non deve aprire trade.

---

## 4.3 Direction

Per futures:

| Impostazione | Effetto |
|---|---|
| Long abilitato | esegue solo segnali long |
| Short abilitato | esegue solo segnali short |
| entrambi | esegue entrambe le direzioni |
| disabilitato | il segnale relativo viene ignorato |

---

## 4.4 Leverage

### Parametri

| Parametro | Valori |
|---|---|
| Margin Type | `Isolated` / `Cross` |
| Multiplier | `Exactly X` / `Up to X` |
| Scope | globale oppure per singolo simbolo |

### Exactly X

Il bot usa sempre il moltiplicatore impostato personalmente.

### Up to X

Il bot usa la leva indicata dal canale se non supera il massimo personale:

```text
leva effettiva = min(leva canale, leva massima bot)
```

### Configurazione globale e override per simbolo

- il valore globale vale per tutti i simboli presenti e futuri;
- un simbolo personalizzato non eredita più le modifiche globali;
- `Select All` trasforma tutti i simboli esistenti in custom: i futuri simboli aggiunti dall’exchange non erediteranno quei valori;
- se la leva scelta non è disponibile sul simbolo, Cornix usa il massimo supportato dall’exchange.

---

# 5. Entries

## 5.1 Parametri configurabili in Personal

La documentazione elenca:

- ratios;
- prezzi;
- numero massimo di ordini;
- numero di ordini attivi;
- trailing entry;
- First Entry as Market.

> Il concetto di **numero di ordini attivi** viene citato, ma la documentazione consultata non descrive l’algoritmo di attivazione/scorrimento. Non è possibile confermarne il comportamento preciso.

---

## 5.2 Entry ratios

### Strategie built-in

| Strategia | Ripartizione |
|---|---|
| Evenly Divided | distribuzione uniforme fra gli entry target |
| One Target | 100% sul primo target |
| Two Targets | 50% / 50% sui primi due |
| Three Targets | 33,33% sui primi tre |
| Fifty On First Target | 50% sul primo, restante 50% ripartito tra gli altri |
| Decreasing Exponential | ogni target successivo riceve metà del precedente |
| Increasing Exponential | ogni target successivo riceve il doppio del precedente |
| Skip First | 0% sul primo target; il resto distribuito uniformemente |
| Custom | pesi definiti dall’utente |
| DCA | pesi costruiti da un `Amount Scale` e, opzionalmente, da `First Entry Amount` |

### Esempio esponenziale con 4 entry

| Strategia | E1 | E2 | E3 | E4 |
|---|---:|---:|---:|---:|
| Decreasing Exponential | 53,333% | 26,666% | 13,333% | 6,666% |
| Increasing Exponential | 6,666% | 13,333% | 26,666% | 53,333% |

### DCA — Amount Scale

Con:

```text
totale = 70 USD
numero ordini = 3
amount scale = 2
```

la distribuzione documentata è:

```text
10 USD / 20 USD / 40 USD
```

`First Entry Amount`, se attivo, permette di fissare esplicitamente la quota del primo ordine.

---

## 5.3 Entry prices in Personal

Questa logica genera una griglia di entry quando si definiscono i prezzi dal bot.

### Price Difference

È la distanza percentuale fra primo e secondo ordine.

Esempio long:

```text
E1 = 100
price difference = 1%

E2 = 99
E3 = 98,01
```

### Price Scale

Moltiplica la distanza dal secondo ordine in avanti.

Esempio:

```text
E1 = 100
price difference = 1%
price scale = 2

E2 = 99
E3 = 97,02
```

### Max Orders Price Difference

Limita la distanza massima fra il primo e l’ultimo ordine della griglia.

### Max Number of Orders

Cornix documenta fino a **10 entry orders** per trade.

---

## 5.4 Entry Zone

Quando un segnale contiene una zona e non target puntuali, Cornix costruisce i prezzi in funzione del numero di entry configurato.

Esempio documentato:

```text
zona = 1000–2000
numero entry = 5

prezzi = 1000, 1250, 1500, 1750, 2000
```

Con una sola entry, la documentazione indica che l’ordine viene posto al lato alto della zona (`2000` nell’esempio).

Il capitale viene poi ripartito con la strategia entry selezionata.

Il valore di default indicato per il numero di entry è 4.

---

## 5.5 First Entry as Market

### Punto essenziale

Nonostante il nome, la documentazione non descrive un singolo ordine market inviato subito. Descrive invece un meccanismo che:

1. parte dall’entry iniziale;
2. riprezza iterativamente l’ordine;
3. aumenta gradualmente il prezzo entro un massimo configurato;
4. smette di riprezzare al raggiungimento del cap;
5. lascia l’ordine aperto come normale limit order se non è ancora fillato.

### Parametri

| Parametro | Logica |
|---|---|
| Maximum Price Cap | range esteso del primo entry; default 1%, intervallo documentato 0,05%–20% |
| Activate when Entry Price Reached | il meccanismo parte solo dopo che l’entry iniziale è stata raggiunta o attraversata |
| Activate Immediately | il meccanismo parte subito all’apertura del trade |

### Logica per un long

```text
entry originale = P0
cap = g%

limite massimo = P0 × (1 + g)
```

Finché non è fillato:

```text
ordine primo entry := prezzo leggermente maggiore
```

fino al limite massimo.

**Effetto:** riduce la probabilità di mancare l’entry per priorità nel book, bassa liquidità o rapidi movimenti post-pubblicazione.

**Rischio:** può peggiorare il prezzo medio di entrata rispetto al segnale originale.

---

## 5.6 Trailing Entry

### Logica

Quando il prezzo raggiunge l’entry:

```text
invece di eseguire un limit normale:
    si attiva un trailing buy
```

Per un long, il trailing segue il minimo raggiunto mantenendo una distanza percentuale sopra quel minimo. Il buy avviene quando il prezzo risale fino al trailing price.

```text
minimo = min(prezzi successivi)
trailing buy = minimo × (1 + trailing %)
```

### Merging

Se un secondo entry target viene raggiunto mentre è già attivo un trailing entry:

```text
non viene creato un secondo trailing order;
la quantità del nuovo target viene sommata al trailing già attivo;
il target inglobato appare come cancellato con stato “Merged”.
```

---

# 6. Take-Profits

## 6.1 Ratios

Le strategie TP includono:

- Evenly Divided;
- One / Two / Three Targets;
- Fifty On First Target;
- Decreasing Exponential;
- Increasing Exponential;
- Skip First;
- Custom;
- DCA tramite Amount Scale.

La logica è equivalente alla ripartizione entry, applicata però alla quantità da chiudere ai target.

---

## 6.2 First Take-Profit Distance

Definisce la distanza percentuale del primo TP rispetto al **primo entry effettivo del trade**.

Quindi la base non è necessariamente la media ponderata, ma il primo entry.

---

## 6.3 Prezzi dei TP

### Price Difference

Esempio long:

```text
TP1 = 100
price difference = 1%

TP2 = 101
TP3 = 102,01
```

### Price Scale

Esempio:

```text
TP1 = 100
price difference = 1%
price scale = 2

TP2 = 101
TP3 = 103,02
```

### Max Number of Orders

Cornix documenta fino a **10 take-profit orders** per trade.

---

## 6.4 Trailing Take-Profit

Quando il prezzo raggiunge un target TP:

```text
invece di eseguire il normale limit TP:
    si crea un trailing sell
```

Per un long:

```text
massimo = max(prezzi successivi)
trailing sell = massimo × (1 - trailing %)
```

La vendita avviene quando il prezzo scende al trailing price.

### Merging

Se raggiunge un TP successivo mentre un trailing TP è attivo:

```text
non viene creato un nuovo trailing TP;
la quantità del nuovo TP è unita al trailing esistente.
```

---

## 6.5 Moving Take-Profits

Questa funzione sposta i TP in base alle entry effettivamente fillate.

### Baseline: First Entry

Mantiene le distanze percentuali originali rispetto al primo entry, ma usando il prezzo effettivo di fill.

Esempio:

```text
entry originale = 100
TP1 = 110  → distanza +10%
TP2 = 120  → distanza +20%

primo entry fillato a 105

TP1 nuovo = 105 × 1,10 = 115,5
TP2 nuovo = 105 × 1,20 = 126
```

### Baseline: Average Entries

Dopo ogni entry fillata, ricalcola i TP in base alla media effettiva delle entry fillate, mantenendo le distanze originarie.

Esempio:

```text
E1 fillata = 100
E2 fillata = 90

media effettiva = 95

TP1 (+10%) = 104,5
TP2 (+20%) = 114
```

**Effetto operativo:** aumenta la probabilità di raggiungere TP dopo un averaging-down, ma riduce il prezzo di uscita rispetto ai TP originari.

---

## 6.6 Take Profit Grace

### Obiettivo

Aumentare la probabilità di fill quando il TP viene toccato ma l’ordine limite è parzialmente eseguito o non eseguito.

### Logica

```text
TP raggiunto
    │
    ├─ ordine fillato interamente → conclusione
    │
    └─ ordine non fillato / fill parziale
            │
            ├─ cancella parte residua
            ├─ riposiziona a prezzo più aggressivo
            └─ ripete finché:
                 - la quantità è venduta, oppure
                 - si raggiunge il Maximum Price Cap
```

### Direzione del repricing

| Trade | Repricing |
|---|---|
| Long | TP riprovati progressivamente sotto il TP originale |
| Short | TP riprovati progressivamente sopra il TP originale |

Esempio long:

```text
TP = 80.000
Grace cap = 0,5%

range minimo consentito = 79.600
```

Cornix prova livelli progressivamente inferiori fino a quel limite.

### Limite verificabile

Cornix dichiara esplicitamente che la fill completa **non è garantita**: dipende comunque dal book dell’exchange.

### Ambiguità

La fonte descrive l’effetto come riferito al “first Take Profit target” in una parte del testo. Non è possibile confermare dalla sola documentazione consultata che Take Profit Grace venga applicato indistintamente a tutti i TP.

---

# 7. Stop

## 7.1 Channel / Personal / Off

| Modalità | Conseguenza |
|---|---|
| Channel | segue lo stop del canale/segnale |
| Personal | applica uno stop personale |
| Off | non crea alcun ordine stop per i trade automatici |

## 7.2 Skip Signals Without Stop-Loss

Opera solo quando lo stop è in modalità `Channel`.

| Modalità stop | Effetto di “Skip signals without Stop-Loss” |
|---|---|
| Channel | applicabile |
| Personal | non applicabile: il trade avrà sempre uno stop personale |
| Off | non applicabile: il trade non avrà stop |

---

## 7.3 Default Stop-Loss

### Parametri

| Parametro | Logica |
|---|---|
| Stop Loss Distance Percentage | distanza percentuale; range documentato 0,01%–100% |
| Baseline | `First Entry` oppure `Average Entries` |

### Formula long

```text
SL = baseline × (1 - default_stop_loss / leverage)
```

### Formula short

```text
SL = baseline × (1 + default_stop_loss / leverage)
```

La formula riporta già l’adattamento per leva previsto per i trade automatici leveraged.

### Baseline Average Entries

La media è calcolata:

```text
dopo aver applicato:
    - entry target del segnale
    - strategia entry personale
```

### Rimozione entry sotto lo stop

Se lo stop calcolato si trova oltre una o più entry, Cornix rimuove quelle entry e redistribuisce la quantità.

Esempio concettuale:

```text
entry = 100 / 98 / 96 / 94
SL calcolato = 95,06

entry 94 < SL
→ entry 94 rimossa
→ quantità redistribuita sulle entry rimanenti
```

La fonte precisa che lo stop resta allo stesso prezzo, quindi la distanza effettiva dal nuovo average entry può diventare maggiore della percentuale configurata.

---

## 7.4 Limit Price Reduction

Questa impostazione riguarda gli stop-limit.

```text
stop trigger = 1000
limit price reduction = 1%

limit order dopo trigger = 990
```

### Trade-off

| Riduzione piccola | Riduzione grande |
|---|---|
| rischio di mancato fill durante un dump rapido | maggiore rischio di vendita molto sotto il trigger |

La documentazione non esplicita una formula separata per short. Non va assunta senza verifica diretta sull’exchange e sul tipo di ordine supportato.

---

## 7.5 Stop-Loss Timeout

### Parametro

Range documentato: da **1 minuto** a **24 ore**.

### Logica

```text
prezzo raggiunge SL
    │
    ├─ attende timeout
    │
    ├─ allo scadere:
    │   ├─ prezzo tornato oltre lo SL → nessuna chiusura
    │   └─ prezzo ancora oltre lo SL → chiusura
    │
    └─ ordine eseguito come market al termine del timeout
```

Durante il timeout Cornix dichiara che lo stop order non è ancora visibile sull’exchange.

**Implicazione operativa:** durante la finestra di attesa la posizione non ha la protezione di uno stop eseguibile già piazzato. Questa è una conseguenza diretta del meccanismo descritto.

---

# 8. Trailing Stop-Loss

## 8.1 Principio

A differenza di Trailing Entry e Trailing TP:

- Trailing Entry/TP agiscono sulla quantità associata al target raggiunto;
- Trailing Stop agisce sulla **quantità residua rimasta nel trade**.

Ogni trailing stop richiede un trigger.

---

## 8.2 Tipi

### 1. Breakeven

Quando scatta:

```text
SL := prezzo medio delle entry fillate
```

Trigger ammessi:

- un target specifico;
- una percentuale oltre/sotto la media entry, a seconda della direzione.

Il breakeven considera solo entry fillate, non entry pendenti.

---

### 2. Moving Target

Può essere attivato solo da un target.

| Target raggiunto | Nuovo stop |
|---|---|
| TP1 | breakeven |
| TP2 | prezzo TP1 |
| TP3 | prezzo TP2 |
| TPn | prezzo TP(n−1) |

Trigger default: TP1.

---

### 3. Moving 2-Target

Può essere attivato solo da un target, minimo TP2.

| Target raggiunto | Nuovo stop |
|---|---|
| TP2 | breakeven |
| TP3 | prezzo TP1 |
| TP4 | prezzo TP2 |
| TPn | prezzo TP(n−2) |

---

### 4. Percent Below Triggers

Al trigger:

```text
long:
    SL = trigger_price × (1 - p)

short:
    SL = trigger_price × (1 + p)
```

Trigger ammessi:

- target specifico;
- percentuale oltre/sotto l’average entry;
- percentuale 0% per attivazione immediata, secondo il formato segnali documentato.

Questa modalità sposta lo stop a una distanza dal livello di trigger. La fonte non descrive un trailing continuo dopo tale spostamento.

---

### 5. Percent Below Highest

Dopo il trigger, lo stop si mantiene a una distanza percentuale dal massimo raggiunto nel caso long.

```text
long:
    SL(t) = max_price_so_far × (1 - p)
```

La documentazione indica che per short la distanza è “above” anziché “below”, ma non formalizza esplicitamente il riferimento dinamico al minimo successivo. Non è corretto completare la formula short senza una fonte aggiuntiva.

Trigger ammessi:

- target specifico;
- movimento percentuale;
- `0%` per attivazione immediata.

---

## 8.3 Visibilità degli update

Con trigger basato su target, Cornix afferma che gli update dello stop vengono riflessi live sul segnale per:

- Breakeven;
- Moving Target;
- Moving 2-Target.

Per:

- Percent Below Triggers;
- Percent Below Highest;
- trigger basati su percentuale;

l’aggiornamento non è necessariamente visibile live sul segnale, anche se la chiusura finale può notificare che il trailing stop è stato eseguito.

---

# 9. Adattamento automatico alla leva

## 9.1 Campo di applicazione

Per trade futures aperti automaticamente o tramite `One Click Follow`, Cornix divide alcuni parametri percentuali per la leva, per mantenere l’esposizione percentuale effettiva.

Si applica a:

- Default Stop-Loss;
- Trailing Entry;
- Trailing Take-Profit;
- parametri percentuali del Trailing Stop-Loss, ma non ai trigger.

Non si applica a:

- First Entry as Market;
- Limit Price Reduction;
- trade manuali;
- Smart Bot trades;
- parametri modificati manualmente dopo l’apertura.

## 9.2 Esempio

```text
default stop configurato = 10%
leva = 10×

stop prezzo effettivo = 10% / 10 = 1%
rischio percentuale sul margine ≈ 10%
```

## 9.3 Ambiguità sulla soglia 0,2%

Le fonti consultate non sono perfettamente coerenti:

- una fonte dice che le regolazioni trailing sono limitate da un cap di `0,2%`;
- un’altra parla di una soglia minima di `0,2%`.

Quindi la semantica esatta della soglia non è confermabile dalla sola documentazione letta. In un’implementazione non va codificata senza un test reale o un chiarimento Cornix.

---

# 10. Advanced: filtri, limiti e comportamento operativo

## 10.1 Risk & limit controls

| Parametro | Logica |
|---|---|
| Number of Simultaneous Trades | numero massimo totale di trade aperti dal bot |
| Number of Simultaneous Trades Per Symbol | massimo trade aperti sullo stesso pair; massimo documentato 30 |
| Max Concurrent Amount (USD) | tetto monetario complessivo dei trade attivi |
| Min Symbol Price (USD) | ignora simboli sotto il prezzo minimo |
| Min Symbol 24H Volume (USD) | ignora simboli con volume 24h inferiore alla soglia |

### Conseguenza di un edit manuale

Se un trade automatico viene modificato manualmente, viene riclassificato come manuale e:

- non riceve più la stessa gestione automatica;
- non viene più conteggiato nei limiti del bot, incluso `Max Concurrent Amount`.

---

## 10.2 Cooldown Between Trades per Symbol

Imposta un intervallo minimo, in secondi, fra l’apertura di trade sullo stesso simbolo.

```text
se now - ultima_apertura(symbol) < cooldown:
    ignora nuovo trade sullo stesso symbol
```

---

## 10.3 Auto-Cancel Trade Timeout

Riguarda il trade in stato waiting, senza entry fillate.

```text
trade creato
    │
    ├─ nessuna entry fillata
    │
    ├─ timeout vuoto → resta aperto finché non:
    │      - il canale lo cancella
    │      - l’utente lo chiude
    │
    └─ timeout impostato → cancella automaticamente allo scadere
```

Questa funzione è distinta da Stop-Loss Timeout:

| Funzione | Condizione |
|---|---|
| Auto-Cancel Trade Timeout | nessuna entry è fillata per troppo tempo |
| Stop-Loss Timeout | il prezzo ha raggiunto lo stop, ma Cornix rimanda la decisione |

---

## 10.4 Close Trade on TP / SL before Entry

Cornix descrive una logica pre-entry che chiude/cancella il trade se TP o SL vengono raggiunti prima di qualunque entry fillata.

La documentazione aggiunge condizioni specifiche:

- se al momento della creazione il prezzo era oltre 0,5% sotto il primo TP e il TP viene raggiunto prima dell’entry, il trade viene chiuso;
- se il prezzo era sopra il primo TP o entro 0,5% sotto di esso, Cornix lo tratta come possibile trade futuro e non lo chiude;
- per breakout, se lo SL viene raggiunto prima di qualunque entry, il trade viene chiuso.

Per i Signals Bots la voce può essere impostata in:

```text
Advanced → Close Trade On TP Before Entry
```

### Nota critica

La descrizione generale parla di TP o SL prima dell’entry, mentre l’articolo dettagliato aggiunge logiche contestuali basate su distanza dal TP e tipo breakout. Quindi il comportamento va modellato come regola condizionale, non come un semplice booleano universale.

---

## 10.5 Alternative USD Pairs

Definisce quale pair alternativo usare quando il segnale originale usa quote USDT, USDC o USD.

La documentazione consultata conferma la funzione, ma non dettaglia qui l’algoritmo di selezione/fallback fra diverse quote.

---

## 10.6 Stop Type (Conditional Orders)

Se l’exchange lo supporta, Cornix consente di scegliere il tipo di ordine dopo un trigger:

- Market;
- Limit.

Questa scelta può influenzare:

- stop-loss;
- breakout entries;
- trailing features.

---

## 10.7 Operation Hours

Definisce finestre di inattività del bot.

Durante tali finestre:

```text
nuovi segnali → ignorati
nuovi trade → non creati
```

La documentazione consultata non chiarisce se un trade già aperto continua a essere gestito normalmente: non bisogna assumere che gli ordini esistenti siano sospesi.

---

## 10.8 Futures Account Configuration

Cornix consente di impostare sul conto futures:

- Hedge Mode;
- One-Way Mode.

---

# 11. Logica di chiusura manuale

## Nessun target/entry fillato

`Close Trade`:

```text
cancella tutti gli ordini attivi
non modifica il portafoglio
```

## Almeno un entry fillato

Web Dashboard:

| Azione | Effetto |
|---|---|
| Sell coins at current price | cancella ordini aperti e chiude la posizione a mercato |
| Keep coins | cancella ordini, chiude il trade in Cornix, ma mantiene l’asset/posizione sull’exchange |

Telegram aggiunge:

| Azione | Effetto |
|---|---|
| Close Trade (Activate Trailing on Remaining Coins) | chiude il trade Cornix e crea un nuovo trailing sell sulla quantità residua |

---

# 12. Parsing della trailing configuration nei segnali

Questa parte è lato pubblicazione/parsing segnale, ma condiziona direttamente quale configurazione possa arrivare al follower.

## 12.1 Header obbligatorio

La documentazione afferma che il parser richiede esattamente:

```text
Trailing Configuration:
```

Senza questo header, le istruzioni trailing possono essere ignorate.

## 12.2 Sintassi entry e TP trailing

```text
Entry: Percentage (0.4%)
Take-Profit: Percentage (0.5%)
```

Il minimo documentato per trailing entry e TP nei segnali è `0,4%`.

## 12.3 Sintassi trailing stop

### Target-only

```text
Stop: Moving Target - Trigger: Target (1)
Stop: Moving 2 Target - Trigger: Target (2)
```

`Moving 2 Target` richiede almeno target 2.

### Trigger flessibili

```text
Stop: Breakeven - Trigger: Target (1)
Stop: Breakeven - Trigger: Percent (0.05%)

Stop: Percent Below Highest (0.4%) - Trigger: Percent (X%)
Stop: Percent Below Highest (0.4%) - Trigger: Percent (0.0%)
Stop: Percent Below Highest (0.2%) - Trigger: Target (1)

Stop: Percent Below Triggers (0.4%) - Trigger: Percent (X%)
Stop: Percent Below Triggers (0.4%) - Trigger: Percent (0.0%)
Stop: Percent Below Triggers (0.2%) - Trigger: Target (1)
```

Il minimo documentato per il trigger percentuale Breakeven è `0,05%`.

---

# 13. Macchina a stati ricostruita

```text
NEW_SIGNAL
    │
    ├─ filtri: simbolo / direzione / operation hours / volume / prezzo
    ├─ limiti: concurrent trades / amount / cooldown
    ├─ risoluzione config: Channel / Personal / Fallback
    │
    ▼
WAITING_ENTRY
    │
    ├─ Auto-Cancel timeout
    ├─ Close on TP/SL before entry
    ├─ First Entry as Market
    └─ Trailing Entry
    │
    ▼
POSITION_OPEN
    │
    ├─ entry aggiuntive
    ├─ TP limit / trailing TP / TP grace
    ├─ default SL / stop-limit or market
    ├─ stop timeout
    └─ trailing SL
    │
    ▼
CLOSING
    │
    ├─ TP completo
    ├─ SL
    ├─ trailing SL
    ├─ close manuale
    ├─ sell reached / pre-entry cancellation
    └─ signal cancellation
    │
    ▼
CLOSED
```

---

# 14. Interazioni critiche da non perdere

## 14.1 Entry strategy ↔ Default SL

La strategia entry determina la media ponderata.  
La media ponderata può determinare lo SL.  
Lo SL può eliminare entry sotto di esso.  
L’eliminazione ricalcola la media effettiva, ma lo stop può restare invariato.

```text
entry ratios
    → weighted average
        → default SL
            → remove invalid lower entries
                → redistribute sizes
                    → effective SL distance changes
```

## 14.2 First Entry as Market ↔ Moving TP

Se First Entry as Market fill-a sopra il prezzo originario:

- con `Moving TP / First Entry`, i TP salgono mantenendo le distanze originali dal nuovo fill;
- con `Moving TP / Average Entries`, i TP possono essere ricalcolati dopo ogni fill entry.

## 14.3 Trailing TP ↔ TP successivi

Se un trailing TP è già attivo e viene raggiunto un TP successivo:

```text
non nasce un secondo trailing;
la quantità del TP successivo viene fusa nel trailing esistente.
```

Questo cambia la granularità dei TP: target distinti diventano una sola quantità gestita da un solo trailing.

## 14.4 Trailing stop ↔ quantità residua

Il trailing stop non gestisce il target raggiunto, ma la parte che resta aperta dopo i fill TP.

## 14.5 Leverage adjustment ↔ percentuali

Per trade automatici levered, una percentuale configurata può essere modificata da Cornix prima dell’invio dell’ordine.

Per replicare Cornix in un proprio sistema, bisogna memorizzare separatamente:

```text
configured_percent
effective_exchange_percent
leverage_used
adjustment_rule_version
```

---

# 15. Punti non confermabili dalle fonti consultate

1. La precedenza esatta fra valore del gruppo e valore scritto nel singolo segnale quando entrambi sono diversi.
2. L’algoritmo completo di “number of active entry orders”.
3. La formula precisa del `Limit Price Reduction` per short.
4. Se Take Profit Grace si applica a tutti i TP oppure solo al primo.
5. La semantica esatta della soglia trailing `0,2%`, perché le fonti la descrivono in modo non perfettamente coerente.
6. Il comportamento degli ordini già aperti durante `Operation Hours`.
7. L’algoritmo di selezione effettiva degli `Alternative USD Pairs`.

---

# 16. Fonti ufficiali

## Raccolta principale

- [Trading Configurations](https://help.cornix.io/en/collections/3260337-trading-configurations)

## General

- [Amount Per Trade](https://help.cornix.io/en/articles/5814867-amount-per-trade)
- [Leverage](https://help.cornix.io/en/articles/5814858-leverage)
- [Long/Short](https://help.cornix.io/en/articles/5814864-long-short)
- [Signals Bot Advanced Settings — General Section](https://help.cornix.io/en/articles/8975691-signals-bot-advanced-settings-general-section)

## Entries

- [Entry Ratios Strategy — Built-in](https://help.cornix.io/en/articles/5814861-entry-ratios-strategy-built-in)
- [First Entry as Market](https://help.cornix.io/en/articles/5814856-first-entry-as-market)
- [Trailing Entry](https://help.cornix.io/en/articles/5814869-trailing-entry)
- [Signals Bot Advanced Settings — Entry Strategy](https://help.cornix.io/en/articles/8976601-signals-bot-advanced-settings-entry-strategy)

## Take-Profits

- [Take-Profit Ratios Strategy](https://help.cornix.io/en/articles/5814868-take-profit-ratios-strategy)
- [Trailing Take-Profit](https://help.cornix.io/en/articles/5814862-trailing-take-profit)
- [Take Profit Grace](https://help.cornix.io/en/articles/11121738-take-profit-grace)
- [Signals Bot Advanced Settings — Take-Profit Strategy](https://help.cornix.io/en/articles/8976604-signals-bot-advanced-settings-take-profit-strategy)

## Stop

- [Default Stop-Loss](https://help.cornix.io/en/articles/5814860-default-stop-loss)
- [Limit Price Reduction](https://help.cornix.io/en/articles/5814866-limit-price-reduction)
- [Stop Loss Timeout](https://help.cornix.io/en/articles/5814870-stop-loss-timeout)
- [Trailing Stop-Loss](https://help.cornix.io/en/articles/5814874-trailing-stop-loss)
- [Signals Bot Advanced Settings — Stop Loss Strategy](https://help.cornix.io/en/articles/8976608-signals-bot-advanced-settings-stop-loss-strategy)

## Advanced / lifecycle / parsing

- [Signals Bot Advanced Settings — Advanced Section](https://help.cornix.io/en/articles/8975701-signals-bot-advanced-settings-advanced-section)
- [Close Trade on Take Profit/Stop-Loss before Entry](https://help.cornix.io/en/articles/5814872-close-trade-on-take-profit-stop-loss-before-entry)
- [Is an open trade updated/cancelled automatically when the signal is edited/canceled?](https://help.cornix.io/en/articles/5814894-is-an-open-trade-updated-cancelled-automatically-when-the-signal-is-edited-canceled)
- [Close Trade](https://help.cornix.io/en/articles/5814888-close-trade)
- [Automated Configuration Leverage Adjustment](https://help.cornix.io/en/articles/6005741-automated-configuration-leverage-adjustment)
- [Only Use If Not Defined By Group](https://help.cornix.io/en/articles/9274477-only-use-if-not-defined-by-group)
- [Signals Behavior](https://help.cornix.io/en/articles/11502620-signals-behavior)
- [Signal Posting](https://help.cornix.io/en/articles/5814956-signal-posting)
- [Understanding Discrepancies Between Signals and Executed Trades](https://help.cornix.io/en/articles/14129169-understanding-discrepancies-between-signals-and-executed-trades)
