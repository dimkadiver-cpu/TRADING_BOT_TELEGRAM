# Hummingbot come Execution Layer — Sintesi operativa

## 1. Scopo del documento

Questo documento riassume il modello discusso per usare **Hummingbot** come layer di esecuzione in un sistema dove:

```text
Telegram signals
  ↓
Parser V2
  ↓
eventi operativi canonici
  ↓
RiskEngine / LifecycleManager
  ↓
Hummingbot / exchange
```

L'obiettivo non è usare Hummingbot come “cervello” del sistema, ma come **motore di esecuzione e monitoraggio**.

---

## 2. Principio centrale

La divisione corretta è:

```text
Tuo sistema = decide
Hummingbot = esegue / monitora
Exchange = fonte reale della verità
DB tuo = audit e stato normalizzato
```

Hummingbot può aiutare su:

- piazzamento ordini;
- cancellazione ordini;
- monitoraggio fill;
- lettura posizioni;
- gestione executor;
- stop loss / take profit / trailing / time limit, se configurati;
- comunicazione con exchange.

Ma non dovrebbe decidere autonomamente la logica completa del trade basata sui segnali Telegram.

---

## 3. Architettura consigliata

```text
Telegram message
  ↓
Parser V2
  ↓
Canonical Operational Event
  ↓
SignalChain DB
  ↓
RiskEngine
  ↓
TradeLifecycleManager
  ↓
ExecutionAdapter
  ├── Hummingbot API / Hummingbot Client
  └── opzionale Exchange Native API
  ↓
Exchange
  ↓
ExchangeEventSync
  ↓
DB: orders / fills / positions / events / warnings
  ↓
TradeLifecycleManager reagisce agli eventi
```

---

## 4. Componenti e responsabilità

| Componente | Responsabilità |
|---|---|
| `Parser V2` | Capisce il messaggio Telegram e produce eventi canonici |
| `SignalChain DB` | Collega messaggi, segnali, update, ordini e posizioni |
| `RiskEngine` | Calcola size, leva, rischio massimo, validazioni |
| `TradeLifecycleManager` | Decide cosa fare in base allo stato e agli eventi |
| `ExecutionAdapter` | Traduce decisioni interne in comandi Hummingbot/exchange |
| `Hummingbot` | Esegue ordini, monitora stato, gestisce executor |
| `Exchange` | Fonte finale della verità |
| `ExchangeEventSync` | Legge eventi reali da Hummingbot/exchange e aggiorna il DB |
| `DB tuo` | Stato normalizzato, audit, storico, reconciliation |

---

## 5. Flusso per segnale market

```text
NEW_SIGNAL MARKET
  ↓
RiskEngine calcola size / leva / rischio
  ↓
ExecutionAdapter valida simbolo e parametri exchange
  ↓
Hummingbot apre posizione market
  ↓
si applicano TP / SL / protezioni
  ↓
ExchangeEventSync registra fill e posizione
  ↓
LifecycleManager aggiorna stato trade
```

Stato atteso:

```text
CREATED
  ↓
POSITION_OPEN
  ↓
PROTECTED
```

---

## 6. Flusso per segnale limit

```text
NEW_SIGNAL LIMIT
  ↓
RiskEngine calcola size / leva / rischio
  ↓
ExecutionAdapter piazza entry limit
  ↓
DB registra ordine pendente
  ↓
Hummingbot/exchange monitora fill
```

Se entry viene fillata:

```text
ORDER_FILLED
  ↓
POSITION_OPENED
  ↓
LifecycleManager applica TP / SL
  ↓
state = POSITION_OPEN
```

Se entry non viene fillata entro il timeout:

```text
TIMEOUT_REACHED
  ↓
LifecycleManager decide CANCEL_PENDING_ENTRY
  ↓
ExecutionAdapter cancella ordine
  ↓
state = EXPIRED
```

Se il prezzo raggiunge TP prima che la entry venga fillata:

```text
PRICE_LEVEL_TOUCHED TP1
  ↓
entry_order_status = OPEN
position_state = NOT_OPEN
  ↓
LifecycleManager cancella entry pendente
  ↓
state = SETUP_CANCELLED_MISSED_ENTRY
```

---

## 7. TP/SL: distinzione critica

Non bisogna assumere che Hummingbot piazzi sempre automaticamente un bracket nativo exchange tipo terminale Bybit/Binance.

Esistono tre casi:

| Modalità | Descrizione | Rischio |
|---|---|---|
| Bot-managed TP/SL | Hummingbot monitora prezzo e invia ordine di uscita quando serve | Se bot/API si ferma, la protezione può non essere attiva |
| Exchange-native TP/SL | SL/TP sono ordini reali residenti sull'exchange | Più sicuro, ma dipende da exchange e connector |
| Misto | SL minimo nativo + gestione avanzata via bot/lifecycle | Più robusto, ma più complesso |

Per trading reale, la policy più prudente è:

```text
appena la posizione è aperta
  ↓
piazzare almeno SL protettivo nativo exchange, se supportato
  ↓
gestire TP, BE, trailing, partial close e update Telegram via LifecycleManager
```

---

## 8. Gestione update Telegram

Esempi di update:

```text
CLOSE_FULL
CLOSE_PARTIAL
MOVE_STOP
MOVE_STOP_TO_BE
CANCEL_PENDING
INVALIDATE_SETUP
MODIFY_ENTRY
MODIFY_TARGETS
```

Flusso:

```text
Telegram update
  ↓
Parser V2
  ↓
Canonical event
  ↓
LifecycleManager
  ↓
ExecutionAdapter
  ↓
Hummingbot / exchange
  ↓
ExchangeEventSync conferma evento reale
  ↓
DB aggiornato
```

Esempio `CLOSE_PARTIAL`:

```text
CLOSE_PARTIAL
  ↓
trova posizione collegata al signal_id
  ↓
calcola quantità da chiudere
  ↓
invia contro-ordine reduce-only / close
  ↓
registra fill
  ↓
aggiorna posizione residua
```

---

## 9. Regole autonome del LifecycleManager

Regole tipiche:

```text
TP1 colpito → sposta SL a BE
entry limit non fillata entro X tempo → cancella ordine
prezzo raggiunge TP prima della entry → cancella ordine pendente
posizione aperta senza SL → warning / emergency action
ordine rejected → blocca trade e segnala errore
posizione chiusa manualmente su exchange → aggiorna stato interno
```

Queste regole devono stare nel tuo sistema, non nel parser e non direttamente in Hummingbot.

---

## 10. Eventi exchange normalizzati

Il sistema dovrebbe normalizzare gli eventi reali in una tabella tipo `exchange_events`.

Esempi:

```text
ORDER_PLACED
ORDER_FILLED
ORDER_PARTIALLY_FILLED
ORDER_CANCELLED
ORDER_REJECTED
POSITION_OPENED
POSITION_SIZE_CHANGED
POSITION_CLOSED
TP_ORDER_FILLED
SL_ORDER_FILLED
PRICE_LEVEL_TOUCHED
TIMEOUT_REACHED
PROTECTION_ORDER_MISSING
```

Questi eventi alimentano il `LifecycleManager`.

---

## 11. Eventi lifecycle interni

Il `LifecycleManager` può produrre eventi interni tipo:

```text
ENTRY_CONFIRMED
SETUP_EXPIRED
SETUP_CANCELLED_MISSED_ENTRY
MOVE_SL_TO_BE_REQUESTED
CLOSE_PARTIAL_REQUESTED
CLOSE_FULL_REQUESTED
CANCEL_PENDING_REQUESTED
PROTECTION_APPLIED
PROTECTION_ORDER_MISSING
RECONCILIATION_REQUIRED
```

Questi eventi non sono necessariamente eventi exchange. Sono decisioni o stati logici del tuo sistema.

---

## 12. State machine minima

Una state machine iniziale potrebbe essere:

```text
CREATED
WAITING_ENTRY_FILL
POSITION_OPEN
PARTIALLY_CLOSED
PROTECTED_BE
CLOSED_TP
CLOSED_SL
CLOSED_MANUAL
CANCELLED
EXPIRED
ERROR
```

Descrizione:

| Stato | Significato |
|---|---|
| `CREATED` | Segnale ricevuto e validato |
| `WAITING_ENTRY_FILL` | Entry limit piazzata, posizione non ancora aperta |
| `POSITION_OPEN` | Posizione aperta |
| `PARTIALLY_CLOSED` | Posizione parzialmente chiusa |
| `PROTECTED_BE` | SL spostato a break-even |
| `CLOSED_TP` | Chiusura per target |
| `CLOSED_SL` | Chiusura per stop |
| `CLOSED_MANUAL` | Chiusura manuale/update trader |
| `CANCELLED` | Setup annullato |
| `EXPIRED` | Setup scaduto |
| `ERROR` | Incoerenza, rejection, stato non sicuro |

---

## 13. DB: tabelle minime consigliate

### `trade_chains`

Collega un segnale operativo alla sua vita completa.

Campi indicativi:

```text
id
source_signal_id
trader_id
symbol
side
entry_type
status
created_at
updated_at
risk_profile_id
```

### `orders`

Ordini inviati all'exchange/Hummingbot.

```text
id
trade_chain_id
exchange_order_id
client_order_id
symbol
side
order_type
position_action
price
quantity
status
created_at
updated_at
```

### `positions`

Snapshot posizione.

```text
id
trade_chain_id
exchange_position_id
symbol
side
size
entry_price
mark_price
unrealized_pnl
realized_pnl
status
updated_at
```

### `exchange_events`

Eventi grezzi/normalizzati da exchange.

```text
id
trade_chain_id
event_type
exchange
symbol
payload_json
event_time
ingested_at
```

### `lifecycle_events`

Decisioni e cambi di stato interni.

```text
id
trade_chain_id
event_type
previous_state
next_state
reason
payload_json
created_at
```

### `warnings`

Anomalie operative.

```text
id
trade_chain_id
warning_type
severity
message
created_at
resolved_at
```

---

## 14. Reconciliation

Il sistema deve periodicamente confrontare:

```text
DB interno
vs
Hummingbot state
vs
Exchange state
```

Esempi:

```text
DB dice ordine OPEN
exchange dice FILLED
  ↓
crea ORDER_FILLED
  ↓
aggiorna stato

DB dice posizione aperta
exchange dice posizione assente
  ↓
crea POSITION_CLOSED_EXTERNAL
  ↓
aggiorna stato e warning

DB dice SL presente
exchange non ha SL
  ↓
crea PROTECTION_ORDER_MISSING
  ↓
LifecycleManager decide emergency action
```

La fonte finale della verità resta sempre l'exchange.

---

## 15. Rischi principali

| Rischio | Descrizione | Mitigazione |
|---|---|---|
| SL solo bot-managed | Se il bot si ferma, lo SL può non essere eseguito | Usare SL nativo exchange quando possibile |
| DB fuori sync | Stato interno diverso da exchange | Reconciliation periodica |
| Fill parziali | Quantità reale diversa da quella prevista | Gestire `ORDER_PARTIALLY_FILLED` |
| Ordini rejected | Exchange rifiuta ordine per size/precision/leverage | Validazione pre-trade |
| Simboli dinamici | Il segnale arriva su symbol non preconfigurato | Symbol resolver + validazione exchange |
| Update Telegram ambiguo | Comando non chiaramente collegato a una posizione | TargetResolver + stato chain |
| Doppia esecuzione | Lo stesso evento viene processato due volte | Idempotency key / client_order_id |

---

## 16. Posizione consigliata su Hummingbot

Hummingbot è utile come:

```text
execution engine
order/position monitor
exchange connector layer
executor framework
```

Non va usato come:

```text
parser
risk brain completo
source of truth assoluta
database principale del ciclo segnale
lifecycle manager semantico per Telegram
```

La soluzione più robusta è:

```text
Hummingbot = esecutore
Tuo sistema = cervello operativo
Exchange = verità reale
```

---

## 17. Prossimo passo progettuale

Il prossimo documento dovrebbe definire il contratto tra `LifecycleManager` ed `ExecutionAdapter`.

Esempio comandi:

```text
OPEN_MARKET_POSITION
PLACE_LIMIT_ENTRY
CANCEL_ORDER
CLOSE_FULL
CLOSE_PARTIAL
MOVE_SL
MOVE_SL_TO_BE
PLACE_PROTECTION_SL
PLACE_TAKE_PROFIT
REPLACE_PROTECTION_ORDER
SYNC_POSITION
```

Ogni comando dovrebbe avere:

```text
command_id
trade_chain_id
symbol
side
quantity
price
order_type
reduce_only
position_action
reason
idempotency_key
```

Questo contratto è il punto più importante per collegare in modo pulito il tuo sistema a Hummingbot o a qualsiasi altro execution engine.
