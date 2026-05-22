# Hardening When Bot Is Down

## Obiettivo

Ridurre il rischio operativo nei casi in cui:

- il bot si spegne;
- il processore lifecycle si blocca;
- il worker execution non gira;
- il sync verso exchange e' temporaneamente assente.

Il principio guida e':

- tutto cio' che puo' vivere in sicurezza su exchange deve vivere su exchange;
- tutto cio' che richiede coordinamento dinamico del bot deve essere riconosciuto come meno resiliente.

---

## Livelli di protezione

## Livello 1 - protezione exchange-native

E' il livello piu' robusto.

Qui i protettivi sono custoditi direttamente da Bybit:

- stop loss nativo;
- take profit nativo;
- trailing stop nativo;
- TP/SL attached all'ordine entry (order-level);
- TP/SL di posizione (`entire position`) quando applicabile.

### Vantaggio

Se il bot si spegne dopo che l'ordine e' stato accettato e la posizione e' aperta:

- la protezione resta viva su exchange;
- non serve il bot per mantenere la protezione minima.

### Casi migliori

- una sola entry;
- un solo TP;
- uno SL;
- mode C / entry con attached protection oppure TP/SL position-level semplice.

---

## Livello 2 - protezione exchange + bot coordination

Qui la protezione esiste, ma per restare coerente dipende da logica applicativa:

- multi-entry;
- multi-TP partial;
- resize di SL/TP dopo fill successivi;
- cancel di leg residue;
- passaggi a breakeven;
- trailing gestito dal bot;
- update che convertono `LIMIT -> MARKET`;
- update che spostano o ricostruiscono i target.

### Rischio

Se il bot si ferma:

- i protettivi possono continuare a esistere;
- ma possono non restare allineati alla posizione reale.

Esempio classico:

- entra una prima leg da `0.7`;
- vengono creati TP/SL su qty `0.7`;
- entra poi una seconda leg da `0.3`;
- la posizione totale diventa `1.0`;
- se il bot non fa sync, TP/SL possono restare ancora dimensionati su `0.7`.

In questo scenario:

- non sei "senza protezione" in assoluto;
- ma sei protetto in modo incompleto o incoerente.

---

## Cosa supporta davvero Bybit

### Distinzione critica: order-level vs position-level

Bybit ha due sistemi di protezione separati che si comportano in modo diverso.

#### Order-level TP/SL (attached all'ordine entry)

Vengono creati al momento del `placeOrder` con i parametri `takeProfit`/`stopLoss`.
Sono legati all'ordine specifico: quando quell'ordine filla, i protettivi diventano attivi per la qty fillata.

- Sono **reduce-only** per la qty di quell'ordine.
- **Coesistono** tra piu' entry: se filla Entry1 (qty 0.7) e poi Entry2 (qty 0.3), i due SL order-level sommano 1.0 = posizione totale.
- Se una entry non filla mai, il suo SL/TP non esiste → nessun ordine spurio.
- **Comportamento se la posizione cambia**: rimangono fissi sulla qty originale dell'ordine. Non si ridimensionano automaticamente.

#### Position-level TP/SL (`Set Trading Stop`)

Vengono impostati via `trading_stop` API dopo che la posizione esiste.

**Modalita' "Full" (entire position)**:

- Il trigger e' basato sul prezzo, non su una qty esplicita.
- Quando il prezzo raggiunge il livello, Bybit chiude l'**intera posizione residua** al momento del trigger.
- Se la posizione si e' ridotta (un partial TP ha fillato), il SL/TP "Full" protegge comunque il residuo.
- **Si adatta automaticamente alla posizione corrente** → e' il modello piu' robusto se il bot cade.

**Modalita' "Partial" (qty esplicita)**:

- Usa `tpSize`/`slSize` come qty fissa.
- Bybit **non ricalcola** automaticamente quando la posizione cambia.
- Se filla una seconda entry e la posizione cresce, i partial TP/SL restano dimensionati sulla qty originale.
- Richiede sync/rebuild applicativo per restare coerenti.

### Implicazione pratica

La frase "Bybit non si adatta automaticamente" si applica **solo ai partial TP/SL con size esplicita**.

Per la modalita' "Full" (entire position), Bybit **si adatta**: quando un partial TP position-level filla e riduce la posizione, il SL "Full" protegge il residuo senza intervento del bot.

---

## 1. Entire Position TP/SL

E' il modello position-level piu' robusto.

- Bybit gestisce il TP/SL sulla posizione intera corrente.
- Quando filla un partial TP, la posizione si riduce; il SL "Full" protegge il residuo senza sync.
- E' il modello preferibile quando la priorita' e': massima protezione a bot spento.

## 2. Partial Position TP/SL

Qui Bybit ragiona in `qty` fissa.

Nel runtime attuale questo si riflette nei campi:

- `tpSize`
- `slSize`

Quindi:

- il bot pensa in `%`;
- Bybit riceve e mantiene size esplicite.

Se la posizione cambia dopo:

- Bybit non ricalcola automaticamente i partial TP/SL;
- serve sync o rebuild applicativo.

## 3. Trailing Stop nativo

Se disponibile e coerente con la strategia:

- aumenta molto la resilienza;
- riduce la dipendenza dal bot per lo spostamento dello stop.

---

## Strategia di esecuzione target

La strategia adottata in questo runtime e' basata su **SL attached all'ordine entry** come protezione primaria exchange-native, con TP gestiti a seconda della complessita' del setup.

Vedere `execution_strategy_design.md` per il dettaglio dei casi e il mapping al codice.

### Principio guida

- **SL sempre attached all'entry**: anche se il bot cade prima che la posizione venga gestita, lo stop loss esiste su exchange per la qty fillata.
- **TP semplice (1 TP)**: attached all'entry insieme al SL → protezione completa exchange-native.
- **TP multipli**: solo il SL (e opzionalmente l'ultimo TP) sono attached; i TP intermedi vengono aggiunti dopo il fill → bot-dependent per i target, ma SL sempre presente.
- **Multi-entry**: ogni entry ha il proprio SL attached → la somma degli SL copre la posizione totale fillata in qualsiasi momento.

---

## Regole architetturali

### 1. Exchange-first per il SL

Il SL deve sempre essere attached all'ordine entry al momento del placement.
Non e' accettabile un setup in cui il SL viene creato solo dopo il fill (WAITING_POSITION).

### 2. TP attached quando possibile

Per setup 1 entry + 1 TP: attaccare anche il TP all'ordine entry.
Per setup piu' complessi: attaccare solo il SL, gestire i TP dopo il fill.

### 3. Non assumere auto-resize dei partial TP/SL

Per i partial TP/SL position-level su Bybit:

- trattare il bot come owner della coerenza size;
- non assumere che l'exchange aggiorni da solo i volumi nel modo voluto.

### 4. Riconoscere i casi "unsafe when bot down"

Il runtime dovrebbe marcare come meno resilienti i casi:

- `deferred_market + multi_tp_partial`
- `MARKET + LIMIT` con TP partial;
- update `LIMIT -> MARKET`;
- resize dopo fill successivi;
- qualsiasi scenario in cui la size reale cambia dopo il primo set di protettivi.

### 5. Modalita' conservative

Dove non esiste sync affidabile, meglio:

- bloccare;
- mandare in review;
- oppure ridurre la strategia a una forma piu' robusta.

---

## Contromisure operative raccomandate

### 1. Watchdog di protezione

Processo separato che controlla periodicamente:

- posizione aperta esiste;
- SL esiste davvero su exchange;
- TP attesi esistono davvero su exchange;
- size protettive sono coerenti con `open_position_qty`.

Se trova mismatch:

- alza alert;
- opzionalmente tenta repair.

### 2. Reconciliation conservativa

Mai trattare un dato parziale exchange come verita' assoluta.

Se il sync e' incompleto o ambiguo:

- non cancellare protettivi automaticamente;
- non assumere che una posizione sia chiusa solo perche' non la vedi in una risposta incompleta;
- preferire retry / incomplete-state / review.

### 3. Alerting esplicito

Segnali da monitorare:

- posizione aperta senza SL su exchange;
- qty TP/SL inferiore a `open_position_qty` quando non previsto;
- comandi `SYNC_PROTECTIVE_ORDERS` falliti o ripetuti;
- bot down con posizioni aperte non exchange-native.

### 4. Policy differenziata per modalita'

Quando il sistema rileva modalita' ad alto rischio operativo:

- consentire solo setup semplici;
- oppure richiedere conferma/review manuale.

---

## Matrice sintetica

| Scenario | SL se bot cade | TP se bot cade | Robustezza |
|---|---|---|---|
| 1 entry + 1 TP (SL+TP attached) | protetto | protetto | alta |
| 1 entry + trailing stop native | protetto | n/a | alta |
| 1 entry + N TP (solo SL attached) | protetto | perso | medio-alta |
| N entry + 1 TP (SL attached per leg) | protetto su qty fillata | protetto su qty fillata | medio-alta |
| N entry + N TP partial (SL attached, TP dinamici) | protetto su qty fillata | TP intermedi non settati | media |
| Setup senza SL attached (D_POSITION_TPSL vecchio) | scoperto fino al fill | scoperto | bassa |
| LIMIT -> MARKET via update con resize protettivi | dipendente dal bot | dipendente dal bot | bassa |

---

## Regola finale

Se la domanda e':

> "Anche se il bot muore, la posizione resta protetta?"

la risposta corretta e':

- **si' per il SL**, se ogni entry viene piazzata con SL attached → la protezione minima esiste sempre per la qty fillata;
- **si' per il TP**, se il setup e' 1 entry + 1 TP con entrambi attached;
- **parzialmente per il TP**, nei setup multi-TP o multi-entry → i TP intermedi potrebbero non essere stati ancora settati;
- **no**, se il SL non e' attached all'entry ma creato solo dopo il fill (vecchio D_POSITION_TPSL).

---

## Riferimenti API Bybit

- Set Trading Stop (position-level TP/SL): https://bybit-exchange.github.io/docs/v5/position/trading-stop
- TP/SL Help Center: https://www.bybit.com/en/help-center/article/Introduction-to-Enhanced-Take-Profit-Stop-Loss-TP-SL
- Trailing Stop: https://www.bybit.com/en/help-center/article/?id=000001140&language=en_US
