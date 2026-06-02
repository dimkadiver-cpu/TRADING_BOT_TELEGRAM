# Fase B+C — Structural Refactor Design

**Data:** 2026-06-01
**Scope:** Contratto payload uniforme (B) + proiezione event-driven (C)
**Rischio:** Medio — una migration DB (Fase C), nessuna per Fase B
**Dipendenze:** Fase A deve essere completata prima

---

## Principio architetturale: WS primario, REST safety net

**WebSocket è l'unico path operativo normale.**
Gestisce tutti gli eventi in real-time ed è l'unico writer su `ops_exchange_events`
durante operazione sana. Copre il 99%+ dei casi.

**REST reconciliation è un safety net, non un loop parallelo.**
Si attiva in tre situazioni:

```
1. Bot restart     → catch-up su tutto il periodo di downtime
2. WS reconnect    → catch-up sugli eventi persi durante il gap
3. Heartbeat lento → check periodico a bassa frequenza
                     solo come ultimo livello di paranoia
```

Questo ha due implicazioni dirette sul design:

**Implicazione 1 — il payload REST non deve essere completo come WS.**
I campi assenti via REST (`fee_rate`, `pos_qty`, `closed_size`) non sono un
problema operativo: nella norma il WS li porta. Nel caso di recovery da downtime,
avere `fill_price`, `filled_qty`, `exec_fee` è sufficiente per ricostruire lo
stato corretto della chain. I campi mancanti appaiono `n/a` nelle notifiche
di recovery — comportamento corretto e atteso, non un bug.

**Implicazione 2 — la riconciliazione non deve girare a ogni tick.**
In `main.py` esistono tre loop di reconciliation con comportamenti diversi:

```python
# Loop 1: _run_sync_worker — gira SEMPRE ogni 8s indipendentemente dal WS
#   → chiama run_once() → run_reconciliation() su comandi SENT/ACK
#   → problema: duplica il lavoro del WS durante operazione normale

# Loop 2: _run_reconciliation_periodically — condizionale
#   → parte solo se websocket.enabled=true E poll_fallback_enabled=true
#   → intervallo: websocket.poll_fallback_period_seconds (GIA CONFIGURABILE in execution.yaml)

# Loop 3: _run_position_reconciliation_periodically — gira SEMPRE ogni 60s
#   → chiama run_position_reconciliation() + run_trade_based_reconciliation()
#              + run_protective_orders_reconciliation()
#   → intervallo: HARDCODED a 60s in main.py:504 (non configurabile oggi)
```

Il vero problema è Loop 1: gira ogni 8s anche quando il WS è sano.
Loop 2 è già configurabile. Loop 3 ha l'intervallo hardcoded.

In Fase B il fix è chirurgico — tre modifiche:
1. Loop 1: condizionato a `poll_fallback_enabled` (stessa flag di Loop 2)
2. Loop 3: intervallo esposto in config (`position_reconciliation_interval_seconds`)
3. Startup catch-up: aggiunta chiamata esplicita a `run_reconciliation()` +
   `run_position_reconciliation()` subito dopo il primo avvio del WS

---

## Contesto

Dopo Fase A il sistema funziona correttamente. Restano due tensioni strutturali:

**Tensione B — due path producono payload semanticamente diversi**
WS e REST scrivono in `ops_exchange_events` con payload di forma diversa.
Il processor è ora autonomo (non si fida dei flag), ma non c'è un contratto
formale che definisce cosa è garantito nel payload. Aggiungere un campo richiede
di ricordarsi di aggiornare entrambi i path.

**Tensione C — proiezione notifiche è O(n eventi totali)**
`project_clean_log_for_chain` rilegge tutta la storia della chain ad ogni
`_persist_result`. Per chain con 10 eventi processa 10 INSERT OR IGNORE.
Con averaging multi-step e TP multipli, questo cresce. Non è un bug, ma è
un debt che peggiora linearmente.

---

## Fase B — Contratto payload uniforme e completo

### Obiettivo

Un tipo `ExchangeEventPayload` validato che entrambi i path devono produrre,
contenente tutti i campi utili downstream: al processor, alle notifiche,
e al logging. Elimina la scissione tra "dati operativi" e "dati audit"
per i campi che hanno valore visibile all'utente.

### Relazione con `exchange_raw_events`

`exchange_raw_events` resta come audit immutabile di tutto il raw Bybit
(incluso `raw_info_json` = dict completo della risposta exchange).
`ExchangeEventPayload` in `ops_exchange_events` porta il subset utile
downstream: non sostituisce l'audit, lo affianca con un contratto tipizzato.

```
exchange_raw_events       <- tutto, immutabile, nessuno lo legge in runtime
ops_exchange_events       <- ExchangeEventPayload, coda lifecycle + notifiche
```

### Disponibilità dei campi per path

Analisi effettiva dei due path di ingest:

| Campo | WS `watch_my_trades` | REST `fetch_orders` | Note |
|-------|---------------------|---------------------|------|
| `fill_price` | `info.execPrice` | `order.average` | garantito da entrambi |
| `filled_qty` | `info.execQty` | `order.filled` | garantito da entrambi |
| `exec_fee` | `info.execFee` | `info.cumExecFee` | REST: fee cumulata sull'ordine |
| `exec_value` | `info.execValue` | `info.cumExecValue` | REST: valore cumulato |
| `exchange_time` | `info.execTime` | `info.updatedTime` | garantito da entrambi |
| `order_id` | `info.orderId` | `order.id` | garantito da entrambi |
| `leaves_qty` | `info.leavesQty` | `info.leavesQty` | garantito da entrambi |
| `cum_exec_qty` | `info.cumExecQty` | `info.cumExecQty` | garantito da entrambi |
| `closed_size` | `info.closedSize` | n/a | endpoint ordine Bybit non lo espone |
| `fee_rate` | `info.feeRate` | n/a | dato per-esecuzione, non per-ordine |
| `pos_qty` | `info.posQty` | n/a | stato posizione, non nell'endpoint ordine |

I campi marcati `n/a` per REST sono genuinamente assenti nell'API Bybit per
l'endpoint ordine — non è una limitazione nostra ma del provider.
I campi "garantiti da entrambi" che oggi non sono in `RawAdapterOrder`
vanno aggiunti estendendo il modello e `StatusMapper`.

### Design — modello `ExchangeEventPayload`

**Nuovo file:** `src/runtime_v2/execution_gateway/event_ingest/payload.py`

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class ExchangeEventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")  # tollerante per forward compat

    # Fill core (garantiti da entrambi i path)
    fill_price: float | None = None      # prezzo medio esecuzione
    filled_qty: float | None = None      # qty eseguita in questo fill
    exec_fee: float | None = None        # fee in USDT (per-exec WS, cumulata REST)
    exec_value: float | None = None      # valore USDT fill (fill_price x qty)
    exchange_time: str | None = None     # timestamp exchange (ISO UTC)
    leaves_qty: float | None = None      # qty rimanente sull'ordine
    cum_exec_qty: float | None = None    # qty totale eseguita sull'ordine

    # Solo WS — None nel path REST (limitazione Bybit, non nostra)
    closed_size: float | None = None     # qty posizione chiusa (info.closedSize)
    fee_rate: float | None = None        # rate fee es. 0.00055 (info.feeRate)
    pos_qty: float | None = None         # size posizione residua dopo fill (info.posQty)

    # Identificatori
    exchange_event_id: str | None = None  # execId (WS) / orderId (REST)
    order_id: str | None = None           # Bybit orderId UUID
    order_link_id: str | None = None      # nostro client_order_id (tsb:...)

    # Routing / classification
    tp_level: int | None = None
    command_id: int | None = None
    source: str | None = None            # watch_my_trades | watch_orders | rest_reconciliation

    # is_final: RIMOSSO — il processor lo deriva da chain state (Fase A)
```

### Perché ogni campo

| Campo | Chi lo usa |
|-------|-----------|
| `fill_price`, `filled_qty` | processor (avg price, qty update), notifiche |
| `closed_size` | processor (closed_position_qty), notifiche (Closed %) |
| `exec_fee` | processor (cumulative_fees), notifiche (Fee: X USDT) |
| `fee_rate` | notifiche (Fee rate %), analisi costo trade — solo WS |
| `exec_value` | notifiche (valore USDT del trade), analisi |
| `pos_qty` | cross-check `open_position_qty` post-fill, debug — solo WS |
| `leaves_qty`, `cum_exec_qty` | debug fill parziali, fill averaging |
| `exchange_event_id`, `order_id`, `order_link_id` | cross-reference exchange, debug |
| `exchange_time` | timing display, analisi latency |
| `tp_level`, `command_id`, `source` | routing processor, audit |

### Modifiche

**`src/runtime_v2/execution_gateway/models.py` — estendi `RawAdapterOrder`**

Aggiungi i campi disponibili via REST che oggi non vengono catturati dal modello:

```python
class RawAdapterOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_order_id: str
    exchange_order_id: str | None = None
    status: str
    filled_qty: float = 0.0
    average_price: float | None = None
    cancel_reason: str | None = None
    # Nuovi:
    exec_fee: float | None = None        # info.cumExecFee
    exec_value: float | None = None      # info.cumExecValue
    exchange_time: str | None = None     # info.updatedTime (ISO UTC)
    leaves_qty: float | None = None      # info.leavesQty
    cum_exec_qty: float | None = None    # info.cumExecQty
```

**`src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py`**

Cattura i nuovi campi da `order.info`:

```python
info = ccxt_order.get("info") or {}
return RawAdapterOrder(
    # campi esistenti...
    exec_fee=float(info["cumExecFee"]) if info.get("cumExecFee") else None,
    exec_value=float(info["cumExecValue"]) if info.get("cumExecValue") else None,
    exchange_time=_ms_to_iso(info.get("updatedTime")),
    leaves_qty=float(info["leavesQty"]) if info.get("leavesQty") else None,
    cum_exec_qty=float(info["cumExecQty"]) if info.get("cumExecQty") else None,
)
```

**`src/runtime_v2/execution_gateway/repositories.py`**

`insert_raw_and_classified()` — path WS, costruisce `ExchangeEventPayload` da `ExchangeRawEvent`:

```python
ep = ExchangeEventPayload(
    fill_price=raw.exec_price,
    filled_qty=raw.exec_qty,
    closed_size=raw.closed_size,    # WS only
    exec_fee=raw.exec_fee,
    fee_rate=raw.fee_rate,          # WS only
    exec_value=raw.exec_value,
    pos_qty=raw.pos_qty,            # WS only
    leaves_qty=raw.leaves_qty,
    cum_exec_qty=raw.cum_exec_qty,
    exchange_event_id=raw.exchange_event_id,
    order_id=raw.order_id,
    order_link_id=raw.order_link_id,
    exchange_time=raw.exchange_time,
    tp_level=classified.tp_level,
    source=classified.source,
)
```

**`src/runtime_v2/execution_gateway/event_sync.py`**

`_save_fill_event()` — path REST, costruisce `ExchangeEventPayload` da `RawAdapterOrder` esteso.
I campi WS-only (`closed_size`, `fee_rate`, `pos_qty`) restano `None` per limitazione Bybit.

```python
ep = ExchangeEventPayload(
    fill_price=raw.average_price,
    filled_qty=raw.filled_qty,
    closed_size=None,               # non disponibile via REST Bybit
    exec_fee=raw.exec_fee,
    fee_rate=None,                  # non disponibile via REST Bybit
    exec_value=raw.exec_value,
    pos_qty=None,                   # non disponibile via REST Bybit
    leaves_qty=raw.leaves_qty,
    cum_exec_qty=raw.cum_exec_qty,
    exchange_event_id=raw.exchange_order_id,
    order_id=raw.exchange_order_id,
    order_link_id=raw.client_order_id,
    exchange_time=raw.exchange_time,
    command_id=coid.command_id,
    source="rest_reconciliation",
)
```

**`src/runtime_v2/lifecycle/event_processor.py`**

Accesso via attributo tipizzato — la logica non cambia, solo il modo di leggere:

```python
payload = ExchangeEventPayload.model_validate_json(exchange_event.payload_json)
fill_price = payload.fill_price    # non piu payload.get("fill_price")
exec_fee   = payload.exec_fee
closed_size = payload.closed_size
```

**`src/runtime_v2/control_plane/outbox_writer.py` + `formatters/clean_log.py`**

`_build_payload()` accede ai nuovi campi via `ev.get(...)` — backward compatible.
I formatter vengono aggiornati per mostrare nel blocco `final_result`:
- `Fee rate: 0.055%` (se `fee_rate` presente — solo WS)
- `Value: X USDT` (se `exec_value` presente)

Per il path REST questi campi appaiono come `n/a` — comportamento corretto e atteso
per notifiche di recovery (bot era spento, WS era giù).

**`src/runtime_v2/execution_gateway/models.py` — estendi `WebsocketConfig`**

Aggiungi il campo mancante per rendere configurabile l'intervallo di Loop 3:

```python
class WebsocketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    poll_fallback_enabled: bool = True
    poll_fallback_period_seconds: int = 60       # gia presente — Loop 2
    position_reconciliation_interval_seconds: int = 600  # NUOVO — Loop 3, default 10 min
```

**`main.py` — tre modifiche chirurgiche**

*Modifica 1: Loop 1 condizionato a `poll_fallback_enabled`*

```python
# PRIMA: sync_task sempre attivo
sync_task = asyncio.create_task(
    _run_sync_worker(sync_worker=execution_runtime.sync_worker, logger=logger)
)

# DOPO: solo se poll_fallback_enabled
ws_cfg = adapter_cfg.websocket  # gia disponibile in _build_execution_runtime
if ws_cfg.poll_fallback_enabled:
    sync_task = asyncio.create_task(
        _run_sync_worker(sync_worker=execution_runtime.sync_worker, logger=logger)
    )
```

*Modifica 2: Loop 3 usa l'intervallo da config*

```python
# PRIMA: hardcoded 60s
_run_position_reconciliation_periodically(..., interval_seconds=60)

# DOPO: da config
_run_position_reconciliation_periodically(
    ...,
    interval_seconds=execution_runtime.position_reconciliation_interval_seconds,
)
```

`ExecutionRuntime` acquisisce il campo `position_reconciliation_interval_seconds: int`
popolato da `adapter_cfg.websocket.position_reconciliation_interval_seconds`.

*Modifica 3: startup catch-up esplicito*

Subito dopo `ws_watcher.start()` in `_build_execution_runtime()`, o subito prima
di lanciare i task in `_async_main()`:

```python
# Catch-up sul downtime: copre i fill avvenuti mentre il bot era spento
if execution_runtime is not None:
    try:
        execution_runtime.sync_worker.run_reconciliation()
        execution_runtime.sync_worker.run_position_reconciliation()
    except Exception:
        logger.warning("startup reconciliation failed (non-critical)")
```

**`ExchangeEventSyncWorker`** — nessuna modifica. L'interfaccia non cambia,
cambia solo quando `main.py` la chiama.

### Cosa non cambia

- Schema DB: nessun cambio — `payload_json` resta TEXT
- `exchange_raw_events`: nessun cambio, resta audit completo con `raw_info_json`
- Comportamento processor: nessun cambio (Fase A gia fatto)
- Notifiche esistenti: backward compatible (nuovi campi aggiunti, nessuno rimosso)

---

## Fase C — Proiezione event-driven

### Obiettivo

`project_clean_log_for_chain` deve processare solo gli eventi lifecycle
non ancora proiettati, non tutta la storia.

### Design

**Migration:** aggiungi colonna a `ops_trade_chains`:

```sql
ALTER TABLE ops_trade_chains
ADD COLUMN last_projected_event_id INTEGER NOT NULL DEFAULT 0;
```

**`src/runtime_v2/control_plane/outbox_writer.py`**
`project_clean_log_for_chain()` — aggiorna la query eventi:

```python
# PRIMA
events = conn.execute(
    "SELECT event_type, payload_json, idempotency_key "
    "FROM ops_lifecycle_events "
    "WHERE trade_chain_id=? ORDER BY event_id",
    (chain_id,),
).fetchall()

# DOPO
last_id = conn.execute(
    "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=?",
    (chain_id,),
).fetchone()[0] or 0

events = conn.execute(
    "SELECT event_id, event_type, payload_json, idempotency_key "
    "FROM ops_lifecycle_events "
    "WHERE trade_chain_id=? AND event_id > ? ORDER BY event_id",
    (chain_id, last_id),
).fetchall()
```

Alla fine della proiezione, aggiorna `last_projected_event_id`:

```python
if events:
    max_event_id = max(row[0] for row in events)
    conn.execute(
        "UPDATE ops_trade_chains SET last_projected_event_id=? WHERE trade_chain_id=?",
        (max_event_id, chain_id),
    )
```

### Re-proiezione manuale (recovery)

Per ri-proiettare una chain (debug/recovery), basta resettare il campo:
```sql
UPDATE ops_trade_chains SET last_projected_event_id=0 WHERE trade_chain_id=?;
```

Il sistema e ancora idempotente: i `dedupe_key` su `ops_notification_outbox`
garantiscono che re-proiettare non duplica notifiche.

### Effetto

| Scenario | Prima | Dopo |
|----------|-------|-------|
| Chain con 5 eventi, nuovo evento | 6 SELECT+INSERT OR IGNORE | 1 SELECT+INSERT OR IGNORE |
| Chain con 20 eventi (averaging), nuovo evento | 21 SELECT+INSERT OR IGNORE | 1 SELECT+INSERT OR IGNORE |
| Recovery: re-proiezione manuale | automatica (idempotente) | manuale (reset colonna) |

### Modifiche

- 1 migration SQL: `ALTER TABLE ops_trade_chains ADD COLUMN last_projected_event_id`
- `outbox_writer.project_clean_log_for_chain()`: query events filtrati + UPDATE colonna
- Nessun cambio ad altri file

---

## Ordine di implementazione

```
Fase A  ->  Fase B  ->  Fase C
```

**Fase B dipende da Fase A** perche rimuove `is_final` dal payload (dopo A il processor
non lo usa piu — rimuoverlo in B e sicuro).

**Fase C e indipendente da B** ma dipende da A (se il processor produce lifecycle events
corretti, la proiezione event-driven e affidabile).

---

## Effort stimato

| Fase | Effort | Rischio |
|------|--------|---------|
| B — payload completo + contratto + scheduling fix | 7-9 ore | Basso |
| C — proiezione incrementale | 2-3 ore | Basso |
| B+C totale | 8-11 ore | Basso/Medio |

---

## Test

**Fase B:**
- Unit test `RawAdapterOrder`: StatusMapper popola `exec_fee`, `exec_value`, `exchange_time` da `info`
- Unit test `ExchangeEventPayload`: path WS con tutti i campi → validazione OK
- Unit test `ExchangeEventPayload`: path REST con `closed_size=None`, `fee_rate=None` → validazione OK
- Integration: processor con payload WS serializzato → stessa chain state di prima
- Integration: notifica `TP_FILLED_FINAL` mostra `fee_rate` e `exec_value` se presenti (WS)
- Integration: notifica `TP_FILLED_FINAL` mostra `n/a` per `fee_rate` se REST path
- Scheduling: con `poll_fallback_enabled=false` → `_run_sync_worker` non parte
- Scheduling: `position_reconciliation_interval_seconds=120` in yaml → Loop 3 usa 120s
- Startup: catch-up esplicito chiama `run_reconciliation()` prima del main loop

**Fase C:**
- Unit test: proiezione di 3 eventi → `last_projected_event_id = max(event_ids)`
- Re-proiezione dopo reset → stesso numero di notifiche, nessun duplicato nel log
- Regression: catena lunga (10+ eventi) → la proiezione dell'evento 11 non riprocessa i primi 10
