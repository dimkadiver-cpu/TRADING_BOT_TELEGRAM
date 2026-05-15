# PRD 04 - Lifecycle Entry Gate + Executor-Neutral Command Outbox

**Data:** 2026-05-15  
**Stato:** design approvato, revisione applicata 2026-05-15  
**Deriva da:** PRD-00 runtime v2 architecture, PRD-03 Signal Enrichment Layer  
**Ambito:** Lifecycle stateful, risk/capacity, universal exchange data ports, command outbox neutrale, lifecycle event processor minimo  
**Fuori ambito:** adapter Hummingbot reale, websocket exchange, execution gateway concreto, dashboard, notifiche Telegram, reconciliation reale

---

## 1. Scopo

PRD-04 introduce il primo layer operativo stateful dopo il Signal Enrichment Layer.

```
parser.sqlite3.enriched_canonical_messages
        |
        v
LifecycleGateWorker
        |
        v
RiskCapacityEngine + ExchangeDataPort
        |
        v
ops.sqlite3
  ops_trade_chains
  ops_lifecycle_events
  ops_execution_commands
  ops_*_snapshots
  ops_control_state
```

Il layer decide e registra cosa deve succedere, ma non esegue ordini reali. I comandi prodotti sono neutrali rispetto all'esecutore e restano `PENDING` in `ops_execution_commands`. PRD-05 li tradurra verso Hummingbot o un altro executor.

---

## 2. Responsabilita

PRD-04 possiede lo stato operativo interno del ciclo di vita.

Include:

- `LifecycleEntryGate`: consuma record PRD-03 eleggibili.
- `RiskCapacityEngine`: calcola size e valida limiti usando config, stato corrente e snapshot live.
- `TradeChainRepository`: persiste chain, stato corrente, eventi e snapshot.
- `ExecutionCommandOutbox`: salva comandi neutrali `PENDING`.
- `LifecycleEventProcessor`: applica eventi normalizzati come `ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`, `TIMEOUT_REACHED`.
- `ExchangeDataPort`: interfaccia universale per account, mercato, ordini e posizioni.
- `StaticExchangeDataPort`: adapter mock/statico per test PRD-04.
- `OperationalControlState`: hook interno per bloccare nuove aperture o richiedere azioni emergency senza dipendere da Telegram bot.

Fuori scope:

- chiamate reali Hummingbot;
- websocket exchange;
- adapter executor concreto;
- sync reale ordini/fill/posizioni;
- reconciliation reale;
- dashboard e notifiche Telegram.
- producer concreto dei comandi admin, incluso Telegram bot.

Regola guida:

```
PRD-04 decide e registra.
PRD-05 traduce ed esegue.
Exchange resta la verita finale.
```

---

## 3. Input da PRD-03

PRD-04 consuma solo:

```sql
SELECT *
FROM enriched_canonical_messages
WHERE lifecycle_processed = 0
  AND enrichment_decision = 'PASS'
  AND primary_class IN ('SIGNAL', 'UPDATE')
ORDER BY created_at ASC;
```

Campi gia disponibili da PRD-03:

- `enriched_signal_json`: symbol, side, entry, TP, SL, pesi entry.
- `enriched_actions_json`: update ammessi con targeting e action items.
- `management_plan_json`: BE trigger, be_buffer_pct, timeout, cancel policy, close distribution, protective SL mode.
- `policy_snapshot_json`: snapshot della policy effettiva, inclusi account cap completi.
- `policy_version`.
- `account_id`.
- `trader_id`.
- `raw_message_id`.
- `canonical_message_id`.

Gap da chiudere prima di PRD-04: `policy_snapshot_json` deve contenere l'intero `AccountConfig` globale (`capital_base_usdt`, `max_leverage`, `max_capital_at_risk_pct`, `hard_max_per_signal_risk_pct`). Se il campo non e completo, PRD-04 usa il config loader come fallback esplicito e registra un warning nell'evento. La soluzione definitiva e estendere PRD-03 a produrre uno snapshot sufficiente a ricostruire la decisione senza dipendere dalla config corrente.

---

## 4. DB operativo

Restano due database fisici:

```
parser.sqlite3
  raw_messages
  canonical_messages
  enriched_canonical_messages

ops.sqlite3
  lifecycle working state
  execution-neutral commands
  exchange/account/market snapshots
  audit/event history
```

Non si crea un terzo DB storico in PRD-04. Stato corrente e storia restano nello stesso `ops.sqlite3` per mantenere transazioni coerenti.

Regola:

```
Nessun move tra active/history tables.
Stato corrente aggiornabile + eventi append-only.
Le operazioni aperte sono una query/view, non una tabella separata.
```

Le chain aperte si distinguono con `lifecycle_state`, non spostando record:

```sql
CREATE VIEW view_active_trade_chains AS
SELECT *
FROM ops_trade_chains
WHERE lifecycle_state NOT IN ('CLOSED', 'CANCELLED', 'EXPIRED');
```

### Atomicita cross-DB

`LifecycleGateWorker` scrive su `ops.sqlite3` e marca `lifecycle_processed=1` su `parser.sqlite3`. SQLite non supporta transazioni cross-DB, quindi il pattern e **ops-first**:

```
1. Scrivi su ops.sqlite3 (chain + eventi + comandi) — transazione atomica.
2. Marca lifecycle_processed=1 su parser.sqlite3 — solo dopo commit ops.
```

In caso di crash dopo il commit ops ma prima del mark, al riavvio il worker rileva `lifecycle_processed=0` e riprocessa. I duplicati sono bloccati silenziosamente da:
- `UNIQUE` constraint su `ops_trade_chains.source_enrichment_id`
- `UNIQUE` constraint su `ops_lifecycle_events.idempotency_key`
- `UNIQUE` constraint su `ops_execution_commands.idempotency_key`

Il secondo passaggio produce solo il mark senza creare nuovi record.

---

## 5. Tabelle PRD-04

### 5.1 `ops_trade_chains`

Stato corrente aggiornabile della chain.

```sql
CREATE TABLE ops_trade_chains (
    trade_chain_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_enrichment_id    INTEGER NOT NULL UNIQUE,
    canonical_message_id    INTEGER NOT NULL,
    raw_message_id          INTEGER NOT NULL,
    trader_id               TEXT NOT NULL,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,          -- LONG | SHORT
    lifecycle_state         TEXT NOT NULL,          -- vedi §6.1
    entry_mode              TEXT NOT NULL,          -- ONE_SHOT | TWO_STEP | RANGE | LADDER
    entry_avg_price         REAL,
    current_stop_price      REAL,
    expected_stop_price     REAL,
    be_protection_status    TEXT NOT NULL DEFAULT 'NOT_PROTECTED',  -- NOT_PROTECTED | BE_MOVE_PENDING | PROTECTED
    entry_timeout_at        TEXT,
    management_plan_json    TEXT NOT NULL,
    risk_snapshot_json      TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
```

`entry_mode` rispecchia `EntryStructure` del contratto canonico v1: `ONE_SHOT`, `TWO_STEP`, `RANGE`, `LADDER`.

`be_protection_status`:
- `NOT_PROTECTED`: stop non ancora a breakeven.
- `BE_MOVE_PENDING`: comando `MOVE_STOP_TO_BREAKEVEN` emesso, in attesa di conferma da PRD-05.
- `PROTECTED`: stop confermato a BE o meglio dall'evento exchange.

### 5.2 `ops_lifecycle_events`

Storia append-only di decisioni e transizioni.

```sql
CREATE TABLE ops_lifecycle_events (
    event_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER,
    event_type              TEXT NOT NULL,
    source_type             TEXT NOT NULL,
    source_id               TEXT,
    previous_state          TEXT,
    next_state              TEXT,
    payload_json            TEXT NOT NULL DEFAULT '{}',
    idempotency_key         TEXT NOT NULL UNIQUE,
    created_at              TEXT NOT NULL
);
```

### 5.3 `ops_execution_commands`

Outbox neutrale verso PRD-05.

```sql
CREATE TABLE ops_execution_commands (
    command_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER NOT NULL,
    command_type            TEXT NOT NULL,
    status                  TEXT NOT NULL,
    payload_json            TEXT NOT NULL,
    idempotency_key         TEXT NOT NULL UNIQUE,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
```

### 5.4 Snapshot usati per decisioni

PRD-04 salva ogni dato live usato per una decisione.

```sql
CREATE TABLE ops_account_snapshots (
    snapshot_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id                  TEXT NOT NULL,
    equity_usdt                 REAL,
    available_balance_usdt      REAL,
    total_open_risk_usdt        REAL,
    total_margin_used_usdt      REAL,
    source                      TEXT NOT NULL,
    captured_at                 TEXT NOT NULL,
    payload_json                TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE ops_market_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    mark_price              REAL,
    bid                     REAL,
    ask                     REAL,
    min_order_size          REAL,
    price_precision         INTEGER,
    qty_precision           INTEGER,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL,
    payload_json            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE ops_order_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT,
    payload_json            TEXT NOT NULL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL
);

CREATE TABLE ops_position_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    payload_json            TEXT NOT NULL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL
);
```

### 5.5 `ops_exchange_events`

Coda di eventi normalizzati. In PRD-04 sono sintetici/test; PRD-05 li alimentera da executor reale.

```sql
CREATE TABLE ops_exchange_events (
    exchange_event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER,
    event_type              TEXT NOT NULL,
    payload_json            TEXT NOT NULL,
    processing_status       TEXT NOT NULL DEFAULT 'NEW',
    idempotency_key         TEXT NOT NULL UNIQUE,
    received_at             TEXT NOT NULL,
    processed_at            TEXT
);
```

### 5.6 `ops_control_state`

Hook minimo per futuri comandi admin. PRD-04 legge questo stato, ma non implementa il bot Telegram che lo modifica.

```sql
CREATE TABLE ops_control_state (
    control_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type              TEXT NOT NULL,  -- GLOBAL | ACCOUNT | TRADER | SYMBOL | SIDE
    scope_value             TEXT,
    execution_pause_mode    TEXT NOT NULL,  -- NONE | BLOCK_NEW_ENTRIES | FULL_STOP
    emergency_action        TEXT,           -- NONE | CLOSE_ALL | CANCEL_PENDING_ALL
    reason                  TEXT,
    created_by              TEXT,
    active                  INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
```

PRD-04 deve leggere solo il record attivo piu specifico applicabile. In caso di conflitto, vince il modo piu restrittivo.

---

## 6. Stati, eventi e comandi

### 6.1 Lifecycle states

```
CREATED
WAITING_ENTRY
OPEN
PARTIALLY_CLOSED
BE_MOVE_PENDING
PROTECTED_BE
CLOSED
CANCELLED
EXPIRED
REVIEW_REQUIRED
ERROR
```

Stati terminali:

```
CLOSED
CANCELLED
EXPIRED
```

Transizioni principali:

```
CREATED           -> WAITING_ENTRY      (ENTRY_COMMAND_CREATED)
WAITING_ENTRY     -> OPEN               (ENTRY_FILLED)
WAITING_ENTRY     -> EXPIRED            (TIMEOUT_REACHED)
WAITING_ENTRY     -> CANCELLED          (CANCEL_PENDING_ENTRY confermato)
OPEN              -> PARTIALLY_CLOSED   (TP_FILLED non finale | CLOSE_PARTIAL confermato)
OPEN              -> BE_MOVE_PENDING    (BE_MOVE_REQUESTED emesso)
OPEN              -> CLOSED             (SL_FILLED | TP_FILLED finale | CLOSE_FULL confermato)
PARTIALLY_CLOSED  -> PARTIALLY_CLOSED   (ulteriore TP_FILLED non finale)
PARTIALLY_CLOSED  -> BE_MOVE_PENDING    (BE_MOVE_REQUESTED emesso)
PARTIALLY_CLOSED  -> CLOSED             (TP_FILLED finale | CLOSE_FULL confermato)
BE_MOVE_PENDING   -> PROTECTED_BE       (BE confermato da evento exchange)
BE_MOVE_PENDING   -> CLOSED             (SL_FILLED | CLOSE_FULL confermato)
PROTECTED_BE      -> CLOSED             (SL_FILLED | TP_FILLED finale | CLOSE_FULL confermato)
```

### 6.2 Lifecycle events

```
SIGNAL_ACCEPTED
TRADE_CHAIN_CREATED
ENTRY_COMMAND_CREATED
ENTRY_FILLED
TP_FILLED                        -- payload: tp_level, is_final
TP_PARTIAL_FILLED                -- alias semantico di TP_FILLED con is_final=false
SL_FILLED
TIMEOUT_REACHED
TELEGRAM_UPDATE_ACCEPTED
BE_MOVE_REQUESTED
NOOP_ALREADY_PROTECTED_BE        -- stop gia a BE o meglio, nessun comando emesso
NOOP_DUPLICATE_COMMAND           -- comando identico gia PENDING/SENT/ACK
NOOP_ALREADY_CLOSED              -- chain gia in stato terminale CLOSED
NOOP_NOT_PENDING                 -- operazione richiede WAITING_ENTRY ma chain e OPEN o altro
NOOP_NO_APPLICABLE_TARGET        -- nessuna chain applicabile per UPDATE batch
REVIEW_REQUIRED
```

### 6.3 Neutral execution commands

```
PLACE_ENTRY
PLACE_PROTECTIVE_STOP
PLACE_TAKE_PROFIT
MOVE_STOP_TO_BREAKEVEN
MOVE_STOP
CANCEL_PENDING_ENTRY
CLOSE_PARTIAL
CLOSE_FULL
```

`ops_trade_chains` dice dove siamo ora. `ops_lifecycle_events` spiega come ci siamo arrivati. `ops_execution_commands` dice cosa PRD-05 deve eseguire.

---

## 7. Universal ports

PRD-04 non importa Hummingbot e non conosce API exchange concrete. Usa port universali.

```python
class ExchangeDataPort:
    def get_account_state(self, account_id: str) -> AccountStateSnapshot: ...
    def get_symbol_market_state(self, account_id: str, symbol: str) -> SymbolMarketSnapshot: ...
    def get_open_orders(self, account_id: str, symbol: str | None = None) -> list[OrderSnapshot]: ...
    def get_open_position(self, account_id: str, symbol: str, side: str) -> PositionSnapshot | None: ...
```

Snapshot minimi:

```text
AccountStateSnapshot
  account_id
  equity_usdt
  available_balance_usdt
  total_open_risk_usdt
  total_margin_used_usdt
  captured_at
  source

SymbolMarketSnapshot
  symbol
  mark_price
  bid
  ask
  min_order_size
  price_precision
  qty_precision
  captured_at
  source

OrderSnapshot
  symbol
  side
  order_role
  status
  price
  qty
  filled_qty
  source_order_id

PositionSnapshot
  symbol
  side
  status
  entry_avg_price
  qty_open
  current_stop_price
  unrealized_pnl
```

Implementazione PRD-04:

```
StaticExchangeDataPort
```

Implementazione PRD-05:

```
HummingbotExchangeDataPort
DirectExchangeDataPort
PaperTradingExchangeDataPort
```

---

## 8. Risk e capacity

`RiskCapacityEngine` controlla:

- `max_concurrent_trades`;
- `max_concurrent_same_symbol`;
- `hedge_mode`;
- posizione gia aperta;
- setup duplicato;
- `max_capital_at_risk_pct`;
- `hard_max_per_signal_risk_pct`;
- `risk_pct_of_capital` o `risk_usdt_fixed` (da `policy_snapshot_json`);
- distanza entry/SL;
- leverage cap;
- prezzo corrente se necessario per MARKET entry.

Regole in assenza di dati live:

```
capital_base_mode=static_config
  usa config loader

capital_base_mode=live_equity
  se AccountStateSnapshot assente/stale -> REVIEW_REQUIRED

MARKET entry richiede prezzo corrente
  se SymbolMarketSnapshot assente/stale -> REVIEW_REQUIRED

LIMIT entry con prezzo e SL nel segnale
  puo calcolare rischio senza prezzo live, salvo precision/min-order checks
```

Ogni snapshot usato per decidere viene salvato in `ops_db`.

---

## 9. Operational control hooks

PRD-04 deve predisporre hook per futuri comandi admin senza implementare il Telegram bot.

Obiettivo:

```
Bloccare nuove aperture senza fermare la gestione delle posizioni gia aperte.
Consentire future azioni emergency come close all / cancel all.
Mantenere audit e idempotenza nel lifecycle.
```

Modalita:

```
NONE:
  comportamento normale

BLOCK_NEW_ENTRIES:
  blocca nuovi SIGNAL (REVIEW_REQUIRED reason=new_entries_paused)
  lascia continuare UPDATE, eventi exchange, timeout, BE automation e chiusure
  le chain esistenti vengono gestite fino alla chiusura naturale

FULL_STOP:
  non crea nuovi comandi non-emergency
  nuovi SIGNAL/UPDATE vanno in REVIEW_REQUIRED
  eventi di sicurezza possono ancora essere registrati
```

Modalita implementate in PRD-04: `NONE`, `BLOCK_NEW_ENTRIES`, `FULL_STOP`.

Modalita riservate per PRD futuri:

```
PAUSE_TRADER:
  blocca nuove aperture solo per trader_id

PAUSE_SYMBOL:
  blocca nuove aperture su symbol specifico

PAUSE_SIDE:
  blocca LONG o SHORT

FORCE_REVIEW_MODE:
  tutto cio che sarebbe eseguibile va in REVIEW_REQUIRED

DISABLE_TELEGRAM_UPDATES:
  ignora update trader, ma continua automation exchange

DISABLE_AUTOMATION:
  disabilita regole automatiche tipo TP -> BE.
  Rischioso: non implementare senza policy di protezione esplicita.

EMERGENCY_PROTECT_ONLY:
  consente solo comandi protettivi tipo MOVE_STOP / PLACE_PROTECTIVE_STOP

LIQUIDATE_AND_STOP:
  CLOSE_ALL + blocco nuove aperture
```

Emergency actions future:

```
CLOSE_ALL:
  genera CLOSE_FULL per chain attive nel perimetro

CANCEL_PENDING_ALL:
  genera CANCEL_PENDING_ENTRY per chain WAITING_ENTRY nel perimetro
```

Perimetri:

```
GLOBAL
ACCOUNT
TRADER
SYMBOL
SIDE
```

Comandi UI/bot rimandati:

```
show open positions
show pending orders
show PnL
show last errors
show review queue
manual close single trade
manual move stop single trade
manual cancel specific order
notification preferences
daily report
```

Regole PRD-04:

```
Prima di accettare un SIGNAL:
  legge OperationalControlState applicabile
  se BLOCK_NEW_ENTRIES -> REVIEW_REQUIRED reason=new_entries_paused
  se FULL_STOP -> REVIEW_REQUIRED reason=full_stop_active

Per UPDATE/eventi su chain esistenti:
  BLOCK_NEW_ENTRIES non blocca la gestione
  FULL_STOP blocca comandi non-emergency e registra REVIEW_REQUIRED

Per emergency actions:
  PRD-04 definisce modello e comandi neutrali
  producer concreto fuori scope
```

Il producer dei comandi admin, incluso Telegram bot, e fuori PRD-04. PRD-04 espone solo lo stato di controllo interno e le regole di consumo.

Ogni cambio o applicazione di controllo operativo deve produrre `ops_lifecycle_events` o audit event equivalente. Nessun comando admin puo modificare stato o generare comandi in modo invisibile.

---

## 10. Flussi

### 10.1 Nuovo SIGNAL

```
1. LifecycleGateWorker legge enriched_canonical_messages PASS/SIGNAL con lifecycle_processed=0.
2. Carica EnrichedCanonicalMessage.
3. Legge OperationalControlState applicabile.
4. Se nuove aperture sono bloccate: REVIEW_REQUIRED e lifecycle_processed=1.
5. Chiede dati necessari a ExchangeDataPort.
6. Salva snapshot normalizzati in ops_db (transazione atomica).
7. RiskCapacityEngine valida risk/capacity.
8. Se passa:
     crea ops_trade_chains
     crea SIGNAL_ACCEPTED / TRADE_CHAIN_CREATED
     crea PLACE_ENTRY / PLACE_PROTECTIVE_STOP / PLACE_TAKE_PROFIT
     commit ops.sqlite3
     marca lifecycle_processed=1 su parser.sqlite3
9. Se non passa:
     crea REVIEW_REQUIRED
     commit ops.sqlite3
     marca lifecycle_processed=1 su parser.sqlite3
```

### 10.2 UPDATE Telegram ammesso da PRD-03

```
1. Worker legge enriched_canonical_messages PASS/UPDATE.
2. Risolve target contro ops_trade_chains aperte.
3. Se target ambiguo o assente: REVIEW_REQUIRED.
4. Se target risolto:
     applica MOVE_STOP, MOVE_STOP_TO_BREAKEVEN, CLOSE_FULL, CLOSE_PARTIAL, CANCEL_PENDING_ENTRY
5. Prima di creare comandi controlla idempotenza semantica.
6. Crea lifecycle_event e command neutrale solo se serve.
7. Commit ops.sqlite3.
8. Marca lifecycle_processed=1 su parser.sqlite3.
```

### 10.3 UPDATE multi-target e globali

Gli update possono risolversi a zero, una o molte chain. PRD-04 li gestisce come batch state-based, non come comando singolo cieco.

Esempi:

```
close all
close all shorts
move SL to BE for all BTC
cancel all pending
chiudete tutte le posizioni del trader
```

Risoluzione target:

```
input:
  enriched_actions.targeting
  trader_id
  symbol se presente
  side se presente
  action_type
  lifecycle_state

output:
  list[trade_chain_id]
```

Regola di ownership:

```
Ogni scope globale e sempre relativo al trader sorgente dell'EnrichedCanonicalMessage.

ALL_SHORT:
  tutte le chain attive con trader_id = enriched.trader_id e side = SHORT

ALL_LONG:
  tutte le chain attive con trader_id = enriched.trader_id e side = LONG

ALL_POSITIONS / ALL_OPEN / ALL_REMAINING:
  tutte le chain non-terminali con trader_id = enriched.trader_id
  (i tre scope sono alias semantici del parser, risoluzione identica in PRD-04)
```

Un update globale non attraversa mai i confini del trader sorgente. Se in futuro servira un comando amministrativo cross-trader, dovra arrivare da un canale/ruolo diverso e non dal parser dei messaggi trader.

Regole:

```
zero match:
  REVIEW_REQUIRED reason=no_update_target

one match:
  applicazione normale

many matches + target globale/multiplo esplicito:
  applicazione batch per-chain

many matches + target non globale:
  REVIEW_REQUIRED reason=ambiguous_update_target
```

Ogni chain produce un evento indipendente:

```
MOVE_STOP_TO_BREAKEVEN:
  gia BE o meglio -> NOOP_ALREADY_PROTECTED_BE
  comando MOVE_STOP_TO_BREAKEVEN PENDING/SENT/ACK attivo -> NOOP_DUPLICATE_COMMAND
  altrimenti -> MOVE_STOP_TO_BREAKEVEN

CLOSE_FULL:
  OPEN/PARTIALLY_CLOSED/BE_MOVE_PENDING/PROTECTED_BE -> CLOSE_FULL
  WAITING_ENTRY -> REVIEW_REQUIRED oppure CANCEL_PENDING_ENTRY secondo policy futura
  CLOSED -> NOOP_ALREADY_CLOSED

CANCEL_PENDING:
  WAITING_ENTRY -> CANCEL_PENDING_ENTRY
  OPEN o altro stato attivo -> NOOP_NOT_PENDING
```

Idempotenza batch:

```
canonical_message_id + trade_chain_id + action_type
```

Un batch non fallisce solo perche una singola chain produce `NOOP`. Se nessuna chain e applicabile, il processor registra `REVIEW_REQUIRED` o `NOOP_NO_APPLICABLE_TARGET` secondo la policy dell'azione.

### 10.4 Evento lifecycle sintetico

```
1. LifecycleEventProcessor riceve evento normalizzato.
2. Carica trade_chain.
3. Applica transizione di stato (vedi §6.1).
4. Applica regole automatiche da management_plan.
5. Produce lifecycle_event e command outbox se necessario.
```

Esempio `TP_FILLED(level=tp1, is_final=false)`:

```
stato -> PARTIALLY_CLOSED

if management_plan.be_trigger == "tp1"
and be_protection_status == NOT_PROTECTED
and non esiste comando MOVE_STOP_TO_BREAKEVEN attivo:
    create MOVE_STOP_TO_BREAKEVEN
    be_protection_status = BE_MOVE_PENDING
    state = BE_MOVE_PENDING
else:
    create NOOP_ALREADY_PROTECTED_BE or NOOP_DUPLICATE_COMMAND
```

Esempio `TP_FILLED(level=tp3, is_final=true)`:

```
stato -> CLOSED
nessun comando BE: posizione chiusa
```

---

## 11. Late, duplicate e out-of-order events

Principio:

```
Un trigger propone un'azione.
Lo stato corrente decide se applicarla, ignorarla o mandarla in review.
```

Per `MOVE_STOP_TO_BREAKEVEN`:

```
Se be_protection_status == PROTECTED o BE o meglio gia confermato:
  non creare comando
  registra NOOP_ALREADY_PROTECTED_BE

Se esiste gia comando MOVE_STOP_TO_BREAKEVEN PENDING/SENT/ACK:
  non creare duplicato
  registra NOOP_DUPLICATE_COMMAND

Se SL non e a BE:
  crea MOVE_STOP_TO_BREAKEVEN
  registra BE_MOVE_REQUESTED
  be_protection_status = BE_MOVE_PENDING
```

Definizione "BE o meglio":

```
LONG:
  current_stop_price >= entry_avg_price * (1 + be_buffer_pct)

SHORT:
  current_stop_price <= entry_avg_price * (1 - be_buffer_pct)
```

`be_buffer_pct` e definito in `management_plan_json` con default `0.0` (puro breakeven).
Rationale: un buffer positivo copre le commissioni di apertura garantendo un minimo profitto prima di dichiarare lo stop protetto.

Se non c'e ancora conferma reale dello stop dall'executor, la chain usa:

```
BE_MOVE_PENDING
```

In questo stato non si generano duplicati finche il comando non fallisce o scade.

---

## 12. Worker model

PRD-04 definisce worker su dati normalizzati.

### 12.1 `LifecycleGateWorker`

Consuma `enriched_canonical_messages` eleggibili e crea chain, eventi, snapshot e command outbox.

### 12.2 `LifecycleEventWorker`

Consuma:

```sql
SELECT *
FROM ops_exchange_events
WHERE processing_status = 'NEW'
ORDER BY received_at
LIMIT 100;
```

Applica eventi normalizzati sintetici tramite `LifecycleEventProcessor`.

### 12.3 `TimeoutWorker`

Controlla solo stati temporalmente maturi:

```sql
SELECT *
FROM ops_trade_chains
WHERE lifecycle_state = 'WAITING_ENTRY'
  AND entry_timeout_at <= CURRENT_TIMESTAMP
LIMIT 100;
```

Per ogni chain scaduta:

```
1. Registra evento TIMEOUT_REACHED.
2. Crea comando CANCEL_PENDING_ENTRY.
3. Transisce lifecycle_state -> EXPIRED.
```

### 12.4 Reconciliation

Solo definita come futura estensione. Non implementata in PRD-04.

Pattern obbligatorio per tutti i worker:

```
input nuovo o stato maturo
  -> carica una chain specifica
  -> applica regole idempotenti
  -> scrive eventi append-only
  -> scrive comandi neutrali PENDING
  -> commit ops.sqlite3
  -> marca lifecycle_processed (se applicabile)
```

Nessun worker deve scansionare tutto `ops_db` per decidere cosa fare.

---

## 13. Package structure

```
src/runtime_v2/lifecycle/
    __init__.py
    models.py                    <- LifecycleState, CommandType, ControlMode, enums, Pydantic snapshots
    ports.py                     <- ExchangeDataPort (ABC), AccountStateSnapshot, SymbolMarketSnapshot, ...
    static_exchange_data_port.py <- StaticExchangeDataPort (implementazione mock per PRD-04)
    risk_capacity.py             <- RiskCapacityEngine
    repositories.py              <- TradeChainRepository, ExecutionCommandOutbox, LifecycleEventRepository
    entry_gate.py                <- LifecycleEntryGate (logica gate + risk), LifecycleGateWorker
    event_processor.py           <- LifecycleEventProcessor
    workers.py                   <- LifecycleEventWorker, TimeoutWorker

db/migrations/
    028_ops_lifecycle_core.sql
```

Regole di import:

- `lifecycle/` puo importare modelli PRD-03 e contratti parser solo per deserializzare input.
- `lifecycle/` non importa Hummingbot, exchange SDK o execution gateway concreto.
- `lifecycle/` scrive solo `ops.sqlite3` e marca `parser.sqlite3.enriched_canonical_messages.lifecycle_processed`.

---

## 14. Acceptance contract

Done significa:

```
Un SIGNAL/UPDATE arricchito da PRD-03 viene consumato in modo idempotente,
produce stato lifecycle in ops_db,
calcola risk/capacity tramite port universali/mock,
produce comandi neutrali PENDING,
e puo reagire almeno a eventi sintetici TP_FILLED/TIMEOUT senza executor reale.
```

Criteri pass/fail:

1. `SIGNAL PASS` crea una sola `ops_trade_chains` idempotente.
2. Risk/capacity manda in `REVIEW_REQUIRED` se limiti superati.
3. `SIGNAL` valido crea comandi neutrali `PLACE_ENTRY`, `PLACE_PROTECTIVE_STOP`, `PLACE_TAKE_PROFIT`.
4. `UPDATE MOVE_STOP_TO_BREAKEVEN` su chain gia protetta produce `NOOP_ALREADY_PROTECTED_BE`, non un comando duplicato.
5. Evento sintetico `TP_FILLED` con `be_trigger` produce `MOVE_STOP_TO_BREAKEVEN` una sola volta.
6. Timeout su `WAITING_ENTRY` produce `CANCEL_PENDING_ENTRY` e stato `EXPIRED`.
7. Tutte le decisioni hanno `ops_lifecycle_events.idempotency_key`.
8. Nessun import o riferimento a Hummingbot/executor concreto.
9. Record PRD-03 viene marcato `lifecycle_processed=1` come ultimo step dopo commit ops riuscito.
10. Snapshot live usati per decisioni sono persistiti in `ops_db`.
11. UPDATE globale esplicito produce batch per-chain con idempotency key indipendente.
12. UPDATE ambiguo che matcha molte chain senza target globale produce `REVIEW_REQUIRED`.
13. Scope globali `ALL_SHORT`, `ALL_LONG`, `ALL_POSITIONS`/`ALL_OPEN`/`ALL_REMAINING` sono sempre limitati al `trader_id` sorgente.
14. `BLOCK_NEW_ENTRIES` blocca nuovi SIGNAL ma non blocca gestione di chain esistenti.
15. `FULL_STOP` manda nuovi SIGNAL/UPDATE non-emergency in `REVIEW_REQUIRED`.
16. Riprocessamento dello stesso `source_enrichment_id` non crea duplicati (idempotency guard).
17. `TP_FILLED` non finale porta in `PARTIALLY_CLOSED`; `TP_FILLED` finale porta in `CLOSED`.

---

## 15. Test minimi

### Unit

- Modelli Pydantic strict per snapshot, chain, events e commands.
- `StaticExchangeDataPort` restituisce snapshot coerenti.
- `RiskCapacityEngine` calcola risk da `static_config`.
- `RiskCapacityEngine` richiede account snapshot per `live_equity`.
- Idempotenza command outbox su `idempotency_key`.
- BE semantic check: be_protection_status PROTECTED o stop gia a BE o meglio -> NOOP.
- BE buffer: `be_buffer_pct=0.001` su LONG con stop esattamente a entry_avg non e "BE o meglio".

### Integration

- `SIGNAL PASS` da PRD-03 -> chain + commands + `lifecycle_processed=1`.
- Doppio processing stesso enrichment -> nessun duplicato (idempotency guard attivo).
- `UPDATE MOVE_STOP_TO_BREAKEVEN` target risolto -> comando o NOOP secondo be_protection_status.
- `UPDATE` globale esplicito su piu chain -> comandi/NOOP per-chain senza duplicati.
- `UPDATE ALL_SHORT` da trader A -> seleziona solo chain SHORT attive di trader A, mai chain di altri trader.
- `UPDATE ALL_REMAINING` da trader A -> identico a `ALL_POSITIONS`, mai chain di altri trader.
- `UPDATE` ambiguo su piu chain -> `REVIEW_REQUIRED`.
- `BLOCK_NEW_ENTRIES` attivo -> nuovo SIGNAL produce `REVIEW_REQUIRED reason=new_entries_paused`.
- `BLOCK_NEW_ENTRIES` attivo -> evento sintetico `TP_FILLED` su chain aperta continua a produrre automation se applicabile.
- `TP_FILLED(is_final=false)` sintetico -> stato `PARTIALLY_CLOSED` + `MOVE_STOP_TO_BREAKEVEN` se be_trigger.
- `TP_FILLED(is_final=true)` sintetico -> stato `CLOSED`.
- Doppio `TP_FILLED` stesso livello -> solo un comando BE.
- `TimeoutWorker` su `WAITING_ENTRY` scaduto -> `CANCEL_PENDING_ENTRY` + stato `EXPIRED`.
- Import isolation: nessun riferimento a Hummingbot/executor concreto.

---

## 16. Rischi e decisioni aperte

### Account config nello snapshot PRD-03

Se `policy_snapshot_json` non contiene account cap completi, PRD-04 usa il config loader come fallback e registra un warning nell'evento lifecycle. Decisione definitiva: estendere PRD-03 a produrre uno snapshot completo prima di andare in produzione. Il fallback al config loader e accettabile solo in fase di sviluppo.

### Conferma reale dei comandi

PRD-04 puo impostare stati attesi come `BE_MOVE_PENDING`, ma la conferma reale arriva solo da PRD-05/exchange events. Finche PRD-05 non esiste, i test usano eventi sintetici.

### Precisioni e min order size

PRD-04 definisce i campi snapshot necessari. La qualita reale dei dati dipende dall'adapter PRD-05.

### Archive DB

Non si crea `ops_archive.sqlite3` in PRD-04. Se lo storico cresce, l'archiviazione sara una futura ottimizzazione.

---

## 17. Output atteso per PRD-05

PRD-05 potra consumare:

```sql
SELECT *
FROM ops_execution_commands
WHERE status = 'PENDING'
ORDER BY created_at
LIMIT 100;
```

Ogni comando e executor-neutral e contiene:

- `command_type`;
- `trade_chain_id`;
- `payload_json`;
- `idempotency_key`;
- stato `PENDING`.

PRD-05 dovra:

- tradurre i comandi verso Hummingbot o altro executor;
- aggiornare `ops_execution_commands.status`;
- scrivere eventi normalizzati in `ops_exchange_events`;
- alimentare `LifecycleEventWorker`.
