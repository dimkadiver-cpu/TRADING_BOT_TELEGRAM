# Riassunto — DB operativo, lifecycle worker e gestione ordini

## 1. Scelta architetturale

Conviene partire con **due database fisici separati**:

```text
parser_db
    ricezione Telegram
    parsing
    canonical events
    audit parser

ops_db
    esecuzione
    ordini
    fill
    posizioni
    lifecycle
    rischio
    riconciliazione exchange
```

La separazione è corretta perché il parser e l'esecutore hanno responsabilità diverse.

Il parser deve solo interpretare messaggi e produrre eventi canonici.  
L'operativo deve gestire soldi, ordini, posizioni, fill e stato reale.

Regola fondamentale:

```text
Il parser non deve mai scrivere direttamente ordini.
Il parser produce canonical_events.
Solo il layer operativo decide se trasformarli in comandi di esecuzione.
```

---

## 2. Non separare fisicamente ordini attivi ed eseguiti

Nel DB operativo non conviene creare due tabelle tipo:

```text
ordini_attivi
ordini_eseguiti
```

È una separazione fragile, perché un ordine può essere:

```text
NEW
OPEN
PARTIALLY_FILLED
FILLED
CANCELLED
REJECTED
EXPIRED
FAILED
REPLACED
```

Un ordine parzialmente fillato e poi cancellato sarebbe sia “eseguito parzialmente” sia “non più attivo”. Due tabelle separate creano ambiguità.

Soluzione corretta:

```text
ops_orders = tutti gli ordini, con status corrente
ops_fills  = tutte le esecuzioni reali/fill
```

Gli “ordini attivi” e “ordini eseguiti” si ottengono tramite query o view.

Esempi:

```sql
CREATE VIEW view_active_orders AS
SELECT *
FROM ops_orders
WHERE status IN ('NEW', 'OPEN', 'PARTIALLY_FILLED');
```

```sql
CREATE VIEW view_filled_orders AS
SELECT *
FROM ops_orders
WHERE status = 'FILLED';
```

---

## 3. Tabelle minime consigliate in `ops_db`

### `ops_trade_chains`

Rappresenta una chain operativa derivata da un segnale.

Campi minimi:

```text
id
source_canonical_event_id
source_message_id
trader_id
symbol
side                    LONG / SHORT
entry_mode              MARKET / LIMIT
lifecycle_state         WAITING_ENTRY / OPEN / PARTIALLY_CLOSED / CLOSED / CANCELLED / ERROR
risk_profile_id
created_at
updated_at
```

---

### `ops_orders`

Contiene tutti gli ordini, attivi e storici.

Campi minimi:

```text
id
trade_chain_id
exchange
symbol
side                    BUY / SELL
position_side           LONG / SHORT
order_role              ENTRY / TP / SL / CLOSE / REDUCE / CANCEL_REPLACE
order_type              MARKET / LIMIT / STOP / TAKE_PROFIT
price
qty
filled_qty
remaining_qty
status                  NEW / OPEN / PARTIALLY_FILLED / FILLED / CANCELLED / REJECTED / FAILED
client_order_id
exchange_order_id
executor_id
created_at
updated_at
```

`order_role` è critico. Senza questo campo non puoi sapere se un fill riguarda entry, TP, SL, close manuale o reduce partial.

---

### `ops_fills`

Contiene ogni esecuzione reale.

Un ordine può avere più fill.

Campi minimi:

```text
id
order_id
trade_chain_id
exchange
symbol
side
price
qty
fee
fee_asset
liquidity                MAKER / TAKER
exchange_trade_id
filled_at
```

---

### `ops_positions`

Stato ricostruito della posizione.

Campi minimi:

```text
id
trade_chain_id
exchange
symbol
side
status                  OPEN / PARTIALLY_CLOSED / CLOSED
entry_avg_price
qty_open
qty_closed
realized_pnl
unrealized_pnl
stop_price_current
breakeven_price
opened_at
closed_at
updated_at
```

Questa tabella non deve essere la fonte grezza della verità. Lo stato posizione va ricostruito da ordini, fill ed eventi exchange.

---

### `ops_exchange_events`

Eventi arrivati da Hummingbot o direttamente dall'exchange.

Campi minimi:

```text
id
exchange
event_type              ORDER_FILLED / ORDER_CANCELLED / POSITION_UPDATED / PRICE_LEVEL_TOUCHED
exchange_order_id
client_order_id
trade_chain_id
raw_payload
normalized_payload
received_at
processed_at
processing_status       NEW / PROCESSING / DONE / FAILED
```

Questa tabella è fondamentale per audit, recovery e debug.

---

### `ops_lifecycle_events`

Eventi decisionali prodotti dal tuo sistema.

Campi minimi:

```text
id
trade_chain_id
event_type              ENTRY_FILLED / MOVE_SL_TO_BE / CANCEL_PENDING / CLOSE_PARTIAL / SETUP_EXPIRED
reason
source_event_id
payload
created_at
```

Esempio:

```text
event_type = MOVE_SL_TO_BE
reason = TP1_FILLED
source_event_id = exchange_event_123
```

---

### `ops_execution_commands`

Comandi da inviare a Hummingbot o all'exchange.

Campi minimi:

```text
id
trade_chain_id
command_type            PLACE_ORDER / CANCEL_ORDER / REPLACE_ORDER / CLOSE_POSITION
status                  PENDING / SENT / CONFIRMED / FAILED
payload
result_payload
created_at
sent_at
confirmed_at
```

Serve per rendere il sistema robusto dopo crash, retry e problemi API.

---

## 4. Il worker lifecycle non deve scansionare tutto il DB

Approccio sbagliato:

```text
ogni X secondi:
    leggo tutte le trade_chains
    leggo tutti gli ordini
    ricalcolo tutto
    decido cosa fare
```

Problemi:

```text
lento
fragile
rischio comandi duplicati
race condition
difficile da debuggare
recovery complicata dopo crash
```

Approccio corretto:

```text
eventi nuovi / stati sensibili / comandi pendenti
    ↓
processo solo ciò che è nuovo o richiede controllo
    ↓
carico una chain specifica
    ↓
applico regole
    ↓
produco comandi
    ↓
marco evento come processato
```

Query tipica:

```sql
SELECT *
FROM ops_exchange_events
WHERE processing_status = 'NEW'
ORDER BY received_at
LIMIT 100;
```

Il worker prende l'evento, trova `trade_chain_id`, carica solo quella chain e decide.

---

## 5. Worker consigliati

### 5.1 `ExchangeEventSync`

Responsabilità:

```text
legge stato da Hummingbot/exchange
normalizza eventi
scrive in ops_exchange_events
aggiorna ordini/fill/posizioni se necessario
```

Esempi eventi:

```text
ORDER_FILLED
ORDER_PARTIALLY_FILLED
ORDER_CANCELLED
ORDER_REJECTED
POSITION_UPDATED
```

---

### 5.2 `LifecycleWorker`

Responsabilità:

```text
consuma ops_exchange_events NEW
carica la trade_chain collegata
applica regole lifecycle
scrive ops_lifecycle_events
crea ops_execution_commands
marca evento DONE
```

Non esegue direttamente ordini. Decide cosa va fatto.

---

### 5.3 `CommandWorker`

Responsabilità:

```text
legge ops_execution_commands PENDING
invia comando a Hummingbot/exchange
salva risultato
aggiorna status comando
```

Esempi comandi:

```text
PLACE_ORDER
CANCEL_ORDER
REPLACE_ORDER
CLOSE_POSITION
```

---

### 5.4 `TimeoutWorker`

Responsabilità:

```text
controlla solo chain in stati temporali sensibili
produce TIMEOUT_REACHED o CANCEL_PENDING
```

Query mirata:

```sql
SELECT *
FROM ops_trade_chains
WHERE lifecycle_state = 'WAITING_ENTRY_FILL'
  AND entry_timeout_at <= CURRENT_TIMESTAMP
LIMIT 100;
```

Non scansiona tutto il DB.

---

### 5.5 `PriceLevelMonitor`

Serve per regole come:

```text
se prezzo raggiunge TP prima della entry limit, cancella ordine pendente
```

Responsabilità:

```text
osserva solo simboli/chain con entry pendente
se un livello rilevante viene toccato, scrive PRICE_LEVEL_TOUCHED
```

Poi il `LifecycleWorker` decide cosa fare.

---

### 5.6 `ReconciliationWorker`

Controllo periodico di sicurezza.

Responsabilità:

```text
confronta DB operativo con exchange
rileva incoerenze
corregge stato o crea eventi di recovery
```

Esempio:

```text
DB dice: ordine OPEN
exchange dice: ordine FILLED
↓
crea evento ORDER_FILLED_RECOVERED
aggiorna DB
LifecycleWorker continua il flusso
```

Questa è l'unica parte dove una scansione più ampia può avere senso, ma non deve essere il meccanismo normale di lifecycle.

---

## 6. Flusso generale consigliato

```text
Telegram
  ↓
Parser V2
  ↓
parser_db.canonical_events
  ↓
OperationalBridge
  ↓
ops_db.ops_trade_chains
  ↓
RiskEngine
  ↓
ops_execution_commands
  ↓
CommandWorker
  ↓
Hummingbot / Exchange
  ↓
ExchangeEventSync
  ↓
ops_exchange_events
  ↓
LifecycleWorker
  ↓
nuovi lifecycle_events / execution_commands
```

---

## 7. Esempi operativi

### 7.1 Entry market

```text
NEW_SIGNAL MARKET
↓
crea trade_chain
↓
calcola size/rischio
↓
crea command PLACE_ORDER MARKET
↓
CommandWorker invia ordine
↓
exchange_event ORDER_FILLED
↓
LifecycleWorker imposta state = POSITION_OPEN
↓
crea comandi per SL/TP/protezioni
```

---

### 7.2 Entry limit

```text
NEW_SIGNAL LIMIT
↓
crea trade_chain state = WAITING_ENTRY_FILL
↓
crea ordine ENTRY LIMIT
↓
se fillata:
    ORDER_FILLED
    POSITION_OPEN
    applica TP/SL
↓
se timeout:
    TIMEOUT_REACHED
    CANCEL_PENDING_ENTRY
```

---

### 7.3 TP1 fillato → sposta SL a BE

```text
ORDER_FILLED role=TP tp_index=1
↓
LifecycleWorker carica chain
↓
aggiorna posizione parziale
↓
crea lifecycle_event TP1_FILLED
↓
regola: TP1_FILLED → MOVE_SL_TO_BE
↓
crea commands:
    CANCEL_OLD_SL
    PLACE_NEW_SL_BE
```

---

### 7.4 Prezzo tocca TP prima della entry limit

```text
state = WAITING_ENTRY_FILL
entry_order = OPEN
prezzo tocca TP1
↓
PriceLevelMonitor scrive PRICE_LEVEL_TOUCHED
↓
LifecycleWorker decide:
    cancella entry pendente
    state = SETUP_CANCELLED_MISSED_ENTRY
```

---

## 8. Fonte della verità

La fonte finale della verità è l'exchange.

```text
Exchange = verità reale
Hummingbot = canale operativo / monitor
ops_db = copia normalizzata e auditabile
LifecycleManager = decision maker
```

Il DB operativo deve poter essere corretto tramite reconciliation.

---

## 9. Idempotenza obbligatoria

Ogni worker deve poter processare due volte lo stesso evento senza fare danni.

Esempio:

```text
TP1_FILLED → MOVE_SL_TO_BE
```

Prima di generare il comando, il sistema deve controllare:

```text
SL è già a BE?
esiste già un comando MOVE_SL_TO_BE per questo TP1?
questo source_event_id è già stato processato?
```

Chiave logica consigliata per evitare duplicati:

```text
trade_chain_id
source_event_id
event_type
```

---

## 10. Regola finale

Struttura consigliata:

```text
LifecycleWorker = consuma eventi e decide
CommandWorker = esegue comandi
TimeoutWorker = controlli temporali mirati
PriceLevelMonitor = livelli prezzo rilevanti
ReconciliationWorker = sicurezza e recovery
```

Non usare il lifecycle worker come scanner globale del DB.

Formula corretta:

```text
event-driven + polling mirato + reconciliation periodica
```

Formula sbagliata:

```text
scansione continua di tutto il DB per decidere ogni volta cosa fare
```

