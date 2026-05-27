# Exchange-Centric Event System — Design Spec
**Data:** 2026-05-27  
**Stato:** Approvato — pronto per implementazione

---

## Contesto

Il sistema attuale (`ws_fill_watcher.py` + `event_sync.py`) è **bot-centric**: monitora solo gli ordini emessi dal bot tramite `clientOrderId`. Non rileva azioni manuali, usa price matching ±1% per i TP position-level (euristica fragile), e mescola parsing/matching/recovery in un singolo modulo monolitico.

Il nuovo sistema è **exchange-centric**: osserva tutto ciò che succede realmente sull'account, classifica in modo deterministico usando i campi raw Bybit, e mantiene un audit trail completo per il futuro sistema di logging Telegram.

---

## Obiettivo

Sostituire la classificazione euristica con classificazione deterministica basata sui campi nativi Bybit (`createType`, `stopOrderType`, `execType`), aggiungere rilevamento di eventi manuali, e separare le responsabilità in moduli testabili indipendentemente.

---

## Campi deterministici Bybit

Disponibili in `info` da `watchMyTrades` (execution stream):

| Campo | Valori chiave |
|---|---|
| `createType` | `CreateByUser`, `CreateByTakeProfit`, `CreateByPartialTakeProfit`, `CreateByStopLoss`, `CreateByPartialStopLoss`, `CreateByLiq` |
| `stopOrderType` | `TakeProfit`, `PartialTakeProfit`, `StopLoss`, `PartialStopLoss`, `""` |
| `execType` | `Trade`, `Funding`, `BustTrade`, `AdlTrade` |
| `closedSize` | float — quanta posizione chiusa |
| `orderLinkId` | nostro `clientOrderId` se presente |
| `seq` | cross-sequence per correlazione con `watchPositions` |
| `posQty` | dimensione posizione dopo il fill |

**Tabella classificazione deterministica:**

| createType | stopOrderType | closedSize | orderLinkId noto | Evento |
|---|---|---|---|---|
| `CreateByUser` | `""` | 0 | sì | `ENTRY_FILLED` (bot) |
| `CreateByUser` | `""` | >0 | sì | `CLOSE_PARTIAL/FULL_FILLED` (bot) |
| `CreateByUser` | `""` | >0 | no | `MANUAL_CLOSE_PARTIAL/FULL` |
| `CreateByTakeProfit/Partial` | `TakeProfit` | >0 | qualsiasi | `TP_FILLED` |
| `CreateByStopLoss/Partial` | `StopLoss` | >0 | qualsiasi | `SL_FILLED` |
| `CreateByLiq` | — | >0 | — | `LIQUIDATION_FILLED` |

---

## Architettura — Approccio 2 (inline classify + raw persistito)

```
WS/REST → EventNormalizer → ExchangeRawEvent
                          → EventClassifier → ClassifiedEvent
                          → repo.insert_raw_and_classified()
                               ├── INSERT exchange_raw_events   (audit trail)
                               └── INSERT ops_exchange_events   (lifecycle engine)
```

`EventNormalizer` e `EventClassifier` sono classi pure (zero I/O, zero DB). Usate sia dal WS path che dal REST path — un solo punto dove vive la logica di classificazione.

---

## Struttura moduli

```
src/runtime_v2/execution_gateway/
├── event_ingest/                       NUOVO package
│   ├── __init__.py
│   ├── models.py                       ExchangeRawEvent, ClassifiedEvent, type aliases
│   ├── normalizer.py                   CCXT dict → ExchangeRawEvent (3 metodi: from_trade, from_order, from_position)
│   └── classifier.py                   ExchangeRawEvent → ClassifiedEvent (logica deterministica)
├── adapters/ccxt_bybit/
│   └── ws_fill_watcher.py              REFACTORED: 3 stream, zero matching, ~160 righe
├── event_sync.py                       REFACTORED: safety net REST, usa classifier, ~280 righe
├── repositories.py                     ESTESO: +5 metodi (insert_raw_and_classified, get_known_order_link_ids, ...)
└── gateway.py                          INVARIATO
```

---

## Schema DB: `exchange_raw_events`

```sql
CREATE TABLE IF NOT EXISTS exchange_raw_events (
    raw_event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_event_id       TEXT NOT NULL,
    source_stream           TEXT NOT NULL,    -- watch_my_trades | watch_orders | watch_positions | fetch_*
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    create_type             TEXT,
    stop_order_type         TEXT,
    exec_type               TEXT,
    order_status            TEXT,
    order_link_id           TEXT,
    order_id                TEXT,
    seq                     INTEGER,
    exec_price              REAL,
    exec_qty                REAL,
    closed_size             REAL,
    leaves_qty              REAL,
    pos_qty                 REAL,
    exec_value              REAL,
    exec_fee                REAL,
    fee_rate                REAL,
    cum_exec_qty            REAL,
    position_take_profit    REAL,
    position_stop_loss      REAL,
    classified_event_type   TEXT,
    classified_source       TEXT,
    trade_chain_id          INTEGER,
    tp_level                INTEGER,
    forwarded_to_lifecycle  INTEGER DEFAULT 0,
    forwarded_at            TEXT,
    raw_info_json           TEXT NOT NULL,
    exchange_time           TEXT,
    received_at             TEXT NOT NULL,
    idempotency_key         TEXT UNIQUE NOT NULL
);
```

**Indici:**
- `(trade_chain_id, classified_event_type)` — lookup lifecycle
- `(symbol, side, received_at DESC)` — lookup Telegram logger
- `(forwarded_to_lifecycle)` WHERE 0 — audit non forwardati
- `(source_stream, received_at DESC)` — debug per stream

**Campi per Telegram logger:** `exec_price`, `exec_qty`, `exec_fee`, `exec_value`, `fee_rate`, `pos_qty` + eventi con `exec_type=Funding` per funding fees.

---

## Data Models

### `ExchangeRawEvent`
Output di `EventNormalizer`. Zero logica di business. Testabile con fixture JSON.

Campi chiave: `source_stream`, `exchange_event_id`, `idempotency_key`, `create_type`, `stop_order_type`, `exec_type`, `order_link_id`, `seq`, `closed_size`, `pos_qty`, `position_take_profit`, `position_stop_loss`, `raw_info`.

### `ClassifiedEvent`
Output di `EventClassifier`. Contiene riferimento al raw.

Campi chiave: `event_type`, `source`, `trade_chain_id`, `tp_level`, `is_actionable`.

Proprietà calcolata `should_forward_to_lifecycle`: True se `is_actionable AND trade_chain_id IS NOT NULL AND event_type != UNKNOWN`.

### `EventSource` (literal type)
`bot_command | exchange_auto | exchange_manual | reconciliation_inferred`

### `ExchangeEventType` (literal type)
Fill: `ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`, `CLOSE_PARTIAL_FILLED`, `CLOSE_FULL_FILLED`, `MANUAL_CLOSE_PARTIAL`, `MANUAL_CLOSE_FULL`, `LIQUIDATION_FILLED`  
Order lifecycle: `PENDING_ENTRY_CANCELLED`, `STANDALONE_PROTECTIVE_CANCELLED`  
Position: `PROTECTIVE_ORDER_CANCELLED`  
Confirmations: `STOP_MOVED_CONFIRMED`, `PROTECTIVE_ORDERS_SYNCED`  
Fallback: `UNKNOWN`

---

## EventNormalizer

Tre metodi pubblici, zero logica di business:

- `from_trade(trade: dict)` — da `watchMyTrades` / `fetchMyTrades`
- `from_order(order: dict)` — da `watchOrders` / `fetchOpenOrders`  
- `from_position(position: dict)` — da `watchPositions`
- `from_rest_trade(trade: dict)` — chiama `from_trade` e sovrascrive `source_stream="fetch_my_trades"` e `idempotency_key="rest_exec:{execId}"` per distinguere WS da REST nel DB

Helper difensivi `_f`, `_i`, `_s` per cast silenzioso dei campi Bybit (arrivano come `str`/`int`/`None` in modo inconsistente tra WS e REST).

---

## EventClassifier

Classe pura, costruita con:
- `known_order_link_ids: dict[str, tuple[int, str, int]]` — `orderLinkId → (chain_id, role, sequence)`
- `open_chain_tp_prices: dict` — riservato, non usato (no price matching)

Metodo pubblico: `classify(raw: ExchangeRawEvent) → ClassifiedEvent`

### Priorità classificazione (execution stream)

1. **Campi raw deterministici** — `createType` in `_CREATE_TYPE_TP/SL/LIQ`, `stopOrderType` in `_STOP_TYPE_TP/SL`
2. **Correlazione `orderLinkId`** — lookup in `known_order_link_ids`
3. **Structural inference** — `closedSize > 0` + no `orderLinkId` → manuale

### TP position-level senza `orderLinkId`
`createType=CreateByTakeProfit` → evento classificato `TP_FILLED` con `trade_chain_id=None`. Il record entra in `exchange_raw_events`. Il `run_trade_based_reconciliation()` REST correla la chain tramite symbol+side (no price matching). Correlazione: chain singola per symbol+side → certa; più chain → chain più recente con warning.

### Position updates (`watchPositions`)
`takeProfit==0.0` o `stopLoss==0.0` → `PROTECTIVE_ORDER_CANCELLED`. Il lifecycle engine verifica se era atteso.

---

## WebSocket: 3 stream

```
BybitWsFillWatcher
├── watch_my_trades    → ENTRY_FILLED, TP_FILLED, SL_FILLED,
│                        MANUAL_CLOSE_FULL/PARTIAL, LIQUIDATION_FILLED
├── watch_orders       → PENDING_ENTRY_CANCELLED, STANDALONE_PROTECTIVE_CANCELLED
└── watch_positions    → PROTECTIVE_ORDER_CANCELLED (TP/SL rimosso senza fill)
```

Pattern `_process_batch(items, normalize_fn)` generico — i 3 stream differiscono solo per la funzione di normalizzazione.

Ogni stream usa istanza `exchange` separata (pattern invariato, evita conflitti CCXT Pro).

---

## REST: safety net puro

`ExchangeEventSyncWorker` — 4 metodi invariati nel contratto, refactored nell'implementazione:

| Metodo | Scopo |
|---|---|
| `run_reconciliation()` | Ordini SENT/ACK non confermati via WS |
| `run_trade_based_reconciliation()` | TP fill persi durante downtime WS |
| `run_position_reconciliation()` | Chiusure manuali perse durante downtime WS |
| `run_protective_orders_reconciliation()` | TP/SL rimossi persi durante downtime WS |

Tutti usano lo stesso `EventNormalizer` + `EventClassifier` del WS path. Zero SQLite inline — tutto via `repo.insert_raw_and_classified`.

---

## `GatewayCommandRepository` — nuovi metodi

| Metodo | Scopo |
|---|---|
| `insert_raw_and_classified(classified)` | Scrive `exchange_raw_events` + `ops_exchange_events` in una transazione |
| `get_known_order_link_ids()` | Carica `orderLinkId → (chain_id, role, seq)` per il classifier |
| `get_open_chains_with_tps()` | Chain aperte con TP attivi per `run_trade_based_reconciliation` |
| `tp_fill_exists(chain_id, tp_level)` | Idempotenza TP_FILLED |
| `protective_cancelled_exists(chain_id, tp_level)` | Idempotenza PROTECTIVE_ORDER_CANCELLED |

Metodi esistenti: **invariati**.

---

## Copertura eventi — mappa completa

| Evento | Stream primario | Fallback REST |
|---|---|---|
| `ENTRY_FILLED` (bot) | `watchMyTrades` via `orderLinkId` | `run_reconciliation` |
| `TP_FILLED` position-level | `watchMyTrades` `createType=CreateByTakeProfit` | `run_trade_based_reconciliation` |
| `TP_FILLED` standalone | `watchMyTrades` via `orderLinkId` | `run_reconciliation` |
| `SL_FILLED` position-level | `watchMyTrades` `createType=CreateByStopLoss` | `run_trade_based_reconciliation` |
| `SL_FILLED` standalone | `watchMyTrades` via `orderLinkId` | `run_reconciliation` |
| `CLOSE_PARTIAL/FULL_FILLED` (bot) | `watchMyTrades` via `orderLinkId` | `run_reconciliation` |
| `MANUAL_CLOSE_FULL` | `watchMyTrades` structural inference | `run_position_reconciliation` |
| `MANUAL_CLOSE_PARTIAL` | `watchMyTrades` structural inference | `run_position_reconciliation` |
| `LIQUIDATION_FILLED` | `watchMyTrades` `createType=CreateByLiq` | — |
| `PENDING_ENTRY_CANCELLED` | `watchOrders` | `run_reconciliation` |
| `STANDALONE_PROTECTIVE_CANCELLED` | `watchOrders` | — |
| `PROTECTIVE_ORDER_CANCELLED` | `watchPositions` `takeProfit→0` | `run_protective_orders_reconciliation` |

**Nessun evento affidato solo al REST. Il REST è fallback puro.**

---

## Integrazione lifecycle engine

`event_processor.py`, `lifecycle/models.py`, `lifecycle/workers.py`, `ops_exchange_events` — **invariati**.

Il lifecycle engine riceve gli stessi event_type da `ops_exchange_events` come prima. Nuovi event_type (`MANUAL_CLOSE_FULL`, `SL_FILLED` position-level, `PROTECTIVE_ORDER_CANCELLED`, `LIQUIDATION_FILLED`) richiedono gestione separata nell'`event_processor.py` — fuori scope di questo refactoring, da pianificare come step successivo.

---

## File toccati

| File | Azione |
|---|---|
| `event_ingest/__init__.py` | NUOVO |
| `event_ingest/models.py` | NUOVO |
| `event_ingest/normalizer.py` | NUOVO |
| `event_ingest/classifier.py` | NUOVO |
| `adapters/ccxt_bybit/ws_fill_watcher.py` | REFACTORED (~395 → ~160 righe) |
| `event_sync.py` | REFACTORED (~500 → ~280 righe) |
| `repositories.py` | ESTESO (+5 metodi, zero breaking changes) |
| `db/migrations/XXXX_exchange_raw_events.sql` | NUOVO |
| `lifecycle/event_processor.py` | INVARIATO |
| `gateway.py` | INVARIATO |

---

## Invarianti di sistema

- `INSERT OR IGNORE` + `idempotency_key UNIQUE` su entrambe le tabelle — nessun duplicato
- WS e REST possono trovare lo stesso evento: `exchange_raw_events` mantiene entrambe le righe (audit), `ops_exchange_events` deduplica via idempotency key
- Evento con `trade_chain_id=NULL` non viene forwardato al lifecycle — rimane in raw per correlazione futura o per il logger
- `EventNormalizer` e `EventClassifier` non hanno dipendenze I/O — testabili con fixture pure
