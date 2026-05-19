# Runtime V2 — Fase 2: CcxtBybitAdapter Production-Ready

**Status:** approvata — pronta per implementation plan
**Data:** 2026-05-19
**Dipende da:** Fase 1 CcxtBybitAdapter (commit 1a12dd7)

---

## §1 — Obiettivo

Consolidare `CcxtBybitAdapter` per uso in produzione su quattro fronti:

1. **WebSocket fill real-time** — sostituisce il polling REST continuo con `ccxt.pro` `watch_orders()` in un thread daemon. REST resta come safety net configurabile.
2. **SYNC_PROTECTIVE_ORDERS reale** — dopo un fill parziale (TP o close partial), amenda la qty dello SL al residuo di posizione. Oggi è un no-op.
3. **OD-F1-2** — fallback `get_order_status` via `fetch_positions` per ordini SL/TP attached (Mode C) non queryabili via `orderLinkId`.
4. **Hedge mode** — flag `hedge_mode` in `AdapterConfig`; aggiunge `positionIdx` a tutti gli ordini e rimuove `reduceOnly` quando l'account Bybit è in hedge mode.

Nessuna modifica al lifecycle o ai modelli canonici.

---

## §2 — File map

```
src/runtime_v2/execution_gateway/
├── adapters/
│   ├── ccxt_bybit/
│   │   ├── adapter.py          (esteso: hedge_mode, amend_sl_qty, OD-F1-2 fallback)
│   │   ├── order_builder.py    (esteso: positionIdx hedge, action amend_sl_qty)
│   │   ├── status_mapper.py    (invariato)
│   │   ├── ws_fill_watcher.py  (NUOVO: BybitWsFillWatcher — ccxt.pro thread daemon)
│   │   └── __init__.py
│   └── factory.py              (invariato)
├── models.py                   (AdapterConfig: + hedge_mode, + WebsocketConfig, + PollFallbackConfig)
└── event_sync.py               (esteso: run_reconciliation(), fallback poll configurabile)

config/
└── execution.yaml              (esempio ccxt_bybit aggiornato: hedge_mode + websocket)

requirements.txt                (ccxt[pro]>=4.0  — sostituisce ccxt>=4.0)

tests/runtime_v2/execution_gateway/
├── test_bybit_order_builder.py         (esteso: hedge_mode positionIdx per tutti i comandi)
├── test_bybit_ws_fill_watcher.py       (NUOVO: unit con exchange mock ccxt.pro)
├── test_ccxt_bybit_adapter_unit.py     (esteso: amend_sl_qty, OD-F1-2 fallback)
└── test_ccxt_bybit_gated.py            (esteso: scenari hedge mode e WS su testnet)
```

---

## §3 — WebSocket: BybitWsFillWatcher

### Ciclo di vita

```
main.py avvia ExecutionCommandWorker
      ↓
Se adapter è CcxtBybitAdapter e websocket.enabled=True:
      ↓
ExchangeEventSyncWorker.run_reconciliation()   ← passaggio REST una-tantum
  → legge tutti i comandi SENT con client_order_id
  → per ognuno: REST get_order_status
  → se FILLED: _normalize_and_save() (idempotente: INSERT OR IGNORE)
      ↓
BybitWsFillWatcher.start()
  → crea daemon thread
  → thread avvia asyncio.run(_watch_loop())
  → _watch_loop(): ccxt.pro exchange.watch_orders() in loop infinito
  → ogni evento fill → _on_fill(order) → INSERT ops_exchange_events

ExchangeEventSyncWorker continua come fallback:
  → se poll_fallback.enabled=True: ogni poll_fallback_period_seconds
  → se poll_fallback.enabled=False: solo startup reconciliation (già fatto)
```

### Mapping WS event → ops_exchange_events

`watch_orders()` restituisce oggetti CCXT con `clientOrderId` = il nostro `orderLinkId`.

Il watcher mantiene un set degli `orderLinkId` attivi, letti da `ops_execution_commands WHERE command_status IN ('SENT', 'ACK')`. Solo i fill con `clientOrderId` riconosciuto vengono processati — gli altri vengono scartati silenziosamente.

Il metodo `_normalize_and_save()` è condiviso con `ExchangeEventSyncWorker` (estratto in funzione comune o copiato). Idempotenza garantita da `UNIQUE(idempotency_key)` su `ops_exchange_events`.

### Riconnessione

`ccxt.pro` gestisce reconnect automaticamente. Al momento della riconnessione, il watcher chiama `run_reconciliation()` una seconda volta per coprire il gap temporale.

### BybitWsFillWatcher — interfaccia

```python
class BybitWsFillWatcher:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        ops_db_path: str,
        repo: GatewayCommandRepository,
    ) -> None: ...

    def start(self) -> None:
        """Avvia daemon thread. Idempotente se già avviato."""

    def stop(self) -> None:
        """Segnala stop al loop async. Attende max 3s."""
```

### Config nuova in AdapterConfig

```python
class WebsocketConfig(BaseModel):
    enabled: bool = False
    poll_fallback_enabled: bool = True
    poll_fallback_period_seconds: int = 60

class AdapterConfig(BaseModel):
    ...
    websocket: WebsocketConfig = WebsocketConfig()
```

```yaml
bybit_main:
  type: ccxt_bybit
  ...
  websocket:
    enabled: true
    poll_fallback_enabled: true
    poll_fallback_period_seconds: 60
```

---

## §4 — SYNC_PROTECTIVE_ORDERS reale

### Trigger

Il lifecycle emette `SYNC_PROTECTIVE_ORDERS` con payload `{"symbol": ..., "side": ...}` in 4 casi:
- TP non finale riempito (`event_processor.py`)
- `CLOSE_PARTIAL` eseguito (`event_processor.py`)
- `CANCEL_PENDING_ENTRY` su catena OPEN/PARTIALLY_CLOSED (`event_processor.py`, `entry_gate.py`)

### Flusso adapter

```
adapter.place_order(SYNC_PROTECTIVE_ORDERS, {symbol, side})
      ↓
BybitOrderBuilder → BybitOrderParams(action="amend_sl_qty", symbol, side)
      ↓
adapter._handle_amend_sl_qty(symbol, side)
      ↓
1. fetch_positions(symbol) → current_qty per side
      ↓
2a. current_qty == 0              2b. current_qty > 0
    → fetch_open_orders(symbol)       → trova SL aperto
    → cancella tutti i              → amend qty a current_qty
      reduce-only per side
    → AdapterResult(success=True)
```

### Trovare e amendare lo SL — Mode B

SL separato, `reduceOnly=True`:

```python
close_side = "sell" if side == "LONG" else "buy"
orders = exchange.fetch_open_orders(symbol)
sl_orders = [o for o in orders
             if o.get("reduceOnly") and o.get("stopPrice")
             and o["side"] == close_side]
if sl_orders:
    sl = sl_orders[-1]
    exchange.edit_order(
        sl["id"], symbol, sl["type"], sl["side"],
        current_qty,
        params={"triggerPrice": float(sl["stopPrice"])},
    )
```

### Trovare e amendare lo SL — Mode C

SL position-level (attached via `tpslMode=Partial`). Non ha `orderLinkId` nostro. Si ricava dal campo `info.stopLoss` nella risposta di `fetch_positions`:

```python
pos_info = positions[0].get("info", {})
attached_sl_price = pos_info.get("stopLoss")
if attached_sl_price and float(attached_sl_price) > 0:
    position_idx = 1 if side == "LONG" else 2
    exchange.private_post_v5_position_trading_stop({
        "category": "linear",
        "symbol": pos_info["symbol"],   # formato Bybit nativo: BTCUSDT
        "positionIdx": position_idx,
        "stopLoss": attached_sl_price,  # prezzo invariato
        "slSize": str(current_qty),     # qty aggiornata
    })
```

### Idempotenza

Se lo SL ha già la qty corretta, `edit_order` è un no-op. Se non esiste nessun SL aperto (già chiuso o cancellato), il metodo restituisce `AdapterResult(success=True)` — non è un errore.

---

## §5 — OD-F1-2: fallback get_order_status per Mode C

### Problema

Ordini SL/TP creati via `native_attached_tpsl=True` non hanno il nostro `orderLinkId`. `fetch_open_orders` e `fetch_closed_orders` restituiscono nothing → `get_order_status` ritorna `None` → il sync worker non registra mai il fill.

### Soluzione: fallback via fetch_positions

```python
def get_order_status(self, *, client_order_id, execution_account_id):
    # path normale (invariato)
    orders = exchange.fetch_open_orders(None, params={"orderLinkId": client_order_id})
    if not orders:
        orders = exchange.fetch_closed_orders(None, params={"orderLinkId": client_order_id})
    if orders:
        return StatusMapper.map(orders[-1], client_order_id=client_order_id)

    # OD-F1-2 fallback: inferisci stato da posizione
    coid = coid_mod.parse(client_order_id)
    if coid.role not in ("sl", "tp"):
        return None

    symbol, side = self._resolve_symbol_side_from_repo(coid)
    if symbol is None:
        return None

    positions = exchange.fetch_positions([symbol])
    current_qty = _extract_qty(positions, side)

    if current_qty == 0.0:
        # posizione chiusa → SL o TP finale ha riempito
        return RawAdapterOrder(
            client_order_id=client_order_id,
            status="FILLED",
            filled_qty=0.0,
            average_price=None,
        )
    return None
```

`_resolve_symbol_side_from_repo` legge symbol e side dal payload in `ops_execution_commands` tramite `client_order_id`. Il repo viene iniettato in `CcxtBybitAdapter` al costruttore (opzionale: `repo: GatewayCommandRepository | None = None`).

### Limite accettato

Il fallback inferisce solo "posizione chiusa = FILLED". Non distingue quale TP o quale SL ha chiuso la posizione. Il lifecycle riceve l'evento e chiude la chain correttamente. I dettagli fill (prezzo medio, qty precisa) non sono disponibili in questo path — il campo `filled_qty=0.0` segnala al lifecycle che il dato è assente.

### Risoluzione con WebSocket

Con `watch_orders()` attivo (§3), Bybit pushes anche gli aggiornamenti degli ordini attached. Questo path fallback diventa secondario — rilevante solo quando il WS è disabilitato o durante la reconciliation iniziale.

---

## §6 — Hedge mode

### Config

`hedge_mode: bool = False` in `AdapterConfig`. Bybit hedge mode è una impostazione account-level — corrisponde a un adapter dedicato nel routing per-trader.

```yaml
bybit_trader_b:
  type: ccxt_bybit
  mode: paper
  connector: bybit
  testnet: true
  api_key: "xyz789"
  leverage: 5
  hedge_mode: true
```

Il campo `hedge_mode` in `config/operation_config.yaml` (per-trader) rimane indipendente — è usato dal lifecycle per decidere se ammettere posizioni opposte sullo stesso symbol. I due flag devono essere coerenti ma non sono accoppiati nel codice.

### BybitOrderBuilder in hedge mode

`build()` accetta `hedge_mode: bool = False`. Quando `True`:
- Aggiunge `positionIdx` a ogni ordine: `1` per LONG, `2` per SHORT
- Rimuove `reduceOnly` (non usato in hedge mode — è `positionIdx` che determina il lato)

Mapping per tipo ordine:

| command_type | Side | positionIdx | reduceOnly |
|---|---|---|---|
| `PLACE_ENTRY` LONG | buy | 1 | — |
| `PLACE_ENTRY` SHORT | sell | 2 | — |
| `PLACE_PROTECTIVE_STOP` su LONG | sell | 1 | rimosso |
| `PLACE_TAKE_PROFIT` su LONG | sell | 1 | rimosso |
| `CLOSE_PARTIAL` / `CLOSE_FULL` LONG | sell | 1 | rimosso |
| stesso per SHORT | buy | 2 | rimosso |

`CcxtBybitAdapter.place_order` passa `hedge_mode=self._hedge_mode` al builder.

### set_leverage in hedge mode

```python
params = {"buyLeverage": str(leverage), "sellLeverage": str(leverage)}
if self._hedge_mode:
    params["positionIdx"] = 0  # 0 = applica a entrambi i lati
exchange.set_leverage(leverage, symbol, params=params)
```

### get_position_qty in hedge mode

Il codice attuale funziona già: in hedge mode Bybit restituisce due entry separate per Long e Short, ognuna con `side` esplicito (`"long"` / `"short"`). Il filtro `pos_side == side.lower()` è corretto.

### SYNC_PROTECTIVE_ORDERS in hedge mode

In `_handle_amend_sl_qty`, il `positionIdx` corretto viene passato a `private_post_v5_position_trading_stop` anche in hedge mode (1 per LONG, 2 per SHORT). Nessuna logica aggiuntiva.

---

## §7 — Testing strategy

### Unit test

**`test_bybit_order_builder.py`** — esteso con:
- Ogni comando con `hedge_mode=True` → `positionIdx` presente, `reduceOnly` assente
- `SYNC_PROTECTIVE_ORDERS` → `action="amend_sl_qty"`

**`test_bybit_ws_fill_watcher.py`** — nuovo:
- Watcher con exchange mock che simula `watch_orders()` restituendo eventi fill
- Verifica che `ops_exchange_events` riceva l'INSERT corretto
- Verifica scarto di eventi con `clientOrderId` non riconosciuto
- Verifica idempotenza su fill duplicato (INSERT OR IGNORE)

**`test_ccxt_bybit_adapter_unit.py`** — esteso con:
- `SYNC_PROTECTIVE_ORDERS` Mode B: `fetch_positions` + `edit_order` chiamati correttamente
- `SYNC_PROTECTIVE_ORDERS` Mode C: `private_post_v5_position_trading_stop` chiamato
- `SYNC_PROTECTIVE_ORDERS` qty=0: cancel di tutti gli ordini reduce-only
- OD-F1-2: `get_order_status` con `orderLinkId` assente → fallback `fetch_positions` → FILLED

### Gated integration test

**`test_ccxt_bybit_gated.py`** — esteso con:
- `set_leverage` in hedge mode → non solleva
- `place_order(PLACE_ENTRY)` con `hedge_mode=True` → `positionIdx` nella risposta Bybit
- `SYNC_PROTECTIVE_ORDERS` su posizione aperta → SL amendato

---

## §8 — Open decisions

| ID | Decisione | Stato |
|---|---|---|
| OD-F1-2 | `get_order_status` fallback via `fetch_positions` per Mode C | **Chiusa** — implementata in Fase 2 |
| OD-F2-1 | `watch_orders()` ccxt.pro include gli ordini SL/TP attached Mode C? | **Aperta** — verificare su testnet; se no, il fallback OD-F1-2 resta il path principale per Mode C |
| OD-F2-2 | `private_post_v5_position_trading_stop` disponibile in ccxt versione corrente? | **Aperta** — verificare; fallback: `exchange.set_trading_stop()` se mappato |

---

## §9 — Fuori scope (Fase 3+)

- Altri exchange CCXT (OKX, Binance)
- WebSocket per market data (prezzi real-time)
- Gestione margin call e liquidation events
- Trailing stop nativo Bybit
