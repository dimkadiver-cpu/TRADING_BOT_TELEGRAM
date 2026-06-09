# Runtime V2 — Architettura corrente

Documento operativo per leggere e modificare `runtime_v2` senza perdere il filo tra parser, enrichment, lifecycle, execution gateway, reconciliation e control plane.

Questo documento descrive lo stato architetturale corrente del progetto, non una proposta teorica. Serve come mappa di navigazione per capire:

* quali livelli esistono;
* quale input/output passa tra i livelli;
* quali tabelle vengono lette/scritte;
* dove nasce una `TradeChain`;
* come un messaggio Telegram diventa un comando exchange;
* come un fill exchange torna nel lifecycle;
* dove sono i punti fragili.

---

## 1. Visione generale

Il runtime V2 è una pipeline a livelli.

Flusso principale:

```text
Telegram Listener
  ↓
Runtime V2 Intake
  ↓
Trader Resolution
  ↓
Parser V2 Pipeline
  ↓
Signal Enrichment
  ↓
Lifecycle
  ↓
Execution Gateway
  ↓
Exchange Adapter
  ↓
Exchange Events / Reconciliation
  ↓
Lifecycle Event Worker
  ↓
Control Plane / Telegram Logs
```

In termini più concreti:

```text
[Telegram message]
    ↓
RawIngestItem
    ↓
raw_messages
    ↓
ParserDispatchCandidate
    ↓
CanonicalMessage / CanonicalParseResult
    ↓
EnrichedCanonicalMessage
    ↓
TradeChain + LifecycleEvents + ExecutionCommands
    ↓
ops_execution_commands
    ↓
ExecutionGateway
    ↓
Exchange / Bybit / CCXT adapter
    ↓
ops_exchange_events
    ↓
LifecycleEventProcessor
    ↓
ops_trade_chains aggiornato
    ↓
clean logs / control plane / Telegram
```

Sintesi:

```text
parser_db = memoria semantica del messaggio
ops_db    = memoria operativa del trade
```

---

## 2. Separazione dei database

Runtime V2 usa due database logici separati.

### 2.1 `parser_db`

Contiene il mondo parser / messaggi / semantica.

Tabelle principali:

```text
raw_messages
canonical_messages
enriched_canonical_messages
parser_runs
parser_results_v2
```

Responsabilità:

* salvare messaggi Telegram grezzi;
* salvare risultati parser;
* salvare messaggi canonical;
* salvare enrichment;
* mantenere tracciabilità dal raw message fino al messaggio arricchito.

### 2.2 `ops_db`

Contiene il mondo operativo.

Tabelle principali:

```text
ops_trade_chains
ops_lifecycle_events
ops_execution_commands
ops_exchange_events
ops_control_state
```

Responsabilità:

* stato corrente delle trade chain;
* eventi lifecycle;
* comandi da eseguire;
* eventi ricevuti o ricostruiti dall’exchange;
* stato del control plane;
* pause/resume/block/manual review.

### 2.3 Regola importante

Non ci sono foreign key cross-database forti.

`ops_db` conserva riferimenti come plain integer:

```text
raw_message_id
canonical_message_id
enrichment_id
```

Questo evita accoppiamento rigido, ma aumenta la necessità di avere logging e trace chiari.

---

## 3. Layer 1 — Telegram Listener

### Scopo

Ricevere messaggi Telegram da canali/gruppi/topic configurati e trasformarli in input normalizzato per il runtime.

### Input

```text
Telegram event / message
```

### Output

```text
RawIngestItem
```

### Responsabilità

* ascoltare messaggi Telegram;
* estrarre chat id, message id, reply id, testo, data, source;
* non decidere logica trading;
* consegnare il messaggio all’intake runtime.

### Non deve fare

* parsing semantico;
* decisioni operative;
* creazione trade;
* gestione exchange.

---

## 4. Layer 2 — Runtime V2 Intake

### Scopo

Prendere il messaggio normalizzato e decidere se è eleggibile per il parser.

### Input

```text
RawIngestItem
```

### Output

```text
ParserDispatchCandidate
```

oppure skip/block.

### Scrive

```text
parser_db.raw_messages
```

### Responsabilità

* salvare il raw message;
* applicare blacklist globale;
* ignorare media-only o messaggi non utili;
* risolvere source/trader;
* scegliere parser profile;
* costruire contesto parser;
* preparare dispatch verso parser pipeline.

### Flusso interno

```text
RawIngestItem
  ↓
save raw message
  ↓
global blacklist check
  ↓
media-only / empty text check
  ↓
eligibility check
  ↓
trader resolution
  ↓
parser profile resolution
  ↓
ParserDispatchCandidate
```

### Nota critica

Questo è il punto giusto per introdurre una identità stabile del messaggio.

Proposta futura:

```json
{
  "source_identity": {
    "source_chat_id": "...",
    "topic_id": "...",
    "telegram_message_id": "...",
    "reply_to_message_id": "...",
    "trader_id": "...",
    "parser_profile": "..."
  }
}
```

Questa identità dovrebbe accompagnare tutto il flusso fino a lifecycle e ops.

---

## 5. Layer 3 — Trader Resolution

### Scopo

Associare il messaggio a un trader configurato.

### Input

```text
Raw message metadata
Source config
Telegram chat/topic
Sender / route
```

### Output

```text
ResolvedTrader
parser_profile
trader_id
account routing metadata
```

### Responsabilità

* capire quale trader ha generato il messaggio;
* caricare configurazione trader;
* stabilire parser profile;
* definire eventuali account/exchange default;
* bloccare messaggi da trader disabilitati se richiesto.

### Punto fragile

Se la risoluzione trader dipende solo da chat/topic e non da una struttura esplicita, gli update multi-chain restano più difficili da attribuire in modo deterministico.

---

## 6. Layer 4 — Parser V2 Pipeline

### Scopo

Trasformare testo e contesto in un messaggio canonical.

### Input

```text
ParserDispatchCandidate
```

### Output

```text
CanonicalParseResult
CanonicalMessage
```

### Scrive

```text
parser_db.canonical_messages
parser_db.parser_runs
parser_db.parser_results_v2
```

### Contratto principale: `CanonicalMessage`

Campi concettuali:

```text
primary_class
signal
update
target_action_groups
target_hints
warnings
diagnostics
raw_context
```

### Classi principali

```text
SIGNAL
UPDATE
REPORT
INFO
UNKNOWN
```

### Regola architetturale

Il parser deve dire **cosa contiene il messaggio**, non se va eseguito.

Corretto:

```text
"Questo messaggio contiene un nuovo segnale LONG BTCUSDT"
"Questo messaggio contiene update MOVE_SL_TO_BE"
"Questo messaggio contiene CLOSE_FULL"
```

Sbagliato:

```text
"Esegui questo trade"
"Chiudi davvero questa posizione"
"Sposta lo stop sull’exchange"
```

Quelle decisioni appartengono a enrichment/lifecycle/execution.

---

## 7. Layer 5 — Signal Enrichment

### Scopo

Applicare policy operative stateless prima di mandare il messaggio al lifecycle.

### Input

```text
CanonicalParseResult
CanonicalMessage
Trader config
Operation config
Market snapshot opzionale
```

### Output

```text
EnrichedCanonicalMessage
```

### Scrive

```text
parser_db.enriched_canonical_messages
```

### Decisioni possibili

```text
PASS
BLOCK
REVIEW
INFO
REPORT
```

### Responsabilità per SIGNAL

* trader abilitato;
* source ammessa;
* symbol blacklist;
* side ammesso;
* entry structure ammessa;
* SL obbligatorio;
* TP trim;
* entry weights;
* range split;
* price sanity;
* risk config;
* account routing preliminare.

### Responsabilità per UPDATE

* verificare action type;
* verificare se update è ammesso;
* classificare update automatico/manual review/block;
* preservare target hints;
* preparare messaggio per lifecycle.

### Regola operativa

Solo questo caso entra nel lifecycle:

```text
enrichment_decision = PASS
lifecycle_processed = False
```

Tutti gli altri casi vengono salvati ma non devono produrre comandi exchange.

---

## 8. Layer 6 — Lifecycle

Il lifecycle è il cuore operativo.

Trasforma messaggi arricchiti in:

```text
TradeChain
LifecycleEvent
ExecutionCommand
```

### 8.1 Input

```text
EnrichedCanonicalMessage
```

### 8.2 Output

```text
ops_trade_chains
ops_lifecycle_events
ops_execution_commands
```

### 8.3 Responsabilità

* creare nuove trade chain;
* aggiornare chain esistenti;
* decidere stato logico trade;
* calcolare piano di gestione;
* generare comandi operativi;
* reagire a eventi exchange;
* mantenere coerenza tra stato previsto e stato reale.

---

## 9. Lifecycle — Nuovo segnale

### Flusso per SIGNAL

```text
EnrichedCanonicalMessage(SIGNAL)
  ↓
LifecycleEntryGate
  ↓
control mode check
  ↓
symbol/side check
  ↓
existing chain check
  ↓
entry validation
  ↓
account snapshot
  ↓
market snapshot
  ↓
risk/capacity validation
  ↓
management plan
  ↓
planned qty
  ↓
execution mode
  ↓
create TradeChain
  ↓
emit lifecycle events
  ↓
create execution commands
```

### Stati iniziali tipici

```text
WAITING_ENTRY
OPEN
REVIEW_REQUIRED
REJECTED
```

### Eventi lifecycle prodotti

Esempi:

```text
SIGNAL_ACCEPTED
TRADE_CHAIN_CREATED
ENTRY_PLAN_CREATED
```

### ExecutionCommand prodotti

Esempi:

```text
PLACE_ENTRY_MARKET
PLACE_ENTRY_LIMIT
PLACE_PROTECTIVE_STOP
PLACE_TAKE_PROFIT
SET_POSITION_TPSL_FULL
SET_POSITION_TPSL_PARTIAL
```

---

## 10. Lifecycle — Update

### Input

```text
EnrichedCanonicalMessage(UPDATE)
```

### Primo problema

Un update deve essere associato a una o più trade chain.

### Strategia attuale di target resolution

Ordine concettuale:

```text
1. explicit chain id / signal id
2. telegram_message_id / reply_to_message_id
3. symbol + trader
4. ALL_SHORT
5. ALL_LONG
6. ALL_POSITIONS / ALL_OPEN / ALL_REMAINING
7. fallback se esiste una sola chain aperta del trader
8. ambiguità se più chain compatibili
```

### Output possibile

```text
target resolved → applica update
target ambiguous → REVIEW_REQUIRED
target missing → BLOCK / REVIEW
```

### Action update principali

```text
SET_STOP ENTRY       → MOVE_STOP_TO_BREAKEVEN
SET_STOP PRICE       → MOVE_STOP
SET_STOP TP_LEVEL    → MOVE_STOP al prezzo TP
CLOSE FULL           → CLOSE_FULL
CLOSE PARTIAL        → CLOSE_PARTIAL
CANCEL_PENDING       → CANCEL_PENDING_ENTRY
MODIFY_ENTRIES       → cancel + re-entry / market now / replace
INVALIDATE_SETUP     → cancel / close / mark invalid
```

### Punto fragile

La target resolution è accettabile per demo, ma non è ancora ideale per produzione multi-chain.

Caso problematico:

```text
Trader A ha 3 trade aperti su BTCUSDT.
Arriva: "стоп в бу"
```

Senza riferimento esplicito al segnale originale, il sistema deve scegliere tra:

```text
- applicare a tutte?
- applicare all’ultima?
- mandare in review?
- usare reply_to?
```

Soluzione consigliata: già dal parser/intake passare un blocco identificativo strutturato.

Esempio:

```json
{
  "message_identity": {
    "source": "telegram",
    "source_chat_id": "-100...",
    "topic_id": "123",
    "telegram_message_id": "456",
    "reply_to_message_id": "410",
    "trader_id": "maxgold",
    "parser_profile": "ru_crypto_signals"
  },
  "detected_references": {
    "symbols": ["BTCUSDT"],
    "sides": ["LONG"],
    "signal_refs": [],
    "message_refs": ["410"],
    "scope": "SYMBOL_OR_REPLY"
  }
}
```

---

## 11. Layer 7 — Execution Gateway

### Scopo

Prendere comandi neutrali dal lifecycle e inviarli all’exchange adapter.

### Input

```text
ops_execution_commands
```

### Output

```text
exchange API calls
ops_execution_commands updated
ops_exchange_events
```

### Responsabilità

* leggere comandi pending;
* risolvere account/exchange adapter;
* verificare live safety gate;
* verificare capability adapter;
* costruire client_order_id;
* applicare idempotency;
* inviare ordine;
* gestire retry/failure;
* marcare comando DONE/FAILED/WAITING;
* salvare eventuali exchange events.

### Client order id

Il client order id deve essere deterministico.

Componenti tipici:

```text
trade_chain_id
command_id
role
sequence
nonce
```

Obiettivo:

```text
stesso comando logico → stesso client_order_id
```

Questo permette:

* idempotenza;
* recovery dopo restart;
* riconciliazione;
* deduplica di ordini già inviati.

---

## 12. ExecutionCommand principali

| Command type                | Origine                        | Destinazione     | Note                         |
| --------------------------- | ------------------------------ | ---------------- | ---------------------------- |
| `PLACE_ENTRY_MARKET`        | Lifecycle nuovo segnale        | Exchange adapter | Entry immediata              |
| `PLACE_ENTRY_LIMIT`         | Lifecycle nuovo segnale        | Exchange adapter | Entry pendente               |
| `CANCEL_PENDING_ENTRY`      | Update / TP policy             | Exchange adapter | Fire-and-forget possibile    |
| `MOVE_STOP_TO_BREAKEVEN`    | Update / auto BE               | Exchange adapter | Sposta SL a entry            |
| `MOVE_STOP`                 | Update SET_STOP PRICE/TP_LEVEL | Exchange adapter | Sposta SL a prezzo specifico |
| `CLOSE_FULL`                | Update close full              | Exchange adapter | Chiusura completa            |
| `CLOSE_PARTIAL`             | Update close partial           | Exchange adapter | Chiusura percentuale/qty     |
| `REBUILD_PARTIAL_TPS`       | Dopo fill/cancel               | Exchange adapter | Ricalcolo TP parziali        |
| `SET_POSITION_TPSL_FULL`    | Post entry                     | Exchange adapter | TP/SL attached full position |
| `SET_POSITION_TPSL_PARTIAL` | Post entry                     | Exchange adapter | TP parziali                  |

---

## 13. Fire-and-forget commands

Alcuni comandi non producono un ordine pollabile classico o hanno conferma difficile da ottenere subito.

Esempi:

```text
CANCEL_PENDING_ENTRY
MOVE_STOP_TO_BREAKEVEN
MOVE_STOP
MOVE_POSITION_STOP
REBUILD_PARTIAL_TPS
SET_POSITION_TPSL_PARTIAL
SET_POSITION_TPSL_FULL
```

Questi possono essere marcati `DONE` subito dopo la chiamata API riuscita.

Rischio: `DONE` non significa necessariamente “stato exchange verificato”. Significa solo “richiesta inviata senza errore immediato”.

Per produzione seria serve distinguere:

```text
SENT
API_ACCEPTED
EXCHANGE_CONFIRMED
RECONCILED
```

---

## 14. Layer 8 — Exchange Adapter

### Scopo

Tradurre comandi astratti del bot in chiamate concrete exchange.

### Input

```text
ExecutionCommand
Resolved account
Symbol
Side
Qty
Price
SL/TP
Position mode
```

### Output

```text
Exchange API response
Normalized exchange event
```

### Responsabilità

* compatibilità exchange;
* normalizzazione simboli;
* precisione qty/price;
* min notional;
* leverage;
* position mode;
* reduce only;
* hedge mode;
* attached TP/SL;
* partial TP;
* position-level stop;
* error mapping.

### Per Bybit

Casi delicati:

```text
- attached SL/TP su entry
- trading_stop position-level
- partial TP
- full position TP/SL
- hedge mode
- slSize/tpSize
- reduceOnly
```

Il gateway non dovrebbe conoscere dettagli Bybit profondi. Questi devono stare nell’adapter.

---

## 15. Layer 9 — Exchange Events / Reconciliation

### Scopo

Riportare la realtà exchange nel sistema.

### Input

```text
websocket events
polling results
manual reconciliation
exchange order/trade/position snapshot
```

### Output

```text
ops_exchange_events
```

### Eventi principali

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_FULL_FILLED
CLOSE_PARTIAL_FILLED
STOP_MOVED_CONFIRMED
PENDING_ENTRY_CANCELLED_CONFIRMED
ORDER_CANCELLED
ORDER_REJECTED
POSITION_CLOSED
```

### Regola

L’execution gateway invia comandi.

La reconciliation dice cosa è successo davvero.

Non vanno confusi.

---

## 16. Lifecycle Event Worker

### Scopo

Consumare `ops_exchange_events` e aggiornare la trade chain.

### Input

```text
ops_exchange_events where status = NEW
```

### Output

```text
updated ops_trade_chains
new ops_lifecycle_events
new ops_execution_commands opzionali
```

### Flusso

```text
NEW exchange event
  ↓
load trade chain
  ↓
ignore if terminal
  ↓
enrich event if needed
  ↓
LifecycleEventProcessor
  ↓
update chain state
  ↓
write lifecycle events
  ↓
create follow-up commands
  ↓
mark exchange event processed
```

---

## 17. Gestione eventi exchange principali

### 17.1 `ENTRY_FILLED`

Effetti:

```text
- aggiorna filled entry qty
- aggiorna average entry price
- imposta stato OPEN se prima fill
- aggiorna open qty
- aggiorna risk remaining
- può creare comandi post-fill
```

Possibili comandi successivi:

```text
SET_POSITION_TPSL_FULL
SET_POSITION_TPSL_PARTIAL
REBUILD_PARTIAL_TPS
```

---

### 17.2 `TP_FILLED`

Effetti:

```text
- aggiorna closed qty
- aggiorna open qty
- aggiorna realized PnL se disponibile
- marca PARTIALLY_CLOSED o CLOSED
- può cancellare averaging pending
- può muovere SL a BE
```

Possibili comandi successivi:

```text
CANCEL_PENDING_ENTRY
MOVE_STOP_TO_BREAKEVEN
REBUILD_PARTIAL_TPS
```

---

### 17.3 `SL_FILLED`

Effetti:

```text
- chiude posizione o residuo
- marca chain CLOSED
- calcola risultato se disponibile
- emette log finale
```

---

### 17.4 `CLOSE_FULL_FILLED`

Effetti:

```text
- marca chain CLOSED
- salva close reason MANUAL_CLOSE / EXTERNAL_UPDATE
- aggiorna PnL se disponibile
```

---

### 17.5 `CLOSE_PARTIAL_FILLED`

Effetti:

```text
- riduce open qty
- aumenta closed qty
- mantiene chain OPEN/PARTIALLY_CLOSED
- può ricalcolare TP/SL residui
```

---

## 18. Control Plane

### Scopo

Controllare e osservare il runtime via Telegram.

### Stream principali

```text
COMMANDS_REPLY
CLEAN_LOG
TECH_LOG
```

### 18.1 `COMMANDS_REPLY`

Risposte a comandi utente:

```text
/status
/trades
/trade #id
/health
/control
/reviews
/pnl
/logs
/debug_on
/debug_off
/version
/pause
/resume
/start
/block
/unblock
```

### 18.2 `CLEAN_LOG`

Log leggibili operativi:

```text
SIGNAL_ACCEPTED
ENTRY_FILLED
TP_FILLED
SL_FILLED
UPDATE_APPLIED
POSITION_CLOSED
REVIEW_REQUIRED
```

Esempio:

```text
✅ SIGNAL ACCEPTED

#12 | BTCUSDT | LONG | WAITING_ENTRY
Trader: maxgold
Risk: 2.0%
Entry:
▪️ Entry_1: Market
▪️ Entry_2: 65,500 Limit
SL: 64,800
TP:
▪️ TP1: 66,300
▪️ TP2: 67,000
```

### 18.3 `TECH_LOG`

Log tecnici:

```text
startup
shutdown
adapter error
reconciliation warning
parser error
db migration error
telegram connection error
```

---

## 19. Tabelle obbligatorie per capire il sistema

### 19.1 Layer → input → output → DB

| Layer                  | Input                    | Output                      | Legge                                     | Scrive                                                               |
| ---------------------- | ------------------------ | --------------------------- | ----------------------------------------- | -------------------------------------------------------------------- |
| Telegram Listener      | Telegram event           | RawIngestItem               | Telegram                                  | —                                                                    |
| Intake                 | RawIngestItem            | ParserDispatchCandidate     | config trader/source                      | `raw_messages`                                                       |
| Trader Resolution      | raw metadata             | trader_id/profile           | config                                    | —                                                                    |
| Parser Pipeline        | ParserDispatchCandidate  | CanonicalParseResult        | parser profiles                           | `canonical_messages`, `parser_runs`                                  |
| Enrichment             | CanonicalParseResult     | EnrichedCanonicalMessage    | trader/account/risk config                | `enriched_canonical_messages`                                        |
| Lifecycle Entry Gate   | Enriched SIGNAL          | TradeChain + commands       | parser_db, ops_db                         | `ops_trade_chains`, `ops_lifecycle_events`, `ops_execution_commands` |
| Lifecycle Update       | Enriched UPDATE          | lifecycle events + commands | `ops_trade_chains`                        | `ops_lifecycle_events`, `ops_execution_commands`                     |
| Execution Gateway      | ExecutionCommand         | exchange calls/events       | `ops_execution_commands`, account config  | `ops_execution_commands`, `ops_exchange_events`                      |
| Reconciliation         | exchange snapshot/events | ExchangeEvent               | exchange                                  | `ops_exchange_events`                                                |
| Lifecycle Event Worker | ExchangeEvent            | updated chain               | `ops_exchange_events`, `ops_trade_chains` | `ops_trade_chains`, `ops_lifecycle_events`, `ops_execution_commands` |
| Control Plane          | commands/events          | Telegram replies/logs       | ops_db/parser_db                          | `ops_control_state`                                                  |

---

### 19.2 Event type → producer → consumer

| Event type                          | Producer                     | Consumer               | Effetto                                |
| ----------------------------------- | ---------------------------- | ---------------------- | -------------------------------------- |
| `SIGNAL_ACCEPTED`                   | Lifecycle Entry Gate         | Control Plane          | clean log nuovo segnale                |
| `TRADE_CHAIN_CREATED`               | Lifecycle Entry Gate         | Control Plane / audit  | chain inizializzata                    |
| `ENTRY_FILLED`                      | Exchange sync/reconciliation | Lifecycle Event Worker | chain passa a OPEN                     |
| `TP_FILLED`                         | Exchange sync/reconciliation | Lifecycle Event Worker | riduce posizione / BE / cancel pending |
| `SL_FILLED`                         | Exchange sync/reconciliation | Lifecycle Event Worker | chiude chain                           |
| `CLOSE_FULL_FILLED`                 | Exchange sync/reconciliation | Lifecycle Event Worker | chiusura manuale/update                |
| `CLOSE_PARTIAL_FILLED`              | Exchange sync/reconciliation | Lifecycle Event Worker | chiusura parziale                      |
| `STOP_MOVED_CONFIRMED`              | Exchange sync/reconciliation | Lifecycle Event Worker | aggiorna stop corrente                 |
| `PENDING_ENTRY_CANCELLED_CONFIRMED` | Exchange sync/reconciliation | Lifecycle Event Worker | aggiorna pending entries               |
| `REVIEW_REQUIRED`                   | Enrichment/Lifecycle         | Control Plane          | richiesta revisione manuale            |
| `UPDATE_APPLIED`                    | Lifecycle Update             | Control Plane          | log update applicato                   |

---

### 19.3 Command type → producer → gateway path → evento atteso

| Command                     | Producer                     | Gateway action             | Evento atteso                        |
| --------------------------- | ---------------------------- | -------------------------- | ------------------------------------ |
| `PLACE_ENTRY_MARKET`        | Lifecycle Entry Gate         | place market order         | `ENTRY_FILLED`                       |
| `PLACE_ENTRY_LIMIT`         | Lifecycle Entry Gate         | place limit order          | `ENTRY_FILLED` oppure pending        |
| `CANCEL_PENDING_ENTRY`      | Lifecycle Update / TP policy | cancel order(s)            | `PENDING_ENTRY_CANCELLED_CONFIRMED`  |
| `MOVE_STOP_TO_BREAKEVEN`    | Lifecycle Update / TP policy | amend stop                 | `STOP_MOVED_CONFIRMED`               |
| `MOVE_STOP`                 | Lifecycle Update             | amend stop to price        | `STOP_MOVED_CONFIRMED`               |
| `CLOSE_FULL`                | Lifecycle Update             | reduce-only market close   | `CLOSE_FULL_FILLED`                  |
| `CLOSE_PARTIAL`             | Lifecycle Update             | reduce-only partial close  | `CLOSE_PARTIAL_FILLED`               |
| `REBUILD_PARTIAL_TPS`       | Lifecycle Event Processor    | cancel/recreate partial TP | TP order events                      |
| `SET_POSITION_TPSL_FULL`    | Lifecycle post-entry         | set full position TP/SL    | exchange confirmation/reconciliation |
| `SET_POSITION_TPSL_PARTIAL` | Lifecycle post-entry         | set partial TP/SL          | exchange confirmation/reconciliation |

---

## 20. Stati principali della TradeChain

Esempio di stati logici:

```text
NEW
WAITING_ENTRY
OPEN
PARTIALLY_CLOSED
CLOSED
CANCELLED
REJECTED
REVIEW_REQUIRED
ERROR
```

### Transizioni tipiche

```text
SIGNAL accepted
  ↓
WAITING_ENTRY
  ↓ ENTRY_FILLED
OPEN
  ↓ TP_FILLED
PARTIALLY_CLOSED
  ↓ TP_FILLED final / SL_FILLED / CLOSE_FULL_FILLED
CLOSED
```

Altro caso:

```text
SIGNAL accepted
  ↓
WAITING_ENTRY
  ↓ INVALIDATE_SETUP / CANCEL_PENDING
CANCELLED
```

Caso review:

```text
UPDATE ambiguous
  ↓
REVIEW_REQUIRED
  ↓ manual accept
UPDATE_APPLIED
  ↓
commands created
```

---

## 21. Manual Review

Manual review non deve essere hardcoded in un singolo punto.

Deve essere trattata come gate modulare applicabile a:

```text
SIGNAL
UPDATE
CLOSE_FULL
CLOSE_PARTIAL
MODIFY_ENTRY
MOVE_SL
CANCEL_PENDING
MULTI_CHAIN_UPDATE
AMBIGUOUS_TARGET
```

### Config consigliata

Esempio concettuale:

```yaml
manual_review:
  enabled: true

  signals:
    mode: "auto"        # auto | always_review | never
    on_startup_recovered_messages: "review"

  updates:
    close_full: "review"
    close_partial: "review"
    move_sl_to_be: "auto"
    move_sl_to_price: "review"
    cancel_pending: "auto"
    modify_entries: "review"
    multi_chain_update: "review"
    ambiguous_target: "review"
```

### Output review

Quando serve review:

```text
ops_review_items
clean log con bottoni:
[Accept] [Reject]
```

L’azione manuale deve produrre un evento auditabile:

```text
REVIEW_ACCEPTED
REVIEW_REJECTED
```

Non deve modificare direttamente exchange senza passare dal lifecycle.

---

## 22. Identità segnale/update

Questo è il punto più importante da migliorare.

### Problema attuale

Il sistema può risolvere target update usando:

```text
symbol
side
trader
reply_to
telegram id
scope ALL_LONG/ALL_SHORT/ALL_POSITIONS
fallback single-chain
```

Ma questi criteri sono insufficienti se:

```text
- stesso trader ha più trade aperti sullo stesso symbol;
- update è generico;
- messaggio non è reply;
- trader usa frasi tipo "стоп в бу";
- update si riferisce a un batch di segnali precedenti;
- update arriva dopo restart;
- ci sono messaggi recuperati da history.
```

### Proposta

Aggiungere già a intake/parser un blocco strutturato:

```json
{
  "signal_identity": {
    "source_type": "telegram",
    "source_chat_id": "-100123",
    "topic_id": "456",
    "telegram_message_id": "789",
    "reply_to_message_id": null,
    "trader_id": "trader_a",
    "parser_profile": "ru_crypto",
    "source_message_url": "https://t.me/c/..."
  },
  "detected_components": {
    "symbols": ["BTCUSDT"],
    "sides": ["LONG"],
    "entries": ["market", "limit"],
    "sl": true,
    "tp_count": 3,
    "scope_markers": [],
    "explicit_refs": []
  }
}
```

Per update:

```json
{
  "update_identity": {
    "source_type": "telegram",
    "source_chat_id": "-100123",
    "topic_id": "456",
    "telegram_message_id": "800",
    "reply_to_message_id": "789",
    "trader_id": "trader_a",
    "parser_profile": "ru_crypto"
  },
  "target_candidates": {
    "symbols": ["BTCUSDT"],
    "sides": ["LONG"],
    "reply_to_message_id": "789",
    "scope": "REPLY_OR_SYMBOL",
    "actions": ["MOVE_SL_TO_BE"]
  }
}
```

### Beneficio

Il lifecycle non deve “indovinare” troppo.

Riceve già:

```text
- identità sorgente;
- componenti identificati;
- riferimenti espliciti;
- ambito update;
- candidati target.
```

Poi il lifecycle decide solo se l’associazione è sicura o richiede review.

---

## 23. Errori architetturali da evitare

### 23.1 Parser che decide execution

Sbagliato:

```text
parser → CLOSE_FULL command
```

Corretto:

```text
parser → Canonical UPDATE CLOSE_FULL
enrichment → PASS/REVIEW/BLOCK
lifecycle → CLOSE_FULL command
execution gateway → exchange
```

---

### 23.2 Execution gateway che decide logica trading

Sbagliato:

```text
gateway decide se cancellare averaging dopo TP1
```

Corretto:

```text
lifecycle decide CANCEL_PENDING_ENTRY
gateway esegue solo il comando
```

---

### 23.3 Control plane che modifica direttamente exchange

Sbagliato:

```text
Telegram button → exchange.close_position()
```

Corretto:

```text
Telegram button
  ↓
review/control event
  ↓
lifecycle
  ↓
execution command
  ↓
execution gateway
  ↓
exchange
```

---

### 23.4 Reconciliation che crea stati senza lifecycle

Sbagliato:

```text
reconciliation aggiorna direttamente chain a CLOSED
```

Corretto:

```text
reconciliation → ops_exchange_events
lifecycle event worker → aggiorna chain
```

---

## 24. Punti deboli attuali

### 24.1 Documentazione in drift

Alcuni documenti storici possono dire che Execution Gateway o Control Plane non sono implementati, mentre il codice corrente li importa e li cabla.

Regola pratica:

```text
docs storici = utili per intenzione
codice corrente = fonte reale
ARCHITECTURE_CURRENT.md = mappa aggiornata
```

### 24.2 Target update fragile

Il caso multi-chain dello stesso trader/symbol resta il rischio principale.

Mitigazione minima:

```text
se update target non è deterministico → REVIEW_REQUIRED
```

Mitigazione migliore:

```text
identità strutturata messaggio/update già da intake/parser
```

### 24.3 Fire-and-forget troppo ottimistico

Marcare `DONE` dopo API call riuscita può nascondere mismatch exchange.

Meglio distinguere:

```text
COMMAND_SENT
API_ACCEPTED
EXCHANGE_CONFIRMED
RECONCILED
```

### 24.4 Dipendenza forte dal DB polling

Il runtime è robusto ma molto DB-driven.

Pregi:

```text
- recovery più semplice
- audit trail
- debug più facile
```

Difetti:

```text
- più latenza
- più stati intermedi
- rischio doppia elaborazione se idempotenza non è rigorosa
```

### 24.5 Portabilità Linux

Se in `main.py` o altrove viene usato:

```python
ctypes.windll.kernel32
```

è codice Windows-only.

Su VPS Linux va sostituito con lock cross-platform:

```text
Windows → msvcrt / portalocker
Linux   → fcntl
oppure  → lockfile atomico cross-platform
```

---

## 25. Sequenza completa: nuovo segnale

```text
1. Telegram riceve messaggio:
   "BTC LONG, entry, SL, TP..."

2. Listener crea RawIngestItem.

3. Intake salva raw_messages.

4. Trader Resolution identifica trader e parser_profile.

5. Parser Pipeline produce CanonicalMessage:
   primary_class = SIGNAL
   symbol = BTCUSDT
   side = LONG
   entries = [...]
   sl = ...
   tp = [...]

6. Enrichment applica policy:
   trader enabled?
   symbol allowed?
   SL presente?
   entry structure ammessa?
   risk config valida?

7. Enrichment salva EnrichedCanonicalMessage:
   decision = PASS
   lifecycle_processed = False

8. Lifecycle Entry Gate consuma il messaggio.

9. Lifecycle crea TradeChain:
   state = WAITING_ENTRY

10. Lifecycle scrive eventi:
    SIGNAL_ACCEPTED
    TRADE_CHAIN_CREATED

11. Lifecycle crea ExecutionCommand:
    PLACE_ENTRY_MARKET
    PLACE_ENTRY_LIMIT
    SET_POSITION_TPSL_FULL / PARTIAL

12. Execution Gateway legge comandi pending.

13. Gateway manda ordini all’exchange adapter.

14. Exchange/reconciliation produce ENTRY_FILLED.

15. Lifecycle Event Worker consuma ENTRY_FILLED.

16. Chain passa a OPEN.

17. Control Plane manda clean log.
```

---

## 26. Sequenza completa: update MOVE_SL_TO_BE

```text
1. Telegram riceve:
   "стоп в бу"

2. Intake salva raw message.

3. Parser produce CanonicalMessage:
   primary_class = UPDATE
   action = SET_STOP
   stop_ref = ENTRY / BREAKEVEN

4. Enrichment verifica update ammesso.

5. Lifecycle Update Resolver cerca target chain:
   - reply_to?
   - explicit refs?
   - symbol?
   - trader?
   - single open chain?

6. Se target unico:
   crea lifecycle event UPDATE_APPLIED
   crea command MOVE_STOP_TO_BREAKEVEN

7. Se target ambiguo:
   crea REVIEW_REQUIRED
   manda clean log con bottoni

8. Execution Gateway invia modifica SL.

9. Reconciliation conferma STOP_MOVED_CONFIRMED.

10. Lifecycle aggiorna current_stop.

11. Control Plane manda clean log:
    "SL moved to BE"
```

---

## 27. Sequenza completa: TP hit

```text
1. Exchange produce fill su TP.

2. WebSocket/polling/reconciliation salva:
   ops_exchange_events.TP_FILLED

3. Lifecycle Event Worker consuma evento.

4. Lifecycle aggiorna:
   closed_qty
   open_qty
   realized_pnl
   current_state

5. Lifecycle valuta policy:
   cancel pending after TP?
   move SL to BE?
   rebuild partial TPs?

6. Lifecycle crea nuovi ExecutionCommand se necessari:
   CANCEL_PENDING_ENTRY
   MOVE_STOP_TO_BREAKEVEN
   REBUILD_PARTIAL_TPS

7. Execution Gateway invia comandi.

8. Control Plane manda clean log:
   TP filled / position partially closed / risk reduced
```

---

## 28. Sequenza completa: close full manuale da update

```text
1. Telegram riceve:
   "закрываем позицию полностью"

2. Parser:
   primary_class = UPDATE
   action = CLOSE
   close_scope = FULL

3. Enrichment:
   verifica se CLOSE_FULL è auto/review/block

4. Se review:
   crea REVIEW_REQUIRED
   bottoni Accept/Reject

5. Se accepted o auto:
   Lifecycle risolve target chain

6. Lifecycle crea command:
   CLOSE_FULL

7. Execution Gateway manda reduce-only market close.

8. Exchange produce fill:
   CLOSE_FULL_FILLED

9. Lifecycle Event Worker:
   chain → CLOSED
   close_reason = MANUAL_UPDATE / TRADER_UPDATE

10. Control Plane:
   log finale con PnL, fee, ROI se disponibili
```

---

## 29. Struttura logica delle cartelle

Mappa concettuale:

```text
src/runtime_v2
│
├── intake
│   └── normalizzazione messaggi e dispatch parser
│
├── trader_resolution
│   └── associazione messaggio → trader/profile
│
├── parser_pipeline
│   └── wrapper runtime parser → canonical message
│
├── signal_enrichment
│   └── policy stateless su segnali/update
│
├── lifecycle
│   ├── entry gate
│   ├── update gate
│   ├── event processor
│   ├── state transitions
│   └── command generation
│
├── execution_gateway
│   ├── command reader
│   ├── idempotency
│   ├── adapter routing
│   └── exchange call orchestration
│
├── exchange
│   ├── adapters
│   ├── websocket/polling
│   └── normalization
│
├── reconciliation
│   └── ricostruzione stato da exchange
│
├── control_plane
│   ├── telegram bot commands
│   ├── clean logs
│   ├── tech logs
│   └── review actions
│
└── persistence
    ├── parser db access
    ├── ops db access
    └── migrations
```

---

## 30. Regola di modifica del sistema

Quando si aggiunge una feature, decidere prima in quale layer deve stare.

### Esempio: aggiungere `MOVE_SL_TO_TP1`

Corretto:

```text
Parser:
  riconosce frase "стоп на первый тейк"

CanonicalMessage:
  UPDATE SET_STOP target_ref=TP_LEVEL level=1

Enrichment:
  verifica se SET_STOP TP_LEVEL ammesso

Lifecycle:
  risolve TP1 price dalla chain
  crea MOVE_STOP price=<tp1_price>

Execution Gateway:
  invia modifica stop

Reconciliation:
  conferma STOP_MOVED_CONFIRMED

Control Plane:
  log "SL moved to TP1"
```

Sbagliato:

```text
Parser modifica direttamente stop
Gateway cerca TP1 dentro raw message
Control button chiama exchange direttamente
```

---

## 31. Checklist prima di modificare runtime_v2

Prima di toccare codice, rispondere a queste domande:

```text
1. Il cambiamento riguarda parsing, policy, lifecycle, execution o log?
2. Qual è il contratto input/output?
3. Quale tabella viene letta?
4. Quale tabella viene scritta?
5. Serve idempotenza?
6. Serve review manuale?
7. Cosa succede dopo restart?
8. Cosa succede se exchange risponde ma il bot crasha?
9. Cosa succede se l’evento exchange arriva due volte?
10. Cosa succede se più chain sono target compatibili?
```

Se non sai rispondere, la modifica è prematura.

---

## 32. Priorità di miglioramento consigliate

### Priorità 1 — Documento contratti

Creare/tenere aggiornate tre tabelle:

```text
Layer → input → output → DB
Event type → producer → consumer
Command type → producer → gateway path → expected event
```

Questo documento copre la base, ma deve restare allineato al codice.

### Priorità 2 — Identità stabile segnale/update

Aggiungere `source_identity` e `detected_components` già da intake/parser.

Obiettivo:

```text
meno euristiche nel lifecycle
meno ambiguità negli update
migliore audit
migliore recovery
```

### Priorità 3 — Manual review modulare

Non hardcodare review solo per alcuni casi.

Creare gate configurabile:

```text
signal review
update review
close review
multi-chain review
startup recovered messages review
```

### Priorità 4 — Stati execution più precisi

Separare:

```text
CREATED
QUEUED
SENT
API_ACCEPTED
DONE
FAILED
RECONCILED
```

`DONE` da solo è troppo ambiguo.

### Priorità 5 — Test end-to-end per casi critici

Casi minimi:

```text
new signal → entry filled → TP1 → move BE → close final
new signal → cancel pending after TP
update MOVE_SL_TO_BE
update CLOSE_FULL
multi-chain ambiguous update
restart con commands pending
restart con exchange position già aperta
```

---

## 33. Conclusione

`runtime_v2` è strutturato correttamente come pipeline a livelli:

```text
message semantics → policy → lifecycle → execution → exchange truth → lifecycle update → control/log
```

La direzione architetturale è buona.

Il rischio principale non è la mancanza di moduli, ma la coerenza tra:

```text
parser output
enrichment decision
lifecycle target resolution
execution idempotency
exchange reconciliation
clean logs
```

Il miglioramento più utile adesso non è rifare la struttura, ma ridurre le euristiche nel collegamento update → chain introducendo identità strutturata del messaggio già nei primi livelli.

Regola finale:

```text
Parser capisce.
Enrichment filtra.
Lifecycle decide.
Execution esegue.
Exchange conferma.
Control Plane mostra.
```
