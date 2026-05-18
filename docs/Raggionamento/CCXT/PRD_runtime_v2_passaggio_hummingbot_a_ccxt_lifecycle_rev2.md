# PRD — Runtime V2: eliminazione Hummingbot e integrazione CCXT/Bybit con lifecycle corretto

**Stato:** revisione architetturale aggiornata  
**Data:** 2026-05-18  
**Ambito:** `TRADING_BOT_TELEGRAM` — `runtime_v2`, execution gateway, lifecycle engine, ops DB  
**Decisione:** rimuovere Hummingbot dal runtime esecutivo e sostituirlo con un’integrazione diretta **CCXT REST + CCXT Pro WebSocket** verso Bybit Main Demo / Bybit Main Live.

---

# 1. Sintesi decisionale

## 1.1 Decisione principale

Il runtime passa da:

```text
runtime_v2
  → ExecutionGateway
  → HummingbotApiAdapter
  → Hummingbot API
  → Bybit
```

a:

```text
runtime_v2
  → ExecutionGateway
  → CcxtBybitAdapter
  → CCXT REST
  → Bybit Main Demo / Bybit Main Live
```

Per gli eventi real-time:

```text
CCXT Pro WebSocket
  → watchOrders
  → watchMyTrades
  → watchPositions
  → CcxtBybitWsEventWorker
  → ops_exchange_events
  → LifecycleEventWorker
```

Per recovery e riconciliazione:

```text
CCXT REST
  → fetchOrder / fetchOpenOrders / fetchClosedOrders
  → fetchMyTrades / fetchPositions
  → CcxtReconciliationWorker
  → ops_exchange_events mancanti
```

## 1.2 Principio fondamentale

CCXT **non** sostituisce il lifecycle engine.

Il lifecycle resta interno al progetto:

- `LifecycleGateWorker`
- `LifecycleEntryGate`
- `LifecycleEventWorker`
- `LifecycleEventProcessor`
- `ops_trade_chains`
- `ops_execution_commands`
- `ops_exchange_events`
- `ops_lifecycle_events`

CCXT sostituisce solo:

- invio ordini;
- interrogazione stato exchange;
- ascolto WebSocket ordini/esecuzioni/posizioni;
- normalizzazione degli eventi exchange.

## 1.3 Nota su CCXT Pro

In questo PRD:

```text
CCXT Pro = modulo WebSocket incluso nel pacchetto CCXT gratuito
```

Non viene trattato come una dipendenza commerciale separata.

---

# 2. Problema da risolvere

La sostituzione di Hummingbot con CCXT non deve essere una semplice sostituzione dell’adapter.  
Serve correggere anche alcuni punti del lifecycle attuale, altrimenti il nuovo adapter replicherebbe incoerenze già presenti.

I punti da chiudere sono:

1. Sequenza corretta:
   ```text
   entry → fill entry → stop/TP
   ```
   e uso reale di `WAITING_POSITION`.

2. Distinzione fra:
   - **ordine accettato**
   - **fill reale**
   - **comando completato**

3. Eventi specifici per:
   - `CLOSE_FULL_FILLED`
   - `CLOSE_PARTIAL_FILLED`
   - `STOP_MOVED_CONFIRMED`
   - `PENDING_ENTRY_CANCELLED_CONFIRMED`

4. Corretto supporto a:
   - multi-entry;
   - fill parziali;
   - media prezzo ponderata;
   - quantità posizione aperta;
   - resize ordini protettivi.

5. `CANCEL_PENDING_ENTRY` funzionante anche su chain già `OPEN` quando restano entry pending.

6. Separazione ortogonale tra:
   - stato della posizione (`lifecycle_state`);
   - stato della protezione BE (`be_protection_status`).

---

# 3. Obiettivi

## 3.1 Obiettivi funzionali

Il nuovo sistema deve:

1. Eliminare Hummingbot dal percorso runtime.
2. Inviare ordini direttamente a Bybit tramite CCXT.
3. Supportare:
   - Bybit Main Demo;
   - Bybit Main Live;
   - opzionalmente Bybit Testnet.
4. Mantenere il modello a:
   - trade chain;
   - execution command;
   - exchange event;
   - lifecycle event.
5. Supportare almeno i comandi operativi:
   - `PLACE_ENTRY`
   - `PLACE_PROTECTIVE_STOP`
   - `PLACE_TAKE_PROFIT`
   - `MOVE_STOP_TO_BREAKEVEN`
   - `MOVE_STOP`
   - `CANCEL_PENDING_ENTRY`
   - `CLOSE_PARTIAL`
   - `CLOSE_FULL`
6. Ricevere eventi real-time da CCXT Pro:
   - ack/cancel/reject ordine;
   - fill entry;
   - fill TP;
   - fill SL;
   - fill exit manuali;
   - aggiornamenti posizione.
7. Riconciliare stato locale ↔ exchange all’avvio e periodicamente.
8. Supportare gestione corretta di entry LIMIT, entry MARKET e lifecycle successivo.
9. Rendere Bybit Demo un banco di prova fedele per lo stesso flusso usato in live.

## 3.2 Obiettivi non funzionali

- Idempotenza forte.
- Correlazione deterministica tramite `client_order_id`.
- Persistenza auditabile di comandi, eventi exchange ed eventi lifecycle.
- Fail-safe sul live trading.
- Separazione netta tra dominio operativo e adapter exchange.
- Recovery dopo crash/disconnessione.
- Nessuna dipendenza funzionale residua da Hummingbot.

---

# 4. Non obiettivi

Questo PRD non rifà:

- parser;
- enrichment;
- operation rules a monte;
- risk model generale;
- reportistica;
- dashboard.

Non introduce subito:

- multi-exchange generico completo;
- portfolio management avanzato;
- funding accounting;
- risk analytics complessi.

---

# 5. Architettura target

## 5.1 Flusso globale

```text
Parser / Canonical / Enrichment
        ↓
LifecycleGateWorker
        ↓
LifecycleEntryGate
        ↓
ops_trade_chains
ops_lifecycle_events
ops_execution_commands
        ↓
ExecutionCommandWorker
        ↓
ExecutionGateway
        ↓
CcxtBybitAdapter
        ↓
CCXT REST
        ↓
Bybit Demo / Live
```

Parallelamente:

```text
CCXT Pro WebSocket
  watchOrders
  watchMyTrades
  watchPositions
        ↓
CcxtBybitWsEventWorker
        ↓
ops_exchange_events
        ↓
LifecycleEventWorker
        ↓
LifecycleEventProcessor
        ↓
ops_lifecycle_events
ops_execution_commands aggiuntivi
ops_trade_chains aggiornato
```

Recovery:

```text
CcxtReconciliationWorker
        ↓
fetchOrder / fetchOpenOrders / fetchClosedOrders
fetchMyTrades / fetchPositions
        ↓
genera eventi mancanti
        ↓
ops_exchange_events
```

---

# 6. Decisioni architetturali vincolanti

## 6.1 CCXT REST per i comandi

CCXT REST gestisce:

- create order;
- cancel order;
- edit order se scelto in futuro;
- fetch order;
- fetch positions;
- leverage / margin mode;
- fetch trade history e closed orders per reconciliation.

## 6.2 CCXT Pro WebSocket per gli eventi

La fonte primaria per il lifecycle non deve essere il polling.

### Uso consigliato degli stream

```text
watchOrders
  → ordine accepted/open/cancelled/rejected/closed

watchMyTrades
  → fill reali / execution events

watchPositions
  → sanity check stato posizione / recovery / cross-check quantità
```

### Regola importante

I fill operativi devono essere derivati principalmente da:

```text
watchMyTrades
```

non soltanto da `watchOrders`.

Motivo: un ordine può essere:
- parzialmente fillato;
- fillato in più esecuzioni;
- chiuso dopo più trade.

Il lifecycle deve reagire ai **fill reali**, non solo allo stato terminale dell’ordine.

---

# 7. Modello corretto del lifecycle

## 7.1 Separazione fra stato posizione e stato BE

### Problema attuale

Nel modello attuale compaiono sia:

```text
lifecycle_state = BE_MOVE_PENDING / PROTECTED_BE
```

sia:

```text
be_protection_status = NOT_PROTECTED / BE_MOVE_PENDING / PROTECTED
```

Questa duplicazione genera conflitti concettuali: una chain può essere insieme:
- `PARTIALLY_CLOSED`;
- `BE protected`.

Un singolo `lifecycle_state` non dovrebbe codificare entrambe le dimensioni.

### Decisione raccomandata

Il target state model deve diventare:

```text
lifecycle_state:
  CREATED
  WAITING_ENTRY
  OPEN
  PARTIALLY_CLOSED
  CLOSED
  CANCELLED
  EXPIRED
  REVIEW_REQUIRED
  ERROR
```

e separatamente:

```text
be_protection_status:
  NOT_PROTECTED
  BE_MOVE_PENDING
  PROTECTED
```

### Impatto

Gli stati:

```text
BE_MOVE_PENDING
PROTECTED_BE
```

vanno:
- rimossi dal `lifecycle_state` target;
- oppure mantenuti solo come compatibilità transitoria durante la migrazione, con refactor successivo obbligatorio.

---

# 8. Trade chain: dati runtime da aggiungere

## 8.1 Problema

La trade chain attuale contiene:
- `entry_avg_price`;
- `current_stop_price`;
- `expected_stop_price`;

ma non contiene quantità runtime necessarie per:
- multi-entry;
- fill parziali;
- partial close;
- resize ordini protettivi.

## 8.2 Nuovi campi raccomandati in `ops_trade_chains`

Aggiungere:

```text
planned_entry_qty       REAL
filled_entry_qty        REAL NOT NULL DEFAULT 0
open_position_qty       REAL NOT NULL DEFAULT 0
closed_position_qty     REAL NOT NULL DEFAULT 0
last_position_sync_at   TEXT NULL
```

### Semantica

- `planned_entry_qty`  
  Quantità teorica prevista dal risk sizing iniziale.

- `filled_entry_qty`  
  Quantità cumulata realmente entrata.

- `open_position_qty`  
  Quantità netta attualmente aperta.

- `closed_position_qty`  
  Quantità cumulata uscita tramite TP / SL / close manuali.

- `last_position_sync_at`  
  Ultimo allineamento affidabile con exchange.

## 8.3 Media prezzo ponderata

Ogni nuovo fill entry deve aggiornare:

```text
entry_avg_price =
  weighted_average(previous_qty, previous_avg, new_fill_qty, new_fill_price)
```

Non deve sovrascrivere il valore con l’ultimo fill.

---

# 9. Comandi: semantica corretta degli status

## 9.1 Stati command target

```text
PENDING
WAITING_POSITION
SENT
ACK
DONE
FAILED
REVIEW_REQUIRED
CANCELLED
```

## 9.2 Semantica

| Stato | Significato |
|---|---|
| `PENDING` | comando pronto, non ancora inviato |
| `WAITING_POSITION` | comando deliberatamente posticipato fino a quando esiste posizione |
| `SENT` | richiesta inviata ma ack exchange non ancora verificato |
| `ACK` | exchange ha accettato l’ordine o l’azione; monitoraggio attivo |
| `DONE` | obiettivo finale del comando raggiunto |
| `FAILED` | errore terminale |
| `REVIEW_REQUIRED` | caso non sicuro / non risolvibile automaticamente |
| `CANCELLED` | comando/order superseded o cancellato volontariamente |

## 9.3 Quando un comando diventa `DONE`

| Command | Stato `DONE` quando |
|---|---|
| `PLACE_ENTRY` | entry order terminalmente fillato, oppure policy futura su fill parziale completato |
| `PLACE_PROTECTIVE_STOP` | stop order fillato |
| `PLACE_TAKE_PROFIT` | TP order fillato |
| `CLOSE_PARTIAL` | exit order parziale eseguito |
| `CLOSE_FULL` | exit order full eseguito |
| `MOVE_STOP_TO_BREAKEVEN` | nuovo stop a BE confermato |
| `MOVE_STOP` | nuovo stop confermato |
| `CANCEL_PENDING_ENTRY` | cancellazione degli entry pending confermata |

## 9.4 Quando un comando diventa `CANCELLED`

| Command | `CANCELLED` quando |
|---|---|
| `PLACE_ENTRY` | entry pending annullata o scaduta |
| `PLACE_TAKE_PROFIT` | TP residuo cancellato per close/stop/final close |
| `PLACE_PROTECTIVE_STOP` | stop sostituito da un nuovo stop o annullato dopo close |
| qualsiasi comando | superseded da decisione successiva equivalente |

---

# 10. Sequenza corretta nuovo segnale → entry → SL/TP

## 10.1 Decisione

Il sistema deve applicare realmente la modalità:

```text
b_entry_stop_then_tp
```

## 10.2 Flusso corretto

### Alla creazione della chain

```text
SIGNAL PASS
  ↓
TradeChain WAITING_ENTRY
```

Comandi creati:

```text
PLACE_ENTRY                 → PENDING
PLACE_PROTECTIVE_STOP       → WAITING_POSITION
PLACE_TAKE_PROFIT           → WAITING_POSITION
```

## 10.3 Invio entry

`ExecutionCommandWorker` invia solo:

```text
PLACE_ENTRY
```

tramite `CcxtBybitAdapter`.

## 10.4 Primo fill entry

Quando arriva il primo fill reale:

```text
ENTRY_FILLED
```

il lifecycle:

1. porta chain a:
   ```text
   OPEN
   ```
2. aggiorna:
   - `filled_entry_qty`;
   - `open_position_qty`;
   - `entry_avg_price`;
3. lascia al worker il compito di rilasciare i comandi:
   ```text
   PLACE_PROTECTIVE_STOP
   PLACE_TAKE_PROFIT
   ```
   precedentemente in `WAITING_POSITION`.

## 10.5 Rilascio protective orders

Quando la chain è `OPEN`, il worker:

1. trova STOP/TP in `WAITING_POSITION`;
2. li porta a `PENDING`;
3. li invia via CCXT.

---

# 11. Eventi exchange da introdurre / chiarire

## 11.1 Eventi fill

Gli eventi `*_FILLED` devono rappresentare **execution events**, non necessariamente “ordine totalmente completato”.

Payload standard minimo:

```json
{
  "client_order_id": "tsb:15:101:entry:1",
  "exchange_order_id": "abc",
  "exchange_trade_id": "trade-xyz",
  "role": "entry",
  "sequence": 1,
  "fill_qty": 0.01,
  "fill_price": 67350.5,
  "fee": null,
  "order_fully_filled": false,
  "position_qty_after": 0.01,
  "event_source": "ccxt_pro_watchMyTrades"
}
```

## 11.2 Event types minimi

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_PARTIAL_FILLED
CLOSE_FULL_FILLED
```

## 11.3 Eventi non-fill necessari

```text
STOP_MOVED_CONFIRMED
PENDING_ENTRY_CANCELLED_CONFIRMED
ORDER_REJECTED
ORDER_CANCELLED
```

## 11.4 Uso di `watchOrders`

`watchOrders` deve alimentare almeno:

- ack ordine;
- cancel ordine;
- reject ordine;
- stato terminale order;
- eventuale completamento ordine in cross-check con `watchMyTrades`.

## 11.5 Uso di `watchPositions`

`watchPositions` serve a:

- sanity check quantità;
- recovery dopo downtime;
- verifica residuo posizione dopo partial close;
- cross-check se una sequenza di fill è stata persa.

---

# 12. Idempotenza eventi

## 12.1 Chiave primaria consigliata

Per eventi di fill:

```text
<event_type>:<trade_chain_id>:<exchange_trade_id>
```

Se `exchange_trade_id` non è disponibile:

```text
<event_type>:<trade_chain_id>:<exchange_order_id>:<fill_qty>:<fill_price>:<exchange_ts>
```

## 12.2 Perché non basta l’order id

Un ordine può produrre più execution fills.  
Se l’idempotency key usa solo `exchange_order_id`, i fill parziali successivi verrebbero persi.

---

# 13. Multi-entry e fill parziali

## 13.1 Problema attuale

Il gate genera più `PLACE_ENTRY` per ladder/range/two-step, ma il lifecycle non ha ancora quantità runtime sufficienti e non ricalcola correttamente la media prezzo.

## 13.2 Comportamento target

### Primo fill entry

```text
WAITING_ENTRY → OPEN
```

Aggiorna:
- `filled_entry_qty`;
- `open_position_qty`;
- `entry_avg_price`.

Protezione:
- piazza SL/TP dimensionati sulla **quantità realmente fillata**, non sulla quantità teorica totale.

### Fill entry successivi

La chain resta:

```text
OPEN
```

Aggiorna:
- `filled_entry_qty`;
- `open_position_qty`;
- `entry_avg_price` ponderato.

Poi deve generare una sincronizzazione degli ordini protettivi.

---

# 14. Sincronizzazione ordini protettivi

## 14.1 Necessità

Dopo un evento che modifica la size reale della posizione, stop e TP residui devono riflettere la nuova size.

Eventi che richiedono sync:

- `ENTRY_FILLED` aggiuntivo;
- `TP_FILLED` non finale;
- `CLOSE_PARTIAL_FILLED`;
- eventuale `SL_FILLED` parziale;
- recovery/reconciliation con mismatch quantità.

## 14.2 Nuovo comando consigliato

Introdurre:

```text
SYNC_PROTECTIVE_ORDERS
```

oppure, se si preferisce nomenclatura più esplicita:

```text
REBUILD_PROTECTIVE_ORDERS
```

### Scelta raccomandata

Usare:

```text
SYNC_PROTECTIVE_ORDERS
```

perché il comando non implica sempre cancel/recreate totale: può decidere la strategia migliore in base allo stato reale.

## 14.3 Responsabilità del comando

- leggere posizione reale;
- leggere TP/SL attivi;
- confrontare quantità richieste;
- aggiornare o ricreare ordini protettivi;
- mantenere coerenza con management plan;
- produrre evento di conferma:
  ```text
  PROTECTIVE_ORDERS_SYNCED
  ```

## 14.4 Se non implementato subito

Se `SYNC_PROTECTIVE_ORDERS` non viene implementato nella prima migrazione CCXT:

- l’esecuzione automatica multi-entry deve essere limitata;
- ladder/range/two-step devono andare in `REVIEW_REQUIRED` oppure essere temporaneamente non auto-eseguibili;
- partial close deve essere trattato con attenzione perché il residuo SL potrebbe non essere ridimensionato correttamente.

---

# 15. TP / SL lifecycle

## 15.1 TP fill

Quando arriva:

```text
TP_FILLED
```

il lifecycle:

1. riduce `open_position_qty`;
2. aumenta `closed_position_qty`;
3. determina se:
   - posizione ancora aperta;
   - posizione chiusa interamente;
4. se residuo > 0:
   ```text
   lifecycle_state = PARTIALLY_CLOSED
   ```
5. se residuo = 0:
   ```text
   lifecycle_state = CLOSED
   ```
6. se il management plan prevede BE su quel TP:
   genera:
   ```text
   MOVE_STOP_TO_BREAKEVEN
   ```
7. genera anche, se necessario:
   ```text
   SYNC_PROTECTIVE_ORDERS
   ```

## 15.2 SL fill

Quando arriva:

```text
SL_FILLED
```

il lifecycle:

1. riduce `open_position_qty`;
2. aumenta `closed_position_qty`;
3. se residuo = 0:
   ```text
   CLOSED
   ```
4. se residuo > 0:
   - stato `PARTIALLY_CLOSED`;
   - trigger `SYNC_PROTECTIVE_ORDERS`;
   - possibile warning/review se la policy richiede stop totale immediato.

---

# 16. Move stop e BE protection

## 16.1 Flusso target

Quando viene generato:

```text
MOVE_STOP_TO_BREAKEVEN
```

oppure:

```text
MOVE_STOP
```

l’adapter CCXT deve:

1. individuare stop attivo;
2. usare la strategia iniziale raccomandata:
   ```text
   cancel old stop
   create new stop
   ```
3. restituire conferma strutturata;
4. produrre evento:
   ```text
   STOP_MOVED_CONFIRMED
   ```

## 16.2 Lifecycle dopo conferma

Se il comando era `MOVE_STOP_TO_BREAKEVEN`:

```text
be_protection_status = PROTECTED
current_stop_price = nuovo stop
```

Il `lifecycle_state` resta coerente con la posizione:
- `OPEN`;
- oppure `PARTIALLY_CLOSED`.

Non deve più diventare `PROTECTED_BE` se si adotta il modello state refactor.

---

# 17. Close manuali da Telegram update

## 17.1 CLOSE_PARTIAL

Flusso:

```text
Telegram update
  → LifecycleEntryGate
  → CLOSE_PARTIAL command
  → CcxtBybitAdapter market reduce-only
  → fill da watchMyTrades
  → CLOSE_PARTIAL_FILLED
  → open_position_qty ridotta
  → lifecycle_state = PARTIALLY_CLOSED se residuo > 0
  → SYNC_PROTECTIVE_ORDERS
```

## 17.2 CLOSE_FULL

Flusso:

```text
Telegram update
  → LifecycleEntryGate
  → CLOSE_FULL command
  → CcxtBybitAdapter market reduce-only
  → fill da watchMyTrades
  → CLOSE_FULL_FILLED
  → open_position_qty = 0
  → lifecycle_state = CLOSED
  → TP/SL residui cancellati
```

## 17.3 Correzione obbligatoria sui role

Nel `client_order_id` non usare più:

```text
role = entry
```

per:

```text
CLOSE_FULL
CLOSE_PARTIAL
```

Usare invece:

```text
role = exit_full
role = exit_partial
```

---

# 18. Cancel pending entry

## 18.1 Problema attuale

La logica attuale tende a considerare `CANCEL_PENDING_ENTRY` applicabile solo se:

```text
lifecycle_state == WAITING_ENTRY
```

Questo è insufficiente.

## 18.2 Caso reale da supportare

Ladder / two-step:

```text
entry1 fillata
entry2 pending
entry3 pending
```

La chain è già:

```text
OPEN
```

Se arriva update:

```text
annulla ordini pendenti
```

il sistema deve:
- cancellare entry2 ed entry3;
- lasciare la chain `OPEN`;
- mantenere i protective orders sulla posizione già aperta.

## 18.3 Regola target

`CANCEL_PENDING_ENTRY` deve cercare:

```text
PLACE_ENTRY ancora attivi
```

non basarsi solo sullo stato chain.

### Se nessuna entry è stata fillata

- cancella tutti gli entry pending;
- chain:
  ```text
  CANCELLED
  ```

### Se esiste già posizione aperta

- cancella solo entry residue;
- chain resta:
  ```text
  OPEN
  ```
  oppure `PARTIALLY_CLOSED` se già in quello stato.

## 18.4 Evento di conferma

Produrre:

```text
PENDING_ENTRY_CANCELLED_CONFIRMED
```

con payload:
- order IDs cancellati;
- quantità entry pending annullata;
- `position_already_open = true|false`.

---

# 19. Timeout entry

## 19.1 Caso semplice

Se la chain è:

```text
WAITING_ENTRY
```

e scade `entry_timeout_at`:

- genera `CANCEL_PENDING_ENTRY`;
- cancella entry pendenti;
- chain diventa:
  ```text
  EXPIRED
  ```

## 19.2 Caso con posizione già aperta e entry residue

Se una parte delle entry è stata fillata ma restano entry pending, e la policy prevede timeout residuo:

- cancellare solo entry residue;
- non chiudere la chain;
- chain resta `OPEN` / `PARTIALLY_CLOSED`.

Questo comportamento va esplicitato nel management plan.

---

# 20. Configurazione target

## 20.1 `config/execution.yaml`

Esempio:

```yaml
execution:
  default_adapter: ccxt_bybit_demo

  account_routing:
    default:
      adapter: ccxt_bybit_demo
      execution_account_id: main_demo

  adapters:
    ccxt_bybit_demo:
      type: ccxt_bybit
      mode: demo
      exchange_id: bybit
      market_type: swap
      settle: USDT
      leverage: 5

      credentials:
        api_key_env: BYBIT_API_KEY
        api_secret_env: BYBIT_API_SECRET

      entry_execution:
        mode: b_entry_stop_then_tp

      retry:
        max_attempts: 3
        backoff_seconds: [5, 15, 60]

      capabilities:
        place_entry: true
        protective_stop_native: true
        take_profit_native: true
        bracket_order: false
        move_stop: true
        close_partial: true
        close_full: true
        sync_protective_orders: true
        executor_position: false

      event_stream:
        enabled: true
        provider: ccxt_pro
        watch_orders: true
        watch_my_trades: true
        watch_positions: true
        reconcile_every_seconds: 30

      take_profit:
        min_order_policy: review
        residual_policy: assign_to_last_tp

      position_management:
        same_symbol_same_side_policy: block
        same_symbol_opposite_side_policy: allow_if_hedge_mode
        require_client_order_id_correlation: true

      live_safety:
        allow_live_trading: false
```

## 20.2 Variabili ambiente

```text
BYBIT_API_KEY=
BYBIT_API_SECRET=
BYBIT_EXECUTION_MODE=demo|live|testnet
TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND
```

## 20.3 Variabili da eliminare

```text
HUMMINGBOT_BASE_URL
HUMMINGBOT_SECRET
```

---

# 21. Componenti da implementare

## 21.1 Nuovi file

```text
src/runtime_v2/execution_gateway/adapters/ccxt_bybit.py
src/runtime_v2/execution_gateway/ccxt_bybit_ws_event_worker.py
src/runtime_v2/execution_gateway/ccxt_reconciliation_worker.py
src/runtime_v2/execution_gateway/protective_sync.py
tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter.py
tests/runtime_v2/execution_gateway/test_ccxt_bybit_ws_event_worker.py
tests/runtime_v2/execution_gateway/test_ccxt_reconciliation_worker.py
tests/runtime_v2/execution_gateway/test_protective_sync.py
```

## 21.2 File da modificare

```text
main.py
config/execution.yaml
src/runtime_v2/execution_gateway/adapters/factory.py
src/runtime_v2/execution_gateway/models.py
src/runtime_v2/execution_gateway/gateway.py
src/runtime_v2/execution_gateway/repositories.py
src/runtime_v2/execution_gateway/command_worker.py
src/runtime_v2/execution_gateway/event_sync.py   # da rifocalizzare o sostituire
src/runtime_v2/lifecycle/models.py
src/runtime_v2/lifecycle/entry_gate.py
src/runtime_v2/lifecycle/event_processor.py
src/runtime_v2/lifecycle/workers.py
src/runtime_v2/lifecycle/repositories.py
db/ops_migrations/*
```

## 21.3 File da rimuovere/deprecare

```text
src/runtime_v2/execution_gateway/adapters/hummingbot_api.py
src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py
hummingbot_scripts/fill_bridge.py
hummingbot_demo_patch/*
docs runtime dedicati a Hummingbot
```

---

# 22. Nuovi/aggiornati command types

## 22.1 Command types attuali da mantenere

```text
PLACE_ENTRY
PLACE_PROTECTIVE_STOP
PLACE_TAKE_PROFIT
MOVE_STOP_TO_BREAKEVEN
MOVE_STOP
CANCEL_PENDING_ENTRY
CLOSE_PARTIAL
CLOSE_FULL
```

## 22.2 Nuovo command type raccomandato

```text
SYNC_PROTECTIVE_ORDERS
```

---

# 23. Nuovi/aggiornati event types

## 23.1 Exchange events

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_PARTIAL_FILLED
CLOSE_FULL_FILLED
STOP_MOVED_CONFIRMED
PENDING_ENTRY_CANCELLED_CONFIRMED
PROTECTIVE_ORDERS_SYNCED
ORDER_REJECTED
ORDER_CANCELLED
```

## 23.2 Lifecycle events eventuali

```text
POSITION_SIZE_UPDATED
ENTRY_AVG_PRICE_UPDATED
PROTECTIVE_SYNC_REQUESTED
STOP_MOVE_CONFIRMED
PENDING_ENTRY_CANCELLED
```

La denominazione finale può essere uniformata, ma il concetto deve restare.

---

# 24. Reconciliation

## 24.1 All’avvio

Il sistema deve:

1. caricare command in:
   - `SENT`;
   - `ACK`;
   - `WAITING_POSITION` dove rilevante;
2. verificare ordini Bybit;
3. rileggere trades recenti;
4. rileggere posizioni correnti;
5. produrre eventi mancanti;
6. sincronizzare quantità e stop/TP.

## 24.2 Periodicamente

Ogni:

```text
30–60 secondi
```

eseguire reconciliation leggera.

## 24.3 Obiettivo

Se il processo muore mentre:
- un TP viene fillato;
- uno stop viene colpito;
- una chiusura manuale avviene;
- un ordine viene cancellato;

al riavvio il runtime deve:
- ricostruire l’evento perso;
- riallineare la chain;
- evitare duplicati.

---

# 25. Live safety

## 25.1 Live trading bloccato di default

Per `mode=live`, richiedere entrambe:

```yaml
live_safety:
  allow_live_trading: true
```

e:

```text
TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND
```

## 25.2 Demo

In `mode=demo` il gate live non deve bloccare l’esecuzione.

---

# 26. Fasi implementative

## Fase 0 — Refactor lifecycle contract prima dell’adapter

### Obiettivo

Congelare il contratto corretto prima di scrivere il nuovo adapter.

### Deliverable

- stato chain e BE status chiariti;
- role exit corretti;
- nuovi event types formalizzati;
- `CANCEL_PENDING_ENTRY` rivisto;
- qty runtime progettate;
- `SYNC_PROTECTIVE_ORDERS` formalizzato.

### Criterio di uscita

Il PRD diventa l’unica fonte normativa per il lifecycle target.

---

## Fase 1 — Adapter CCXT REST

### Deliverable

- `CcxtBybitAdapter`;
- demo/live/testnet;
- place/cancel/fetch positions;
- create entry market/limit;
- create stop/TP;
- close partial/full;
- move stop;
- cancel pending entry;
- factory/config aggiornati;
- Hummingbot rimosso dal path di invio.

### Event path in questa fase

Può restare una reconciliation CCXT polling come transizione, ma:
- non deve consolidarsi come soluzione finale;
- deve già produrre eventi compatibili con il contratto target.

---

## Fase 2 — CCXT Pro event stream

### Deliverable

- `CcxtBybitWsEventWorker`;
- `watchOrders`;
- `watchMyTrades`;
- `watchPositions`;
- eventi real-time idempotenti;
- fill events basati su trade/execution ID;
- inserimento in `ops_exchange_events`.

---

## Fase 3 — Lifecycle processor corretto

### Deliverable

- qty runtime aggiornate;
- avg entry ponderata;
- TP/SL/close events corretti;
- `STOP_MOVED_CONFIRMED`;
- `PENDING_ENTRY_CANCELLED_CONFIRMED`;
- `CLOSE_FULL_FILLED`;
- `CLOSE_PARTIAL_FILLED`;
- BE status aggiornato senza sovraccaricare lifecycle_state.

---

## Fase 4 — Protective sync

### Deliverable

- `SYNC_PROTECTIVE_ORDERS`;
- resize stop e TP su posizione reale;
- supporto robusto a:
  - multi-entry;
  - partial close;
  - TP progressivi;
  - entry ladder/range.

---

## Fase 5 — Recovery e reconciliation robusta

### Deliverable

- recovery on boot;
- reconciliation periodica;
- ricostruzione fill persi;
- cross-check posizione;
- deduplica eventi.

---

## Fase 6 — Pulizia Hummingbot

### Deliverable

- rimozione adapter Hummingbot;
- rimozione env obsolete;
- rimozione bridge e patch demo;
- aggiornamento docs runtime;
- aggiornamento test no-Hummingbot.

---

# 27. Acceptance criteria

La revisione è completata quando:

1. `runtime_v2` esegue ordini Bybit Demo senza Hummingbot.
2. Lo stesso adapter può essere configurato per Bybit Live con safety gate.
3. Un nuovo segnale produce:
   - chain `WAITING_ENTRY`;
   - entry `PENDING`;
   - SL/TP `WAITING_POSITION`.
4. Il primo fill entry:
   - apre la chain;
   - aggiorna qty;
   - calcola media entry corretta;
   - rilascia SL/TP.
5. TP fill:
   - riduce la size;
   - chiude o parzializza correttamente;
   - genera BE move se previsto;
   - attiva protective sync se necessario.
6. SL fill:
   - chiude o riduce correttamente la posizione;
   - non lascia chain incoerente.
7. `CLOSE_PARTIAL` e `CLOSE_FULL` producono eventi dedicati, non `ENTRY_FILLED`.
8. `MOVE_STOP_TO_BREAKEVEN` passa a `PROTECTED` solo dopo conferma exchange.
9. `CANCEL_PENDING_ENTRY` funziona:
   - prima del fill;
   - dopo fill parziale / chain già OPEN con entry residue.
10. `client_order_id` resta la chiave di correlazione principale.
11. Fill parziali non vengono persi per idempotency key troppo grossolana.
12. Reconciliation recupera eventi persi dopo downtime.
13. Hummingbot non è più necessario al runtime finale.
14. Multi-entry o viene supportato con protective sync, oppure resta esplicitamente bloccato/review-required fino a implementazione completa.

---

# 28. Rischi principali

## 28.1 STOP/TP Bybit: semantica e parametri

Mitigazione:
- test specifici su Bybit Main Demo;
- log payload raw adapter;
- fissare chiaramente la strategia stop/TP.

## 28.2 Eventi WS incompleti / disconnessioni

Mitigazione:
- reconciliation periodica;
- recovery on boot;
- idempotency robusta.

## 28.3 Partial fills complessi

Mitigazione:
- usare `watchMyTrades`;
- memorizzare execution ID;
- aggiornare quantità runtime;
- introdurre protective sync.

## 28.4 Multi-entry senza resize protettivi

Mitigazione:
- implementare `SYNC_PROTECTIVE_ORDERS`;
- in alternativa bloccare auto-execution multi-entry fino a supporto completo.

## 28.5 State model ambiguo fra partial close e BE protection

Mitigazione:
- tenere `be_protection_status` separato;
- deprecare `BE_MOVE_PENDING` / `PROTECTED_BE` come `lifecycle_state`.

---

# 29. Decisione finale raccomandata

La soluzione target è:

```text
Eliminare Hummingbot.
Usare CCXT REST per esecuzione.
Usare CCXT Pro WebSocket per eventi real-time.
Usare reconciliation CCXT REST come recovery.
Rafforzare il lifecycle prima di implementare l’adapter.
```

La migrazione non deve essere trattata come semplice “swap adapter”, ma come:

```text
refactor del contratto di execution/lifecycle
+
sostituzione del transport Hummingbot con CCXT/Bybit
```
